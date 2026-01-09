"""
Template Workflow - Skeleton for creating custom quantification workflows.

This file demonstrates the BaseWorkflow plugin interface with examples of
various UI controls for the settings panel. Copy this file and customize
for your specific quantification needs.

The workflow will be automatically discovered when Fiji restarts.
"""
import os

from ij import IJ
from ij.measure import ResultsTable, Measurements
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.lang import System

# Swing imports for building the settings panel
from javax.swing import (JPanel, JLabel, JComboBox, JCheckBox, JSpinner,
                         JTextField, JSlider, SpinnerNumberModel, BorderFactory)
from java.awt import GridLayout, BorderLayout

# BaseWorkflow is injected by the workflow loader - do not import


class TemplateWorkflow(BaseWorkflow):
    """
    Template workflow demonstrating the plugin interface.
    
    This skeleton shows:
    - How to create a settings panel with various UI controls
    - How to extract settings from the panel
    - The structure of process_roi() and analyze_results()
    
    To create your own workflow:
    1. Copy this file to a new file in the workflows directory (e.g., my_workflow.py)
    2. Rename the class
    3. Set display_name and description
    4. Customize get_settings_panel() for your needs
    5. Implement process_roi() with your detection logic
    6. Implement analyze_results() to extract measurements
    """
    
    # ========================================================================
    # METADATA - Shown in the workflow dropdown
    # ========================================================================
    display_name = "Template Workflow"
    description = "Skeleton template demonstrating the workflow plugin interface"
    
    def __init__(self):
        """Initialize UI component references."""
        # Store references to UI components for later retrieval
        self.model_combo = None
        self.threshold_spinner = None
        self.min_size_spinner = None
        self.max_size_spinner = None
        self.exclude_edges_checkbox = None
        self.watershed_checkbox = None
        self.channel_combo = None
        self.notes_field = None
        
        # Store models dict reference
        self._models_dict = {}
    
    # ========================================================================
    # SETTINGS PANEL - UI Controls
    # ========================================================================
    
    def get_settings_panel(self, models_dict):
        """
        Build and return a JPanel with workflow-specific settings.
        
        This method demonstrates various common UI controls:
        - JComboBox: dropdowns for model selection, channel selection
        - JSpinner: numeric inputs with min/max bounds
        - JCheckBox: boolean toggles
        - JTextField: free-form text input
        
        Args:
            models_dict: dict of {display_name: full_path} for available Ilastik models
            
        Returns:
            JPanel containing the settings UI
        """
        self._models_dict = models_dict or {}
        
        # Main panel with grid layout (rows, cols, hgap, vgap)
        # Use 0 rows to auto-expand based on components added
        panel = JPanel(GridLayout(0, 2, 10, 8))
        panel.setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10))
        
        # ---------------------------------------------------------------------
        # EXAMPLE 1: Model/Classifier Selection (JComboBox)
        # ---------------------------------------------------------------------
        panel.add(JLabel("Classification Model:"))
        model_names = list(self._models_dict.keys()) if self._models_dict else ["No models found"]
        self.model_combo = JComboBox(model_names)
        panel.add(self.model_combo)
        
        # ---------------------------------------------------------------------
        # EXAMPLE 2: Channel Selection (JComboBox with fixed options)
        # ---------------------------------------------------------------------
        panel.add(JLabel("Image Channel:"))
        channels = ["All Channels", "Red", "Green", "Blue", "Gray"]
        self.channel_combo = JComboBox(channels)
        panel.add(self.channel_combo)
        
        # ---------------------------------------------------------------------
        # EXAMPLE 3: Threshold Value (JSpinner with bounds)
        # ---------------------------------------------------------------------
        # SpinnerNumberModel(initial_value, min, max, step)
        panel.add(JLabel("Threshold (0-255):"))
        self.threshold_spinner = JSpinner(SpinnerNumberModel(128, 0, 255, 1))
        panel.add(self.threshold_spinner)
        
        # ---------------------------------------------------------------------
        # EXAMPLE 4: Particle Size Range (paired JSpinners)
        # ---------------------------------------------------------------------
        panel.add(JLabel("Min Particle Size (px):"))
        self.min_size_spinner = JSpinner(SpinnerNumberModel(20, 0, 100000, 10))
        panel.add(self.min_size_spinner)
        
        panel.add(JLabel("Max Particle Size (px):"))
        # Use float('inf') in code, but UI needs a large concrete value
        self.max_size_spinner = JSpinner(SpinnerNumberModel(10000, 0, 1000000, 100))
        panel.add(self.max_size_spinner)
        
        # ---------------------------------------------------------------------
        # EXAMPLE 5: Boolean Options (JCheckBox)
        # ---------------------------------------------------------------------
        panel.add(JLabel("Analysis Options:"))
        # Empty label to maintain grid alignment
        panel.add(JLabel(""))
        
        self.exclude_edges_checkbox = JCheckBox("Exclude edge particles", True)
        panel.add(self.exclude_edges_checkbox)
        
        self.watershed_checkbox = JCheckBox("Apply watershed segmentation", True)
        panel.add(self.watershed_checkbox)
        
        # ---------------------------------------------------------------------
        # EXAMPLE 6: Text Input (JTextField)
        # ---------------------------------------------------------------------
        panel.add(JLabel("Notes (optional):"))
        self.notes_field = JTextField(20)
        panel.add(self.notes_field)
        
        return panel
    
    def gather_settings(self, panel):
        """
        Extract settings from the panel into a dictionary.
        
        Called when the user clicks 'Run' in the dialog.
        The returned dict is merged into the global settings and passed
        to process_roi() and available throughout processing.
        
        Args:
            panel: the JPanel returned by get_settings_panel()
            
        Returns:
            dict of workflow-specific settings
        """
        settings = {}
        
        # Get model path from name
        if self.model_combo:
            model_name = self.model_combo.getSelectedItem()
            settings['model_path'] = self._models_dict.get(model_name, '')
            settings['model_name'] = model_name
        
        # Get channel selection
        if self.channel_combo:
            settings['channel'] = self.channel_combo.getSelectedItem()
        
        # Get numeric values from spinners
        if self.threshold_spinner:
            settings['threshold'] = int(self.threshold_spinner.getValue())
        
        if self.min_size_spinner:
            settings['min_size'] = int(self.min_size_spinner.getValue())
            
        if self.max_size_spinner:
            settings['max_size'] = int(self.max_size_spinner.getValue())
        
        # Get boolean values from checkboxes
        if self.exclude_edges_checkbox:
            settings['exclude_edges'] = self.exclude_edges_checkbox.isSelected()
            
        if self.watershed_checkbox:
            settings['apply_watershed'] = self.watershed_checkbox.isSelected()
        
        # Get text field value
        if self.notes_field:
            settings['notes'] = self.notes_field.getText()
        
        return settings
    
    # ========================================================================
    # RESULT COLUMNS - Define custom CSV output columns
    # ========================================================================
    
    def get_result_columns(self):
        """
        Return list of custom CSV column names for this workflow.
        
        These columns are added to the base columns (filename, roi_name, 
        roi_area, bregma_value) in the Results_DB.csv output.
        
        The analyze_results() method should return a dict containing
        values for each of these column names.
        
        Returns:
            list of str column names
        """
        return ['object_count', 'total_object_area', 'mean_object_size']
    
    # ========================================================================
    # PROCESS ROI - Main detection/processing logic
    # ========================================================================
    
    def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
        """
        Run detection/classification on a cropped ROI image.
        
        This is where your main image processing logic goes.
        
        Common operations:
        - Run Ilastik classification
        - Apply thresholding
        - Run other ImageJ/Fiji plugins
        - Apply filters or transformations
        
        Args:
            cropped_imp: ImagePlus of the cropped ROI region
            temp_path: path to temporary saved cropped image (for external tools)
            prob_map_path: base path for saving intermediate outputs
            settings: dict containing all settings from gather_settings() plus
                      system settings like 'show_images'
                      
        Returns:
            ImagePlus: result image to be passed to analyze_results()
                       (e.g., binary mask, classification output, etc.)
        """
        show_images = settings.get('show_images', False)
        threshold = settings.get('threshold', 128)
        channel = settings.get('channel', 'All Channels')
        
        IJ.log("Template workflow processing ROI...")
        IJ.log("  - Using threshold: {}".format(threshold))
        IJ.log("  - Channel: {}".format(channel))
        
        # ---------------------------------------------------------------------
        # EXAMPLE: Basic thresholding workflow
        # Replace this with your actual processing logic
        # ---------------------------------------------------------------------
        
        # Duplicate to avoid modifying original
        result_imp = cropped_imp.duplicate()
        result_imp.setTitle("template_result_" + str(System.nanoTime()))
        
        # Convert to 8-bit if needed
        if result_imp.getBitDepth() != 8:
            IJ.run(result_imp, "8-bit", "")
        
        # Optional: Extract specific channel
        # if channel != "All Channels":
        #     # Channel extraction logic here
        #     pass
        
        # Apply threshold (example using manual threshold)
        IJ.setThreshold(result_imp, threshold, 255)
        IJ.run(result_imp, "Convert to Mask", "")
        
        # Optional: Apply watershed
        if settings.get('apply_watershed', True):
            IJ.run(result_imp, "Watershed", "")
        
        # Save intermediate result if needed for debugging
        # output_path = prob_map_path + "_processed.tif"
        # IJ.saveAs(result_imp, "Tiff", output_path)
        
        if show_images:
            result_imp.show()
        
        return result_imp
    
    # ========================================================================
    # ANALYZE RESULTS - Extract measurements from processed image
    # ========================================================================
    
    def analyze_results(self, result_imp, roi, offset_x, offset_y):
        """
        Analyze the processed result image and extract measurements.
        
        This method should:
        1. Run particle analysis or other measurement extraction
        2. Collect ROI outlines for visualization
        3. Return a dict with required keys plus custom columns
        
        Args:
            result_imp: ImagePlus from process_roi()
            roi: original ROI object (for masking if needed)
            offset_x: x coordinate of ROI bounding box (for coordinate translation)
            offset_y: y coordinate of ROI bounding box (for coordinate translation)
            
        Returns:
            dict with keys:
                - 'count': int, number of detected objects
                - 'total_area': float, sum of object areas  
                - 'outlines': list of ROI objects for cell outlines
                - Plus any keys matching get_result_columns()
        """
        # Get settings for particle analysis
        # Note: settings dict isn't passed here, but you stored them in __init__ 
        # if needed, or use sensible defaults
        
        # Initialize ROI manager and results table
        rm = RoiManager(True)  # True = don't show window
        rt = ResultsTable()
        
        # Configure particle analyzer options
        # Common options:
        #   ParticleAnalyzer.SHOW_OUTLINES - get particle outlines
        #   ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES - exclude particles touching edges
        #   ParticleAnalyzer.INCLUDE_HOLES - include particles with holes
        options = ParticleAnalyzer.SHOW_OUTLINES
        
        # Uncomment to exclude edge particles:
        # options |= ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
        
        # Configure measurements to collect
        # Common measurements:
        #   Measurements.AREA - particle area
        #   Measurements.MEAN - mean intensity
        #   Measurements.INTEGRATED_DENSITY - sum of pixel values
        #   Measurements.PERIMETER - particle perimeter
        measurements = Measurements.AREA
        
        # Create particle analyzer
        # ParticleAnalyzer(options, measurements, rt, minSize, maxSize, minCirc, maxCirc)
        min_size = 20  # Minimum particle size in pixels
        max_size = float('inf')  # Maximum particle size (infinity = no limit)
        
        pa = ParticleAnalyzer(options, measurements, rt, min_size, max_size, 0.0, 1.0)
        pa.setRoiManager(rm)
        pa.analyze(result_imp)
        
        # Extract statistics from results table
        count = rt.getCounter()
        total_area = 0
        mean_size = 0
        
        if count > 0:
            area_col_index = rt.getColumnIndex("Area")
            if area_col_index != -1:
                area_values = rt.getColumn(area_col_index)
                if area_values is not None:
                    total_area = sum(area_values)
                    mean_size = total_area / count
        
        # Get particle outlines
        particle_outlines = rm.getRoisAsArray() or []
        rm.reset()
        rm.close()
        
        # Clean up result image
        result_imp.changes = False
        result_imp.close()
        
        # Return results dictionary
        # Outlines are in cropped image coordinates - base code handles translation
        return {
            'outlines': particle_outlines,  # Optional: for cell selection visualization
            
            # Custom columns (must match get_result_columns())
            'object_count': count,
            'total_object_area': total_area,
            'mean_object_size': mean_size
        }
