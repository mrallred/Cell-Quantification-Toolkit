import os
import json
import csv

from ij import IJ
from ij.gui import Overlay
from ij.plugin.frame import RoiManager

from javax.swing import JDialog, JPanel, JCheckBox, JLabel, JComboBox, BorderFactory
from javax.swing.border import EmptyBorder

from java.awt import GridLayout, BorderLayout
from java.awt.event import WindowAdapter, ItemListener

class ResultsViewer(WindowAdapter):
    """
    A self-contained dialog for viewing an image with toggleable overlays
    for analysis ROIs and quantified cell outlines from run-based folders.
    """
    def __init__(self, parent_frame, project_image, project):
        self.image_obj = project_image
        self.project = project
        self.imp = IJ.openImage(self.image_obj.full_path)
        if not self.imp:
            IJ.error("Failed to open image: " + self.image_obj.full_path)
            return
        self.imp.show()

        self.image_window = self.imp.getWindow()
        
        # Find runs containing this image
        self.available_runs = self._find_runs_for_image()
        self.selected_run = self.available_runs[0] if self.available_runs else None

        # Load analysis ROIs (these are per-image, not per-run)
        self.analysis_rois = self._load_rois_from_zip(self.image_obj.roi_path)
        
        # Load cell outlines from selected run
        self.outline_rois = self._load_outlines_for_run(self.selected_run) if self.selected_run else []

        # Build the control dialog
        self.dialog = JDialog(self.image_window, "Results Viewer: " + self.image_obj.filename, False)
        self.dialog.addWindowListener(self)

        self.image_window.addWindowListener(ImageWindowListener(self.dialog))
        
        # Use BorderLayout for main panel structure
        main_panel = JPanel(BorderLayout(5, 5))
        main_panel.setBorder(EmptyBorder(10, 10, 10, 10))

        # Top section: Run selector (only if multiple runs exist)
        if len(self.available_runs) > 1:
            run_panel = JPanel(GridLayout(1, 2, 5, 5))
            run_panel.setBorder(BorderFactory.createTitledBorder("Select Run"))
            run_panel.add(JLabel("Run:"))
            self.run_combo = JComboBox(self.available_runs)
            self.run_combo.addItemListener(RunChangeListener(self))
            run_panel.add(self.run_combo)
            main_panel.add(run_panel, BorderLayout.NORTH)
        
        # Center section: Overlay options
        overlay_panel = JPanel(GridLayout(2, 1, 2, 2))
        overlay_panel.setBorder(BorderFactory.createTitledBorder("Overlay Options"))
        
        self.analysis_checkbox = JCheckBox("Show Analysis ROIs", True)
        self.outlines_checkbox = JCheckBox("Show Cell Outlines", True)

        # Enable checkboxes only if their corresponding ROIs were found
        self.analysis_checkbox.setEnabled(bool(self.analysis_rois))
        self.outlines_checkbox.setEnabled(bool(self.outline_rois))

        # Add a single action listener to both
        action_listener = self._update_overlay
        self.analysis_checkbox.addActionListener(action_listener)
        self.outlines_checkbox.addActionListener(action_listener)

        overlay_panel.add(self.analysis_checkbox)
        overlay_panel.add(self.outlines_checkbox)
        main_panel.add(overlay_panel, BorderLayout.CENTER)
        
        # Bottom section: Processing metadata
        self.info_panel = JPanel(GridLayout(0, 1, 2, 2))
        self.info_panel.setBorder(BorderFactory.createTitledBorder("Processing Info"))
        self._update_info_panel()
        main_panel.add(self.info_panel, BorderLayout.SOUTH)
        
        self.dialog.add(main_panel)
        self.dialog.pack()
        self.dialog.setMinimumSize(self.dialog.getSize())

        # Initial display
        self._update_overlay()
    
    def _find_runs_for_image(self):
        """Find all runs that contain outlines for this image."""
        runs_with_image = []
        runs_dir = self.project.paths.get('runs', '')
        
        if not os.path.exists(runs_dir):
            return []
        
        base_name, _ = os.path.splitext(self.image_obj.filename)
        outline_name = base_name + "_Outlines.zip"
        
        # Scan all run folders
        for run_id in sorted(os.listdir(runs_dir), reverse=True):  # Most recent first
            run_path = os.path.join(runs_dir, run_id)
            if not os.path.isdir(run_path):
                continue
            
            cell_selections_dir = os.path.join(run_path, 'Cell_Selections')
            outline_path = os.path.join(cell_selections_dir, outline_name)
            
            if os.path.exists(outline_path):
                runs_with_image.append(run_id)
        
        return runs_with_image
    
    def _load_outlines_for_run(self, run_id):
        """Load cell outlines from a specific run."""
        if not run_id:
            return []
        
        base_name, _ = os.path.splitext(self.image_obj.filename)
        outline_path = os.path.join(
            self.project.paths['runs'], run_id,
            'Cell_Selections', base_name + "_Outlines.zip"
        )
        return self._load_rois_from_zip(outline_path)
    
    def _on_run_change(self, run_id):
        """Handle run selection change."""
        self.selected_run = run_id
        self.outline_rois = self._load_outlines_for_run(run_id)
        self.outlines_checkbox.setEnabled(bool(self.outline_rois))
        self._update_info_panel()
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
    
    def _load_metadata_for_run(self, run_id):
        """Load processing metadata from run's run_metadata.json."""
        if not run_id:
            return None
        
        try:
            metadata_path = os.path.join(
                self.project.paths['runs'], run_id, 'run_metadata.json'
            )
            
            if not os.path.exists(metadata_path):
                return None
            
            with open(metadata_path, 'r') as f:
                return json.load(f)
            
        except Exception as e:
            IJ.log("Could not load processing metadata: " + str(e))
            return None
    
    def _update_info_panel(self):
        """Update the info panel with metadata from selected run."""
        self.info_panel.removeAll()
        
        metadata = self._load_metadata_for_run(self.selected_run)
        if metadata:
            workflow_name = metadata.get('workflow_name', 'Unknown')
            processed_date = metadata.get('processed_date', 'Unknown')
            # Format date nicely if in ISO format
            if 'T' in processed_date:
                processed_date = processed_date.replace('T', ' ').split('.')[0]
            
            self.info_panel.add(JLabel("Workflow: " + workflow_name))
            self.info_panel.add(JLabel("Processed: " + processed_date))
            
            # Show all workflow settings dynamically (excluding non-display values)
            settings = metadata.get('workflow_settings', {})
            for key, value in sorted(settings.items()):
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
                self.info_panel.add(JLabel("{}: {}".format(display_key, display_value)))
        else:
            self.info_panel.add(JLabel("No metadata available"))
        
        self.info_panel.revalidate()
        self.info_panel.repaint()

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

class RunChangeListener(ItemListener):
    """Listener for run combo box selection changes."""
    def __init__(self, viewer):
        self.viewer = viewer
    
    def itemStateChanged(self, event):
        if event.getStateChange() == event.SELECTED:
            self.viewer._on_run_change(event.getItem())

class ImageWindowListener(WindowAdapter):
    """A listener that closes the control dialog when its image window is closed."""
    def __init__(self, viewer_dialog):
        self.viewer_dialog = viewer_dialog

    def windowClosing(self, event):
        # When the image window is closed by the user,
        # programmatically close and dispose of our control dialog.
        if self.viewer_dialog:
            self.viewer_dialog.dispose()
