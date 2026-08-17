"""Classification provider: ilastik Object Classification -> class-label image."""
import os

from ij import IJ
from java.lang import System

try:
    StepProvider
except NameError:
    import sys
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    from base_step import StepProvider


class IlastikObjectClassification(StepProvider):
    stage = "classification"
    type_id = "ilastik_object"
    display_name = "ilastik Object Classification"
    produces = ["class_labels"]
    consumes = ["probability_map"]
    expected_workflow = "Object Classification (from prediction image)"

    def default_params(self):
        return {'project': ''}

    def build_panel(self, params):
        from javax.swing import JPanel, JLabel, JComboBox
        from java.awt import GridLayout
        params = params or {}
        self._models = self._list_models()
        names = sorted(self._models.keys())
        panel = JPanel(GridLayout(0, 2, 6, 6))
        panel.add(JLabel("Object classifier (.ilp):"))
        self._combo = JComboBox(names)
        if params.get('project') in self._models:
            self._combo.setSelectedItem(params.get('project'))
        panel.add(self._combo)
        return panel

    def gather_params(self, panel=None):
        proj = self._combo.getSelectedItem() if getattr(self, '_combo', None) is not None else ''
        p = {'project': proj or ''}
        models = getattr(self, '_models', None) or self._list_models()
        if proj in models:
            p['project_path'] = models[proj]
        return p

    def validate(self):
        proj = self.params.get('project', '')
        if not proj:
            return ["No object classifier selected."]
        path = self.params.get('project_path') or self._list_models().get(proj)
        if not path or not os.path.exists(path):
            return ["Object classifier file not found: " + str(proj)]
        wf = self._ilp_workflow_name(path)
        if wf and wf != self.expected_workflow:
            return ["'{}' is a '{}', not an Object Classification project.".format(proj, wf)]
        return []

    def run(self, ctx):
        project = self.params.get('project_path', '')
        temp_path = ctx['temp_path']
        prob_map_path = ctx['prob_map_path']
        show_images = ctx.get('show_images', False)
        prob_path = ctx.get('probability_map_path') or (prob_map_path + "_probabilities.tif")
        object_prob_path = prob_map_path + "_objects.tif"

        if os.path.exists(object_prob_path):
            imp = IJ.openImage(object_prob_path)
            if show_images and imp:
                imp.show()
            ctx['class_labels_imp'] = imp
            ctx['class_labels_path'] = object_prob_path
            return ctx

        cmd = ('run("Run Object Classification Prediction", '
               '"projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] '
               'secondinputtype=Probabilities objectexportsource=[Object Predictions]");').format(
                   project, temp_path, prob_path)
        IJ.runMacro(cmd)
        object_imp = IJ.getImage()
        if not object_imp:
            raise Exception("Object classification did not produce a result image.")

        # Convert ilastik's virtual-stack output to a real image before saving/
        # closing (prevents the ImageJ2 colour-tool virtual-stack crash on close).
        self._materialize(object_imp)
        IJ.saveAs(object_imp, "Tiff", object_prob_path)
        if not show_images:
            object_imp.hide()
            # Close ilastik4ij's own output display (the saved object_imp, now
            # titled '..._objects.tif', is kept and returned).
            self._close_transient_windows(
                ["probabilit", "prediction", "exported_data", ".h5"])
        IJ.run("Collect Garbage", "")
        System.gc()

        ctx['class_labels_imp'] = object_imp
        ctx['class_labels_path'] = object_prob_path
        return ctx
