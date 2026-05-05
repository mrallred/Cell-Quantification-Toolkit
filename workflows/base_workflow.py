"""
Base workflow abstract class defining the plugin interface.
All workflow plugins must inherit from BaseWorkflow and implement the required methods.
"""

class BaseWorkflow(object):
    """
    Abstract base class for all quantification workflows.
    
    Subclasses must override:
        - display_name: str shown in dialog dropdown
        - process_roi(): classification/detection logic
        - analyze_results(): measurement extraction
    
    Optional overrides:
        - description: str shown as tooltip
        - get_settings_panel(): custom UI for workflow settings
        - gather_settings(): extract settings from custom panel
        - get_result_columns(): custom CSV columns beyond base columns
    """
    
    # Metadata shown in dialog
    display_name = "Unnamed Workflow"
    description = ""
    
    def get_settings_panel(self, models_dict):
        """
        Return a JPanel with workflow-specific settings UI, or None for no custom settings.
        
        Args:
            models_dict: dict of {display_name: path} for available Ilastik models
            
        Returns:
            JPanel or None
        """
        return None
    
    def gather_settings(self, panel):
        """
        Extract settings from the custom panel into a dict.
        Called when user clicks 'Run'.
        
        Args:
            panel: the JPanel returned by get_settings_panel()
            
        Returns:
            dict of workflow-specific settings
        """
        return {}
    
    def get_result_columns(self):
        """
        Return list of custom CSV column names for this workflow.
        These are in addition to base columns (filename, roi_name, roi_area, bregma_value).
        
        Returns:
            list of str column names
        """
        return []
    
    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        """
        Run classification/detection on a cropped ROI image.
        
        Args:
            cropped_imp: ImagePlus of the cropped ROI region
            temp_path: path to temporary saved cropped image (for external tools)
            prob_map_path: base path for saving probability/classification outputs
            settings: dict containing workflow settings from gather_settings()
            
        Returns:
            ImagePlus: result image for analysis (e.g., classification labels)
        """
        raise NotImplementedError("Subclasses must implement process_roi()")
    
    def analyze_results(self, result_imp, roi, offset_x, offset_y, settings):
        """
        Analyze the processed result image and extract measurements.
        
        Args:
            result_imp: ImagePlus from process_roi()
            roi: original ROI object (for masking)
            offset_x: x coordinate of ROI bounding box (for coordinate translation)
            offset_y: y coordinate of ROI bounding box (for coordinate translation)
            settings: dict containing workflow settings from gather_settings()
            
        Returns:
            dict with:
                - Keys matching get_result_columns() for CSV output
                - Optional 'outlines': list of ROI objects for cell visualization
        """
        raise NotImplementedError("Subclasses must implement analyze_results()")
