"""
Generic, definition-driven ilastik workflow.

A single workflow that runs the two-step ilastik pipeline (pixel classification
-> object classification) and analyzes the resulting Object Predictions image
according to a WorkflowDefinition's class map. This replaces the hardcoded
brightfield_cfos / costained_brightfield_cfos workflows: single-label is just a
definition with one 'cell' class, multi-class is a definition with several.

It is constructed directly with a definition (see workflow_config.build_workflow_instance),
not discovered from the workflows folder, so it is flagged hidden = True.
"""
import os
import sys

from ij import IJ
from ij.measure import ResultsTable, Measurements
from ij.plugin import ImageCalculator
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.lang import System
from java.awt import Color

# BaseWorkflow is injected by the workflow loader when this file is discovered.
# When imported directly (the normal path for this class) it is not present, so
# import it from the sibling base_workflow module.
try:
    BaseWorkflow
except NameError:
    _wf_dir = os.path.dirname(os.path.abspath(__file__))
    if _wf_dir not in sys.path:
        sys.path.insert(0, _wf_dir)
    from base_workflow import BaseWorkflow


def _awt_color(rgb):
    """Convert an [r, g, b] list to java.awt.Color (defaults to yellow)."""
    try:
        r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
        return Color(r, g, b)
    except Exception:
        return Color.YELLOW


class ConfigurableIlastikWorkflow(BaseWorkflow):
    """Definition-driven ilastik pixel + object classification workflow."""

    # Constructed directly with a definition; keep it out of the code-discovery picker.
    hidden = True

    def __init__(self, definition=None):
        self.definition = definition

    # ---- metadata (used only if ever discovered; real name comes from definition) ----
    @property
    def display_name(self):
        return self.definition.name if self.definition else "Configurable ilastik Workflow"

    @property
    def description(self):
        return self.definition.description if self.definition else ""

    def _cell_classes(self):
        return self.definition.cell_classes() if self.definition else []

    def get_result_columns(self):
        """Per cell class: <key>_count and <key>_total_area."""
        columns = []
        for c in self._cell_classes():
            key = c.get('key')
            columns.append(key + '_count')
            columns.append(key + '_total_area')
        return columns

    # -----------------------------------------------------------------
    # Processing (pixel -> object ilastik), unchanged pipeline
    # -----------------------------------------------------------------
    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        pixel_imp = None
        show_images = settings.get('show_images', False)
        try:
            pixel_classifier = settings.get('_pixel_classifier_path', '')
            object_classifier = settings.get('_object_classifier_path', '')
            force_recalculate = settings.get('force_recalculate', False)

            pixel_prob_path = prob_map_path + "_probabilities.tif"
            object_prob_path = prob_map_path + "_objects.tif"

            if force_recalculate:
                if os.path.exists(pixel_prob_path):
                    os.remove(pixel_prob_path)
                if os.path.exists(object_prob_path):
                    os.remove(object_prob_path)

            # Case 1: final object classification file exists - skip processing
            if os.path.exists(object_prob_path):
                result_imp = IJ.openImage(object_prob_path)
                if show_images:
                    result_imp.show()
                return result_imp

            # Case 2: intermediate pixel probability exists - run object classification only
            elif os.path.exists(pixel_prob_path):
                pixel_imp = IJ.openImage(pixel_prob_path)
                if not show_images:
                    pixel_imp.hide()

                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities objectexportsource=[Object Predictions]");'.format(
                    object_classifier, temp_path, pixel_prob_path)
                IJ.runMacro(object_macro_cmd)
                object_imp = IJ.getImage()

                if not object_imp or (pixel_imp and object_imp.getID() == pixel_imp.getID()):
                    raise Exception("Object classification did not produce a new result image.")

                IJ.saveAs(object_imp, "Tiff", object_prob_path)
                if not show_images:
                    object_imp.hide()

                IJ.run("Collect Garbage", "")
                System.gc()
                return object_imp

            # Case 3: neither file exists - run the full workflow
            else:
                pixel_macro_cmd = 'run("Run Pixel Classification Prediction", "projectfilename=[{}] inputimage=[{}] pixelclassificationtype=Probabilities");'.format(
                    pixel_classifier, temp_path)
                IJ.runMacro(pixel_macro_cmd)
                pixel_imp = IJ.getImage()

                if not pixel_imp:
                    raise Exception("No probability map was generated by pixel classifier.")

                IJ.saveAs(pixel_imp, "Tiff", pixel_prob_path)
                if not show_images:
                    pixel_imp.hide()

                IJ.run("Collect Garbage", "")
                System.gc()

                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities objectexportsource=[Object Predictions]");'.format(
                    object_classifier, temp_path, pixel_prob_path)
                IJ.runMacro(object_macro_cmd)
                object_imp = IJ.getImage()

                if not object_imp or (pixel_imp and object_imp.getID() == pixel_imp.getID()):
                    raise Exception("Object classification did not produce a new result image.")

                IJ.saveAs(object_imp, "Tiff", object_prob_path)
                if not show_images:
                    object_imp.hide()

                IJ.run("Collect Garbage", "")
                System.gc()
                return object_imp

        except Exception as e:
            IJ.log("Ilastik processing failed: " + str(e))
            raise e
        finally:
            if pixel_imp and not show_images:
                pixel_imp.changes = False
                pixel_imp.close()

    # -----------------------------------------------------------------
    # Analysis: one particle-analysis pass per 'cell' class
    # -----------------------------------------------------------------
    def analyze_results(self, result_imp, roi, offset_x, offset_y, settings):
        width = result_imp.getWidth()
        height = result_imp.getHeight()

        # ROI mask
        mask_title = "mask_" + str(System.nanoTime())
        mask_imp = IJ.createImage(mask_title, "8-bit black", width, height, 1)
        roi_clone = roi.clone()
        roi_clone.setLocation(0, 0)
        mask_imp.setRoi(roi_clone)
        IJ.run(mask_imp, "Fill", "slice")
        mask_imp.deleteRoi()

        ic = ImageCalculator()
        ic.run("AND", result_imp, mask_imp)
        mask_imp.changes = False
        mask_imp.close()

        apply_watershed = settings.get('apply_watershed', True)
        exclude_edges = settings.get('exclude_edges', True)
        min_circularity = settings.get('min_circularity', 0.0)
        min_cell_size = settings.get('min_cell_size', 10)

        options = ParticleAnalyzer.SHOW_OUTLINES
        if exclude_edges:
            options |= ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
        measurements = Measurements.AREA

        results = {'outlines': []}

        for cls in self._cell_classes():
            label = cls.get('label')
            key = cls.get('key')
            display = cls.get('display', key)
            color = _awt_color(cls.get('color'))

            class_imp = result_imp.duplicate()
            class_imp.setTitle("class_{}_{}".format(key, System.nanoTime()))

            IJ.setThreshold(class_imp, label, label)
            IJ.run(class_imp, "Convert to Mask", "")

            if apply_watershed:
                IJ.run(class_imp, "Watershed", "")

            rm = RoiManager(True)
            rt = ResultsTable()
            pa = ParticleAnalyzer(options, measurements, rt, min_cell_size, float('inf'), min_circularity, 1.0)
            pa.setRoiManager(rm)
            pa.analyze(class_imp)

            count = rt.getCounter()
            total_area = 0
            if count > 0:
                area_col_index = rt.getColumnIndex("Area")
                if area_col_index != -1:
                    area_col = rt.getColumn(area_col_index)
                    if area_col is not None:
                        total_area = sum(area_col)

            class_outlines = rm.getRoisAsArray()
            rm.reset()
            rm.close()
            if class_outlines is None:
                class_outlines = []

            for idx, outline in enumerate(class_outlines):
                outline.setName("{}_{}".format(display, idx + 1))
                outline.setProperty("cell_class", key)
                outline.setStrokeColor(color)
                results['outlines'].append(outline)

            results[key + '_count'] = count
            results[key + '_total_area'] = total_area

            class_imp.changes = False
            class_imp.close()

        if not settings.get('show_images', False):
            result_imp.changes = False
            result_imp.close()

        return results
