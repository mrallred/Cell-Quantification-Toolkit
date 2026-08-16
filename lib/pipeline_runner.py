"""
Pipeline runner: executes segmentation -> classification -> post-processing.

Constructed by workflow_config.build_workflow_instance with a definition and the
two providers. Exposes the interface the QuantificationWorker expects
(process_roi / analyze_results / get_result_columns). `run_post` is injected so
this module has no cross-package imports of its own.
"""
import os

from ij import IJ
from java.lang import System


class PipelineRunner(object):
    hidden = True

    def __init__(self, definition, segmentation, classification, run_post):
        self.definition = definition
        self.segmentation = segmentation
        self.classification = classification
        self._run_post = run_post

    @property
    def display_name(self):
        return self.definition.name if self.definition else "Pipeline"

    def _cell_classes(self):
        return self.definition.cell_classes() if self.definition else []

    def get_result_columns(self):
        cols = []
        for c in self._cell_classes():
            cols.append(c.get('key') + '_count')
            cols.append(c.get('key') + '_total_area')
        return cols

    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        show = settings.get('show_images', False)
        force = settings.get('force_recalculate', False)
        object_prob_path = prob_map_path + "_objects.tif"
        pixel_prob_path = prob_map_path + "_probabilities.tif"

        if force:
            for pth in (pixel_prob_path, object_prob_path):
                if os.path.exists(pth):
                    try:
                        os.remove(pth)
                    except OSError:
                        pass

        # Fast path: final class-label image already cached.
        if os.path.exists(object_prob_path):
            imp = IJ.openImage(object_prob_path)
            if show and imp:
                imp.show()
            return imp

        ctx = {
            'temp_path': temp_path,
            'prob_map_path': prob_map_path,
            'show_images': show,
            'force_recalculate': force,
        }
        self.segmentation.run(ctx)
        self.classification.run(ctx)

        imp = ctx.get('class_labels_imp')
        if not imp:
            raise Exception("Pipeline produced no class-label image.")
        IJ.run("Collect Garbage", "")
        System.gc()
        return imp

    def analyze_results(self, result_imp, roi, offset_x, offset_y, settings):
        post = self.definition.post if self.definition else {}
        return self._run_post(result_imp, roi, post, self._cell_classes())
