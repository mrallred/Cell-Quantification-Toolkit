import os
import glob
import json
import csv

from ij import IJ
from ij.gui import Overlay
from ij.plugin.frame import RoiManager

from javax.swing import (JDialog, JPanel, JCheckBox, JLabel, JComboBox, JButton,
                         JSpinner, SpinnerNumberModel, BorderFactory, JOptionPane)
from javax.swing.border import EmptyBorder

from java.awt import GridLayout, BorderLayout, Color
from java.awt.event import WindowAdapter, ItemListener

from . import results_export as rexport

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

        # Center: overlay toggles (top) + interactive post-processing (below)
        center = JPanel(BorderLayout(5, 5))
        center.add(self.overlay_panel, BorderLayout.NORTH)
        center.add(self._build_post_panel(), BorderLayout.CENTER)
        main_panel.add(center, BorderLayout.CENTER)

        # Bottom section: Processing metadata
        self.info_panel = JPanel(GridLayout(0, 1, 2, 2))
        self.info_panel.setBorder(BorderFactory.createTitledBorder("Processing Info"))
        self._update_info_panel()
        main_panel.add(self.info_panel, BorderLayout.SOUTH)
        
        self.dialog.add(main_panel)
        self.dialog.pack()
        self.dialog.setMinimumSize(self.dialog.getSize())

        # Initial display, then auto-preview so detected objects show immediately
        # (post-processing is computed from cached labels, not from a prior export).
        self._update_overlay()
        self._preview()

    # ------------------------------------------------------------------
    # Interactive post-processing
    # ------------------------------------------------------------------
    def _build_post_panel(self):
        panel = JPanel(BorderLayout(5, 5))
        panel.setBorder(BorderFactory.createTitledBorder("Post-processing (interactive)"))

        init = self._initial_post()
        controls = JPanel(GridLayout(0, 2, 4, 4))
        self.ws_cb = JCheckBox("Apply watershed", bool(init.get('apply_watershed', True)))
        self.edge_cb = JCheckBox("Exclude edge particles", bool(init.get('exclude_edges', True)))
        controls.add(self.ws_cb)
        controls.add(self.edge_cb)
        controls.add(JLabel("Min Cell Area (px):"))
        self.minsize_spin = JSpinner(SpinnerNumberModel(int(init.get('min_cell_size', 10)), 1, 1000000, 1))
        controls.add(self.minsize_spin)
        controls.add(JLabel("Min Circularity:"))
        self.mincirc_spin = JSpinner(SpinnerNumberModel(float(init.get('min_circularity', 0.0)), 0.0, 1.0, 0.1))
        controls.add(self.mincirc_spin)
        panel.add(controls, BorderLayout.NORTH)

        self.counts_label = JLabel(" ")
        panel.add(self.counts_label, BorderLayout.CENTER)

        btns = JPanel(GridLayout(0, 2, 4, 4))
        self.preview_btn = JButton("Preview", actionPerformed=self._preview)
        # One export button: the SAME post-processing is applied to every image
        # in the run (no per-image settings), and only then is the CSV written.
        self.export_btn = JButton("Export results (all images)", actionPerformed=self._export_all)
        btns.add(self.preview_btn)
        btns.add(self.export_btn)
        panel.add(btns, BorderLayout.SOUTH)

        # Live preview when any control changes
        self.ws_cb.addActionListener(self._preview)
        self.edge_cb.addActionListener(self._preview)
        self.minsize_spin.addChangeListener(self._preview)
        self.mincirc_spin.addChangeListener(self._preview)

        # Recompute needs the class map (with label values) from the run snapshot.
        if not self._cell_classes_for_run():
            for b in (self.export_btn, self.preview_btn):
                b.setEnabled(False)
            self.counts_label.setText("Interactive editing needs a workflow snapshot "
                                      "(re-run this workflow to enable).")
        return panel

    def _cell_classes_for_run(self, run_id=None):
        run_id = run_id or self.selected_run
        meta = self._load_metadata_for_run(run_id) if run_id else None
        defn = meta.get('workflow_definition') if meta else None
        if not defn:
            return []
        return [c for c in defn.get('classes', [])
                if c.get('include', c.get('role', 'cell') == 'cell')]

    def _initial_post(self):
        post = {}
        meta = self._load_metadata_for_run(self.selected_run) if self.selected_run else None
        if meta:
            defn = meta.get('workflow_definition')
            if isinstance(defn, dict) and isinstance(defn.get('post'), dict):
                post = dict(defn['post'])
            if isinstance(meta.get('post_overrides'), dict):
                post.update(meta['post_overrides'])
        merged = {'apply_watershed': True, 'exclude_edges': True,
                  'min_cell_size': 10, 'min_circularity': 0.0}
        merged.update(post)
        return merged

    def _post_params(self):
        return {
            'apply_watershed': bool(self.ws_cb.isSelected()),
            'exclude_edges': bool(self.edge_cb.isSelected()),
            'min_cell_size': int(self.minsize_spin.getValue()),
            'min_circularity': float(self.mincirc_spin.getValue()),
        }

    def _update_counts_label(self, rows, classes, missing):
        totals = dict((c.get('key'), 0) for c in classes)
        for r in rows:
            for c in classes:
                k = c.get('key')
                totals[k] += r.get(k + '_count', 0)
        parts = ["{}: {}".format(c.get('display', c.get('key')), totals[c.get('key')]) for c in classes]
        txt = "   ".join(parts)
        if missing:
            txt += "    ({} ROI(s) not yet processed)".format(missing)
        self.counts_label.setText(txt)

    def _preview(self, event=None):
        classes = self._cell_classes_for_run()
        if not classes:
            return
        outlines, rows, missing = rexport.recompute_image(
            self.project, self.image_obj, self._post_params(), classes)
        self.outline_rois = outlines
        self.outline_buckets = self._bucket_outlines(outlines)
        self._populate_overlay_panel()
        self._update_overlay()
        self._update_counts_label(rows, classes, missing)

    def _save_this_image(self, event=None):
        classes = self._cell_classes_for_run()
        if not classes:
            return
        post = self._post_params()
        outlines, rows, missing = rexport.recompute_image(self.project, self.image_obj, post, classes)
        rexport.write_image_outlines(self.project, self.selected_run, self.image_obj, outlines)
        rexport.splice_image_into_csv(self.project, self.selected_run, self.image_obj, rows, classes)
        rexport.update_run_post(self.project, self.selected_run, post)
        self.outline_rois = outlines
        self.outline_buckets = self._bucket_outlines(outlines)
        self._populate_overlay_panel()
        self._update_overlay()
        self._update_counts_label(rows, classes, missing)
        JOptionPane.showMessageDialog(self.dialog,
                                      "Saved results for {}.".format(self.image_obj.filename),
                                      "Saved", JOptionPane.INFORMATION_MESSAGE)

    def _export_all(self, event=None):
        classes = self._cell_classes_for_run()
        if not classes:
            return
        result = JOptionPane.showConfirmDialog(
            self.dialog,
            "Apply the current post-processing to ALL images in this run and\n"
            "export outlines + results CSV? (The same settings are used for every image.)",
            "Export results", JOptionPane.YES_NO_OPTION)
        if result != JOptionPane.YES_OPTION:
            return
        post = self._post_params()
        done, missing = rexport.reexport_all(self.project, self.selected_run, post, classes)
        msg = "Exported {} image(s) with the current settings.".format(done)
        if missing:
            msg += "\nSkipped (no cached labels yet): {}".format(", ".join(missing))
        JOptionPane.showMessageDialog(self.dialog, msg, "Export complete", JOptionPane.INFORMATION_MESSAGE)
        self._preview()

    def _find_runs_for_image(self):
        """Find runs applicable to this image: any run with a metadata snapshot
        (so it can be reviewed/exported from cached labels) or already-exported
        outlines for this image."""
        runs_with_image = []
        runs_dir = self.project.paths.get('runs', '')
        if not os.path.exists(runs_dir):
            return []

        base_name, _ = os.path.splitext(self.image_obj.filename)
        outline_name = base_name + "_Outlines.zip"
        has_cached = self.image_obj.has_cached_objects() if hasattr(self.image_obj, 'has_cached_objects') else False

        for run_id in sorted(os.listdir(runs_dir), reverse=True):  # most recent first
            run_path = os.path.join(runs_dir, run_id)
            if not os.path.isdir(run_path):
                continue

            has_meta = (os.path.exists(os.path.join(run_path, 'run_metadata.json'))
                        or bool(glob.glob(os.path.join(run_path, '*_run_metadata.json'))))
            has_outline = os.path.exists(os.path.join(run_path, 'Cell_Selections', outline_name))

            if has_outline or (has_meta and has_cached):
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

    def _runtime_class_config(self):
        """(key, label, Color) list from the run's workflow snapshot; falls back
        to the module default (OUTLINE_CLASS_CONFIG) for legacy runs."""
        try:
            meta = self._load_metadata_for_run(self.selected_run)
            defn = meta.get('workflow_definition') if meta else None
            if defn:
                cfg = []
                for c in defn.get('classes', []):
                    # Included if 'include' is set, else legacy role == 'cell'
                    included = c.get('include', c.get('role', 'cell') == 'cell')
                    if not included:
                        continue
                    col = c.get('color', [255, 255, 0])
                    try:
                        color = Color(int(col[0]), int(col[1]), int(col[2]))
                    except Exception:
                        color = DEFAULT_OUTLINE_COLOR
                    cfg.append((c.get('key'), c.get('display', c.get('key')), color))
                if cfg:
                    return cfg
        except Exception:
            pass
        return OUTLINE_CLASS_CONFIG

    def _ordered_outline_groups(self):
        """Return present outline groups as (key, label, color, rois).

        Class config comes from the run's workflow snapshot when available,
        otherwise the module default; any remaining keys follow.
        """
        groups = []
        seen = set()
        for key, label, color in self._runtime_class_config():
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

        # Do not draw per-ROI number labels on the image.
        overlay.drawLabels(False)
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
