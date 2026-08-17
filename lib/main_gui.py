# Python Standard Library 
import os
import traceback

# ImageJ/Fiji API
from ij import IJ

# Java I/O and NIO 
from java.io import File
from java.nio.file import Files, StandardCopyOption
from java.lang import Throwable

# Java Concurrency & Events
from java.beans import PropertyChangeListener
from java.awt.event import WindowAdapter

# Java Swing (GUI Framework)
from javax.swing import (JFrame, JMenuBar, JMenu, JMenuItem, JSplitPane,
                         JPanel, JScrollPane, JOptionPane, JTable,
                         JButton, JLabel, JFileChooser, ListSelectionModel,
                         BorderFactory, ProgressMonitor, SwingWorker, DefaultListModel, JList)
from javax.swing.table import AbstractTableModel, DefaultTableModel
from javax.swing.border import EmptyBorder
from javax.swing.filechooser import FileNameExtensionFilter, FileFilter

#  Java AWT (Graphics & Layout)
from java.awt import BorderLayout, FlowLayout, Font, GridLayout, Color, Dimension

# Internal Modules
from .project_model import Project, ProjectImage
from .roi_editor import ROIEditor
from .quantification import QuantificationDialog, QuantificationWorker, ProgressDialog
from .results_viewer import ResultsViewer
from .workflow_config import WorkflowStore, WorkflowDefinition, build_workflow_instance
from .workflow_editor import WorkflowEditorDialog
from .manual_counter import ManualCountingDialog


# Internal folder names that should be hidden during project folder selection
INTERNAL_PROJECT_FOLDERS = {'Images', 'ROI_Files', 'Runs', 'Probabilities', 'temp'}


class ProjectFolderFilter(FileFilter):
    """
    FileFilter that hides internal project folders from the file chooser.
    This prevents users from accidentally navigating into and selecting
    internal folders as project roots.
    """
    def accept(self, f):
        if f.isDirectory():
            # Hide folders that are internal to project structure
            return f.getName() not in INTERNAL_PROJECT_FOLDERS
        return False
    
    def getDescription(self):
        return "Project Folders"


class ProjectManagerGUI(WindowAdapter):
    """ Builds and manages the main GUI, facilitating dialogs and and controling the script """
    def __init__(self):
        self.project = None
        self.unsaved_changes = False
        self.save_proj_item = None

        # Global workflow definitions (shared across projects)
        self.workflow_store = WorkflowStore()
        self.current_workflow_def = None

        self.frame = JFrame("Project Manager")
        self.frame.setSize(1100, 700)
        self.frame.setLayout(BorderLayout())
        self.frame.setDefaultCloseOperation(JFrame.DO_NOTHING_ON_CLOSE)

        self.build_menu()
        self.build_main_panel()
        self.build_status_bar()

        self.frame.addWindowListener(self)

    def show(self):
        self.frame.setLocationRelativeTo(None)
        self.frame.setVisible(True)

    def build_menu(self):
        menu_bar = JMenuBar()
        file_menu = JMenu("File")
        open_proj_item = JMenuItem("Open Project", actionPerformed=self.open_project_action)
        self.save_proj_item = JMenuItem("Save Project", actionPerformed=self.save_project_action, enabled=False)
        exit_item = JMenuItem("Exit", actionPerformed=lambda event: self.frame.dispose())
        file_menu.add(open_proj_item)
        file_menu.add(self.save_proj_item)
        file_menu.addSeparator()
        file_menu.add(exit_item)
        menu_bar.add(file_menu)
        self.frame.setJMenuBar(menu_bar)

    def build_main_panel(self):
        # Project header
        self.project_name_label = JLabel("No Project Loaded")
        self.project_name_label.setFont(Font("SansSerif", Font.BOLD, 16))
        self.project_name_label.setBorder(EmptyBorder(10,10,10,10))
        self.frame.add(self.project_name_label, BorderLayout.NORTH)

        # --- Regions to analyze (list + management buttons) ---
        self.template_list_model = DefaultListModel()
        self.template_list = JList(self.template_list_model)
        template_scroll_pane = JScrollPane(self.template_list)
        template_scroll_pane.setBorder(BorderFactory.createTitledBorder("Templates"))

        template_button_panel = JPanel(GridLayout(0, 1, 5, 5))
        self.add_template_btn = JButton("Add Template", actionPerformed=self._add_template_action)
        self.remove_template_btn = JButton("Remove Template", actionPerformed=self._remove_template_action)
        self.add_template_btn.setEnabled(False)
        self.remove_template_btn.setEnabled(False)
        template_button_panel.add(self.add_template_btn)
        template_button_panel.add(self.remove_template_btn)

        regions_panel = JPanel(BorderLayout())
        regions_panel.add(template_scroll_pane, BorderLayout.CENTER)
        regions_panel.add(template_button_panel, BorderLayout.SOUTH)

        # Right side: Regions on top, Workflow below (flipped vertically)
        right_panel = JPanel(BorderLayout())
        right_panel.add(regions_panel, BorderLayout.CENTER)
        right_panel.add(self._build_workflow_panel(), BorderLayout.SOUTH)

        # --- Project images table ---
        image_cols = ["Filename", "Regions File", "# Regions", "Status", "Location"]
        self.image_table_model = DefaultTableModel(None, image_cols)
        self.image_table = JTable(self.image_table_model)
        self.image_table.setSelectionMode(ListSelectionModel.MULTIPLE_INTERVAL_SELECTION)
        self.image_table.getSelectionModel().addListSelectionListener(self.on_image_selection)
        image_table_pane = JScrollPane(self.image_table)
        image_table_pane.setBorder(BorderFactory.createTitledBorder("Project Images"))

        # --- Project summary (replaces the old ROI Details table) ---
        summary_pane = self._build_summary_panel()

        # Left side: images on top, summary below
        left_split_pane = JSplitPane(JSplitPane.VERTICAL_SPLIT, image_table_pane, summary_pane)
        left_split_pane.setDividerLocation(320)

        # Main split: tables on the LEFT, regions + workflow on the RIGHT
        main_split_pane = JSplitPane(JSplitPane.HORIZONTAL_SPLIT, left_split_pane, right_panel)
        main_split_pane.setDividerLocation(720)
        self.frame.add(main_split_pane, BorderLayout.CENTER)

    # ------------------------------------------------------------------
    # Workflow panel (global workflow definitions)
    # ------------------------------------------------------------------
    def _build_workflow_panel(self):
        """Panel listing the global workflows; the selected one is current."""
        panel = JPanel(BorderLayout(4, 4))
        panel.setBorder(BorderFactory.createTitledBorder("Current Workflow"))

        self.workflow_list_model = DefaultListModel()
        self.workflow_list = JList(self.workflow_list_model)
        self.workflow_list.setSelectionMode(ListSelectionModel.SINGLE_SELECTION)
        self.workflow_list.addListSelectionListener(self._on_workflow_list_select)
        list_scroll = JScrollPane(self.workflow_list)
        list_scroll.setPreferredSize(Dimension(220, 110))
        panel.add(list_scroll, BorderLayout.CENTER)

        south = JPanel(BorderLayout(2, 2))
        self.workflow_summary_label = JLabel(self._workflow_summary_html())
        south.add(self.workflow_summary_label, BorderLayout.NORTH)
        btns = JPanel(GridLayout(0, 3, 4, 4))
        self.wf_new_btn = JButton("New...", actionPerformed=self._new_workflow_action)
        self.wf_edit_btn = JButton("Edit...", actionPerformed=self._edit_workflow_action)
        self.wf_dup_btn = JButton("Duplicate...", actionPerformed=self._duplicate_workflow_action)
        self.wf_delete_btn = JButton("Delete...", actionPerformed=self._delete_workflow_action)
        for b in (self.wf_new_btn, self.wf_edit_btn, self.wf_dup_btn, self.wf_delete_btn):
            btns.add(b)
        south.add(btns, BorderLayout.SOUTH)
        panel.add(south, BorderLayout.SOUTH)

        self._refresh_workflow_list()
        return panel

    def _refresh_workflow_list(self):
        """Repopulate the workflow list and select the current definition."""
        if getattr(self, 'workflow_list_model', None) is None:
            return
        self._updating_list = True
        try:
            self.workflow_list_model.clear()
            names = self.workflow_store.names()
            for n in names:
                self.workflow_list_model.addElement(n)
            cur = self.current_workflow_def.name if self.current_workflow_def else None
            if cur in names:
                self.workflow_list.setSelectedValue(cur, True)
            else:
                self.workflow_list.clearSelection()
        finally:
            self._updating_list = False

    def _on_workflow_list_select(self, event=None):
        if getattr(self, '_updating_list', False):
            return
        if event is not None and event.getValueIsAdjusting():
            return
        val = self.workflow_list.getSelectedValue()
        if val is not None:
            self._set_current_workflow(str(val))

    def _workflow_summary_html(self):
        d = self.current_workflow_def
        if not d:
            return "<html><i>No workflow selected.</i> Use New... to create one.</html>"
        cells = ", ".join(c.get('display', c.get('key', '')) for c in d.cell_classes()) or "(none)"
        if d.is_manual():
            return "<html><b>{name}</b> &nbsp;(manual counting)<br>classes: {cells}</html>".format(
                name=d.name, cells=cells)
        return "<html><b>{name}</b> &nbsp;(automated)<br>classes: {cells}</html>".format(
            name=d.name, cells=cells)

    def _refresh_workflow_summary(self):
        if getattr(self, 'workflow_summary_label', None) is not None:
            self.workflow_summary_label.setText(self._workflow_summary_html())

    def _set_current_workflow(self, name, persist=True):
        """Load a workflow definition by name and make it the project's current one."""
        defn = self.workflow_store.load(name) if name else None
        self.current_workflow_def = defn
        if persist and self.project is not None:
            self.project.selected_workflow = defn.name if defn else None
            self.set_unsaved_changes(True)
        self._refresh_workflow_summary()

    def _new_workflow_action(self, event):
        editor = WorkflowEditorDialog(self.frame, self.workflow_store, definition=None)
        saved = editor.show_dialog()
        if saved is not None:
            self._set_current_workflow(saved.name)
            self._refresh_workflow_list()

    def _edit_workflow_action(self, event):
        if not self.current_workflow_def:
            JOptionPane.showMessageDialog(self.frame, "Select a workflow first.",
                                          "No Workflow", JOptionPane.INFORMATION_MESSAGE)
            return
        original_name = self.current_workflow_def.name
        editor = WorkflowEditorDialog(self.frame, self.workflow_store, definition=self.current_workflow_def)
        saved = editor.show_dialog()
        if saved is not None:
            if saved.name != original_name:
                self.workflow_store.delete(original_name)  # renamed: drop the old file
            self._set_current_workflow(saved.name)
            self._refresh_workflow_list()

    def _duplicate_workflow_action(self, event):
        if not self.current_workflow_def:
            JOptionPane.showMessageDialog(self.frame, "Select a workflow to duplicate.",
                                          "No Workflow", JOptionPane.INFORMATION_MESSAGE)
            return
        clone = WorkflowDefinition(self.current_workflow_def.to_dict())
        clone.name = self.current_workflow_def.name + " copy"
        editor = WorkflowEditorDialog(self.frame, self.workflow_store, definition=clone, is_new=True)
        saved = editor.show_dialog()
        if saved is not None:
            self._set_current_workflow(saved.name)
            self._refresh_workflow_list()

    def _delete_workflow_action(self, event):
        if not self.current_workflow_def:
            JOptionPane.showMessageDialog(self.frame, "Select a workflow to delete.",
                                          "No Workflow", JOptionPane.INFORMATION_MESSAGE)
            return
        name = self.current_workflow_def.name
        result = JOptionPane.showConfirmDialog(self.frame, "Delete workflow '{}'?".format(name),
                                               "Confirm Delete", JOptionPane.YES_NO_OPTION)
        if result == JOptionPane.YES_OPTION:
            self.workflow_store.delete(name)
            self._set_current_workflow(None)
            self._refresh_workflow_list()

    # ------------------------------------------------------------------
    # Project summary panel
    # ------------------------------------------------------------------
    def _build_summary_panel(self):
        """Project-wide summary: image counts + ROIs per region."""
        panel = JPanel(BorderLayout(6, 6))
        panel.setBorder(BorderFactory.createTitledBorder("Project Summary"))

        header = JPanel(GridLayout(0, 1, 2, 2))
        self.summary_images_label = JLabel("Images: 0")
        self.summary_total_roi_label = JLabel("Total Regions: 0")
        self.summary_noroi_label = JLabel("Images without Regions: 0")
        header.add(self.summary_images_label)
        header.add(self.summary_total_roi_label)
        header.add(self.summary_noroi_label)
        panel.add(header, BorderLayout.NORTH)

        self.summary_table_model = DefaultTableModel(["Template", "Regions"], 0)
        self.summary_table = JTable(self.summary_table_model)
        roi_scroll = JScrollPane(self.summary_table)
        roi_scroll.setBorder(BorderFactory.createTitledBorder("Regions per template"))
        panel.add(roi_scroll, BorderLayout.CENTER)
        return panel

    def _update_summary(self):
        """Recompute the project summary from the current project state."""
        if getattr(self, 'summary_table_model', None) is None:
            return

        images = self.project.images if self.project else []
        region_names = [t.get('name', '') for t in (self.project.roi_templates if self.project else [])]

        num_images = len(images)
        num_without = sum(1 for img in images if len(img.rois) == 0)
        region_counts = dict((n, 0) for n in region_names)
        unassigned = 0
        total_rois = 0

        for img in images:
            for roi in img.rois:
                total_rois += 1
                name = roi.get('roi_name', '') or ''
                best = None
                for rn in region_names:
                    # A region matches when the ROI name equals it or starts with
                    # it (ROI names look like "<region>" or "<region> <sub#>").
                    if rn and (name == rn or name.startswith(rn)):
                        if best is None or len(rn) > len(best):
                            best = rn
                if best is not None:
                    region_counts[best] += 1
                else:
                    unassigned += 1

        self.summary_images_label.setText("Images: {}".format(num_images))
        self.summary_total_roi_label.setText("Total Regions: {}".format(total_rois))
        self.summary_noroi_label.setText("Images without Regions: {}".format(num_without))

        self.summary_table_model.setRowCount(0)
        for rn in region_names:
            self.summary_table_model.addRow([rn, region_counts.get(rn, 0)])
        if unassigned:
            self.summary_table_model.addRow(["(unassigned)", unassigned])

    def build_status_bar(self):
        control_panel = JPanel(BorderLayout())
        control_panel.setBorder(EmptyBorder(5,5,5,5))

        self.status_label = JLabel("Open a project folder to begin")
        control_panel.add(self.status_label, BorderLayout.CENTER)
        
        button_panel = JPanel(FlowLayout(FlowLayout.RIGHT))

        self.import_button = JButton("Import Images", enabled=False)
        self.copy_link_button = JButton("Copy Link into Project", enabled=False)
        self.remove_button = JButton("Remove Selected Image", enabled=False)
        self.select_all_button = JButton("Select All / None")
        self.roi_button = JButton("Draw Regions", enabled=False)
        self.quant_button = JButton("Run Quantification", enabled=False)
        self.show_results_button = JButton("Results", enabled=False)

        button_panel.add(self.import_button)
        button_panel.add(self.copy_link_button)
        button_panel.add(self.remove_button)
        button_panel.add(self.select_all_button)
        button_panel.add(self.roi_button)
        button_panel.add(self.quant_button)
        button_panel.add(self.show_results_button)

        control_panel.add(button_panel, BorderLayout.EAST)
        self.frame.add(control_panel, BorderLayout.SOUTH)

        self.import_button.addActionListener(self.import_images_action)
        self.copy_link_button.addActionListener(self.copy_links_into_project_action)
        self.remove_button.addActionListener(self.remove_images_action)
        self.select_all_button.addActionListener(self.toggle_select_all_action)
        self.roi_button.addActionListener(self.open_roi_editor_action)
        self.quant_button.addActionListener(self.open_quantification_dialog_action)
        self.show_results_button.addActionListener(self.show_results_action)

    def set_unsaved_changes(self, state):
        """ Updates UI to show if there are unsaved changes """
        self.unsaved_changes = state
        self.save_proj_item.setEnabled(state)
        title = "Project Manager"
        if state:
            title += " *"
        self.frame.setTitle(title)

    # Event Handlers and actions

    def open_project_action(self, event):
        chooser = JFileChooser()
        chooser.setFileSelectionMode(JFileChooser.DIRECTORIES_ONLY)
        chooser.setDialogTitle("Select Project Directory")
        # Apply filter to hide internal project folders from navigation
        chooser.setFileFilter(ProjectFolderFilter())
        if chooser.showOpenDialog(self.frame) == JFileChooser.APPROVE_OPTION:
            project_dir = chooser.getSelectedFile().getAbsolutePath()
            self.load_project(project_dir)

    def save_project_action(self, event):
        """ Saves current state of project to csv file"""
        if not (self.project and self.unsaved_changes):
            return True

        # Sync database
        if self.project.sync_project_db():
            self.status_label.setText("Project saved successfully.")
            self.set_unsaved_changes(False)
            return True
        else:
            self.status_label.setText("Error saving project. See Log.")
            return False
        
    def show_results_action(self, event):
        """Launches the ResultsViewer dialog for the selected image."""
        selected_row = self.image_table.getSelectedRow()
        if selected_row == -1: return
        self.open_results_viewer(self.project.images[selected_row])

    def open_results_viewer(self, image_obj):
        """Open the Results Viewer for a given image (used by Show Results and
        automatically after a quantification run)."""
        viewer = ResultsViewer(self.frame, image_obj, self.project)
        viewer.show()

    def copy_links_into_project_action(self, event):
        """Replace selected linked images with real copies inside the project."""
        selected_rows = self.image_table.getSelectedRows()
        if not selected_rows:
            return
        linked = [self.project.images[r] for r in selected_rows
                  if self.project.images[r].is_linked()]
        if not linked:
            JOptionPane.showMessageDialog(self.frame, "None of the selected images are links.",
                                          "Nothing to Copy", JOptionPane.INFORMATION_MESSAGE)
            return

        done = 0
        failed = []
        for im in linked:
            dest = im.full_path
            try:
                target = os.path.realpath(dest)   # resolve the link before removing it
                if not os.path.exists(target):
                    failed.append(im.filename + " (broken link)")
                    continue
                # Copy target -> temp, then swap in, so a failed copy never loses the link.
                tmp = dest + ".copytmp"
                Files.copy(File(target).toPath(), File(tmp).toPath(),
                           StandardCopyOption.REPLACE_EXISTING)
                os.remove(dest)       # remove the symlink
                os.rename(tmp, dest)  # replace it with the real copy
                done += 1
            except (Exception, Throwable) as e:
                IJ.log("Could not copy '{}' into project: {}".format(im.filename, e))
                failed.append(im.filename)

        self.update_ui_for_project()
        msg = "Copied {} linked image(s) into the project.".format(done)
        if failed:
            msg += "\nFailed: {}".format(", ".join(failed))
        JOptionPane.showMessageDialog(self.frame, msg, "Copy Links", JOptionPane.INFORMATION_MESSAGE)

    def on_image_selection(self, event):
        """ 
        Called when the user selects image(s) in the top table.
        It can also be called programmatically by passing event=None to refresh the view.
        """
        # This condition allows the method's logic to run either when a user 
        # selection event has finalized (getValueIsAdjusting is False) or 
        # when the method is called directly without an event.
        if event is None or not event.getValueIsAdjusting():
            selection_count = self.image_table.getSelectedRowCount()

            # Enable/disable action buttons based on how many images are selected
            self.roi_button.setEnabled(selection_count == 1)
            self.remove_button.setEnabled(selection_count > 0)

            # Enable quant button only if at least one selected image has ROIs;
            # enable "Copy Link into Project" if any selected image is a link.
            if selection_count > 0:
                selected_rows = self.image_table.getSelectedRows()
                selected_imgs = [self.project.images[r] for r in selected_rows]
                self.quant_button.setEnabled(any(im.has_roi() for im in selected_imgs))
                self.copy_link_button.setEnabled(any(im.is_linked() for im in selected_imgs))
            else:
                self.quant_button.setEnabled(False)
                self.copy_link_button.setEnabled(False)

            if selection_count == 1:
                selected_row = self.image_table.getSelectedRow()
                # Safety check in case the selection is cleared before this code runs
                if selected_row == -1: 
                    return

                selected_image = self.project.images[selected_row]
                self.status_label.setText("Selected: {}".format(selected_image.filename))
                # Reviewable once segmented (cached objects) or already exported.
                self.show_results_button.setEnabled(
                    selected_image.has_outlines() or selected_image.has_cached_objects())

            elif selection_count > 1:
                self.status_label.setText("Selected: {} images".format(selection_count))
                self.show_results_button.setEnabled(False)

            else: # Corresponds to selection_count == 0
                self.status_label.setText("No Image(s) Selected")
                self.show_results_button.setEnabled(False)

    def toggle_select_all_action(self, event):
        """ Selects all rows in the image table if not all are selected or clears selection if all are already selected"""
        row_count = self.image_table.getRowCount()
        if row_count == 0:
            return
        
        selected_count = self.image_table.getSelectedRowCount()

        if selected_count == row_count:
            self.image_table.clearSelection()
        else:
            self.image_table.selectAll()

    def open_roi_editor_action(self, event):
        """ Opens ROI editor window for selected image """
        selected_row = self.image_table.getSelectedRow()
        if selected_row != -1:
            selected_image = self.project.images[selected_row]

            editor = ROIEditor(self, self.project, selected_image)
            editor.show()

    def open_quantification_dialog_action(self, event):
        """ Gathers selected images and opens the quantification settings dialog. """
        selected_rows = self.image_table.getSelectedRows()
        if not selected_rows: return

        selected_images = [self.project.images[row] for row in selected_rows]

        if not self.current_workflow_def:
            JOptionPane.showMessageDialog(
                self.frame,
                "Select or create a workflow first, using the Current Workflow panel.",
                "No Workflow Selected", JOptionPane.WARNING_MESSAGE)
            return

        problems = self.current_workflow_def.validate()
        if problems:
            JOptionPane.showMessageDialog(
                self.frame,
                "Workflow '{}' can't run:\n- {}".format(
                    self.current_workflow_def.name, "\n- ".join(problems)),
                "Invalid Workflow", JOptionPane.WARNING_MESSAGE)
            return

        # Manual counting: open the point-placement dialog instead of the pipeline.
        if self.current_workflow_def.is_manual():
            ManualCountingDialog(self, self.project, selected_images,
                                 self.current_workflow_def).show()
            return

        workflow = build_workflow_instance(self.current_workflow_def)
        quant_dialog = QuantificationDialog(self.frame, selected_images, workflow)
        settings = quant_dialog.show_dialog()

        if settings:
            progress_dialog = ProgressDialog(self.frame, "Processing images...", 100)
            worker = QuantificationWorker(self, self.project, settings, progress_dialog)
            worker.execute()
            progress_dialog.setVisible(True)

    def remove_images_action(self, event):
        """ Removes selected image(s) and all of their associated data (ROIs, outlines) from the project file """
        selected_rows = self.image_table.getSelectedRows()
        if not selected_rows:
            return
        
        # Confirm deletion
        count = len(selected_rows)
        message = ("Are you sure you want to permanently delete these {} image(s) "
                   "and all associated Region and result files?\n\nThis action cannot be undone.".format(count))
        title = "Confirm Deletion"
        
        result = JOptionPane.showConfirmDialog(self.frame, message, title, 
                                               JOptionPane.YES_NO_OPTION, 
                                               JOptionPane.WARNING_MESSAGE)
        
        if result != JOptionPane.YES_OPTION:
            return
        
        model_indices = [self.image_table.convertRowIndexToModel(row) for row in selected_rows]
        images_to_delete = [self.project.images[idx] for idx in model_indices]

        deleted_count = self.project.remove_images(images_to_delete)
        if deleted_count > 0:
            self.set_unsaved_changes(True)
            self.save_project_action(None) # This syncs the DB and resets the unsaved flag
            self.status_label.setText("Successfully removed {} image(s).".format(deleted_count))
    
        self.update_ui_for_project()


    def import_images_action(self, event):
        """Opens a file chooser and starts the background import process."""
        if not self.project:
            return
        
        chooser = JFileChooser()
        chooser.setDialogTitle("Select Images to Import")
        chooser.setMultiSelectionEnabled(True)
        chooser.setFileFilter(FileNameExtensionFilter("Image Files (tif, tiff, jpg, jpeg)", ["tif","tiff","jpg","jpeg"]))

        if chooser.showOpenDialog(self.frame) == JFileChooser.APPROVE_OPTION:
            selected_files = chooser.getSelectedFiles()

            # Ask whether to copy the files into the project or link to them.
            options = ["Copy into project", "Create links", "Cancel"]
            choice = JOptionPane.showOptionDialog(
                self.frame,
                "How should the selected images be added to the project?\n\n"
                "Copy: duplicates the files into the project's Images folder\n"
                "  (uses disk space, but self-contained).\n\n"
                "Link: creates symbolic links to the originals\n"
                "  (saves space, but breaks if the originals are moved or deleted;\n"
                "   on Windows this may require Developer Mode / admin rights and\n"
                "   will fall back to copying if links can't be created).",
                "Import Images", JOptionPane.DEFAULT_OPTION, JOptionPane.QUESTION_MESSAGE,
                None, options, options[0])
            if choice != 0 and choice != 1:   # Cancel or closed
                return
            link_mode = (choice == 1)

            # 1. Create an instance of our new worker class
            worker = ImageImportWorker(self, self.project, selected_files, link_mode)

            # 2. Create a ProgressMonitor to watch the worker
            progress_monitor = ProgressMonitor(self.frame, "Importing Images", "Starting...", 0, 100)
            progress_monitor.setMillisToDecideToPopup(100) # Show the dialog quickly

            # 3. Link the worker's progress changes to the monitor's display
            class ProgressListener(PropertyChangeListener):
                def propertyChange(self, evt):
                    prop = evt.getPropertyName()
                    if "progress" == prop:
                        progress_monitor.setProgress(evt.getNewValue())
                    elif "note" == prop:
                        progress_monitor.setNote(evt.getNewValue())
                    
                    if progress_monitor.isCanceled():
                        worker.cancel(True)
            
            worker.addPropertyChangeListener(ProgressListener())

            # 4. Start the background task
            worker.execute()


    def windowClosing(self, event):
        """ Called when user attempts to close window, intercepts and prompts to save changes """
        if self.unsaved_changes:
            title = "Unsaved Changes"
            message = "You have unsaved changes. Would you like to save before closing?"

            # show dialog
            result = JOptionPane.showConfirmDialog(self.frame, message, title, JOptionPane.YES_NO_CANCEL_OPTION)

            if result == JOptionPane.YES_OPTION:
                if self.save_project_action(None):
                    self.frame.dispose()
                # If save fails, do nothing

            elif result == JOptionPane.NO_OPTION:
                self.frame.dispose()

            # if cancel, do nothing

        else: # no unsaved changes
            self.frame.dispose()

    # UI update logic
    def load_project(self, project_dir):
        """ Loads a project's data and update entire UI"""
        # Safety check: detect if user selected an internal project folder
        folder_name = os.path.basename(project_dir)
        if folder_name in INTERNAL_PROJECT_FOLDERS:
            parent_dir = os.path.dirname(project_dir)
            result = JOptionPane.showConfirmDialog(
                self.frame,
                "You selected an internal project folder '{}'.\n"
                "Did you mean to open the project at:\n{}?".format(folder_name, parent_dir),
                "Confirm Project Location",
                JOptionPane.YES_NO_OPTION
            )
            if result == JOptionPane.YES_OPTION:
                project_dir = parent_dir
            else:
                self.status_label.setText("Project loading cancelled.")
                return
        
        self.status_label.setText("Loading Project {}".format(project_dir))
        try:
            self.project = Project(project_dir)
            self.update_ui_for_project()

            self.import_button.setEnabled(True)

            self.status_label.setText("Sucessfully loaded project: {}".format(self.project.name))
            self.set_unsaved_changes(False)
        except Exception as e:
            self.status_label.setText("Error Loading Project. See Log for details")
            IJ.log("--- ERROR while loading project ---")
            IJ.log(traceback.format_exc())
            IJ.log("-----------------------------------")

    def update_ui_for_project(self):
        """ Populates the UI componenets with the current project's data """
        if not self.project:
            return
        
        # Update project name label
        self.project_name_label.setText("Project: " + self.project.name)
        
        # 1. Get the table's selection model and temporarily remove our listener.
        selection_model = self.image_table.getSelectionModel()
        listeners = selection_model.getListSelectionListeners()
        for l in listeners:
            selection_model.removeListSelectionListener(l)
        
        try:
            # Clear the table efficiently and repopulate it. 
            self.image_table_model.setRowCount(0) 
            
            for img in self.project.images:
                roi_file_status = "Yes" if img.has_roi() else "No"
                self.image_table_model.addRow([
                    img.filename,
                    roi_file_status,
                    len(img.rois),
                    img.status,
                    img.location_label()
                ])
        finally:
            # Re-attach the listener so the UI works normally again.

            for l in listeners:
                selection_model.addListSelectionListener(l)

        # 4. Manually call the listener logic to ensure the details pane is correctly updated
        self.on_image_selection(None)
        
        # 5. Update the template list
        self._update_template_list()
        
        # 6. Enable template buttons now that a project is loaded
        self.add_template_btn.setEnabled(True)
        self.remove_template_btn.setEnabled(True)

        # 7. Restore the project's selected workflow (if it still exists globally)
        name = getattr(self.project, 'selected_workflow', None)
        if name and self.workflow_store.exists(name):
            self.current_workflow_def = self.workflow_store.load(name)
        else:
            self.current_workflow_def = None
        self._refresh_workflow_summary()
        self._refresh_workflow_list()

        # 8. Refresh the project summary (image + per-region ROI counts)
        self._update_summary()

    def update_view_for_image(self, updated_image):
        """
        Finds and updates a single image's row in the JTable instead of
        reloading the entire UI.
        """
        for i in range(self.image_table_model.getRowCount()):
            # Find the row corresponding to our image
            if self.image_table_model.getValueAt(i, 0) == updated_image.filename:
                # Update the values in the table model
                self.image_table_model.setValueAt("Yes" if updated_image.has_roi() else "No", i, 1)
                self.image_table_model.setValueAt(len(updated_image.rois), i, 2)
                self.image_table_model.setValueAt(updated_image.status, i, 3)
                
                # Refresh selection-dependent buttons/status
                self.on_image_selection(None) # Pass a dummy event or refactor to take an index
                break

        # ROI counts changed for this image, so refresh the project summary
        self._update_summary()

    def _update_template_list(self):
        """Refreshes the template list display."""
        self.template_list_model.clear()
        if not self.project:
            return
        for t in self.project.roi_templates:
            display = t['name']
            if t.get('default_bregma'):
                display += " (Bregma: {})".format(t['default_bregma'])
            self.template_list_model.addElement(display)

    def _add_template_action(self, event):
        """Prompts user to add a new ROI template."""
        name = JOptionPane.showInputDialog(self.frame, "Enter template name:", "Add Template", JOptionPane.PLAIN_MESSAGE)
        if name and name.strip():
            # Check for duplicates
            for t in self.project.roi_templates:
                if t['name'].lower() == name.strip().lower():
                    JOptionPane.showMessageDialog(self.frame, "A template with this name already exists.", "Duplicate", JOptionPane.WARNING_MESSAGE)
                    return
            self.project.roi_templates.append({'name': name.strip(), 'default_bregma': ''})
            self._update_template_list()
            self.set_unsaved_changes(True)

    def _remove_template_action(self, event):
        """Removes the selected template."""
        idx = self.template_list.getSelectedIndex()
        if idx == -1:
            JOptionPane.showMessageDialog(self.frame, "Please select a template to remove.", "No Selection", JOptionPane.WARNING_MESSAGE)
            return
        template = self.project.roi_templates[idx]
        result = JOptionPane.showConfirmDialog(self.frame, "Remove template '{}'?".format(template['name']), "Confirm", JOptionPane.YES_NO_OPTION)
        if result == JOptionPane.YES_OPTION:
            del self.project.roi_templates[idx]
            self._update_template_list()
            self.set_unsaved_changes(True)


class ImageImportWorker(SwingWorker):
    """
    Handles the image import process on a background thread to keep the GUI responsive,
    and reports progress updates that can be displayed by a progress bar.
    """
    def __init__(self, parent_gui, project, selected_files, link_mode=False):
        super(ImageImportWorker, self).__init__()

        self.parent_gui = parent_gui
        self.project = project
        self.selected_files = selected_files
        self.link_mode = link_mode   # True: symlink into Images/; False: copy
        self.newly_added_count = 0
        self.skipped_files = []

    def doInBackground(self):
        """This is where the long-running work happens."""
        images_dir = self.project.paths['images']
        total_files = len(self.selected_files)

        for i, source_file in enumerate(self.selected_files):
            # Check if the user has clicked the "Cancel" button on the progress monitor
            if self.isCancelled():
                break

            # Update the note on the progress monitor to show the current file
            verb = "Linking" if self.link_mode else "Copying"
            self.firePropertyChange("note", "", "{} {}...".format(verb, source_file.getName()))

            dest_file = File(images_dir, source_file.getName())

            if dest_file.exists():
                self.skipped_files.append(source_file.getName())
                continue # Skip existing files

            try:
                if self.link_mode:
                    # Symlink into Images/; fall back to a copy if the OS refuses
                    # (e.g. Windows without Developer Mode / admin rights).
                    try:
                        Files.createSymbolicLink(dest_file.toPath(), source_file.toPath())
                    except (Exception, Throwable) as le:
                        IJ.log("Could not link '{}' ({}); copying instead.".format(
                            source_file.getName(), le))
                        Files.copy(source_file.toPath(), dest_file.toPath(),
                                   StandardCopyOption.REPLACE_EXISTING)
                else:
                    Files.copy(source_file.toPath(), dest_file.toPath(), StandardCopyOption.REPLACE_EXISTING)

                # Update the project data structure in memory
                new_image = ProjectImage(dest_file.getName(), self.project.root_dir)
                new_image.status = "In Progress"
                self.project.images.append(new_image)
                self.newly_added_count += 1
            except Exception as e:
                # Proper error handling should be added here if needed
                IJ.log("Failed to import '{}': {}".format(source_file.getName(), e))

            # Report the percentage complete
            progress = int(100.0 * (i + 1) / total_files)
            self.super__setProgress(progress)
        
        return self.newly_added_count

    def done(self):
        """This runs on the GUI thread after doInBackground is finished."""
        try:
            # The get() method retrieves the result and also raises any exceptions
            # that occurred during the background task.
            count = self.get()
            
            if count > 0:
                self.parent_gui.status_label.setText("Successfully imported {} new images.".format(count))
                self.parent_gui.update_ui_for_project()
                self.parent_gui.set_unsaved_changes(True)
            
            if self.skipped_files:
                IJ.log("Skipped {} existing files.".format(len(self.skipped_files)))

        except Exception as e:
            error_msg = "An error occurred during import: {}".format(e)
            IJ.log(error_msg)
            JOptionPane.showMessageDialog(self.parent_gui.frame, error_msg, "Import Error", JOptionPane.ERROR_MESSAGE)

class EditableROIsTableModel(AbstractTableModel):
    """ Helper class to creat custom table model that allows editing of ROI details table"""
    def __init__(self, project_image):
        self.image = project_image
        self.headers = ["ROI Name", "Bregma", "Status"]
        self.data = self.image.rois if self.image else []
        self.header_map = {'roi_name': 0, 'bregma': 1, 'status': 2}

    def getRowCount(self):
        return len(self.data)
    
    def getColumnCount(self):
        return len(self.headers)
    
    def getValueAt(self, rowIndex, columnIndex):
        key = self.headers[columnIndex].lower().replace(" ", "_")
        return self.data[rowIndex].get(key, "")
    
    def getColumnName(self, columnIndex):
        return self.headers[columnIndex]
    
    def isCellEditable(self, rowIndex, columnIndex):
        return True

    def setValueAt(self, aValue, rowIndex, columnIndex):
        key = self.headers[columnIndex].lower().replace(" ", "_")
        self.data[rowIndex][key] = aValue
        # Updates data in projectImage directly
        self.fireTableCellUpdated(rowIndex, columnIndex)
