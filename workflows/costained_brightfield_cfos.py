"""
Two color brightfield cFos workflow for colabeled cFos in brown and CtB in blue/gray - cell detection using Ilastik pixel + object classification. Segements all positive cells, identifies them as cFos+, CtB+, or cFos & CtB+.

Uses `Pixel.ilp` and `Object.ilp` Ilastik models.
"""
import os
import math
import traceback

from ij import IJ
from ij.gui import Roi, PolygonRoi, Wand
from ij.measure import ResultsTable, Measurements
from ij.plugin import ImageCalculator
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.lang import System

from javax.swing import JPanel, JLabel, JComboBox, JCheckBox, JSpinner, SpinnerNumberModel
from java.awt import GridLayout, BorderLayout, Color

# BaseWorkflow is injected by the workflow loader - do not import

# Object-classifier label mapping for this workflow.
# Pixel value 0 in the Object Predictions image is background; each detected
# object carries its class label as its pixel value. (label, class_key, display, color)
# NOTE: if a test object-prediction image ever shows different values, this is
# the place to correct them.
CELL_CLASSES = [
    (1, "cfos", "cFos", Color.RED),
    (2, "ctb", "CtB", Color.CYAN),
    (3, "cfos_ctb", "cFos+CtB", Color.YELLOW),
]


class CostainedBrightfieldCfosWorkflow(BaseWorkflow):
    """
    Cell detection workflow using Ilastik two-step classification:
    1. Pixel Classification - generates probability maps
    2. Object Classification - identifies individual cells and classifies them as either cFos+, CtB+, or cFos & CtB+.

    The object step is exported twice: once as *Object Predictions* (each object
    painted with its class label) and once as *Object Identities* (each object
    painted with a unique id). Analysis iterates the unique ids so ilastik's own
    object boundaries are preserved and touching same-class cells are never
    merged into one oversized particle; each object's class is read back from the
    predictions image. If the identities export or the identity-based analysis is
    unavailable for any reason, the workflow falls back to the legacy per-class
    method so a run never fails outright.
    """

    display_name = "Brightfield Costained cFos + CtB"
    description = "Cell detection using Ilastik pixel + object classification for constained DAB-cFos and CtB (blue/gray) tissue"

    def __init__(self):
        self.pixel_combo = None
        self.object_combo = None
        self.watershed_checkbox = None
        self.exclude_edges_checkbox = None
        self.min_circularity_spinner = None
        self.min_size_spinner = None
        # Set per-ROI by process_roi so analyze_results can pair the class
        # predictions image with its matching object-identities image.
        self._object_ids_path = None

    def get_settings_panel(self, models_dict):
        """Create panel with pixel and object classifier dropdowns."""
        panel = JPanel(GridLayout(0, 2, 10, 10))

        models = list(models_dict.keys()) if models_dict else []

        # Pixel classifier selection
        panel.add(JLabel("Pixel Classification Project:"))
        self.pixel_combo = JComboBox(models)
        panel.add(self.pixel_combo)

        # Object classifier selection
        panel.add(JLabel("Object Classification Project:"))
        self.object_combo = JComboBox(models)
        panel.add(self.object_combo)

        # Analysis options
        panel.add(JLabel("Analysis Options:"))
        panel.add(JLabel(""))  # Empty label for grid alignment

        # Watershed is only used by the legacy per-class fallback. When the
        # Object Identities image is available, ilastik has already separated
        # touching cells, so no watershed is applied.
        self.watershed_checkbox = JCheckBox("Apply watershed segmentation (fallback only)", True)
        panel.add(self.watershed_checkbox)

        self.exclude_edges_checkbox = JCheckBox("Exclude edge particles", True)
        panel.add(self.exclude_edges_checkbox)

        # Circularity filter (0.0 = any shape, 1.0 = perfect circles only)
        panel.add(JLabel("Min Circularity (0.0-1.0):"))
        self.min_circularity_spinner = JSpinner(SpinnerNumberModel(0.0, 0.0, 1.0, 0.1))
        panel.add(self.min_circularity_spinner)

        # Minimum particle area (pixels). Should match (or be below) the object
        # classifier's segmentation MinSize so cells ilastik keeps are not
        # dropped here. Default 10 matches the ThresholdTwoLevels MinSize.
        panel.add(JLabel("Min Cell Area (px):"))
        self.min_size_spinner = JSpinner(SpinnerNumberModel(10, 1, 1000000, 1))
        panel.add(self.min_size_spinner)

        # Store models_dict reference for gather_settings
        self._models_dict = models_dict

        return panel

    def gather_settings(self, panel):
        """Extract selected classifiers from panel."""
        settings = {}
        if self.pixel_combo and self.object_combo and self._models_dict:
            pixel_name = self.pixel_combo.getSelectedItem()
            object_name = self.object_combo.getSelectedItem()
            # Store basenames for logging, full paths in separate keys for processing
            settings['pixel_classifier'] = os.path.basename(self._models_dict.get(pixel_name, ''))
            settings['object_classifier'] = os.path.basename(self._models_dict.get(object_name, ''))
            # Store full paths for actual processing (not logged)
            settings['_pixel_classifier_path'] = self._models_dict.get(pixel_name, '')
            settings['_object_classifier_path'] = self._models_dict.get(object_name, '')

        # Get analysis options
        if self.watershed_checkbox:
            settings['apply_watershed'] = self.watershed_checkbox.isSelected()
        if self.exclude_edges_checkbox:
            settings['exclude_edges'] = self.exclude_edges_checkbox.isSelected()
        if self.min_circularity_spinner:
            settings['min_circularity'] = float(self.min_circularity_spinner.getValue())
        if self.min_size_spinner:
            settings['min_cell_size'] = float(self.min_size_spinner.getValue())

        return settings

    def get_result_columns(self):
        """Return custom columns for this workflow (per class: count + total area)."""
        columns = []
        for _label, key, _display, _color in CELL_CLASSES:
            columns.append(key + '_count')
            columns.append(key + '_total_area')
        return columns

    def _run_object_classification(self, object_classifier, temp_path,
                                   pixel_prob_path, export_source, out_path,
                                   show_images):
        """
        Run one ilastik Object Classification export pass and save the result.

        export_source is the ilastik `objectexportsource` value, e.g.
        'Object Predictions' (class labels) or 'Object Identities' (unique ids).
        Returns the resulting ImagePlus (also saved to out_path).
        """
        object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities objectexportsource=[{}]");'.format(
            object_classifier, temp_path, pixel_prob_path, export_source)
        IJ.runMacro(object_macro_cmd)
        result_imp = IJ.getImage()

        if not result_imp:
            raise Exception("Object classification ({}) produced no result image.".format(export_source))

        IJ.saveAs(result_imp, "Tiff", out_path)
        if not show_images:
            result_imp.hide()

        IJ.run("Collect Garbage", "")
        System.gc()
        return result_imp

    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        """
        Run the full Ilastik workflow with resume capability.

        Produces two object-classification exports that share the same object
        footprints:
            *_objects.tif      - Object Predictions (each object = its class label)
            *_identities.tif   - Object Identities (each object = a unique id)
        The predictions image is returned for analysis; the identities path is
        stashed on self so analyze_results can pair them. A failure of the
        identities export is logged and does NOT fail the ROI - analysis then
        falls back to the legacy per-class method.
        """
        pixel_imp = None
        show_images = settings.get('show_images', False)
        try:
            # Use full paths for Ilastik (basenames are stored separately for logging)
            pixel_classifier = settings.get('_pixel_classifier_path', '')
            object_classifier = settings.get('_object_classifier_path', '')
            force_recalculate = settings.get('force_recalculate', False)

            pixel_prob_path = prob_map_path + "_probabilities.tif"
            object_prob_path = prob_map_path + "_objects.tif"
            object_ids_path = prob_map_path + "_identities.tif"

            # Assume identities will be available; cleared below if it fails.
            self._object_ids_path = object_ids_path

            # If force recalculate is enabled, delete existing derived files
            if force_recalculate:
                for stale_path in (pixel_prob_path, object_prob_path, object_ids_path):
                    if os.path.exists(stale_path):
                        os.remove(stale_path)

            # Case 1: both final object outputs exist - skip processing entirely
            if os.path.exists(object_prob_path) and os.path.exists(object_ids_path):
                result_imp = IJ.openImage(object_prob_path)
                if show_images:
                    result_imp.show()
                return result_imp

            # Ensure the pixel-probability map exists (compute it only if missing)
            if not os.path.exists(pixel_prob_path):
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

            # Object Classification - pass 1: class predictions (each object = class label)
            object_imp = None
            if not os.path.exists(object_prob_path):
                object_imp = self._run_object_classification(
                    object_classifier, temp_path, pixel_prob_path,
                    "Object Predictions", object_prob_path, show_images)

            # Object Classification - pass 2: unique object identities (reuses cached pixel probs).
            # Guarded: if this export is unsupported/fails, keep going with the
            # predictions image only and let analysis fall back to per-class.
            if not os.path.exists(object_ids_path):
                try:
                    ids_imp = self._run_object_classification(
                        object_classifier, temp_path, pixel_prob_path,
                        "Object Identities", object_ids_path, show_images)
                    if ids_imp and not show_images:
                        ids_imp.changes = False
                        ids_imp.close()
                except Exception as ids_err:
                    IJ.log("Object Identities export failed ({}); using per-class fallback for this ROI.".format(ids_err))
                    self._object_ids_path = None
                    # Remove any partial/incorrect file so we don't reuse it.
                    if os.path.exists(object_ids_path):
                        try:
                            os.remove(object_ids_path)
                        except Exception:
                            pass

            # Return the class-prediction image for analysis. If we skipped its
            # computation above (it already existed), open it from disk now.
            if object_imp is None:
                object_imp = IJ.openImage(object_prob_path)
                if show_images:
                    object_imp.show()

            IJ.run("Collect Garbage", "")
            System.gc()
            return object_imp

        except Exception as e:
            IJ.log("Ilastik processing failed: " + str(e))
            raise e
        finally:
            # Keep the intermediate pixel-probability image open when the user
            # asked to see images during processing.
            if pixel_imp and not show_images:
                pixel_imp.changes = False
                pixel_imp.close()

    def analyze_results(self, result_imp, roi, offset_x, offset_y, settings):
        """
        Analyze ilastik object-classification output.

        Preferred path uses BOTH exported images: result_imp is the Object
        Predictions image (each object painted with its class label) and the
        matching Object Identities image (each object painted with a unique id)
        is opened from self._object_ids_path. Iterating by unique id preserves
        ilastik's own object boundaries, so touching same-class cells stay
        separate. Each object's class is read from the predictions image at the
        same location.

        If the identities image is missing, or identity-based analysis raises for
        any reason, this falls back to the legacy per-class method so the run
        always produces outlines and the results viewer can open.
        """
        ids_path = getattr(self, '_object_ids_path', None)
        ids_imp = None
        if ids_path and os.path.exists(ids_path):
            ids_imp = IJ.openImage(ids_path)

        if ids_imp is not None:
            try:
                return self._analyze_by_identity(result_imp, ids_imp, roi, settings)
            except Exception as e:
                IJ.log("Identity-based analysis failed ({}); falling back to per-class.".format(e))
                IJ.log(traceback.format_exc())
            finally:
                ids_imp.changes = False
                ids_imp.close()
        else:
            IJ.log("Object Identities image not found - using per-class analysis.")

        return self._analyze_by_class(result_imp, roi, settings)

    def _analyze_by_identity(self, pred_imp, ids_imp, roi, settings):
        """
        One outline per ilastik object, using the unique-id (identities) image
        for boundaries and the predictions image for each object's class.
        """
        width = ids_imp.getWidth()
        height = ids_imp.getHeight()

        exclude_edges = settings.get('exclude_edges', True)
        min_circularity = settings.get('min_circularity', 0.0)
        min_cell_size = settings.get('min_cell_size', 10)

        # Calibrated area per pixel so totals match the old ParticleAnalyzer
        # (both are pixel counts when the image is uncalibrated).
        cal = ids_imp.getCalibration()
        unit_area = cal.pixelWidth * cal.pixelHeight

        ids_ip = ids_imp.getProcessor()
        pred_ip = pred_imp.getProcessor()

        # Build an 8-bit ROI mask (255 inside the selection, 0 outside) using
        # only core ImageJ ops, then zero every id pixel outside the ROI so
        # traced outlines are clipped to the selection. (An AND with a 255 mask
        # would corrupt 16-bit object-id values, so we zero explicitly instead.)
        roi_clone = roi.clone()
        roi_clone.setLocation(0, 0)
        mask_imp = IJ.createImage("idmask_" + str(System.nanoTime()),
                                  "8-bit black", width, height, 1)
        mask_imp.setRoi(roi_clone)
        IJ.run(mask_imp, "Fill", "slice")
        mask_imp.deleteRoi()
        mask_ip = mask_imp.getProcessor()

        # Single pass over the id image: clip outside-ROI pixels to 0 and record
        # a seed pixel, pixel area, and class label (from predictions) per id.
        #
        # IMPORTANT: ilastik exports Object Identities as uint32, which ImageJ
        # loads as a 32-bit FLOAT image. On a float processor get()/set() operate
        # on raw IEEE bits, not pixel values - so we must use getf()/setf(), which
        # return/accept true pixel values for 8-bit, 16-bit, and 32-bit alike.
        # Object ids are integer-valued, so we round to an int key.
        seed_x = {}
        seed_y = {}
        px_area = {}
        obj_class = {}
        n = width * height
        for i in range(n):
            if mask_ip.getf(i) == 0:
                ids_ip.setf(i, 0.0)
                continue
            v = int(round(ids_ip.getf(i)))
            if v == 0:
                continue
            if v in px_area:
                px_area[v] += 1
            else:
                seed_x[v] = i % width
                seed_y[v] = i // width
                obj_class[v] = int(round(pred_ip.getf(i)))
                px_area[v] = 1

        mask_imp.changes = False
        mask_imp.close()

        # class label -> (key, display, color)
        class_lookup = {}
        for label, key, display, color in CELL_CLASSES:
            class_lookup[label] = (key, display, color)

        counts = {}
        areas = {}
        for _label, key, _display, _color in CELL_CLASSES:
            counts[key] = 0
            areas[key] = 0.0

        results = {'outlines': []}

        for v in px_area.keys():
            cell_area = px_area[v] * unit_area

            # Minimum size filter (calibrated area, matching the old MinSize).
            if cell_area < min_cell_size:
                continue

            cls = obj_class.get(v, 0)
            if cls not in class_lookup:
                # Object whose class is background/untracked - skip.
                continue
            key, display, color = class_lookup[cls]

            # Trace this object's exact boundary from the id image.
            wand = Wand(ids_ip)
            wand.autoOutline(seed_x[v], seed_y[v], v, v, Wand.EIGHT_CONNECTED)
            if wand.npoints == 0:
                continue
            outline = PolygonRoi(wand.xpoints, wand.ypoints, wand.npoints, Roi.TRACED_ROI)

            bounds = outline.getBounds()

            # Exclude particles touching the crop edge (configurable).
            if exclude_edges and (bounds.x <= 0 or bounds.y <= 0 or
                                  bounds.x + bounds.width >= width or
                                  bounds.y + bounds.height >= height):
                continue

            # Circularity filter (configurable; 0.0 disables it).
            if min_circularity > 0.0:
                perim = outline.getLength()
                if perim <= 0:
                    circularity = 1.0
                else:
                    circularity = (4.0 * math.pi * cell_area) / (perim * perim)
                    if circularity > 1.0:
                        circularity = 1.0
                if circularity < min_circularity:
                    continue

            idx = counts[key] + 1
            outline.setName("{}_{}".format(display, idx))
            outline.setProperty("cell_class", key)
            outline.setStrokeColor(color)
            results['outlines'].append(outline)

            counts[key] = idx
            areas[key] += cell_area

        for _label, key, _display, _color in CELL_CLASSES:
            results[key + '_count'] = counts[key]
            results[key + '_total_area'] = areas[key]

        return results

    def _analyze_by_class(self, result_imp, roi, settings):
        """
        Legacy fallback: threshold the predictions image to each class label and
        run particle analysis. Used only when the Object Identities image is
        unavailable. Note: touching same-class cells may merge into a single
        particle here - watershed mitigates but does not fully fix this.
        """
        # Create mask from ROI
        width = result_imp.getWidth()
        height = result_imp.getHeight()
        mask_title = "mask_" + str(System.nanoTime())
        mask_imp = IJ.createImage(mask_title, "8-bit black", width, height, 1)

        roi_clone = roi.clone()
        roi_clone.setLocation(0, 0)
        mask_imp.setRoi(roi_clone)
        IJ.run(mask_imp, "Fill", "slice")
        mask_imp.deleteRoi()

        # Apply ROI mask to the labeled object image (AND keeps labels inside ROI).
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

        # Process each object class independently against the shared label image.
        for label, key, display, color in CELL_CLASSES:
            # Duplicate the labeled (masked) image so Convert to Mask, which is
            # destructive, does not consume it before the next class is processed.
            class_imp = result_imp.duplicate()
            class_imp.setTitle("class_{}_{}".format(key, System.nanoTime()))

            # Isolate only this class's label, then binarize.
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

            # Tag each outline with its class + color so the viewer can split them.
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
