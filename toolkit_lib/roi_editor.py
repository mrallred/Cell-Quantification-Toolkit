# --- Python Standard Library ---
import os
import traceback

from ij import IJ
from ij.plugin.frame import RoiManager

from javax.swing import (JDialog, JPanel, JList, JScrollPane, JTextField,
                         JLabel, JCheckBox, JButton, DefaultListModel,
                         ListSelectionModel, BorderFactory, JOptionPane,
                         SwingUtilities)

from java.awt import BorderLayout, GridLayout
from java.awt.event import WindowAdapter, ActionListener, FocusAdapter

class ROIEditor(WindowAdapter):
    """ 
    Creates a JFrame with tools for creating, modifying, and managing ROIs 
    for a single image using a "live update" model.
    """
    def __init__(self, parent_gui, project, project_image):
        self.parent_gui = parent_gui
        self.project = project
        self.image_obj = project_image
        self.win = None
        self.unsaved_changes = False 
        self.updating_fields = False  # Flag to prevent recursive updates
        self.last_selected_index = -1  # Track the last selected ROI index

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
        self.frame.setSize(350, 650)  # Made taller to accommodate new button
        self.frame.addWindowListener(self)
        self.frame.setLayout(BorderLayout(5,5))

        # ROI list - Initialize components first, then populate
        self.roi_list_model = DefaultListModel()
        self.roi_list = JList(self.roi_list_model)
        self.update_roi_list_from_manager()  # Now safe to call
        self.roi_list.setSelectionMode(ListSelectionModel.SINGLE_SELECTION)
        self.roi_list.addListSelectionListener(self._on_roi_select)

        list_pane = JScrollPane(self.roi_list)
        list_pane.setBorder(BorderFactory.createTitledBorder("ROIs"))

        # Edit Panel
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
        
        # Event listeners for live updates with debouncing
        class TextFieldUpdater(FocusAdapter, ActionListener):
            def __init__(self, editor):
                self.editor = editor
            def actionPerformed(self, e):
                self.editor._apply_text_field_changes()
            def focusLost(self, e):
                self.editor._apply_text_field_changes()
        
        updater = TextFieldUpdater(self)
        self.roi_name_field.addActionListener(updater)
        self.roi_name_field.addFocusListener(updater)
        self.bregma_field.addActionListener(updater)
        self.bregma_field.addFocusListener(updater)
        
        # Button panel
        button_panel = JPanel(GridLayout(0, 1, 10, 10))
        button_panel.setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10))

        create_button = JButton("Create New From Selection", actionPerformed=self._create_new_roi)
        delete_button = JButton("Delete Selected ROI", actionPerformed=self._delete_selected_roi)
        
        # NEW: Save Current ROI button
        self.save_current_button = JButton("Save Current ROI Changes", actionPerformed=self._save_current_roi)
        self.save_current_button.setEnabled(False)  # Disabled until ROI is selected
        
        self.ready_checkbox = JCheckBox("Mark as Ready for Quantification")
        is_ready = (self.image_obj.status == "Ready to Quantify")
        self.ready_checkbox.setSelected(is_ready)
        self.ready_checkbox.addActionListener(self._toggle_ready_status)
        
        save_button = JButton("Save All ROIs & Close", actionPerformed=self._save_and_close)

        button_panel.add(create_button)
        button_panel.add(delete_button)
        button_panel.add(self.save_current_button)  # NEW button added here
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

    def update_roi_list_from_manager(self):
        """Syncs the JList with the IJ ROI manager."""
        current_selection = self.roi_list.getSelectedIndex()
        self.roi_list_model.clear()
        
        rois = self.rm.getRoisAsArray()
        for i, roi in enumerate(rois):
            roi_name = roi.getName() or "Untitled"
            display_text = "{}. {}".format(i + 1, roi_name)
            self.roi_list_model.addElement(display_text)
        
        # Restore selection more carefully
        if current_selection != -1 and current_selection < self.roi_list_model.getSize():
            SwingUtilities.invokeLater(lambda: self.roi_list.setSelectedIndex(current_selection))

    def _set_unsaved_changes(self, state):
        """Updates the UI to show if there are unsaved changes."""
        self.unsaved_changes = state
        title = self.base_title
        if state:
            title += " *"
        self.frame.setTitle(title)

    def _toggle_show_all(self, event):
        """Toggles visibility of all ROIs on the image."""
        try:
            if event.getSource().isSelected():
                self.rm.runCommand("Show All")
            else:
                self.rm.runCommand("Show None")
            
            # Force a refresh of the image display
            if self.imp:
                self.imp.updateAndDraw()
        except Exception as e:
            IJ.log("Error toggling ROI visibility: " + str(e))

    def _on_roi_select(self, event):
        """Populates text fields when a new ROI is selected."""
        if not event.getValueIsAdjusting() and not self.updating_fields:
            selected_index = self.roi_list.getSelectedIndex()
            
            # Before switching to a new ROI, save any changes to the previously selected one
            if (self.last_selected_index != -1 and 
                self.last_selected_index != selected_index and 
                self.last_selected_index < self.rm.getCount()):
                self._sync_current_roi_from_image()
            
            if selected_index != -1 and selected_index < self.rm.getCount():
                try:
                    self.updating_fields = True  # Prevent recursive updates
                    
                    # Load and display the selected ROI
                    self._refresh_roi_display(selected_index)
                    
                    # Update text fields with the selected ROI's data
                    selected_roi = self.rm.getRoi(selected_index)
                    if selected_roi:
                        self.roi_name_field.setText(selected_roi.getName() or "")
                        bregma_val = selected_roi.getProperty("comment") or ''
                        self.bregma_field.setText(str(bregma_val))
                    
                    # Enable the save current button
                    self.save_current_button.setEnabled(True)
                    self.last_selected_index = selected_index
                    
                except Exception as e:
                    IJ.log("Error in ROI selection: " + str(e))
                finally:
                    self.updating_fields = False
            else:
                # Clear fields if no valid selection
                if not self.updating_fields:
                    self.roi_name_field.setText("")
                    self.bregma_field.setText("")
                    self.save_current_button.setEnabled(False)
                    self.last_selected_index = -1

    def _refresh_roi_display(self, selected_index):
        """Properly loads and displays the selected ROI from the manager."""
        try:
            # Clear any existing selection on the image first
            self.imp.deleteRoi()
            
            # Get the ROI from the manager and set it on the image
            selected_roi = self.rm.getRoi(selected_index)
            if selected_roi:
                # Clone the ROI to avoid modifying the original in the manager
                display_roi = selected_roi.clone()
                self.imp.setRoi(display_roi)
            
            # Handle overlay display
            if self.show_all_checkbox.isSelected():
                self.rm.runCommand("Show All")
            else:
                self.rm.runCommand("Show None")
            
            # Force display update
            self.imp.updateAndDraw()
            
        except Exception as e:
            IJ.log("Error refreshing ROI display: " + str(e))

    def _sync_current_roi_from_image(self):
        """
        NEW METHOD: Captures any geometric changes made to the ROI on the image
        and updates the corresponding ROI in the manager.
        """
        if self.last_selected_index == -1 or self.last_selected_index >= self.rm.getCount():
            return
            
        try:
            # Get the current ROI from the image (which may have been modified by dragging)
            current_image_roi = self.imp.getRoi()
            
            if current_image_roi:
                # Get the original ROI from the manager
                original_manager_roi = self.rm.getRoi(self.last_selected_index)
                
                # Create a new ROI with the updated geometry but preserve the metadata
                updated_roi = current_image_roi.clone()
                if original_manager_roi:
                    # Preserve the name from the original
                    updated_roi.setName(original_manager_roi.getName())
                    
                    # Copy specific properties we know about (safer approach)
                    comment = original_manager_roi.getProperty("comment")
                    if comment is not None:
                        updated_roi.setProperty("comment", comment)
                
                # Replace the ROI in the manager
                self.rm.setRoi(updated_roi, self.last_selected_index)
                self._set_unsaved_changes(True)
                
        except Exception as e:
            IJ.log("Error syncing ROI from image: " + str(e))

    def _save_current_roi(self, event):
        """
        NEW METHOD: Explicitly saves changes to the currently selected ROI.
        """
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1:
            JOptionPane.showMessageDialog(self.frame, 
                "Please select an ROI from the list first.", 
                "No ROI Selected", 
                JOptionPane.WARNING_MESSAGE)
            return
            
        try:
            # First apply any text field changes
            self._apply_text_field_changes()
            
            # Then sync any geometric changes from the image
            self._sync_current_roi_from_image()
            
            # Update the list display to reflect any name changes
            self.update_roi_list_from_manager()
            
            # Re-select the same ROI to maintain selection
            if selected_index < self.roi_list_model.getSize():
                self.roi_list.setSelectedIndex(selected_index)
            
            # Show confirmation
            roi_name = self.rm.getRoi(selected_index).getName() or "Untitled"
                
        except Exception as e:
            IJ.log("Error saving current ROI: " + str(e))
            JOptionPane.showMessageDialog(self.frame, 
                "Error saving ROI: " + str(e), 
                "Save Error", 
                JOptionPane.ERROR_MESSAGE)

    def _apply_text_field_changes(self):
        """Applies text field changes with better error handling."""
        if self.updating_fields:  # Prevent recursive calls
            return
            
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1 or selected_index >= self.rm.getCount():
            return

        try:
            selected_roi = self.rm.getRoi(selected_index)
            if not selected_roi:
                return
                
            new_name = self.roi_name_field.getText().strip()
            new_bregma = self.bregma_field.getText().strip()
            
            # Get current values for comparison
            current_name = selected_roi.getName() or ""
            current_bregma = str(selected_roi.getProperty("comment") or "")
            
            # Check if anything actually changed
            if current_name == new_name and current_bregma == new_bregma:
                return
                
            # Validate bregma value if not empty
            if new_bregma:
                try:
                    float(new_bregma)  # Test if it's a valid number
                except ValueError:
                    JOptionPane.showMessageDialog(self.frame, 
                        "Bregma value must be a number or empty.", 
                        "Invalid Input", 
                        JOptionPane.WARNING_MESSAGE)
                    # Reset to original value
                    self.bregma_field.setText(current_bregma)
                    return
            
            # Apply the changes
            self.updating_fields = True
            
            # Update ROI properties
            selected_roi.setProperty("comment", new_bregma if new_bregma else None)
            if new_name != current_name:
                self.rm.rename(selected_index, new_name)
            
            self._set_unsaved_changes(True)
                
        except Exception as e:
            IJ.log("Error applying text field changes: " + str(e))
            JOptionPane.showMessageDialog(self.frame, 
                "Error updating ROI: " + str(e), 
                "Update Error", 
                JOptionPane.ERROR_MESSAGE)
        finally:
            self.updating_fields = False

    def _create_new_roi(self, event):
        """Creates a new ROI from the current image selection using the name from the text field."""
        current_roi = self.imp.getRoi()
        if not current_roi:
            JOptionPane.showMessageDialog(self.frame, 
                "Please draw a selection on the image first.", 
                "No Selection Found", 
                JOptionPane.WARNING_MESSAGE)
            return
        
        # Get the name from the ROI name text field
        new_name = self.roi_name_field.getText().strip()
        if not new_name:
            JOptionPane.showMessageDialog(self.frame, 
                "Please enter a name in the 'ROI Name' field first.", 
                "No Name Provided", 
                JOptionPane.WARNING_MESSAGE)
            return

        try:
            # Clone the ROI to avoid modifying the original
            roi_clone = current_roi.clone()
            roi_clone.setName(new_name)
            
            # Also set the bregma value if provided
            bregma_value = self.bregma_field.getText().strip()
            if bregma_value:
                try:
                    float(bregma_value)  # Validate it's a number
                    roi_clone.setProperty("comment", bregma_value)
                except ValueError:
                    JOptionPane.showMessageDialog(self.frame, 
                        "Invalid bregma value. Using empty value instead.", 
                        "Invalid Bregma", 
                        JOptionPane.WARNING_MESSAGE)
            
            # Add to ROI manager
            self.rm.addRoi(roi_clone)
            
            # Update UI
            self.update_roi_list_from_manager()
            new_index = self.rm.getCount() - 1
            self.roi_list.setSelectedIndex(new_index)
            self._set_unsaved_changes(True)
            
            # Clear the image selection
            self.imp.deleteRoi()
            
            # Clear the text fields for the next ROI
            self.roi_name_field.setText("")
            self.bregma_field.setText("")
            
        except Exception as e:
            IJ.log("Error creating new ROI: " + str(e))
            JOptionPane.showMessageDialog(self.frame, 
                "Error creating ROI: " + str(e), 
                "Creation Error", 
                JOptionPane.ERROR_MESSAGE)

    def _delete_selected_roi(self, event):
        """Deletes the selected ROI."""
        selected_index = self.roi_list.getSelectedIndex()
        if selected_index == -1:
            JOptionPane.showMessageDialog(self.frame, 
                "Please select an ROI from the list to delete.", 
                "No ROI Selected", 
                JOptionPane.WARNING_MESSAGE)
            return

        # Confirm deletion
        roi_name = self.rm.getRoi(selected_index).getName() or "Untitled"
        result = JOptionPane.showConfirmDialog(self.frame, 
            "Delete ROI '{}'?".format(roi_name), 
            "Confirm Deletion", 
            JOptionPane.YES_NO_OPTION)
        
        if result != JOptionPane.YES_OPTION:
            return

        try:
            # Delete from ROI manager
            self.rm.select(selected_index)
            self.rm.runCommand("Delete")
            
            # Reset tracking since we deleted the selected ROI
            self.last_selected_index = -1
            
            # Update UI
            self.update_roi_list_from_manager()
            self.roi_name_field.setText("")
            self.bregma_field.setText("")
            self.save_current_button.setEnabled(False)
            self._set_unsaved_changes(True)
            
            # Clear image selection
            self.imp.deleteRoi()
            
        except Exception as e:
            IJ.log("Error deleting ROI: " + str(e))
            JOptionPane.showMessageDialog(self.frame, 
                "Error deleting ROI: " + str(e), 
                "Deletion Error", 
                JOptionPane.ERROR_MESSAGE)

    def _toggle_ready_status(self, event):
        """Updates the image's status in memory when the checkbox is toggled."""
        if self.ready_checkbox.isSelected():
            self.image_obj.status = "Ready to Quantify"
        else:
            self.image_obj.status = "Pending ROIs"
        self._set_unsaved_changes(True)
        
    def _save_all_changes(self):
        """Save with better error handling and validation."""
        try:
            # Before saving, sync any changes to the currently selected ROI
            if self.last_selected_index != -1:
                self._sync_current_roi_from_image()
            
            # Validate all ROI names are not empty (duplicate names are allowed)
            rois_from_manager = self.rm.getRoisAsArray()
            
            for i, roi in enumerate(rois_from_manager):
                name = roi.getName()
                if not name or name.strip() == "":
                    JOptionPane.showMessageDialog(self.frame, 
                        "ROI #{} has no name. Please name all ROIs before saving.".format(i + 1), 
                        "Validation Error", 
                        JOptionPane.WARNING_MESSAGE)
                    return False
            
            # Update project data structure
            new_rois_list = []
            for roi in rois_from_manager:
                new_rois_list.append({
                    'roi_name': roi.getName(),
                    'bregma': roi.getProperty("comment") or 'N/A',
                })
            self.image_obj.rois = new_rois_list

            # Save to .zip file
            if not os.path.exists(os.path.dirname(self.image_obj.roi_path)):
                os.makedirs(os.path.dirname(self.image_obj.roi_path))
                
            self.rm.runCommand("Save", self.image_obj.roi_path)
            return True
            
        except Exception as e:
            IJ.log("Error saving ROIs: " + str(e))
            IJ.log(traceback.format_exc())
            JOptionPane.showMessageDialog(self.frame, 
                "Failed to save ROI data: " + str(e), 
                "Save Error", 
                JOptionPane.ERROR_MESSAGE)
            return False

    def _save_and_close(self, event=None):
        """Saves all changes and closes the editor."""
        # Validate current field contents before saving
        if not self.updating_fields:
            self._apply_text_field_changes()
        
        # Save ROIs to .zip and update in-memory object
        if not self._save_all_changes():
            return

        # Save project databases
        if not self.project.sync_project_db():
            JOptionPane.showMessageDialog(self.frame, 
                "Could not save project databases. See log for details.", 
                "Database Sync Failed", 
                JOptionPane.ERROR_MESSAGE)
            return

        # Update main GUI
        self.parent_gui.update_view_for_image(self.image_obj)
        self.parent_gui.set_unsaved_changes(True)

        self._set_unsaved_changes(False)
        self.cleanup()

    def cleanup(self):
        """Closes the image, ROI Manager, and disposes the frame."""
        try:
            if self.imp:
                self.imp.changes = False  # Prevent save dialog
                self.imp.close()
            if self.rm:
                self.rm.close()
            if self.frame:
                self.frame.dispose()
        except Exception as e:
            IJ.log("Error during cleanup: " + str(e))

    def windowClosing(self, event):
        """Handles the window 'X' button."""
        if self.unsaved_changes:
            title = "Unsaved ROI Changes"
            message = "You have unsaved changes. Would you like to save before closing?"
            result = JOptionPane.showConfirmDialog(self.frame, message, title, 
                JOptionPane.YES_NO_CANCEL_OPTION, JOptionPane.QUESTION_MESSAGE)

            if result == JOptionPane.YES_OPTION:
                self._save_and_close(None)
            elif result == JOptionPane.NO_OPTION:
                self.cleanup()
            # If CANCEL, do nothing
        else:
            self.cleanup()
