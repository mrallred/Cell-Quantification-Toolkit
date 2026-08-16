"""
Stage-based workflow editor.

A workflow is three stages - Segmentation, Classification, Post-processing. The
editor offers a provider dropdown per stage (classification filtered to providers
compatible with the chosen segmentation's outputs), each provider supplying its
own parameter panel. The class map + post-processing defaults are edited here and
saved as a schema-v2 definition.
"""
from ij import IJ

from javax.swing import (JDialog, JPanel, JLabel, JTextField, JComboBox,
                         JButton, JCheckBox, JSpinner, SpinnerNumberModel,
                         JScrollPane, JTable, BorderFactory, JOptionPane, BoxLayout)
from javax.swing.table import DefaultTableModel
from javax.swing.border import EmptyBorder
from java.awt import BorderLayout, GridLayout, FlowLayout, Dimension
from java.lang import Boolean, String

import os

from .workflow_config import (WorkflowDefinition, classes_from_ilp, models_dir,
                              is_included, sanitize_name)
from .step_registry import providers_for, create_provider

CLASS_COLUMNS = ["Label", "Display", "Color (r,g,b)", "Include"]


class _ClassTableModel(DefaultTableModel):
    """Table model that renders the Include column as a checkbox."""
    def __init__(self):
        DefaultTableModel.__init__(self, CLASS_COLUMNS, 0)

    def getColumnClass(self, col):
        if col == 3:
            return Boolean
        return String

    def isCellEditable(self, row, col):
        return True


class WorkflowEditorDialog(JDialog):
    def __init__(self, parent_frame, store, definition=None, is_new=False):
        title = "New Workflow" if (definition is None or is_new) else "Edit Workflow"
        super(WorkflowEditorDialog, self).__init__(parent_frame, title, True)

        self.store = store
        self.saved = None
        self.definition = definition if definition is not None else WorkflowDefinition()

        # Provider catalogs by display name
        self._seg_by_display = dict((c.display_name, c) for c in providers_for('segmentation'))
        self._cls_all = list(providers_for('classification'))

        self.seg_provider = None
        self.cls_provider = None
        self.seg_type = None
        self.cls_type = None

        root = JPanel(BorderLayout(10, 10))
        root.setBorder(EmptyBorder(12, 12, 12, 12))
        self.setContentPane(root)

        # ---- name / description ----
        form = JPanel(GridLayout(0, 2, 8, 8))
        form.add(JLabel("Name:"))
        self.name_field = JTextField(self.definition.name or "")
        form.add(self.name_field)
        form.add(JLabel("Description:"))
        self.desc_field = JTextField(self.definition.description or "")
        form.add(self.desc_field)
        root.add(form, BorderLayout.NORTH)

        # ---- stages (stacked) ----
        center = JPanel()
        center.setLayout(BoxLayout(center, BoxLayout.Y_AXIS))

        # Segmentation
        seg_panel = JPanel(BorderLayout(4, 4))
        seg_panel.setBorder(BorderFactory.createTitledBorder("1. Segmentation"))
        self.seg_combo = JComboBox(sorted(self._seg_by_display.keys()))
        self.seg_combo.addActionListener(self._on_seg_change)
        seg_panel.add(self.seg_combo, BorderLayout.NORTH)
        self.seg_param_container = JPanel(BorderLayout())
        seg_panel.add(self.seg_param_container, BorderLayout.CENTER)
        center.add(seg_panel)

        # Classification
        cls_panel = JPanel(BorderLayout(4, 4))
        cls_panel.setBorder(BorderFactory.createTitledBorder("2. Classification"))
        self.cls_combo = JComboBox()
        self.cls_combo.addActionListener(self._on_cls_change)
        cls_panel.add(self.cls_combo, BorderLayout.NORTH)
        self.cls_param_container = JPanel(BorderLayout())
        cls_panel.add(self.cls_param_container, BorderLayout.CENTER)
        cls_panel.add(self._build_class_table_panel(), BorderLayout.SOUTH)
        center.add(cls_panel)

        # Post-processing is configured in the Results Viewer, not here.

        root.add(center, BorderLayout.CENTER)

        # ---- save / cancel ----
        actions = JPanel(FlowLayout(FlowLayout.RIGHT))
        actions.add(JButton("Save", actionPerformed=self._save_action))
        actions.add(JButton("Cancel", actionPerformed=self._cancel_action))
        root.add(actions, BorderLayout.SOUTH)

        # ---- initialize selections from the definition ----
        self._init_selection()
        for c in self.definition.classes:
            self._add_class_row(c)

        self.pack()

    # ------------------------------------------------------------------
    # Stage selection
    # ------------------------------------------------------------------
    def _init_selection(self):
        seg_spec = self.definition.segmentation_spec()
        cls_spec = self.definition.classification_spec()
        # pick segmentation matching the definition's type (else first)
        seg_display = None
        for disp, cls in self._seg_by_display.items():
            if cls.type_id == seg_spec.get('type'):
                seg_display = disp
                break
        if seg_display is None and self._seg_by_display:
            seg_display = sorted(self._seg_by_display.keys())[0]
        self._pending_seg_params = seg_spec.get('params', {}) or {}
        self._pending_cls_spec = cls_spec
        if seg_display is not None:
            self.seg_combo.setSelectedItem(seg_display)  # fires _on_seg_change

    def _on_seg_change(self, event=None):
        disp = self.seg_combo.getSelectedItem()
        cls = self._seg_by_display.get(disp)
        if cls is None:
            return
        self.seg_type = cls.type_id
        params = getattr(self, '_pending_seg_params', {}) or {}
        self._pending_seg_params = {}  # only used for the initial build
        self.seg_provider = create_provider('segmentation', cls.type_id, dict(params))
        panel = self.seg_provider.build_panel(params) if self.seg_provider else None
        self.seg_param_container.removeAll()
        if panel is not None:
            self.seg_param_container.add(panel, BorderLayout.CENTER)
        self.seg_param_container.revalidate()
        self.seg_param_container.repaint()
        self._refresh_cls_combo(cls)

    def _refresh_cls_combo(self, seg_cls):
        produced = set(getattr(seg_cls, 'produces', []) or [])
        compatible = []
        for c in self._cls_all:
            cons = set(getattr(c, 'consumes', []) or [])
            if not cons or (cons & produced):
                compatible.append(c)
        self._cls_by_display = dict((c.display_name, c) for c in compatible)

        # preserve current selection if still compatible, else pick from pending/first
        want_type = None
        pending = getattr(self, '_pending_cls_spec', None)
        if pending:
            want_type = pending.get('type')
            self._pending_cls_params = pending.get('params', {}) or {}
            self._pending_cls_spec = None
        elif self.cls_type:
            want_type = self.cls_type

        self.cls_combo.removeActionListener(self._on_cls_change)
        self.cls_combo.removeAllItems()
        for disp in sorted(self._cls_by_display.keys()):
            self.cls_combo.addItem(disp)
        self.cls_combo.addActionListener(self._on_cls_change)

        target = None
        for disp, c in self._cls_by_display.items():
            if c.type_id == want_type:
                target = disp
                break
        if target is None and self._cls_by_display:
            target = sorted(self._cls_by_display.keys())[0]
        if target is not None:
            self.cls_combo.setSelectedItem(target)  # fires _on_cls_change
        else:
            self.cls_param_container.removeAll()
            self.cls_param_container.revalidate()
            self.cls_param_container.repaint()

    def _on_cls_change(self, event=None):
        disp = self.cls_combo.getSelectedItem()
        cls = getattr(self, '_cls_by_display', {}).get(disp)
        if cls is None:
            return
        self.cls_type = cls.type_id
        params = getattr(self, '_pending_cls_params', {}) or {}
        self._pending_cls_params = {}
        self.cls_provider = create_provider('classification', cls.type_id, dict(params))
        panel = self.cls_provider.build_panel(params) if self.cls_provider else None
        self.cls_param_container.removeAll()
        if panel is not None:
            self.cls_param_container.add(panel, BorderLayout.CENTER)
        self.cls_param_container.revalidate()
        self.cls_param_container.repaint()

    # ------------------------------------------------------------------
    # Class table
    # ------------------------------------------------------------------
    def _build_class_table_panel(self):
        panel = JPanel(BorderLayout(4, 4))
        panel.setBorder(BorderFactory.createTitledBorder("Classes"))
        self.table_model = _ClassTableModel()
        self.table = JTable(self.table_model)
        scroll = JScrollPane(self.table)
        scroll.setPreferredSize(Dimension(440, 130))
        panel.add(scroll, BorderLayout.CENTER)

        btns = JPanel(FlowLayout(FlowLayout.LEFT))
        btns.add(JButton("Populate from object classifier", actionPerformed=self._populate_from_classifier))
        btns.add(JButton("Add row", actionPerformed=self._add_row))
        btns.add(JButton("Remove selected row", actionPerformed=self._remove_row))
        panel.add(btns, BorderLayout.SOUTH)
        return panel

    def _add_class_row(self, c):
        color = c.get('color', [255, 255, 0])
        try:
            color_str = "{},{},{}".format(int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            color_str = "255,255,0"
        self.table_model.addRow([
            str(c.get('label', self.table_model.getRowCount() + 1)),
            c.get('display', ''),
            color_str,
            Boolean(bool(is_included(c))),
        ])

    def _add_row(self, event=None):
        n = self.table_model.getRowCount() + 1
        self.table_model.addRow([str(n), "Class {}".format(n), "255,255,0", Boolean(True)])

    def _remove_row(self, event=None):
        row = self.table.getSelectedRow()
        if row >= 0:
            if self.table.isEditing():
                self.table.getCellEditor().stopCellEditing()
            self.table_model.removeRow(row)

    def _populate_from_classifier(self, event=None):
        if self.cls_provider is None:
            return
        params = self.cls_provider.gather_params()
        proj = params.get('project')
        path = params.get('project_path') or (os.path.join(models_dir(), proj) if proj else None)
        if not path or not os.path.exists(path):
            JOptionPane.showMessageDialog(self, "Select an object classifier first.",
                                          "No Classifier", JOptionPane.INFORMATION_MESSAGE)
            return
        classes = classes_from_ilp(path)
        if not classes:
            JOptionPane.showMessageDialog(
                self, "Could not read class labels from that classifier.\nEnter classes manually.",
                "No Labels Found", JOptionPane.WARNING_MESSAGE)
            return
        self.table_model.setRowCount(0)
        for c in classes:
            self._add_class_row(c)

    def _collect_classes(self):
        if self.table.isEditing():
            self.table.getCellEditor().stopCellEditing()
        classes = []
        for r in range(self.table_model.getRowCount()):
            label_raw = self.table_model.getValueAt(r, 0)
            display = self.table_model.getValueAt(r, 1)
            color_raw = self.table_model.getValueAt(r, 2)
            include_raw = self.table_model.getValueAt(r, 3)
            try:
                label = int(str(label_raw).strip())
            except (ValueError, TypeError):
                raise ValueError("Row {}: label must be a whole number.".format(r + 1))
            try:
                parts = [int(x.strip()) for x in str(color_raw).split(",")]
                if len(parts) != 3:
                    raise ValueError()
                color = [max(0, min(255, v)) for v in parts]
            except (ValueError, TypeError):
                raise ValueError("Row {}: color must be 'r,g,b' (0-255).".format(r + 1))
            include = str(include_raw).strip().lower() in ('true', '1')
            key_src = (display or "class_{}".format(label)).strip().lower()
            key = ''.join(ch if ch.isalnum() else '_' for ch in key_src).strip('_') or "class_{}".format(label)
            classes.append({'label': label, 'key': key, 'display': display or key,
                            'color': color, 'include': include})
        return classes

    # ------------------------------------------------------------------
    # Save / cancel
    # ------------------------------------------------------------------
    def _save_action(self, event=None):
        name = self.name_field.getText().strip()
        if self.seg_provider is None or self.cls_provider is None:
            JOptionPane.showMessageDialog(self, "Select a provider for each stage.",
                                          "Incomplete", JOptionPane.WARNING_MESSAGE)
            return
        try:
            classes = self._collect_classes()
        except ValueError as e:
            JOptionPane.showMessageDialog(self, str(e), "Invalid Class Table", JOptionPane.WARNING_MESSAGE)
            return

        seg_params = self.seg_provider.gather_params()
        cls_params = self.cls_provider.gather_params()
        seg_store = dict((k, v) for k, v in seg_params.items() if k != 'project_path')
        cls_store = dict((k, v) for k, v in cls_params.items() if k != 'project_path')

        defn = WorkflowDefinition({
            'schema_version': 2,
            'name': name,
            'description': self.desc_field.getText().strip(),
            'segmentation': {'type': self.seg_type, 'params': seg_store},
            'classification': {'type': self.cls_type, 'params': cls_store},
            'classes': classes,
            # Post-processing is tuned per-run in the Results Viewer; carry the
            # definition's existing defaults through unchanged.
            'post': dict(self.definition.post),
        })

        problems = defn.validate(models_dir())
        if problems:
            JOptionPane.showMessageDialog(self, "Please fix:\n- " + "\n- ".join(problems),
                                          "Incomplete Workflow", JOptionPane.WARNING_MESSAGE)
            return

        if (self.definition.name != name) and self.store.exists(name):
            result = JOptionPane.showConfirmDialog(
                self, "A workflow named '{}' already exists. Overwrite it?".format(name),
                "Overwrite?", JOptionPane.YES_NO_OPTION)
            if result != JOptionPane.YES_OPTION:
                return

        self.store.save(defn)
        self.saved = defn
        self.dispose()

    def _cancel_action(self, event=None):
        self.saved = None
        self.dispose()

    def show_dialog(self):
        self.setLocationRelativeTo(self.getParent())
        self.setVisible(True)
        return self.saved
