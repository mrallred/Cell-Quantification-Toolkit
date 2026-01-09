import os
import traceback

# --- ImageJ/Java Libraries ---
from ij import IJ
from ij.plugin.frame import RoiManager

from javax.swing import (JDialog, JPanel, JList, JScrollPane, JTextField,
                         JLabel, JCheckBox, JButton, DefaultListModel,
                         ListSelectionModel, BorderFactory, JOptionPane)

from java.awt import BorderLayout, GridLayout
from java.awt.event import WindowAdapter

class ROIEditor(WindowAdapter):
    """ 
    Creates a JFrame with tools for creating, modifying, and managing ROIs 
    for a single image using a "commit on action" model.
    """
    def __init__(self, parent_gui, project, project_image):
        self.parent_gui = parent_gui
        self.project = project
        self.image_obj = project_image
        self.win = None
        self.unsaved_changes = False
        self.updating_fields = False  # Flag to prevent event cascades
        self.last_selected_index = -1 # Track the last selected ROI index
        self.templates = self.project.roi_templates  # Reference to project templates
        self.list_item_map = []  # Maps JList indices to {'type': 'template'/'roi', ...}

        # Open Image and create canvas and imagewindow to hold it
        self.imp = IJ.openImage(self.image_obj.full_path)
        if not self.imp:
            IJ.error("Failed to open image:", self.image_obj.full_path)
            return
        self.imp.show()
        self.win = self.imp.getWindow()

        # Open a local ROI manager instance
        self.rm = RoiManager(True) 
        self.rm.reset()

        if self.image_obj.has_roi():
            self.rm.runCommand("Open", self.image_obj.roi_path)
            self.rm.runCommand("Show All")

        # Build GUI
        self.base_title = "ROI Editor: " + self.image_obj.filename
        self.frame = JDialog(self.win, self.base_title, False)
        self.frame.setSize(350, 650)
        self.frame.addWindowListener(self)
        self.frame.setLayout(BorderLayout(5, 5))

        # --- GUI Components ---

        # ROI list
        self.roi_list_model = DefaultListModel()
        self.roi_list = JList(self.roi_list_model)
        self.update_roi_list_from_manager()
        self.roi_list.setSelectionMode(ListSelectionModel.SINGLE_SELECTION)
        self.roi_list.addListSelectionListener(self._on_roi_select)
        list_pane = JScrollPane(self.roi_list)
        list_pane.setBorder(BorderFactory.createTitledBorder("ROIs"))

        # Edit Panel for selected ROI
        edit_panel = JPanel(GridLayout(0, 2, 5, 5))
        edit_panel.setBorder(BorderFactory.createTitledBorder("Edit Selected ROI"))
        self.roi_name_field = JTextField()
        self.bregma_field = JTextField()
        edit_panel.add(JLabel("ROI Name:"))
        edit_panel.add(self.roi_name_field)
        edit_panel.add(JLabel("Bregma Value:"))
        edit_panel.add(self.bregma_field)
        
        self.show_all_checkbox = JCheckBox("Show All ROIs", True)
        self.show_all_checkbox.addActionListener(self._toggle_show_all)
        edit_panel.add(self.show_all_checkbox)
        
        # Button panel for actions
        button_panel = JPanel(GridLayout(0, 1, 10, 10))
        button_panel.setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10))
        add_subregion_button = JButton("Add selection to current ROI", actionPerformed=self._add_subregion)
        create_button = JButton("Create New ROI (not defined in project)", actionPerformed=self._create_new_roi)
        update_button = JButton("Update Selection", actionPerformed=self._update_selected_roi)
        delete_button = JButton("Delete Selection", actionPerformed=self._delete_selected_roi)
        save_button = JButton("Save All ROIs & Close", actionPerformed=self._save_and_close)

        self.ready_checkbox = JCheckBox("Mark as 'ROIs completed'", True)
        is_ready = (self.image_obj.status == "ROIs completed")
        self.ready_checkbox.setSelected(is_ready)
        self.ready_checkbox.addActionListener(self._toggle_ready_status)

        button_panel.add(add_subregion_button)
        button_panel.add(create_button)
        button_panel.add(update_button)
        button_panel.add(delete_button)
        button_panel.add(self.ready_checkbox)
        button_panel.add(save_button)

        # Main layout
        south_panel = JPanel(BorderLayout())
        south_panel.add(edit_panel, BorderLayout.NORTH)
        south_panel.add(button_panel, BorderLayout.CENTER)
        self.frame.add(list_pane, BorderLayout.CENTER)
        self.frame.add(south_panel, BorderLayout.SOUTH)

    def show(self):
        if not self.frame or not self.win:
            return
        img_win_x = self.win.getX()
        img_win_width = self.win.getWidth()
        img_win_y = self.win.getY()
        self.frame.setLocation(img_win_x + img_win_width, img_win_y)
        self.frame.setVisible(True)
    
    # --------------------------------------------------------------------------
    # Core Logic and Event Handling
    # --------------------------------------------------------------------------

    def _commit_changes_for_index(self, index, commit_geometry=True):
        """
        A single, unified method to save changes for the ROI at a given index.
        The commit_geometry flag prevents overwriting geometry during new ROI creation.
        Returns True if the ROI name was changed.
        """
        if index < 0 or index >= self.rm.getCount():
            return False

        name_was_changed = False
        try:
            manager_roi = self.rm.getRoi(index)
            if not manager_roi: 
                return False

            # Part 1: Commit metadata (text fields)
            new_name = self.roi_name_field.getText().strip()
            new_bregma = self.bregma_field.getText().strip()
            current_bregma = str(manager_roi.getProperty("comment") or "")

            if manager_roi.getName() != new_name and new_name:
                self.rm.rename(index, new_name)
                self._set_unsaved_changes(True)
                name_was_changed = True
            
            if new_bregma != current_bregma:
                if new_bregma:
                    try:
                        float(new_bregma)
                        manager_roi.setProperty("comment", new_bregma)
                    except ValueError:
                        JOptionPane.showMessageDialog(self.frame, "Bregma must be a number.", "Invalid Input", JOptionPane.WARNING_MESSAGE)
                        self.bregma_field.setText(current_bregma)
                else:
                    manager_roi.setProperty("comment", None)
                self._set_unsaved_changes(True)

            # Part 2: Commit geometry (shape on canvas)
            if commit_geometry:
                image_roi = self.imp.getRoi()
                if image_roi:
                    updated_roi = image_roi.clone()
                    # Preserve the name and properties from the manager ROI
                    current_manager_roi = self.rm.getRoi(index)
                    updated_roi.setName(current_manager_roi.getName()) 
                    updated_roi.setProperty("comment", current_manager_roi.getProperty("comment"))
                    self.rm.setRoi(updated_roi, index)
                    self._set_unsaved_changes(True)
                
        except Exception as e:
            IJ.log("Error during commit for index {}: {}".format(index, str(e)))
            traceback.print_exc()
        
        return name_was_changed

    def _on_roi_select(self, event):
        """ Handles a user manually selecting an item in the list. """
        if event.getValueIsAdjusting() or self.updating_fields:
            return

        selected_index = self.roi_list.getSelectedIndex()
        if selected_index != -1 and selected_index < len(self.list_item_map):
            self.updating_fields = True
            try:
                item = self.list_item_map[selected_index]
                
                if item['type'] == 'template':
                    # User clicked a template header
                    template_name = item['template_name']
                    self.roi_name_field.setText(template_name)
                    # Find default bregma for this template
                    for t in self.templates:
                        if t['name'] == template_name:
                            self.bregma_field.setText(t.get('default_bregma', ''))
                            break
                    else:
                        self.bregma_field.setText('')
                    self.imp.deleteRoi()  # Clear any selection on image
                    
                elif item['type'] == 'roi':
                    # User clicked a sub-region (actual ROI)
                    roi_index = item['roi_index']
                    self._refresh_roi_display_by_manager_index(roi_index)
                    selected_roi = self.rm.getRoi(roi_index)
                    if selected_roi:
                        self.roi_name_field.setText(selected_roi.getName() or "")
                        bregma_prop = selected_roi.getProperty("comment")
                        if bregma_prop is not None:
                            self.bregma_field.setText(str(bregma_prop))
                        else:
                            self.bregma_field.setText("")
            finally:
                self.updating_fields = False
        else:
            self.roi_name_field.setText("")
            self.bregma_field.setText("")
        
        self.last_selected_index = selected_index
        
    # --------------------------------------------------------------------------
    # GUI Actions (Buttons & Checkboxes)
    # --------------------------------------------------------------------------

    def _create_new_roi(self, event):
        """
        Creates a new ROI that is NOT associated with any template.
        For template-based ROIs, use 'Add selection to current ROI' instead.
        """
        # Step 1: Validate that a new ROI can be created.
        current_roi = self.imp.getRoi()
        if not current_roi:
            JOptionPane.showMessageDialog(self.frame, "Please draw a selection on the image first.", "No Selection", JOptionPane.WARNING_MESSAGE)
            return
        
        new_name = self.roi_name_field.getText().strip()
        if not new_name:
            JOptionPane.showMessageDialog(self.frame, "Please enter a name in the 'ROI Name' field.", "No Name Provided", JOptionPane.WARNING_MESSAGE)
            return

        # Check if name matches a template - warn user
        for t in self.templates:
            if t['name'].lower() == new_name.lower():
                result = JOptionPane.showConfirmDialog(
                    self.frame, 
                    "'{}' matches a template. Use 'Add selection to current ROI' instead?\nClick No to create as non-template ROI anyway.".format(new_name),
                    "Template Name Detected", 
                    JOptionPane.YES_NO_OPTION
                )
                if result == JOptionPane.YES_OPTION:
                    return  # User chose to use the template workflow
                break

        # Step 2: Save the current selection and create the new ROI object
        roi_clone = current_roi.clone()
        roi_clone.setName(new_name)
        
        bregma_value = self.bregma_field.getText().strip()
        if bregma_value:
            try:
                float(bregma_value)
                roi_clone.setProperty("comment", bregma_value)
            except ValueError: 
                pass

        # Step 3: DON'T commit changes to the previous ROI
        # The text fields now contain data for the NEW roi, not the old one
        # So committing would overwrite the old ROI's name/bregma with the new ROI's data
        
        # Step 4: Add the new ROI to the manager
        self.rm.addRoi(roi_clone)
        new_index = self.rm.getCount() - 1
        
        # Step 5: Update the UI to reflect the new ROI
        try:
            self.updating_fields = True
            
            # Update the list display
            self.update_roi_list_from_manager()
            
            # Select the newly created ROI
            self.roi_list.setSelectedIndex(new_index)
            
            # Clear the text fields for the next ROI entry
            self.roi_name_field.setText("")
            self.bregma_field.setText("")
            
            # Update the tracking index
            self.last_selected_index = new_index
            
            # Refresh the display to show the new ROI
            self._refresh_roi_display(new_index)
            
        finally:
            self.updating_fields = False

        # Step 6: Finalize the creation process
        self._set_unsaved_changes(True)
        

    def _update_selected_roi(self, event):
        """Updates the currently selected ROI with values from the text fields."""
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1 or selected_index >= len(self.list_item_map):
            JOptionPane.showMessageDialog(self.frame, "Please select an ROI from the list to update.", "No ROI Selected", JOptionPane.WARNING_MESSAGE)
            return
        
        item = self.list_item_map[selected_index]
        if item['type'] != 'roi':
            JOptionPane.showMessageDialog(self.frame, "Please select a sub-region (not a template header) to update.", "Invalid Selection", JOptionPane.WARNING_MESSAGE)
            return
        
        roi_index = item['roi_index']
        
        # Commit both metadata and geometry changes
        if self._commit_changes_for_index(roi_index, commit_geometry=True):
            self.update_roi_list_from_manager()
        
        # Refresh to show the updated ROI
        self._refresh_roi_display_by_manager_index(roi_index)
        


    def _delete_selected_roi(self, event):
        """Deletes the selected ROI(s) from the manager based on selection type."""
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1 or selected_index >= len(self.list_item_map):
            JOptionPane.showMessageDialog(self.frame, "Please select an item from the list to delete.", "No Selection", JOptionPane.WARNING_MESSAGE)
            return

        item = self.list_item_map[selected_index]
        
        if item['type'] == 'roi':
            # Case 1: User selected a sub-selection - delete just that ROI
            roi_index = item['roi_index']
            roi_name = self.rm.getRoi(roi_index).getName() or "Untitled"
            result = JOptionPane.showConfirmDialog(self.frame, "Delete ROI '{}'?".format(roi_name), "Confirm Deletion", JOptionPane.YES_NO_OPTION)
            if result != JOptionPane.YES_OPTION:
                return

            self.rm.select(roi_index)
            self.rm.runCommand("Delete")
            
        elif item['type'] == 'template':
            template_name = item['template_name']
            is_orphan = item.get('orphan', False)
            is_undefined = item.get('undefined', False)
            
            if is_undefined:
                # Case 2: Template with no ROIs - nothing to delete
                JOptionPane.showMessageDialog(self.frame, "This template has no selections to delete.", "Nothing to Delete", JOptionPane.INFORMATION_MESSAGE)
                return
            
            # Find all ROIs with this template name
            rois_to_delete = []
            for i, roi in enumerate(self.rm.getRoisAsArray()):
                if roi.getName() == template_name:
                    rois_to_delete.append(i)
            
            if not rois_to_delete:
                JOptionPane.showMessageDialog(self.frame, "No ROIs found for this entry.", "Nothing to Delete", JOptionPane.INFORMATION_MESSAGE)
                return
            
            if is_orphan:
                # Case 3: Non-template header - delete all ROIs (removes the header too)
                result = JOptionPane.showConfirmDialog(
                    self.frame, 
                    "Delete all {} ROIs named '{}'?\nThis will remove the entire entry.".format(len(rois_to_delete), template_name), 
                    "Confirm Deletion", 
                    JOptionPane.YES_NO_OPTION
                )
            else:
                # Case 4: Template header - clear all sub-selections (template stays as undefined)
                result = JOptionPane.showConfirmDialog(
                    self.frame, 
                    "Clear all {} selections for template '{}'?\nThe template will remain but show as undefined.".format(len(rois_to_delete), template_name), 
                    "Confirm Clear", 
                    JOptionPane.YES_NO_OPTION
                )
            
            if result != JOptionPane.YES_OPTION:
                return
            
            # Delete in reverse order to avoid index shifting issues
            for roi_index in sorted(rois_to_delete, reverse=True):
                self.rm.select(roi_index)
                self.rm.runCommand("Delete")
        
        self.last_selected_index = -1
        self.update_roi_list_from_manager()
        self.roi_name_field.setText("")
        self.bregma_field.setText("")
        self._set_unsaved_changes(True)
        self.imp.deleteRoi()

    def _toggle_ready_status(self, event):
        """Updates the image's status in the project object."""
        self.image_obj.status = "ROIs completed" if self.ready_checkbox.isSelected() else "In Progress"
        self._set_unsaved_changes(True)
        
    def _save_and_close(self, event=None):
        """Saves all changes, updates project, and closes the editor."""
        # No need to commit individual ROI changes - all ROIs are already in the manager
        # and will be saved to the file
        
        if not self._save_all_rois_to_file():
            return

        if not self.project.sync_project_db():
            JOptionPane.showMessageDialog(self.frame, "Could not save project databases.", "Database Sync Failed", JOptionPane.ERROR_MESSAGE)
            return

        self.parent_gui.update_view_for_image(self.image_obj)
        self.parent_gui.set_unsaved_changes(True)
        self._set_unsaved_changes(False)
        self.cleanup()
        
    # --------------------------------------------------------------------------
    # Helper & Utility Methods
    # --------------------------------------------------------------------------
    
    def update_roi_list_from_manager(self):
        """Syncs the JList with hierarchical display: templates as headers, ROIs as indented sub-items."""
        listeners = self.roi_list.getListSelectionListeners()
        for l in listeners: 
            self.roi_list.removeListSelectionListener(l)
        try:
            current_selection = self.roi_list.getSelectedIndex()
            self.roi_list_model.clear()
            self.list_item_map = []
            
            existing_rois = self.rm.getRoisAsArray()
            
            # Group ROIs by their name (matching to templates)
            rois_by_name = {}
            for i, roi in enumerate(existing_rois):
                name = roi.getName() or "Untitled"
                if name not in rois_by_name:
                    rois_by_name[name] = []
                rois_by_name[name].append(i)  # Store the manager index
            
            template_number = 0
            
            # First, iterate through templates in order
            for template in self.templates:
                template_name = template['name']
                template_number += 1
                
                # Check if this template has any ROIs defined
                matching_rois = rois_by_name.get(template_name, [])
                
                if matching_rois:
                    # Template has ROIs - show as header
                    self.roi_list_model.addElement("{}. {}".format(template_number, template_name))
                    self.list_item_map.append({'type': 'template', 'template_name': template_name})
                    
                    # Add indented sub-items for each ROI
                    for sub_num, roi_index in enumerate(matching_rois, 1):
                        self.roi_list_model.addElement("      - {} {}".format(template_name, sub_num))
                        self.list_item_map.append({'type': 'roi', 'roi_index': roi_index, 'template_name': template_name})
                else:
                    # Template has no ROIs - show as undefined
                    self.roi_list_model.addElement("{}. {} (undefined)".format(template_number, template_name))
                    self.list_item_map.append({'type': 'template', 'template_name': template_name, 'undefined': True})
            
            # Also show any ROIs that don't match a template (orphans)
            for name, roi_indices in rois_by_name.items():
                # Check if this name matches any template
                is_template = any(t['name'] == name for t in self.templates)
                if not is_template:
                    template_number += 1
                    self.roi_list_model.addElement("{}. {} (no template)".format(template_number, name))
                    self.list_item_map.append({'type': 'template', 'template_name': name, 'orphan': True})
                    for sub_num, roi_index in enumerate(roi_indices, 1):
                        self.roi_list_model.addElement("      - {} {}".format(name, sub_num))
                        self.list_item_map.append({'type': 'roi', 'roi_index': roi_index, 'template_name': name})
            
            if -1 < current_selection < self.roi_list_model.getSize():
                self.roi_list.setSelectedIndex(current_selection)
        finally:
            for l in listeners: 
                self.roi_list.addListSelectionListener(l)

    def _refresh_roi_display(self, selected_index):
        """Loads and displays the selected ROI using the Manager's native select method."""
        # Enforce "Show All" state
        if self.show_all_checkbox.isSelected():
            self.rm.runCommand("Show All")
        else:
            self.rm.runCommand("Show None")

        # Then select the specific ROI to make it active and editable
        if -1 < selected_index < self.rm.getCount():
            self.rm.select(selected_index)
        else:
            self.imp.deleteRoi()

    def _refresh_roi_display_by_manager_index(self, manager_index):
        """Loads and displays an ROI by its index in the RoiManager."""
        # Enforce "Show All" state
        if self.show_all_checkbox.isSelected():
            self.rm.runCommand("Show All")
        else:
            self.rm.runCommand("Show None")

        # Select the specific ROI to make it active and editable
        if -1 < manager_index < self.rm.getCount():
            self.rm.select(manager_index)
        else:
            self.imp.deleteRoi()

    def _save_all_rois_to_file(self):
        """Validates and saves ROI data to a .zip file."""
        rois = self.rm.getRoisAsArray()
        for i, roi in enumerate(rois):
            if not roi.getName() or not roi.getName().strip():
                JOptionPane.showMessageDialog(self.frame, "ROI #{} has no name. Please name all ROIs.".format(i+1), "Validation Error", JOptionPane.WARNING_MESSAGE)
                return False
        
        self.image_obj.rois = [{'roi_name': r.getName(), 'bregma': r.getProperty("comment") or 'N/A'} for r in rois]
        roi_dir = os.path.dirname(self.image_obj.roi_path)
        if not os.path.exists(roi_dir):
            os.makedirs(roi_dir)
            
        self.rm.runCommand("Save", self.image_obj.roi_path)
        return True

    def _toggle_show_all(self, event):
        """Toggles visibility of all ROIs."""
        self._refresh_roi_display(self.roi_list.getSelectedIndex())

    def _add_subregion(self, event):
        """Creates a new ROI with the same name as the selected template or ROI (for disconnected regions)."""
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1 or selected_index >= len(self.list_item_map):
            JOptionPane.showMessageDialog(self.frame, "Select a template or existing ROI first.", "No Selection", JOptionPane.WARNING_MESSAGE)
            return
        
        current_roi = self.imp.getRoi()
        if not current_roi:
            JOptionPane.showMessageDialog(self.frame, "Draw a selection on the image first.", "No Selection", JOptionPane.WARNING_MESSAGE)
            return
        
        # Get the template name from the mapping
        item = self.list_item_map[selected_index]
        template_name = item['template_name']
        
        # Clone the current selection and give it the template name
        roi_clone = current_roi.clone()
        roi_clone.setName(template_name)
        
        # Find default bregma for this template
        for t in self.templates:
            if t['name'] == template_name:
                default_bregma = t.get('default_bregma', '')
                if default_bregma:
                    roi_clone.setProperty("comment", default_bregma)
                break
        
        self.rm.addRoi(roi_clone)
        self.update_roi_list_from_manager()
        self._set_unsaved_changes(True)

    def _set_unsaved_changes(self, state):
        """Updates the UI to show if there are unsaved changes."""
        self.unsaved_changes = state
        self.frame.setTitle(self.base_title + (" *" if state else ""))

    def cleanup(self):
        """Closes all associated windows and resources."""
        if self.imp: 
            self.imp.close()
        if self.rm: 
            self.rm.close()
        if self.frame: 
            self.frame.dispose()

    def windowClosing(self, event):
        """Handles the window 'X' button with a save confirmation."""
        if self.unsaved_changes:
            result = JOptionPane.showConfirmDialog(self.frame, "You have unsaved changes. Save before closing?", "Unsaved Changes", JOptionPane.YES_NO_CANCEL_OPTION)
            if result == JOptionPane.YES_OPTION:
                self._save_and_close()
            elif result == JOptionPane.NO_OPTION:
                self.cleanup()
        else:
            self.cleanup()