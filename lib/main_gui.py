# Python Standard Library 
import os
import traceback

# ImageJ/Fiji API
from ij import IJ

# Java I/O and NIO 
from java.io import File
from java.nio.file import Files, StandardCopyOption

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
from java.awt import BorderLayout, FlowLayout, Font, GridLayout

# Internal Modules
from .project_model import Project, ProjectImage
from .roi_editor import ROIEditor
from .quantification import QuantificationDialog, QuantificationWorker, ProgressDialog
from .results_viewer import ResultsViewer


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

        # ROI Template List (replaces file tree)
        self.template_list_model = DefaultListModel()
        self.template_list = JList(self.template_list_model)
        template_scroll_pane = JScrollPane(self.template_list)
        template_scroll_pane.setBorder(BorderFactory.createTitledBorder("Regions to analyze"))

        # Template management buttons
        template_button_panel = JPanel(GridLayout(0, 1, 5, 5))
        self.add_template_btn = JButton("Add Region", actionPerformed=self._add_template_action)
        self.remove_template_btn = JButton("Remove Region", actionPerformed=self._remove_template_action)
        self.add_template_btn.setEnabled(False)
        self.remove_template_btn.setEnabled(False)
        template_button_panel.add(self.add_template_btn)
        template_button_panel.add(self.remove_template_btn)

        left_panel = JPanel(BorderLayout())
        left_panel.add(template_scroll_pane, BorderLayout.CENTER)
        left_panel.add(template_button_panel, BorderLayout.SOUTH)

        right_panel = JPanel(BorderLayout())

        # Image table 
        image_cols = ["Filename", "ROI File", "# ROIs", "Status"]
        self.image_table_model = DefaultTableModel(None, image_cols)
        self.image_table = JTable(self.image_table_model)
        self.image_table.setSelectionMode(ListSelectionModel.MULTIPLE_INTERVAL_SELECTION)
        self.image_table.getSelectionModel().addListSelectionListener(self.on_image_selection)
        image_table_pane = JScrollPane(self.image_table)
        image_table_pane.setBorder(BorderFactory.createTitledBorder("Project Images"))
        
        # ROI detail table
        self.roi_table = JTable()
        roi_table_pane = JScrollPane(self.roi_table)
        roi_table_pane.setBorder(BorderFactory.createTitledBorder("ROI Details"))
        
        # Split pane for two tables
        right_split_pane = JSplitPane(JSplitPane.VERTICAL_SPLIT, image_table_pane, roi_table_pane)
        right_split_pane.setDividerLocation(300)
        right_panel.add(right_split_pane, BorderLayout.CENTER)

        # Main split pane for template list and tables
        main_split_pane = JSplitPane(JSplitPane.HORIZONTAL_SPLIT, left_panel, right_panel)
        main_split_pane.setDividerLocation(220)
        self.frame.add(main_split_pane, BorderLayout.CENTER)

    def build_status_bar(self):
        control_panel = JPanel(BorderLayout())
        control_panel.setBorder(EmptyBorder(5,5,5,5))

        self.status_label = JLabel("Open a project folder to begin")
        control_panel.add(self.status_label, BorderLayout.CENTER)
        
        button_panel = JPanel(FlowLayout(FlowLayout.RIGHT))

        self.import_button = JButton("Import Images", enabled=False)
        self.remove_button = JButton("Remove Selected Image", enabled=False)
        self.select_all_button = JButton("Select All / None")
        self.roi_button = JButton("Define/Edit ROIs", enabled=False)
        self.quant_button = JButton("Run Quantification", enabled=False)
        self.show_results_button = JButton("Show Results", enabled=False)

        button_panel.add(self.import_button)
        button_panel.add(self.remove_button)
        button_panel.add(self.select_all_button)
        button_panel.add(self.roi_button)
        button_panel.add(self.quant_button)
        button_panel.add(self.show_results_button)

        control_panel.add(button_panel, BorderLayout.EAST)
        self.frame.add(control_panel, BorderLayout.SOUTH)

        self.import_button.addActionListener(self.import_images_action)
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

        selected_image = self.project.images[selected_row]
        
        # The ResultsViewer will scan run folders for this image's outlines
        viewer = ResultsViewer(self.frame, selected_image, self.project)
        viewer.show()

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
            
            # Enable quant button only if at least one selected image has ROIs
            if selection_count > 0:
                selected_rows = self.image_table.getSelectedRows()
                has_rois = any(self.project.images[r].has_roi() for r in selected_rows)
                self.quant_button.setEnabled(has_rois)
            else:
                self.quant_button.setEnabled(False)

            if selection_count == 1:
                selected_row = self.image_table.getSelectedRow()
                # Safety check in case the selection is cleared before this code runs
                if selected_row == -1: 
                    return

                selected_image = self.project.images[selected_row]
                self.status_label.setText("Selected: {}".format(selected_image.filename))
                self.show_results_button.setEnabled(selected_image.has_outlines())

                # Populate the bottom ROI details table for the selected image
                editable_model = EditableROIsTableModel(selected_image)
                editable_model.addTableModelListener(lambda e: self.set_unsaved_changes(True))
                self.roi_table.setModel(editable_model)

            elif selection_count > 1:
                self.status_label.setText("Selected: {} images".format(selection_count))
                # Clear the details table when multiple images are selected
                self.roi_table.setModel(EditableROIsTableModel(None)) 
                self.show_results_button.setEnabled(False)

            else: # Corresponds to selection_count == 0
                self.status_label.setText("No Image(s) Selected")
                # Clear the details table when the selection is empty
                self.roi_table.setModel(EditableROIsTableModel(None)) 
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

        quant_dialog = QuantificationDialog(self.frame, selected_images)
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
                   "and all associated ROI and result files?\n\nThis action cannot be undone.".format(count))
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

            # 1. Create an instance of our new worker class
            worker = ImageImportWorker(self, self.project, selected_files)

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
                    img.status
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
                
                # Refresh the ROI details table as well
                self.on_image_selection(None) # Pass a dummy event or refactor to take an index
                break

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
        name = JOptionPane.showInputDialog(self.frame, "Enter ROI region name:", "Add Template", JOptionPane.PLAIN_MESSAGE)
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
    def __init__(self, parent_gui, project, selected_files):
        super(ImageImportWorker, self).__init__()

        self.parent_gui = parent_gui
        self.project = project
        self.selected_files = selected_files
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
            self.firePropertyChange("note", "", "Copying {}...".format(source_file.getName()))
            
            dest_file = File(images_dir, source_file.getName())

            if dest_file.exists():
                self.skipped_files.append(source_file.getName())
                continue # Skip existing files

            try:
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
