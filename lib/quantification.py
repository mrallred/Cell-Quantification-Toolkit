import os
import sys
import csv
import json
import datetime
import traceback
import imp
import re

from ij import IJ, WindowManager
from ij.plugin.frame import RoiManager
from ij.gui import PolygonRoi, Roi

from java.lang import Runnable, System

from javax.swing import (JDialog, JPanel, JLabel, JComboBox, JCheckBox,
                         JButton, BorderFactory, JProgressBar, SwingWorker,
                         SwingUtilities, JOptionPane)
from javax.swing.border import EmptyBorder

from java.awt import BorderLayout, FlowLayout, GridLayout, CardLayout


def _discover_workflows():
    """
    Scan the workflows folder and import all BaseWorkflow subclasses.
    Returns a tuple of (dict of {display_name: workflow_instance}, list of error messages)
    """
    workflows = {}
    errors = []
    try:
        plugins_dir = IJ.getDirectory("plugins")
        toolkit_dir = os.path.join(plugins_dir, "Cell_Quantification_Toolkit")
        workflows_dir = os.path.join(toolkit_dir, "workflows")
        
        if not os.path.isdir(workflows_dir):
            IJ.log("Workflows directory not found: " + workflows_dir)
            return workflows, errors
        
        # Add workflows dir to path if not present
        if workflows_dir not in sys.path:
            sys.path.insert(0, workflows_dir)
        
        # Load BaseWorkflow class
        base_workflow_path = os.path.join(workflows_dir, 'base_workflow.py')
        if not os.path.exists(base_workflow_path):
            IJ.log("base_workflow.py not found")
            return workflows, errors
        
        base_namespace = {}
        execfile(base_workflow_path, base_namespace)
        BaseWorkflow = base_namespace['BaseWorkflow']
        
        # Find workflow files
        workflow_files = [f for f in os.listdir(workflows_dir) 
                         if f.endswith('.py') and not f.startswith('_') and f != 'base_workflow.py']
        
        # Load each workflow
        for filename in workflow_files:
            try:
                module_path = os.path.join(workflows_dir, filename)
                
                # Execute workflow file with BaseWorkflow in namespace
                namespace = {'BaseWorkflow': BaseWorkflow}
                with open(module_path, 'r') as f:
                    source_code = f.read()
                
                compiled = compile(source_code, module_path, 'exec')
                exec(compiled, namespace)
                
                # Find and instantiate workflow classes
                for name, obj in namespace.items():
                    if isinstance(obj, type) and issubclass(obj, BaseWorkflow) and obj is not BaseWorkflow:
                        instance = obj()
                        workflows[instance.display_name] = instance
            except Exception as e:
                errors.append("{}: {}".format(filename, str(e)))
                IJ.log("Error loading workflow '{}': {}".format(filename, e))
                IJ.log(traceback.format_exc())
    except Exception as e:
        IJ.log("Error discovering workflows: " + str(e))
        IJ.log(traceback.format_exc())
    
    return workflows, errors


def _sanitize_filename(name):
    """
    Sanitize a string for use in filenames by replacing invalid characters.
    """
    return re.sub(r'[^\w\-]', '_', name)


def _ensure_closed_area_roi(roi):
    """
    Ensures an ROI is a closed area selection suitable for cropping.
    Converts open line ROIs (FREELINE, POLYLINE) to closed polygons.
    Returns the original ROI if it's already an area type, or a new closed ROI.
    """
    roi_type = roi.getType()
    
    # Check if ROI is an open line type that needs closing
    # Roi.FREELINE = 7, Roi.POLYLINE = 6
    if roi_type == Roi.FREELINE or roi_type == Roi.POLYLINE:
        # Get the polygon coordinates from the line ROI
        polygon = roi.getPolygon()
        if polygon and polygon.npoints > 2:
            # Create a new closed polygon ROI from the same points
            closed_roi = PolygonRoi(polygon.xpoints, polygon.ypoints, polygon.npoints, Roi.POLYGON)
            # Preserve the original ROI's name and properties
            closed_roi.setName(roi.getName())
            comment = roi.getProperty("comment")
            if comment:
                closed_roi.setProperty("comment", comment)
            IJ.log("INFO: Converted open line ROI '{}' to closed polygon for cropping.".format(roi.getName()))
            return closed_roi
        else:
            IJ.log("WARNING: ROI '{}' has insufficient points to form a closed area.".format(roi.getName()))
            return None
    
    # Check for point ROIs which cannot be cropped
    # Roi.POINT = 10
    if roi_type == Roi.POINT:
        IJ.log("WARNING: ROI '{}' is a point selection and cannot be used for cropping. Skipping.".format(roi.getName()))
        return None
    
    # Check for simple line ROIs
    # Roi.LINE = 5
    if roi_type == Roi.LINE:
        IJ.log("WARNING: ROI '{}' is a straight line and cannot be used for cropping. Skipping.".format(roi.getName()))
        return None
    
    # ROI is already an area type (RECTANGLE, OVAL, POLYGON, FREEROI, etc.)
    return roi



class QuantificationDialog(JDialog):
    """
    Modal dialog to configure settings for a batch quantification process.
    Dynamically loads workflows from the workflows folder.
    """
    def __init__(self, parent_frame, selected_images):
        super(QuantificationDialog, self).__init__(parent_frame, "Quantification Settings", True)

        self.selected_images = selected_images
        self.settings = None
        self.models_dict = self._get_models()
        
        # Discover available workflows
        self.workflows_dict, self.workflow_errors = _discover_workflows()
        if self.workflow_errors:
            error_msg = "Some workflows failed to load:\n" + "\n".join(self.workflow_errors)
            JOptionPane.showMessageDialog(parent_frame, error_msg, "Workflow Loading Errors", JOptionPane.WARNING_MESSAGE)
        if not self.workflows_dict:
            IJ.log("Warning: No workflows found in workflows folder.")
        
        # Track current workflow's settings panel
        self.current_workflow_panel = None

        # Main panel
        main_panel = JPanel(BorderLayout(10, 10))
        main_panel.setBorder(EmptyBorder(15, 15, 15, 15))
        self.add(main_panel)

        # Info label
        info_text = "Ready to process {} selected images.".format(len(self.selected_images))
        info_label = JLabel(info_text)
        main_panel.add(info_label, BorderLayout.NORTH)

        # Settings panel container
        settings_container = JPanel(BorderLayout(10, 10))
        settings_container.setBorder(BorderFactory.createTitledBorder("Processing Options"))

        # Top: workflow selection + common options
        top_panel = JPanel(GridLayout(0, 2, 10, 10))
        
        # Workflow selection
        workflow_names = list(self.workflows_dict.keys())
        top_panel.add(JLabel("Choose Your Quantification Workflow:"))
        self.workflow_combo = JComboBox(workflow_names)
        self.workflow_combo.addActionListener(self._on_workflow_change)
        top_panel.add(self.workflow_combo)

        # Common display option
        top_panel.add(JLabel("Display Options:"))
        self.show_images_checkbox = JCheckBox("Show images during processing", False)
        top_panel.add(self.show_images_checkbox)
        
        # Force recalculate option (deletes cached probability maps)
        self.force_recalculate_checkbox = JCheckBox("Force recalculate probabilities", False)
        top_panel.add(self.force_recalculate_checkbox)
        
        settings_container.add(top_panel, BorderLayout.NORTH)

        # Workflow-specific settings panel (will be swapped dynamically)
        self.workflow_settings_container = JPanel(BorderLayout())
        self.workflow_settings_container.setBorder(BorderFactory.createTitledBorder("Workflow Settings"))
        settings_container.add(self.workflow_settings_container, BorderLayout.CENTER)

        main_panel.add(settings_container, BorderLayout.CENTER)

        # Bottom button panel
        button_panel = JPanel(FlowLayout(FlowLayout.RIGHT))
        run_button = JButton("Run", actionPerformed=self._run_action)
        cancel_button = JButton("Cancel", actionPerformed=self._cancel_action)
        button_panel.add(run_button)
        button_panel.add(cancel_button)
        main_panel.add(button_panel, BorderLayout.SOUTH)

        # Initialize with first workflow's settings panel
        self._on_workflow_change(None)
        self.pack()

    def _on_workflow_change(self, event):
        """Swap the settings panel based on selected workflow."""
        selected_name = self.workflow_combo.getSelectedItem()
        if not selected_name:
            return
            
        workflow = self.workflows_dict.get(selected_name)
        if not workflow:
            return
        
        # Clear existing panel
        self.workflow_settings_container.removeAll()
        
        # Get workflow-specific panel
        self.current_workflow_panel = workflow.get_settings_panel(self.models_dict)
        if self.current_workflow_panel:
            self.workflow_settings_container.add(self.current_workflow_panel, BorderLayout.CENTER)
        else:
            # No custom settings for this workflow
            self.workflow_settings_container.add(JLabel("No additional settings for this workflow."), BorderLayout.CENTER)
        
        self.workflow_settings_container.revalidate()
        self.workflow_settings_container.repaint()
        self.pack()

    def _run_action(self, event):
        """Gathers settings into dictionary and closes dialog."""
        selected_name = self.workflow_combo.getSelectedItem()
        workflow = self.workflows_dict.get(selected_name)
        
        if workflow:
            # Gather workflow-specific settings
            workflow_settings = workflow.gather_settings(self.current_workflow_panel)
            
            self.settings = {
                'workflow': workflow,  # Store workflow instance, not name
                'workflow_name': selected_name,
                'images': self.selected_images,
                'show_images': self.show_images_checkbox.isSelected(),
                'force_recalculate': self.force_recalculate_checkbox.isSelected()
            }
            # Merge workflow-specific settings
            self.settings.update(workflow_settings)
            
        self.dispose()

    def _cancel_action(self,event):
        """ Leaves settings=None and closes dialog"""
        self.settings = None
        self.dispose()

    def show_dialog(self):
        """ Public method called by the GUI """
        self.setLocationRelativeTo(self.getParent())
        self.setVisible(True)
        return self.settings
    
    def _get_models(self):
        """
        Finds models in the Cell_Quantification_Toolkit folder. 
        Returns a dictionary of key:value pairs as display_name:full_path
        """
        models = {}
        
        try:
            plugins_dir = IJ.getDirectory("plugins")
            plugin_folder_name = "Cell_Quantification_Toolkit"
            toolkit_dir = os.path.join(plugins_dir, plugin_folder_name)
            models_dir = os.path.join(toolkit_dir, "models")
            if os.path.isdir(models_dir):
                for f in os.listdir(models_dir):
                    if f.lower().endswith('.ilp'):
                        display_name = os.path.splitext(f)[0]
                        full_path = os.path.join(models_dir, f)
                        models[display_name] = full_path
            else:
                IJ.log("Model directory not found. Please create it at: " + models_dir)

        except Exception as e:
            IJ.log("Error discovering models: " + str(e))
            IJ.log(traceback.format_exc())

        return models


class ProgressDialog(JDialog):
    """ A simple, modal dialog to display a progress bar. """
    def __init__(self, parent_frame, title, max_value):
        super(ProgressDialog, self).__init__(parent_frame, title, True)
        self.setDefaultCloseOperation(JDialog.DO_NOTHING_ON_CLOSE)
        self.progress_bar = JProgressBar(0, max_value)
        self.progress_bar.setStringPainted(True)
        self.add(self.progress_bar)
        self.pack()
        self.setSize(400, 80)
        self.setLocationRelativeTo(parent_frame)

class QuantificationWorker(SwingWorker):
    """ Processor Classs facilitating image quantification on a background thread given settings from the dialog """
    def __init__(self, parent_gui, project, settings, progress_dialog):
        super(QuantificationWorker, self).__init__()
        self.parent_gui = parent_gui
        self.project = project
        self.settings = settings
        self.progress_dialog = progress_dialog
        self.all_results = []

    def doInBackground(self):
        """
        Processes each ROI individually after loading all ROIs from the zip file.
        Uses an index to create unique temporary filenames, preventing overwrites.
        """
        # Generate unique run ID for this processing session (includes microseconds to prevent collisions)
        self.run_id = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        
        # Create run folder structure: Runs/{run_id}/Cell_Selections/
        self.run_folder = os.path.join(self.project.paths['runs'], self.run_id)
        self.cell_selections_folder = os.path.join(self.run_folder, 'Cell_Selections')
        os.makedirs(self.cell_selections_folder)  # Creates both folders
        
        # --- Helper class for updating the progress bar on the GUI thread ---
        class UpdateProgressBarTask(Runnable):
            def __init__(self, dialog, value):
                self.dialog = dialog
                self.value = value
            def run(self):
                self.dialog.progress_bar.setValue(self.value)

        images_to_process = self.settings['images']

        # Set status to "Processing" at the beginning, storing previous status for rollback
        previous_statuses = {}
        for image_obj in images_to_process:
            previous_statuses[image_obj.filename] = image_obj.status
            image_obj.status = "Processing"
        
        # Immediately save and refresh the UI to show the "Processing" status
        self.project.sync_project_db()
        SwingUtilities.invokeLater(self.parent_gui.update_ui_for_project)
        
        # Calculate total ROIs from cached data (avoids reopening zip files)
        total_rois_to_process = sum(len(img.rois) for img in images_to_process if img.has_roi())

        if total_rois_to_process == 0: 
            return "No ROIs to process."
        roi_counter = 0

        for image_obj in images_to_process:
            try:    
                all_image_outlines = []
                if self.isCancelled():
                    # Restore previous statuses on cancellation
                    for img in images_to_process:
                        if img.status == "Processing":
                            img.status = previous_statuses.get(img.filename, "In Progress")
                    break
                
                if not image_obj.has_roi(): 
                    continue

                imp_original = IJ.openImage(image_obj.full_path)
                if not imp_original:
                    IJ.log("ERROR: Failed to open original image: " + image_obj.full_path)
                    continue

                if self.settings.get('show_images', False):
                    imp_original.show()

                # 1. Load ALL ROIs from the .zip file ONCE per image.
                rm = RoiManager(True)
                rm.open(image_obj.roi_path)
                all_rois_for_image = rm.getRoisAsArray()
                rm.close()

                # 2. Loop through the loaded ROIs using enumerate to get a unique index 'i'
                for i, roi in enumerate(all_rois_for_image):
                    if self.isCancelled():
                        # Restore previous statuses on cancellation
                        for img in images_to_process:
                            if img.status == "Processing":
                                img.status = previous_statuses.get(img.filename, "In Progress")
                        break
                    
                    temp_cropped_path = None
                    try:
                        # Read the bregma value directly from the ROI object's property
                        bregma_val_str = roi.getProperty("comment")
                        try:
                            bregma_val = float(bregma_val_str) if bregma_val_str else 0.0
                        except (ValueError, TypeError):
                            bregma_val = 0.0

                        # Ensure ROI is a valid closed area for cropping
                        crop_roi = _ensure_closed_area_roi(roi)
                        if crop_roi is None:
                            # ROI type cannot be converted to area - skip this ROI
                            IJ.log("Skipping ROI #{} ('{}') - not a valid area selection.".format(i, roi.getName()))
                            continue
                        
                        # Get bounding box coordinates for offsetting results later
                        roi_x = crop_roi.getBounds().x
                        roi_y = crop_roi.getBounds().y

                        # Create a duplicate for cropping to preserve the original image
                        imp_cropped = imp_original.duplicate()
                        imp_cropped.setRoi(crop_roi)
                        IJ.run(imp_cropped, "Crop", "")
                        
                        # 3. Add the unique index 'i' to the base_name to prevent file overwriting
                        # Sanitize ROI name to remove characters invalid for filenames
                        safe_roi_name = _sanitize_filename(roi.getName())
                        base_name = "{}_{}_{}".format(os.path.splitext(image_obj.filename)[0], safe_roi_name, i)
                        
                        temp_cropped_path = os.path.join(self.project.paths['temp'], base_name + "_cropped.tif")
                        prob_map_path = os.path.join(self.project.paths['probabilities'], base_name)
                        IJ.saveAs(imp_cropped, "Tiff", temp_cropped_path)

                        if self.settings.get('show_images', False):
                            imp_cropped.show()

                        # Delegate to workflow plugin for processing
                        workflow = self.settings.get('workflow')
                        if workflow:
                            # Run workflow-specific processing
                            result_imp = workflow.process_roi(imp_cropped, temp_cropped_path, prob_map_path, self.settings)

                            if not self.settings.get('show_images', False):
                                if imp_cropped and imp_cropped.isVisible():
                                    imp_cropped.close()

                            # Analyze the results using workflow plugin
                            # Pass crop_roi (the closed area version) to ensure mask creation works correctly
                            analysis = workflow.analyze_results(result_imp, crop_roi, roi_x, roi_y, self.settings)

                            if not self.settings.get('show_images', False):
                                if result_imp:
                                    result_imp.changes = False
                                    result_imp.close()

                            if analysis.get('outlines'):
                                # Translate outlines from cropped to absolute coordinates
                                for outline in analysis['outlines']:
                                    bounds = outline.getBounds()
                                    outline.setLocation(bounds.x + roi_x, bounds.y + roi_y)
                                all_image_outlines.extend(analysis['outlines'])

                            # Collect the base result for this single ROI piece
                            single_roi_result = {
                                'filename': image_obj.filename,
                                'roi_name': roi.getName(),
                                'roi_area': roi.getStatistics().area,
                                'bregma_value': bregma_val,
                                'processing_run_id': self.run_id,
                            }
                            # Add workflow-specific columns
                            for col in workflow.get_result_columns():
                                if col in analysis:
                                    single_roi_result[col] = analysis[col]
                            
                            self.all_results.append(single_roi_result)


                    except Exception as e:
                        IJ.log("ERROR processing ROI #{} ('{}') in '{}': {}".format(i, roi.getName(), image_obj.filename, e))
                        IJ.log(traceback.format_exc())
                        continue 

                    finally:
                        # Clean up temporary cropped file
                        if temp_cropped_path and os.path.exists(temp_cropped_path):
                            try:
                                os.remove(temp_cropped_path)
                            except Exception as ex:
                                IJ.log("Warning: Could not delete temporary file " + temp_cropped_path)

                        if not self.settings.get('show_images', False):
                            self._cleanup_stray_windows()
                        
                        # Update progress
                        roi_counter += 1
                        progress = int(100.0 * roi_counter / total_rois_to_process)
                        update_task = UpdateProgressBarTask(self.progress_dialog, progress)
                        SwingUtilities.invokeLater(update_task)
                
                # After processing all ROIs for an image, save the collected cell outlines to run folder
                if all_image_outlines:
                    outline_rm = RoiManager(True)
                    for outline_roi in all_image_outlines:
                        outline_rm.addRoi(outline_roi)
                    # Save to run-based folder: Runs/{run_id}/Cell_Selections/{image}_Outlines.zip
                    base_name, _ = os.path.splitext(image_obj.filename)
                    outline_path = os.path.join(self.cell_selections_folder, base_name + "_Outlines.zip")
                    outline_rm.runCommand("Save", outline_path)
                    outline_rm.close()

                # Close the original image window if it's not meant to be shown
                if not self.settings.get('show_images', False) and imp_original and imp_original.isVisible():
                    imp_original.close()

                image_obj.status = "Completed" # Mark for final update

            except Exception as e:
                IJ.log("ERROR processing '{}': {}".format(image_obj.filename, e))
                image_obj.status = "Failed" # Mark as failed
                continue # Move to the next image

            finally:
                IJ.run("Collect Garbage", "")
                System.gc()

                if not self.settings.get('show_images', False):
                    self._cleanup_stray_windows()

        return "Quantification completed successfully for {} ROIs.".format(roi_counter)
                
    


    def _cleanup_stray_windows(self):
        """Aggressively find and close any stray temporary image windows."""
        # Get a list of all currently open image windows
        image_ids = WindowManager.getIDList()
        if not image_ids:
            return
        
        # Keywords found in the titles of temporary windows
        temp_keywords = ["_cropped", "_probabilities", "_objects", "mask of"]

        # Iterate over a copy of the list, as closing images can modify it
        for img_id in list(image_ids):
            img = WindowManager.getImage(img_id)
            if not img:
                continue
            
            title = img.getTitle().lower()
            
            # If the window title contains any of our keywords, close it
            if any(keyword in title for keyword in temp_keywords):
                img.changes = False  # Prevent "Save changes?" dialog
                img.close()
    
    def _build_metadata(self):
        """
        Build a metadata dictionary with relevant processing settings.
        Only includes settings that affect processing output.
        """
        # Filter settings to only relevant, JSON-serializable values
        # Exclude: internal keys (_prefix), workflow object, images list, display-only options
        exclude_keys = {'workflow', 'workflow_name', 'images', 'show_images', 'force_recalculate'}
        
        serializable_settings = {}
        for key, value in self.settings.items():
            # Skip internal keys (start with _) and excluded keys
            if key.startswith('_') or key in exclude_keys:
                continue
            # Only include JSON-serializable types
            # Use basestring to cover both str and unicode in Python 2/Jython
            if isinstance(value, (basestring, int, float, bool, type(None))):
                serializable_settings[key] = value
            elif isinstance(value, (list, dict)):
                try:
                    json.dumps(value)
                    serializable_settings[key] = value
                except (TypeError, ValueError):
                    pass  # Skip non-serializable values
        
        return {
            'processed_date': datetime.datetime.now().isoformat(),
            'workflow_name': self.settings.get('workflow_name', 'Unknown'),
            'workflow_settings': serializable_settings,
            'images_processed': [img.filename for img in self.settings.get('images', [])],
            'total_results': len(self.all_results)
        }
    
    def _save_processing_metadata(self):
        """
        Save processing metadata to the run folder as run_metadata.json.
        Each run is self-contained with its own metadata file.
        """
        try:
            date_prefix = datetime.datetime.now().strftime('%Y%m%d')
            metadata_path = os.path.join(self.run_folder, '{}_run_metadata.json'.format(date_prefix))
            
            with open(metadata_path, 'w') as f:
                json.dump(self._build_metadata(), f, indent=2)
            
        except Exception as e:
            IJ.log("Warning: Could not save processing metadata: " + str(e))
    
    def done(self):
        """ Runs on GUI thread after background work is finished. """
        try:
            if self.all_results:
                # Get workflow to retrieve custom column names
                workflow = self.settings.get('workflow')
                custom_columns = workflow.get_result_columns() if workflow else []
                
                aggregated_results = {}
                bregma_data = {}

                for result in self.all_results:
                    key = (result['filename'], result['roi_name'])
                    if key not in aggregated_results:
                        aggregated_results[key] = result.copy()
                        bregma_data[key] = {'sum': result['bregma_value'], 'count': 1}
                    else:
                        # Sum the base quantitative value
                        aggregated_results[key]['roi_area'] += result['roi_area']
                        # Sum workflow-specific numeric columns
                        for col in custom_columns:
                            if col in result and col in aggregated_results[key]:
                                try:
                                    aggregated_results[key][col] += result[col]
                                except TypeError:
                                    pass  # Non-numeric column, skip aggregation
                        # Add to sum and increment count for averaging bregma
                        bregma_data[key]['sum'] += result['bregma_value']
                        bregma_data[key]['count'] += 1
                
                # Calculate the average Bregma for each group
                for key, data in aggregated_results.items():
                    bregma_sum = bregma_data[key]['sum']
                    bregma_count = bregma_data[key]['count']
                    average_bregma = (bregma_sum / bregma_count) if bregma_count > 0 else 0
                    aggregated_results[key]['bregma_value'] = "{:.3f}".format(average_bregma)

                final_results_list = list(aggregated_results.values())
                
                # Build headers: base columns + workflow-specific columns (no run_id needed, it's in folder name)
                date_prefix = datetime.datetime.now().strftime('%Y%m%d')
                results_path = os.path.join(self.run_folder, '{}_results.csv'.format(date_prefix))
                base_headers = ['filename', 'roi_name', 'roi_area', 'bregma_value']
                headers = base_headers + custom_columns
                
                with open(results_path, 'w') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=headers, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(final_results_list)
                
                # Save processing metadata to JSON log
                self._save_processing_metadata()
            
            # Show final status message
            final_message = self.get()
            JOptionPane.showMessageDialog(self.progress_dialog, final_message, "Status", JOptionPane.INFORMATION_MESSAGE)


        except Exception as e:
            # This will catch errors from the background thread
            IJ.log(traceback.format_exc())
            JOptionPane.showMessageDialog(self.progress_dialog, "An error occurred during processing:\n" + str(e), "Error", JOptionPane.ERROR_MESSAGE)
            for image in self.settings['images']:
                if image.status == "Processing":
                    image.status = "Failed"
        finally:
            self.progress_dialog.dispose()

            image_ids = WindowManager.getIDList()
            if image_ids:
                # Iterate over a copy of the list, as closing images modifies the original list.
                for img_id in list(image_ids):
                    img = WindowManager.getImage(img_id)
                    if img:
                        img.changes = False
                        img.close()

            # Save the final "Completed" or "Failed" statuses and refresh the UI
            self.project.sync_project_db()
            self.parent_gui.update_ui_for_project()
