"""
Manual counting dialog.

For a manual-kind workflow: the user goes class by class and clicks on the cells
of that class using the multi-point tool. Each class's points are kept separately
(the active class is the live PointRoi; other classes show as a coloured overlay).
On finish, points inside each analysis ROI are counted per class and exported.
"""
import datetime

from ij import IJ
from ij.gui import PointRoi, Overlay
from ij.plugin.frame import RoiManager

from javax.swing import (JDialog, JPanel, JLabel, JButton, JRadioButton,
                         ButtonGroup, BorderFactory, JOptionPane)
from javax.swing.border import EmptyBorder
from java.awt import BorderLayout, GridLayout, FlowLayout, Color
from java.awt.event import MouseAdapter, WindowAdapter

from . import manual_export


def _color(rgb):
    try:
        return Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return Color.YELLOW


def _make_pointroi(points, color):
    if not points:
        return None
    pr = PointRoi(float(points[0][0]), float(points[0][1]))
    for (x, y) in points[1:]:
        pr.addPoint(float(x), float(y))
    if color is not None:
        pr.setStrokeColor(color)
    return pr


def _extract_points(roi):
    if roi is None or not isinstance(roi, PointRoi):
        return []
    try:
        poly = roi.getPolygon()
        return [(poly.xpoints[i], poly.ypoints[i]) for i in range(poly.npoints)]
    except Exception:
        return []


class _CanvasMouse(MouseAdapter):
    def __init__(self, owner):
        self.owner = owner

    def mouseReleased(self, event):
        self.owner._on_canvas_click()


class _WindowGuard(WindowAdapter):
    """When the control panel (or the image window it is attached to) is closed by
    the user, clear the image 'changes' flag and cancel cleanly so ImageJ never
    prompts to save the image."""
    def __init__(self, owner):
        self.owner = owner

    def windowClosing(self, event):
        self.owner._on_window_closing()


class ManualCountingDialog(object):
    def __init__(self, parent_gui, project, images, definition):
        self.parent_gui = parent_gui
        self.project = project
        self.definition = definition
        self.classes = definition.cell_classes()
        # Only images with analysis ROIs can be counted.
        self.images = [im for im in images if im.has_roi()]

        self.points = {}          # {filename: {class_key: [(x, y), ...]}}
        self.count_labels = {}    # {class_key: JLabel}
        self.idx = 0
        self.imp = None
        self._analysis_rois = []
        self._mouse = _CanvasMouse(self)
        self._guard = _WindowGuard(self)
        self._loaded = set()      # images whose saved points have been reloaded
        self.active_key = self.classes[0].get('key') if self.classes else None

        self.dialog = None
        self.win = None
        self._build_ui()   # builds self.content (attached to each image window later)

    # ------------------------------------------------------------------
    def show(self):
        if not self.images:
            JOptionPane.showMessageDialog(self.parent_gui.frame,
                                          "No selected image has Regions. Draw Regions first.",
                                          "Manual Counting", JOptionPane.WARNING_MESSAGE)
            return
        if not self.classes:
            JOptionPane.showMessageDialog(self.parent_gui.frame,
                                          "This workflow has no classes to count.",
                                          "Manual Counting", JOptionPane.WARNING_MESSAGE)
            return
        self._open_current()

    def _attach_dialog(self, win):
        """Parent the control panel to the current image window (like the ROI
        editor), re-parenting it when the user navigates to another image."""
        old = self.dialog
        self.dialog = JDialog(win, "Manual Counting: " + self.definition.name, False)
        self.dialog.setDefaultCloseOperation(JDialog.DO_NOTHING_ON_CLOSE)
        self.dialog.addWindowListener(self._guard)
        self.dialog.add(self.content)   # reparents self.content from the old dialog
        self.dialog.pack()
        try:
            p = win.getLocationOnScreen()
            self.dialog.setLocation(p.x + win.getWidth(), p.y)
        except Exception:
            self.dialog.setLocationRelativeTo(win)
        self.dialog.setVisible(True)
        if old is not None:
            try:
                old.dispose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _build_ui(self):
        main = JPanel(BorderLayout(6, 6))
        main.setBorder(EmptyBorder(10, 10, 10, 10))

        self.image_label = JLabel(" ")
        main.add(self.image_label, BorderLayout.NORTH)

        # class selector with per-class live counts
        classes_panel = JPanel(GridLayout(0, 1, 2, 2))
        classes_panel.setBorder(BorderFactory.createTitledBorder("Classes (select one, then click cells)"))
        group = ButtonGroup()
        first = True
        for cls in self.classes:
            key = cls.get('key')
            row = JPanel(BorderLayout(4, 4))
            rb = JRadioButton(cls.get('display', key), first)
            rb.setForeground(_color(cls.get('color')))
            rb.addActionListener(lambda e, k=key: self._select_class(k))
            group.add(rb)
            count = JLabel("0")
            self.count_labels[key] = count
            row.add(rb, BorderLayout.CENTER)
            row.add(count, BorderLayout.EAST)
            classes_panel.add(row)
            first = False
        main.add(classes_panel, BorderLayout.CENTER)

        # controls
        controls = JPanel(GridLayout(0, 2, 4, 4))
        controls.add(JButton("Remove last point", actionPerformed=self._remove_last))
        controls.add(JButton("Clear class", actionPerformed=self._clear_class))
        self.prev_btn = JButton("< Prev image", actionPerformed=self._prev)
        self.next_btn = JButton("Next image >", actionPerformed=self._next)
        controls.add(self.prev_btn)
        controls.add(self.next_btn)
        controls.add(JButton("Save & Close", actionPerformed=self._finish))
        controls.add(JButton("Cancel", actionPerformed=self._cancel))
        main.add(controls, BorderLayout.SOUTH)

        self.content = main

    # ------------------------------------------------------------------
    # Image lifecycle
    # ------------------------------------------------------------------
    def _current_fname(self):
        return self.images[self.idx].filename

    def _open_current(self):
        img = self.images[self.idx]
        self.imp = IJ.openImage(img.full_path)
        if self.imp is None:
            JOptionPane.showMessageDialog(self.parent_gui.frame,
                                          "Could not open " + img.filename,
                                          "Manual Counting", JOptionPane.WARNING_MESSAGE)
            return
        self.imp.show()
        self.imp.changes = False
        self.win = self.imp.getWindow()
        try:
            self.win.addWindowListener(self._guard)  # user-closing the image also cancels cleanly
        except Exception:
            pass
        self._attach_dialog(self.win)

        # analysis ROIs (reference overlay)
        self._analysis_rois = []
        if img.has_roi():
            rm = RoiManager(True)
            rm.open(img.roi_path)
            self._analysis_rois = list(rm.getRoisAsArray())
            rm.close()

        self.points.setdefault(img.filename, {})
        # On first visit, reload any previously drawn dots for this image so the
        # user can continue/edit rather than starting over.
        if img.filename not in self._loaded:
            self._loaded.add(img.filename)
            try:
                prior = manual_export.latest_points_for_image(self.project, img)
                keys = set(c.get('key') for c in self.classes)
                filtered = dict((k, list(v)) for k, v in prior.items() if k in keys and v)
                if filtered:
                    self.points[img.filename] = filtered
            except Exception as e:
                IJ.log("Could not reload previous points for {}: {}".format(img.filename, e))

        try:
            self.imp.getCanvas().addMouseListener(self._mouse)
        except Exception:
            pass

        self.image_label.setText("Image {} / {}: {}".format(
            self.idx + 1, len(self.images), img.filename))
        self.prev_btn.setEnabled(self.idx > 0)
        self.next_btn.setEnabled(self.idx < len(self.images) - 1)

        # Clear active_key so the initial _select_class does not _commit_active an
        # empty ROI over the just-reloaded points of the default (active) class.
        want = self.active_key or self.classes[0].get('key')
        self.active_key = None
        self._select_class(want)

    def _close_current(self):
        if self.imp is None:
            return
        try:
            self.imp.getCanvas().removeMouseListener(self._mouse)
        except Exception:
            pass
        self.imp.changes = False
        self.imp.close()
        self.imp = None

    # ------------------------------------------------------------------
    # Points
    # ------------------------------------------------------------------
    def _commit_active(self):
        if self.imp is None or not self.active_key:
            return
        self.points[self._current_fname()][self.active_key] = _extract_points(self.imp.getRoi())

    def _select_class(self, key):
        if self.imp is None:
            self.active_key = key
            return
        self._commit_active()
        self.active_key = key
        self._refresh_overlay()

        pts = self.points[self._current_fname()].get(key, [])
        color = None
        for c in self.classes:
            if c.get('key') == key:
                color = _color(c.get('color'))
                break
        pr = _make_pointroi(pts, color)
        if pr is not None:
            self.imp.setRoi(pr)
        else:
            self.imp.deleteRoi()
        IJ.setTool("multipoint")
        self._mark_unchanged()
        self._refresh_counts()

    def _refresh_overlay(self):
        ov = Overlay()
        for aroi in self._analysis_rois:
            ov.add(aroi)
        fpoints = self.points.get(self._current_fname(), {})
        for cls in self.classes:
            key = cls.get('key')
            if key == self.active_key:
                continue
            pr = _make_pointroi(fpoints.get(key, []), _color(cls.get('color')))
            if pr is not None:
                ov.add(pr)
        ov.drawLabels(False)
        self.imp.setOverlay(ov)
        self.imp.changes = False

    def _refresh_counts(self):
        fpoints = self.points.get(self._current_fname(), {})
        for cls in self.classes:
            key = cls.get('key')
            if key == self.active_key and self.imp is not None:
                n = len(_extract_points(self.imp.getRoi()))
            else:
                n = len(fpoints.get(key, []))
            if key in self.count_labels:
                self.count_labels[key].setText(str(n))

    def _mark_unchanged(self):
        # Placing points flips ImagePlus.changes, which makes ImageJ prompt to
        # "save the image" on close. Points are never written into the image, so
        # keep the flag cleared after every interaction.
        if self.imp is not None:
            self.imp.changes = False

    def _on_canvas_click(self):
        self._commit_active()
        self._mark_unchanged()
        self._refresh_counts()

    def _remove_last(self, event=None):
        if self.imp is None:
            return
        pts = _extract_points(self.imp.getRoi())
        if pts:
            pts = pts[:-1]
            color = None
            for c in self.classes:
                if c.get('key') == self.active_key:
                    color = _color(c.get('color'))
                    break
            pr = _make_pointroi(pts, color)
            if pr is not None:
                self.imp.setRoi(pr)
            else:
                self.imp.deleteRoi()
            self._commit_active()
            self._mark_unchanged()
            self._refresh_counts()

    def _clear_class(self, event=None):
        if self.imp is None:
            return
        self.points[self._current_fname()][self.active_key] = []
        self.imp.deleteRoi()
        self._mark_unchanged()
        self._refresh_counts()

    # ------------------------------------------------------------------
    # Navigation / finish
    # ------------------------------------------------------------------
    def _prev(self, event=None):
        if self.idx > 0:
            self._commit_active()
            self._close_current()
            self.idx -= 1
            self._open_current()

    def _next(self, event=None):
        if self.idx < len(self.images) - 1:
            self._commit_active()
            self._close_current()
            self.idx += 1
            self._open_current()

    def _finish(self, event=None):
        self._commit_active()
        per_image = [(im, self.points.get(im.filename, {})) for im in self.images]
        run_id = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        try:
            done = manual_export.save_points_run(self.project, run_id, self.definition, per_image)
        except Exception as e:
            import traceback
            IJ.log(traceback.format_exc())
            JOptionPane.showMessageDialog(self.parent_gui.frame,
                                          "Saving points failed:\n" + str(e),
                                          "Manual Counting", JOptionPane.ERROR_MESSAGE)
            return
        self._close_current()
        self.dialog.dispose()
        try:
            self.project.sync_project_db()
            self.parent_gui.update_ui_for_project()
        except Exception:
            pass
        JOptionPane.showMessageDialog(
            self.parent_gui.frame,
            "Saved points for {} image(s).\nOpen the Results tab to review and export counts.".format(done),
            "Manual Counting", JOptionPane.INFORMATION_MESSAGE)

    def _on_window_closing(self):
        """User closed the control panel or the image window: clear the changes
        flag first so ImageJ never prompts to save, then close cleanly."""
        if getattr(self, '_closing', False):
            return
        self._closing = True
        if self.imp is not None:
            self.imp.changes = False
        self._cancel()

    def _cancel(self, event=None):
        if self.imp is not None:
            self.imp.changes = False
        self._close_current()
        if self.dialog is not None:
            try:
                self.dialog.dispose()
            except Exception:
                pass
