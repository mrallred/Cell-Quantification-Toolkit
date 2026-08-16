import os
import glob
import json
import csv

from ij import IJ
from ij.gui import Overlay
from ij.plugin.frame import RoiManager

from javax.swing import JDialog, JPanel, JCheckBox, JLabel, JComboBox, BorderFactory
from javax.swing.border import EmptyBorder

from java.awt import GridLayout, BorderLayout, Color
from java.awt.event import WindowAdapter, ItemListener

# Display config for multi-class cell outlines, keyed by the 'cell_class'
# property that workflows write onto each outline ROI. Order here controls the
# order the toggles appear in. Outlines with no 'cell_class' property (legacy
# runs and single-class workflows) fall back to a single default toggle.
OUTLINE_CLASS_CONFIG = [
    ("cfos", "cFos", Color.RED),
    ("ctb", "CtB", Color.CYAN),
    ("cfos_ctb", "cFos+CtB", Color.YELLOW),
]
DEFAULT_OUTLINE_KEY = "_default"
DEFAULT_OUTLINE_LABEL = "Cell Outlines"
DEFAULT_OUTLINE_COLOR = Color.MAGENTA


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
        
        # Load cell outlines from selected run and group them by class
        self.outline_rois = self._load_outlines_for_run(self.selected_run) if self.selected_run else []
        self.outline_buckets = self._bucket_outlines(self.outline_rois)
        self.outline_checkboxes = {}

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
        
        # Center section: Overlay options (analysis ROIs + one toggle per cell class)
        self.overlay_panel = JPanel(GridLayout(0, 1, 2, 2))
        self.overlay_panel.setBorder(BorderFactory.createTitledBorder("Overlay Options"))

        self.analysis_checkbox = JCheckBox("Show Analysis ROIs", True)
        self.analysis_checkbox.setEnabled(bool(self.analysis_rois))
        self.analysis_checkbox.addActionListener(self._update_overlay)

        self._populate_overlay_panel()
        main_panel.add(self.overlay_panel, BorderLayout.CENTER)
        
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
    
    def _bucket_outlines(self, outlines):
        """Group outlines by their 'cell_class' property.

        Outlines with no 'cell_class' (legacy runs and single-class workflows)
        are collected under DEFAULT_OUTLINE_KEY so they still render.
        """
        buckets = {}
        for roi in outlines:
            cell_class = None
            try:
                cell_class = roi.getProperty("cell_class")
            except Exception:
                cell_class = None
            key = cell_class if cell_class else DEFAULT_OUTLINE_KEY
            buckets.setdefault(key, []).append(roi)
        return buckets

    def _ordered_outline_groups(self):
        """Return present outline groups as (key, label, color, rois).

        Configured classes come first in OUTLINE_CLASS_CONFIG order; any
        remaining keys (default bucket or unknown classes) follow.
        """
        groups = []
        seen = set()
        for key, label, color in OUTLINE_CLASS_CONFIG:
            rois = self.outline_buckets.get(key)
            if rois:
                groups.append((key, label, color, rois))
                seen.add(key)
        for key, rois in self.outline_buckets.items():
            if key in seen or not rois:
                continue
            if key == DEFAULT_OUTLINE_KEY:
                groups.append((key, DEFAULT_OUTLINE_LABEL, DEFAULT_OUTLINE_COLOR, rois))
            else:
                groups.append((key, key, DEFAULT_OUTLINE_COLOR, rois))
        return groups

    def _populate_overlay_panel(self):
        """(Re)build the overlay checkboxes: analysis ROIs + one per cell class."""
        self.overlay_panel.removeAll()
        self.outline_checkboxes = {}

        self.overlay_panel.add(self.analysis_checkbox)

        for key, label, color, rois in self._ordered_outline_groups():
            checkbox = JCheckBox("Show " + label, True)
            checkbox.setForeground(color)
            checkbox.setEnabled(bool(rois))
            checkbox.addActionListener(self._update_overlay)
            self.outline_checkboxes[key] = checkbox
            self.overlay_panel.add(checkbox)

        self.overlay_panel.revalidate()
        self.overlay_panel.repaint()

    def _on_run_change(self, run_id):
        """Handle run selection change."""
        self.selected_run = run_id
        self.outline_rois = self._load_outlines_for_run(run_id)
        self.outline_buckets = self._bucket_outlines(self.outline_rois)
        self._populate_overlay_panel()
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
            run_path = os.path.join(self.project.paths['runs'], run_id)
            metadata_path = os.path.join(run_path, 'run_metadata.json')

            if not os.path.exists(metadata_path):
                # Older runs saved this file with a date prefix
                # (e.g. 20260728_run_metadata.json).
                legacy_matches = sorted(
                    glob.glob(os.path.join(run_path, '*_run_metadata.json'))
                )
                if not legacy_matches:
                    return None
                metadata_path = legacy_matches[-1]

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

        # Add each enabled cell-class group, enforcing its color so overlays are
        # consistent even when a saved ROI lacks a stored stroke color.
        for key, label, color, rois in self._ordered_outline_groups():
            checkbox = self.outline_checkboxes.get(key)
            if checkbox is not None and checkbox.isSelected():
                for roi in rois:
                    roi.setStrokeColor(color)
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
