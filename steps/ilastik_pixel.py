"""Segmentation provider: ilastik Pixel Classification -> probability map.

Optional **Append L*a*b*** mode (a checkbox in the workflow editor): when enabled,
each ROI crop is expanded to a 6-channel R,G,B,L*,a*,b* image (via
lib/color_lab.py) BEFORE prediction, and that image replaces ctx['temp_path'] so
the DOWNSTREAM object classifier runs on the same 6-channel layout too. When you
turn this on, both the pixel and object .ilp you select must have been trained on
images exported with macros/Export_RGB_plus_Lab_for_Training.py. Off (default) =
plain RGB, identical to the original behaviour.
"""
import os

from ij import IJ
from java.lang import System

# StepProvider is injected by the step registry when this file is discovered.
try:
    StepProvider
except NameError:
    import sys
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    from base_step import StepProvider


def _rgblab_converter():
    """Import the shared, canonical RGB->RGB+Lab file converter."""
    import sys
    root = os.path.join(IJ.getDirectory("plugins"), "Cell_Quantification_Toolkit")
    if root not in sys.path:
        sys.path.insert(0, root)
    from lib.color_lab import rgb_to_rgblab_file
    return rgb_to_rgblab_file


class IlastikPixelSegmentation(StepProvider):
    stage = "segmentation"
    type_id = "ilastik_pixel"
    display_name = "ilastik Pixel Classification"
    produces = ["probability_map"]
    consumes = []
    expected_workflow = "Pixel Classification"

    def default_params(self):
        return {'project': '', 'append_lab': False}

    def build_panel(self, params):
        from javax.swing import JPanel, JLabel, JComboBox, JCheckBox
        from java.awt import GridLayout
        params = params or {}
        self._models = self._list_models()
        names = sorted(self._models.keys())
        panel = JPanel(GridLayout(0, 2, 6, 6))
        panel.add(JLabel("Pixel classifier (.ilp):"))
        self._combo = JComboBox(names)
        if params.get('project') in self._models:
            self._combo.setSelectedItem(params.get('project'))
        panel.add(self._combo)
        panel.add(JLabel("Append L*a*b* channels:"))
        self._lab_check = JCheckBox(
            "Expand crop to R,G,B,L*,a*,b* (classifiers must be trained on RGB+Lab)",
            bool(params.get('append_lab', False)))
        panel.add(self._lab_check)
        return panel

    def gather_params(self, panel=None):
        proj = self._combo.getSelectedItem() if getattr(self, '_combo', None) is not None else ''
        p = {'project': proj or ''}
        chk = getattr(self, '_lab_check', None)
        p['append_lab'] = (bool(chk.isSelected()) if chk is not None
                           else bool(self.params.get('append_lab', False)))
        models = getattr(self, '_models', None) or self._list_models()
        if proj in models:
            p['project_path'] = models[proj]
        return p

    def validate(self):
        proj = self.params.get('project', '')
        if not proj:
            return ["No pixel classifier selected."]
        path = self.params.get('project_path') or self._list_models().get(proj)
        if not path or not os.path.exists(path):
            return ["Pixel classifier file not found: " + str(proj)]
        wf = self._ilp_workflow_name(path)
        if wf and wf != self.expected_workflow:
            return ["'{}' is a '{}', not a Pixel Classification project.".format(proj, wf)]
        return []

    def run(self, ctx):
        project = self.params.get('project_path', '')
        temp_path = ctx['temp_path']
        prob_map_path = ctx['prob_map_path']
        force = ctx.get('force_recalculate', False)
        append_lab = bool(self.params.get('append_lab', False))
        pixel_prob_path = prob_map_path + "_probabilities.tif"

        # Optional: expand the crop to R,G,B,L*,a*,b* and make it the input for
        # BOTH this stage and the downstream object classifier. Done BEFORE the
        # cache check so ctx['temp_path'] is set even when the probability map is
        # already cached (the object stage still needs the 6-channel image).
        if append_lab:
            rgblab_path = prob_map_path + "_rgblab.tif"
            if force and os.path.exists(rgblab_path):
                try:
                    os.remove(rgblab_path)
                except OSError:
                    pass
            if not os.path.exists(rgblab_path):
                _rgblab_converter()(temp_path, rgblab_path)
            temp_path = rgblab_path
            ctx['temp_path'] = rgblab_path

        # Reuse the cached probability map unless it was cleared upstream.
        if os.path.exists(pixel_prob_path):
            ctx['probability_map_path'] = pixel_prob_path
            return ctx

        cmd = ('run("Run Pixel Classification Prediction", '
               '"projectfilename=[{}] inputimage=[{}] pixelclassificationtype=Probabilities");').format(
                   project, temp_path)
        IJ.runMacro(cmd)
        pixel_imp = IJ.getImage()
        if not pixel_imp:
            raise Exception("No probability map was generated by the pixel classifier.")

        # Convert ilastik's virtual-stack output to a real image before saving/
        # closing (prevents the ImageJ2 colour-tool virtual-stack crash on close).
        self._materialize(pixel_imp)
        IJ.saveAs(pixel_imp, "Tiff", pixel_prob_path)
        pixel_imp.changes = False
        pixel_imp.close()

        # ilastik4ij also opens its own output display; close it unless the user
        # asked to see intermediate images.
        if not ctx.get('show_images', False):
            self._close_transient_windows(
                ["probabilit", "prediction", "exported_data", ".h5"])

        IJ.run("Collect Garbage", "")
        System.gc()

        ctx['probability_map_path'] = pixel_prob_path
        return ctx
