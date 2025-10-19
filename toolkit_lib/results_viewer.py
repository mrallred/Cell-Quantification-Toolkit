import os

from ij import IJ
from ij.gui import Overlay
from ij.plugin.frame import RoiManager

from javax.swing import JDialog, JPanel, JCheckBox
from javax.swing.border import EmptyBorder

from java.awt import GridLayout
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
        self.dialog.add(panel)

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
