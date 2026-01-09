import os
import json
import csv

from ij import IJ
from ij.gui import Overlay
from ij.plugin.frame import RoiManager

from javax.swing import JDialog, JPanel, JCheckBox, JLabel, BorderFactory
from javax.swing.border import EmptyBorder

from java.awt import GridLayout, BorderLayout
from java.awt.event import WindowAdapter

class ResultsViewer(WindowAdapter):
    """
    A self-contained dialog for viewing an image with toggleable overlays
    for analysis ROIs and quantified cell outlines.
    """
    def __init__(self, parent_frame, project_image):
        self.image_obj = project_image
        self.imp = IJ.openImage(self.image_obj.full_path)
        if not self.imp:
            IJ.error("Failed to open image: " + self.image_obj.full_path)
            return
        self.imp.show()

        self.image_window = self.imp.getWindow()

        # Load both sets of ROIs into memory
        self.analysis_rois = self._load_rois_from_zip(self.image_obj.roi_path)
        self.outline_rois = self._load_rois_from_zip(self.image_obj.outline_path)

        # Build the control dialog
        self.dialog = JDialog(self.image_window, "Results Viewer: " + self.image_obj.filename, False)
        self.dialog.setSize(300, 150)
        self.dialog.addWindowListener(self)

        self.image_window.addWindowListener(ImageWindowListener(self.dialog))
        
        panel = JPanel(GridLayout(0, 1, 10, 10))
        panel.setBorder(EmptyBorder(10, 10, 10, 10))

        # Create checkboxes
        self.analysis_checkbox = JCheckBox("Show Analysis ROIs", True)
        self.outlines_checkbox = JCheckBox("Show Cell Outlines", True)

        # Enable checkboxes only if their corresponding ROIs were found
        self.analysis_checkbox.setEnabled(bool(self.analysis_rois))
        self.outlines_checkbox.setEnabled(bool(self.outline_rois))

        # Add a single action listener to both
        action_listener = self._update_overlay
        self.analysis_checkbox.addActionListener(action_listener)
        self.outlines_checkbox.addActionListener(action_listener)

        panel.add(self.analysis_checkbox)
        panel.add(self.outlines_checkbox)
        
        # Load and display processing metadata
        metadata = self._load_metadata_for_image()
        if metadata:
            info_panel = JPanel(GridLayout(0, 1, 5, 5))
            info_panel.setBorder(BorderFactory.createTitledBorder("Processing Info"))
            
            workflow_name = metadata.get('workflow_name', 'Unknown')
            processed_date = metadata.get('processed_date', 'Unknown')
            # Format date nicely if in ISO format
            if 'T' in processed_date:
                processed_date = processed_date.replace('T', ' ').split('.')[0]
            
            info_panel.add(JLabel("Workflow: " + workflow_name))
            info_panel.add(JLabel("Processed: " + processed_date))
            
            # Show all workflow settings dynamically (excluding non-display values)
            settings = metadata.get('workflow_settings', {})
            exclude_keys = ['workflow', 'workflow_name', 'images', 'show_images']
            for key, value in sorted(settings.items()):
                if key in exclude_keys:
                    continue
                # Format key nicely: apply_watershed -> Apply watershed
                display_key = key.replace('_', ' ').capitalize()
                # Format boolean values as Yes/No
                if isinstance(value, bool) or value in (0, 1, '0', '1', 'True', 'False'):
                    if value in (True, 1, '1', 'True'):
                        display_value = 'Yes'
                    else:
                        display_value = 'No'
                else:
                    display_value = str(value)
                info_panel.add(JLabel("{}: {}".format(display_key, display_value)))
            
            panel.add(info_panel)
        
        self.dialog.add(panel)
        self.dialog.pack()
        self.dialog.setSize(max(300, self.dialog.getWidth()), self.dialog.getHeight())

        # Initial display
        self._update_overlay()

    def _load_rois_from_zip(self, zip_path):
        """Helper function to load all ROIs from a zip file into a list."""
        if not os.path.exists(zip_path):
            return []
        rm = RoiManager(True)
        rm.open(zip_path)
        rois = rm.getRoisAsArray()
        rm.close()
        return list(rois)
    
    def _load_metadata_for_image(self):
        """
        Load processing metadata for this image by:
        1. Finding the processing_run_id from Results_DB.csv
        2. Looking up that run in processing_log.json
        """
        try:
            # Derive paths from image location
            # Image is in Images/, CSV and JSON are in project root
            project_dir = os.path.dirname(os.path.dirname(self.image_obj.full_path))
            csv_path = os.path.join(project_dir, 'Results_DB.csv')
            metadata_path = os.path.join(project_dir, 'processing_log.json')
            
            if not os.path.exists(metadata_path):
                return None
            
            # Find this image's run_id from the CSV
            run_id = None
            if os.path.exists(csv_path):
                with open(csv_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('filename') == self.image_obj.filename:
                            run_id = row.get('processing_run_id')
                            break
            
            if not run_id:
                return None
            
            # Load metadata log and get this run's entry
            with open(metadata_path, 'r') as f:
                all_metadata = json.load(f)
            
            return all_metadata.get(run_id)
            
        except Exception as e:
            IJ.log("Could not load processing metadata: " + str(e))
            return None

    def _update_overlay(self, event=None):
        """Builds and applies a new overlay based on checkbox states."""
        overlay = Overlay()

        if self.analysis_checkbox.isSelected() and self.analysis_rois:
            for roi in self.analysis_rois:
                overlay.add(roi)
        
        if self.outlines_checkbox.isSelected() and self.outline_rois:
            for roi in self.outline_rois:
                overlay.add(roi)
        
        self.imp.setOverlay(overlay)
        self.imp.updateAndDraw()

    def show(self):
        """Positions and shows the dialog."""
        if not self.dialog: return
        # Position control dialog next to the image window
        self.dialog.setLocation(self.imp.getWindow().getX() + self.imp.getWindow().getWidth(), self.imp.getWindow().getY())
        self.dialog.setVisible(True)

    def windowClosing(self, event):
        """Cleans up when the dialog is closed."""
        if self.imp:
            self.imp.close()

class ImageWindowListener(WindowAdapter):
    """A listener that closes the control dialog when its image window is closed."""
    def __init__(self, viewer_dialog):
        self.viewer_dialog = viewer_dialog

    def windowClosing(self, event):
        # When the image window is closed by the user,
        # programmatically close and dispose of our control dialog.
        if self.viewer_dialog:
            self.viewer_dialog.dispose()
