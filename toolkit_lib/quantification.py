import os
import csv
import traceback

from ij import IJ, WindowManager
from ij.measure import ResultsTable, Measurements
from ij.plugin import ImageCalculator
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.io import File
from java.net import URLDecoder

from java.lang import Runnable, System

from javax.swing import (JDialog, JPanel, JLabel, JComboBox, JCheckBox,
                         JButton, BorderFactory, JProgressBar, SwingWorker,
                         SwingUtilities, JOptionPane)
from javax.swing.border import EmptyBorder

from java.awt import BorderLayout, FlowLayout, GridLayout

class QuantificationDialog(JDialog):
    """
    modal dialog to configure setting for a batch quantification process.
    Returns selected settings to be passed to the worker class.
    """
    def __init__(self, parent_frame, selected_images):
        super(QuantificationDialog, self).__init__(parent_frame, "Quantification Setting", True)

        self.selected_images = selected_images
        self.settings = None
        self.available_models = self._get_models()

        # Main panel
        main_panel = JPanel(BorderLayout(10,10))
        main_panel.setBorder(EmptyBorder(15,15,15,15))
        self.add(main_panel)

        # Info label
        info_text = "Ready to process {} selected images.".format(len(self.selected_images))
        info_label = JLabel(info_text)
        main_panel.add(info_label, BorderLayout.NORTH)

        # Settings panel
        settings_panel = JPanel(GridLayout(0,2,10,10))
        settings_panel.setBorder(BorderFactory.createTitledBorder("Processing Options"))

        # workflow selection
        workflows = ["cFosDAB+ Detection (Generic Model)", "cFosDAB+ Detection (region specific model)"]
        settings_panel.add(JLabel("Choose Your Quantification Type: "))
        self.workflow_combo = JComboBox(workflows)
        settings_panel.add(self.workflow_combo)

        # Verbose images or no
        settings_panel.add(JLabel("Display Options: "))
        self.show_images_checkbox = JCheckBox("Show images during processing", False)
        settings_panel.add(self.show_images_checkbox)

        main_panel.add(settings_panel, BorderLayout.CENTER)

        # Bottom button panel
        button_panel = JPanel(FlowLayout(FlowLayout.RIGHT))
        run_button = JButton("Run", actionPerformed=self._run_action)
        cancel_button = JButton("Cancel", actionPerformed=self._cancel_action)
        button_panel.add(run_button)
        button_panel.add(cancel_button)
        main_panel.add(button_panel, BorderLayout.SOUTH)

        self.pack()

    def _run_action(self, event):
        """ Gathers settings into dictionary and closes dialog """
        selected_workflow = self.workflow_combo.getSelectedItem()

        if selected_workflow == "cFosDAB+ Detection (Generic Model)": 
            self.settings = {
                'images': self.selected_images,
                'pixel_classifier': self.available_models['PIXEL_cFosDAB_TiffIO_Generic'],
                'object_classifier': self.available_models['OBJECT_cFosDAB_TiffIO_Generic'],  
                'show_images': self.show_images_checkbox.isSelected()
                }
        elif selected_workflow == "cFosDAB+ Detection (region specific model)":
            IJ.error("NOT IMPLEMENTED", "Havnent made this yet. use the generic model.")
        
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
        Finds models in a dedicated folder inside Fiji's 'lib' directory.
        This works by locating the core ImageJ .jar file
        to determine the Fiji root directory, regardless of how
        the application was launched.
        """
        from java.net import URLDecoder
        from java.lang import System

        MODELS_FOLDER_NAME = "cell-quantifier-toolkit-models"
        models = {}
        
        try:
            class_loader = IJ.getClassLoader()
            if class_loader is None:
                raise IOError("Could not get ImageJ ClassLoader.")

            resource_url = class_loader.getResource("IJ_Props.txt")
            if resource_url is None:
                raise IOError("Could not find core resource 'IJ_Props.txt'. Is Fiji installed correctly?")

            url_str = URLDecoder.decode(resource_url.toString(), "UTF-8")
            path_part = url_str.split("!")[0].replace("jar:file:", "")

            if System.getProperty("os.name").lower().startswith("windows") and path_part.startswith("/"):
                path_part = path_part[1:]

            jar_file = File(path_part)
            fiji_root_file = jar_file.getParentFile().getParentFile()
            fiji_root = fiji_root_file.getAbsolutePath()
           
            models_dir = os.path.join(fiji_root, "lib", MODELS_FOLDER_NAME)

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
    """ A simple, non-modal dialog to display a progress bar. """
    def __init__(self, parent_frame, title, max_value):
        super(ProgressDialog, self).__init__(parent_frame, title, False)
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
        # --- Helper class for updating the progress bar on the GUI thread ---
        class UpdateProgressBarTask(Runnable):
            def __init__(self, dialog, value):
                self.dialog = dialog
                self.value = value
            def run(self):
                self.dialog.progress_bar.setValue(self.value)

        # --- Main processing logic ---
        images_to_process = self.settings['images']

        # Set status to "Processing" at the beginning
        for image_obj in images_to_process:
            image_obj.status = "Processing"
        
        # Immediately save and refresh the UI to show the "Processing" status
        self.project._sync_image_status_db()
        SwingUtilities.invokeLater(self.parent_gui.update_ui_for_project)
        
        # Calculate total number of individual ROIs for the progress bar
        total_rois_to_process = 0
        for img in images_to_process:
            if img.has_roi():
                rm_temp = RoiManager(True)
                rm_temp.open(img.roi_path)
                total_rois_to_process += rm_temp.getCount()
                rm_temp.close()

        if total_rois_to_process == 0: 
            return "No ROIs to process."
        roi_counter = 0

        for image_obj in images_to_process:
            try:    
                all_image_outlines = []
                if self.isCancelled(): 
                    break
                
                if not image_obj.has_roi(): 
                    continue

                imp_original = IJ.openImage(image_obj.full_path)
                if not imp_original:
                    IJ.log("ERROR: Failed to open original image: " + image_obj.full_path)
                    continue
                
                # 1. Load ALL ROIs from the .zip file ONCE per image.
                rm = RoiManager(True)
                rm.open(image_obj.roi_path)
                all_rois_for_image = rm.getRoisAsArray()
                rm.close()

                # 2. Loop through the loaded ROIs using enumerate to get a unique index 'i'
                for i, roi in enumerate(all_rois_for_image):
                    if self.isCancelled(): 
                        break
                    
                    temp_cropped_path = None
                    try:
                        # Read the bregma value directly from the ROI object's property
                        bregma_val_str = roi.getProperty("comment")
                        try:
                            bregma_val = float(bregma_val_str) if bregma_val_str else 0.0
                        except (ValueError, TypeError):
                            bregma_val = 0.0

                        # Get bounding box coordinates for offsetting results later
                        roi_x = roi.getBounds().x
                        roi_y = roi.getBounds().y

                        # Create a duplicate for cropping to preserve the original image
                        imp_cropped = imp_original.duplicate()
                        imp_cropped.setRoi(roi)
                        IJ.run(imp_cropped, "Crop", "")
                        
                        # 3. Add the unique index 'i' to the base_name to prevent file overwriting
                        base_name = "{}_{}_{}".format(os.path.splitext(image_obj.filename)[0], roi.getName(), i)
                        
                        temp_cropped_path = os.path.join(self.project.paths['temp'], base_name + "_cropped.tif")
                        prob_map_path = os.path.join(self.project.paths['probabilities'], base_name)
                        IJ.saveAs(imp_cropped, "Tiff", temp_cropped_path)

                        imp_cropped.show()

                        # Run external processing (e.g., ilastik)
                        result_imp = self._run_ilastik_classification(roi, temp_cropped_path, image_obj.filename, prob_map_path)

                        if not self.settings.get('show_images', True):
                            if imp_cropped and imp_cropped.isVisible():
                                imp_cropped.close()

                        # Analyze the results in Fiji
                        analysis = self._analyze_results(result_imp, roi, roi_x, roi_y)

                        if not self.settings.get('show_images', True):
                            if result_imp:
                                result_imp.changes = False
                                result_imp.close()

                        if analysis['outlines']:
                            all_image_outlines.extend(analysis['outlines'])

                        # Collect the result for this single ROI piece
                        single_roi_result = {
                            'filename': image_obj.filename,
                            'roi_name': roi.getName(),
                            'roi_area': roi.getStatistics().area,
                            'bregma_value': bregma_val,
                            'cell_count': analysis['count'],
                            'total_cell_area': analysis['total area']
                        }
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

                        if not self.settings.get('show_images', True):
                            self._cleanup_stray_windows()
                        
                        # Update progress
                        roi_counter += 1
                        progress = int(100.0 * roi_counter / total_rois_to_process)
                        update_task = UpdateProgressBarTask(self.progress_dialog, progress)
                        SwingUtilities.invokeLater(update_task)
                
                # After processing all ROIs for an image, save the collected cell outlines
                if all_image_outlines:
                    outline_rm = RoiManager(True)
                    for outline_roi in all_image_outlines:
                        outline_rm.addRoi(outline_roi)
                    outline_rm.runCommand("Save", image_obj.outline_path)
                    outline_rm.close()
                    IJ.log("Saved {} cell outlines for {}.".format(len(all_image_outlines), image_obj.filename))

                # Close the original image window if it's not meant to be shown
                if not self.settings.get('show_images', True) and imp_original and imp_original.isVisible():
                    imp_original.close()

                image_obj.status = "Completed" # Mark for final update

            except Exception as e:
                IJ.log("ERROR processing '{}': {}".format(image_obj.filename, e))
                image_obj.status = "Failed" # Mark as failed
                continue # Move to the next image

            finally:
                IJ.run("Collect Garbage", "")
                System.gc()

                self._cleanup_stray_windows()  

        return "Quantification completed successfully for {} ROIs.".format(roi_counter)
                
    
    def _run_ilastik_classification(self, roi, temp_cropped_path, img_name, prob_map_path):
        """
        Runs the full Ilastik workflow, correctly resuming from intermediate steps
        and handling the 'show images' setting by keeping required images open but hidden.
        """
        pixel_imp = None  # Define here for access in finally block
        try:
            pixel_classifier = self.settings['pixel_classifier']
            object_classifier = self.settings['object_classifier']
    
            pixel_prob_path = prob_map_path + "_probabilities.tif"
            object_prob_path = prob_map_path + "_objects.tif"

            # Case 1: The final object classification file already exists.
            if os.path.exists(object_prob_path):
                IJ.log("Found existing object file, skipping Ilastik processing for: " + os.path.basename(object_prob_path))
                result_imp = IJ.openImage(object_prob_path)
                if self.settings.get('show_images', True):
                    result_imp.show()
                return result_imp

            # Case 2: The intermediate pixel probability file exists, but the final one does not.
            elif os.path.exists(pixel_prob_path):
                IJ.log("Found existing probability map, running Object Classification only for: " + os.path.basename(pixel_prob_path))
                # Open the existing probability map, as the next step depends on it.
                pixel_imp = IJ.openImage(pixel_prob_path)
                if not self.settings.get('show_images', True):
                    pixel_imp.hide() # Keep it open but invisible

                # Run only the Object Classification step
                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] rawinputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities ");'.format(object_classifier, temp_cropped_path, pixel_prob_path)
                IJ.runMacro(object_macro_cmd)
                object_imp = IJ.getImage()
                if not object_imp or (pixel_imp and object_imp.getID() == pixel_imp.getID()):
                    raise Exception("Object classification did not produce a new result image.")
                
                IJ.saveAs(object_imp, "Tiff", object_prob_path)
                if not self.settings.get('show_images', True):
                    object_imp.hide()

                IJ.run("Collect Garbage", "")
                System.gc()

                return object_imp

            # Case 3: Neither file exists. Run the full workflow.
            else:
                # Run Pixel Classification
                pixel_macro_cmd = 'run("Run Pixel Classification Prediction", "projectfilename=[{}] inputimage=[{}] pixelclassificationtype=Probabilities");'.format(pixel_classifier, temp_cropped_path)
                IJ.runMacro(pixel_macro_cmd)
                pixel_imp = IJ.getImage()
                if not pixel_imp:
                    raise Exception("No probability map was generated by the Ilastik pixel classifier.")

                IJ.saveAs(pixel_imp, "Tiff", pixel_prob_path)

                # Keep the image open but hide it for the next step.
                if not self.settings.get('show_images', True):
                    pixel_imp.hide()

                IJ.run("Collect Garbage", "")
                System.gc()

                # Run Object Classification
                object_macro_cmd = 'run("Run Object Classification Prediction", "projectfilename=[{}] rawinputimage=[{}] inputproborsegimage=[{}] secondinputtype=Probabilities ");'.format(object_classifier, temp_cropped_path, pixel_prob_path)
                IJ.runMacro(object_macro_cmd)
                object_imp = IJ.getImage()
                if not object_imp or (pixel_imp and object_imp.getID() == pixel_imp.getID()):
                    raise Exception("Object classification did not produce a new result image.")
                
                IJ.saveAs(object_imp, "Tiff", object_prob_path)
                if self.settings.get('show_images', True):
                    object_imp.show()

                IJ.run("Collect Garbage", "")
                System.gc()
                return object_imp

        except Exception as e:
            IJ.log("Ilastik processing failed: " + str(e))
            raise e
        finally:
            # Final cleanup of any lingering intermediate windows
            if pixel_imp:
                pixel_imp.changes = False
                pixel_imp.close() 
                

    def _analyze_results(self, result_imp, roi, offset_x, offset_y):
        """
        Final processing and analysis of ilastik output in Fiji.
        This version includes a thresholding step to create the required
        binary image for the Watershed command, resolving the error.
        """
        # --- START: MANUAL MASKING AND BINARIZATION ---

        # 1. Create a perfect black-and-white mask from the user's ROI.
        width = result_imp.getWidth()
        height = result_imp.getHeight()
        mask_title = "mask_" + str(System.nanoTime())
        mask_imp = IJ.createImage(mask_title, "8-bit black", width, height, 1)
        
        roi_clone_for_masking = roi.clone()
        roi_clone_for_masking.setLocation(0, 0)
        mask_imp.setRoi(roi_clone_for_masking)
        IJ.run(mask_imp, "Fill", "slice")
        mask_imp.deleteRoi()

        # 2. Use the Image Calculator's "AND" operation to apply the ROI mask
        # to the original Ilastik label image.
        from ij.plugin import ImageCalculator
        ic = ImageCalculator()
        ic.run("AND", result_imp, mask_imp)

        mask_imp.changes = False
        mask_imp.close()

        # The Watershed command requires a binary input. We select all labeled
        # pixels (values 1 and up) and convert them to a single mask.
        IJ.setThreshold(result_imp, 1, 65535) 
        IJ.run(result_imp, "Convert to Mask", "") 

        # 4. Now, run Watershed on the proper binary image.
        IJ.run(result_imp, "Watershed", "")
        
        rm = RoiManager(True)
        rt = ResultsTable()

        # Configure and run the ParticleAnalyzer
        options = ParticleAnalyzer.SHOW_OUTLINES | ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
        measurements = Measurements.AREA
        pa = ParticleAnalyzer(options, measurements, rt, 20, float('inf'), 0.0, 1.0)
        pa.setRoiManager(rm)
        pa.analyze(result_imp)

        # Get stats safely from our local results table.
        count = rt.getCounter()
        total_area = 0
        if count > 0:
            area_col_index = rt.getColumnIndex("Area")
            if area_col_index != -1:
                area_col = rt.getColumn(area_col_index)
                if area_col is not None:
                    total_area = sum(area_col)

        # Get the particle outlines
        particle_outlines_relative = rm.getRoisAsArray()
        rm.reset()
        rm.close()
        
        result_imp.changes = False
        result_imp.close()

        if particle_outlines_relative is None:
            particle_outlines_relative = []

        # Translate outlines to the full image coordinates
        particle_outlines_absolute = []
        for outline in particle_outlines_relative:
            current_bounds = outline.getBounds()
            outline.setLocation(current_bounds.x + offset_x, current_bounds.y + offset_y)
            particle_outlines_absolute.append(outline)

        analysis = {
            'count': count,
            'total area': total_area,
            'outlines': particle_outlines_absolute
        }
        
        return analysis
    
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
    
    def done(self):
        """ Runs on GUI thread after background work is finished. """
        try:
            if self.all_results:
                aggregated_results = {}
                # This dictionary will hold temporary sums for averaging
                bregma_data = {}

                for result in self.all_results:
                    key = (result['filename'], result['roi_name'])
                    if key not in aggregated_results:
                        aggregated_results[key] = result.copy()
                        # Initialize sum and count for averaging Bregma
                        bregma_data[key] = {'sum': result['bregma_value'], 'count': 1}
                    else:
                        # Sum the quantitative values
                        aggregated_results[key]['roi_area'] += result['roi_area']
                        aggregated_results[key]['cell_count'] += result['cell_count']
                        aggregated_results[key]['total_cell_area'] += result['total_cell_area']
                        # Add to sum and increment count for averaging
                        bregma_data[key]['sum'] += result['bregma_value']
                        bregma_data[key]['count'] += 1
                
                # Now, calculate the average Bregma for each group
                for key, data in aggregated_results.items():
                    bregma_sum = bregma_data[key]['sum']
                    bregma_count = bregma_data[key]['count']
                    # Calculate average and format to 3 decimal places, avoid division by zero
                    average_bregma = (bregma_sum / bregma_count) if bregma_count > 0 else 0
                    aggregated_results[key]['bregma_value'] = "{:.3f}".format(average_bregma)

                final_results_list = aggregated_results.values()
                
                # Now write the FINAL, aggregated list to the CSV
                results_db_path = self.project.paths['results_db']
                headers = ['filename', 'roi_name', 'roi_area', 'bregma_value', 'cell_count', 'total_cell_area']
                file_exists = os.path.isfile(results_db_path)
                with open(results_db_path, 'ab') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=headers)
                    if not file_exists or os.path.getsize(results_db_path) == 0:
                        writer.writeheader()
                    writer.writerows(final_results_list)
            
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
