"""
Brightfield cFos workflow - Cell detection using Ilastik pixel + object classification.
Migrated from the original quantification.py hardcoded workflow.
"""
import os

from ij import IJ
from ij.measure import ResultsTable, Measurements
from ij.plugin import ImageCalculator
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.lang import System

from javax.swing import JPanel, JLabel, JComboBox, JCheckBox
from java.awt import GridLayout, BorderLayout

# BaseWorkflow is injected by the workflow loader - do not import



class BrightfieldCfosWorkflow(BaseWorkflow):
    """
    Cell detection workflow using Ilastik two-step classification:
    1. Pixel Classification - generates probability maps
    2. Object Classification - identifies individual cells
    """
    
    display_name = "Brightfield cFos"
    description = "Cell detection using Ilastik pixel + object classification for DAB-stained tissue"
    
    def __init__(self):
        self.pixel_combo = None
        self.object_combo = None
        self.watershed_checkbox = None
        self.exclude_edges_checkbox = None
    
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
        
        self.watershed_checkbox = JCheckBox("Apply watershed segmentation", True)
        panel.add(self.watershed_checkbox)
        
        self.exclude_edges_checkbox = JCheckBox("Exclude edge particles", True)
        panel.add(self.exclude_edges_checkbox)
        
        # Store models_dict reference for gather_settings
        self._models_dict = models_dict
        
        return panel
    
    def gather_settings(self, panel):
        """Extract selected classifiers from panel."""
        settings = {}
        if self.pixel_combo and self.object_combo and self._models_dict:
            pixel_name = self.pixel_combo.getSelectedItem()
            object_name = self.object_combo.getSelectedItem()
            settings['pixel_classifier'] = self._models_dict.get(pixel_name, '')
            settings['object_classifier'] = self._models_dict.get(object_name, '')
        
        # Get analysis options
        if self.watershed_checkbox:
            settings['apply_watershed'] = self.watershed_checkbox.isSelected()
        if self.exclude_edges_checkbox:
            settings['exclude_edges'] = self.exclude_edges_checkbox.isSelected()
        
        return settings
    
    def get_result_columns(self):
        """Return custom columns for this workflow."""
        return ['cell_count', 'total_cell_area']
    
    def get_log_metadata(self, settings):
        """Return model names and settings for processing log."""
        return {
            'pixel_classifier': os.path.basename(settings.get('pixel_classifier', '')),
            'object_classifier': os.path.basename(settings.get('object_classifier', '')),
            'apply_watershed': settings.get('apply_watershed', True),
            'exclude_edges': settings.get('exclude_edges', True)
        }
    
    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        """
        Run the full Ilastik workflow with resume capability.
        """
        # Store settings for use in analyze_results
        self._current_settings = settings
        
        pixel_imp = None
        try:
            pixel_classifier = settings.get('pixel_classifier', '')
            object_classifier = settings.get('object_classifier', '')
            show_images = settings.get('show_images', False)
    
            pixel_prob_path = prob_map_path + "_probabilities.tif"
            object_prob_path = prob_map_path + "_objects.tif"

            # Case 1: Final object classification file exists - skip processing
            if os.path.exists(object_prob_path):
                result_imp = IJ.openImage(object_prob_path)
                if show_images:
                    result_imp.show()
                return result_imp

            # Case 2: Intermediate pixel probability exists - run object classification only
            elif os.path.exists(pixel_prob_path):
                pixel_imp = IJ.openImage(pixel_prob_path)
                if not show_images:
                    pixel_imp.hide()

                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities");'.format(
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

            # Case 3: Neither file exists - run full workflow
            else:
                # Pixel Classification
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

                # Object Classification
                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] inputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities");'.format(
                    object_classifier, temp_path, pixel_prob_path)
                IJ.runMacro(object_macro_cmd)
                object_imp = IJ.getImage()
                
                if not object_imp or (pixel_imp and object_imp.getID() == pixel_imp.getID()):
                    raise Exception("Object classification did not produce a new result image.")
                
                IJ.saveAs(object_imp, "Tiff", object_prob_path)
                if show_images:
                    object_imp.show()

                IJ.run("Collect Garbage", "")
                System.gc()
                return object_imp

        except Exception as e:
            IJ.log("Ilastik processing failed: " + str(e))
            raise e
        finally:
            if pixel_imp:
                pixel_imp.changes = False
                pixel_imp.close()

    def analyze_results(self, result_imp, roi, offset_x, offset_y):
        """
        Analyze Ilastik output: threshold, watershed, particle analysis.
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

        # Apply ROI mask using AND operation
        ic = ImageCalculator()
        ic.run("AND", result_imp, mask_imp)

        mask_imp.changes = False
        mask_imp.close()

        # Threshold and convert to binary
        IJ.setThreshold(result_imp, 1, 65535)
        IJ.run(result_imp, "Convert to Mask", "")

        # Watershed to separate touching cells (configurable)
        apply_watershed = getattr(self, '_current_settings', {}).get('apply_watershed', True)
        if apply_watershed:
            IJ.run(result_imp, "Watershed", "")
        
        rm = RoiManager(True)
        rt = ResultsTable()

        # Particle analysis (exclude edges is configurable)
        exclude_edges = getattr(self, '_current_settings', {}).get('exclude_edges', True)
        options = ParticleAnalyzer.SHOW_OUTLINES
        if exclude_edges:
            options |= ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
        measurements = Measurements.AREA
        pa = ParticleAnalyzer(options, measurements, rt, 20, float('inf'), 0.0, 1.0)
        pa.setRoiManager(rm)
        pa.analyze(result_imp)

        # Extract statistics
        count = rt.getCounter()
        total_area = 0
        if count > 0:
            area_col_index = rt.getColumnIndex("Area")
            if area_col_index != -1:
                area_col = rt.getColumn(area_col_index)
                if area_col is not None:
                    total_area = sum(area_col)

        # Get particle outlines
        particle_outlines_relative = rm.getRoisAsArray()
        rm.reset()
        rm.close()
        
        result_imp.changes = False
        result_imp.close()

        if particle_outlines_relative is None:
            particle_outlines_relative = []

        return {
            'count': count,
            'total_area': total_area,
            'outlines': particle_outlines_relative,  # Translation handled by base code
            # Custom columns for CSV
            'cell_count': count,
            'total_cell_area': total_area
        }
