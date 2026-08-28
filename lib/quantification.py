import os
import csv
import json
import datetime
import traceback
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

from .workflow_config import make_run_id, cache_dir, workflow_cache_signature


def _invalidate_cache_if_changed(cdir, signature):
    """Ensure the per-workflow cache dir exists; if a stored signature differs
    from `signature` (classifiers/append_lab changed), drop cached prediction
    tifs so they are regenerated. Best-effort."""
    if not os.path.isdir(cdir):
        os.makedirs(cdir)
    sig_path = os.path.join(cdir, '.signature')
    old = None
    if os.path.exists(sig_path):
        try:
            with open(sig_path) as f:
                old = f.read().strip()
        except IOError:
            old = None
    if old is not None and old != signature:
        for fn in os.listdir(cdir):
            if (fn.endswith('_probabilities.tif') or fn.endswith('_objects.tif')
                    or fn.endswith('_rgblab.tif')):
                try:
                    os.remove(os.path.join(cdir, fn))
                except OSError:
                    pass
    try:
        with open(sig_path, 'w') as f:
            f.write(signature or '')
    except IOError:
        pass


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
    def __init__(self, parent_frame, selected_images, workflow):
        super(QuantificationDialog, self).__init__(parent_frame, "Quantification Settings", True)

        self.selected_images = selected_images
        self.settings = None
        # Workflow is chosen in the main window's Workflow panel and passed in.
        self.workflow = workflow
        self.definition = getattr(workflow, 'definition', None)

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
        
        # Selected workflow (chosen in the main window's Workflow panel)
        top_panel.add(JLabel("Workflow:"))
        wf_name = self.definition.name if self.definition else "(none selected)"
        top_panel.add(JLabel(wf_name))

        # Common display option
        top_panel.add(JLabel("Display Options:"))
        self.show_images_checkbox = JCheckBox("Show images during processing", False)
        top_panel.add(self.show_images_checkbox)
        
        # Force recalculate option (deletes cached probability maps)
        self.force_recalculate_checkbox = JCheckBox("Force recalculate probabilities", False)
        top_panel.add(self.force_recalculate_checkbox)
        
        settings_container.add(top_panel, BorderLayout.NORTH)

        # Read-only summary of the selected workflow definition
        summary = JPanel(BorderLayout())
        summary.setBorder(BorderFactory.createTitledBorder("Workflow Summary"))
        summary.add(JLabel(self._summary_html()), BorderLayout.CENTER)
        settings_container.add(summary, BorderLayout.CENTER)

        main_panel.add(settings_container, BorderLayout.CENTER)

        # Bottom button panel
        button_panel = JPanel(FlowLayout(FlowLayout.RIGHT))
        run_button = JButton("Run", actionPerformed=self._run_action)
        cancel_button = JButton("Cancel", actionPerformed=self._cancel_action)
        button_panel.add(run_button)
        button_panel.add(cancel_button)
        main_panel.add(button_panel, BorderLayout.SOUTH)

        self.pack()

    def _summary_html(self):
        """Read-only HTML summary of the selected definition."""
        d = self.definition
        if not d:
            return "<html>No workflow selected.</html>"
        cell = ", ".join(c.get('display', c.get('key', '')) for c in d.cell_classes()) or "(none)"
        return (
            "<html>"
            "<b>{name}</b><br>"
            "Pixel classifier: {px}<br>"
            "Object classifier: {obj}<br>"
            "Cell classes: {cells}<br>"
            "<i>Post-processing is set later, in the Results Viewer.</i>"
            "</html>"
        ).format(
            name=d.name, px=d.pixel_classifier, obj=d.object_classifier, cells=cell)

    def _run_action(self, event):
        """Gathers settings into dictionary and closes dialog."""
        if not self.workflow or not self.definition:
            self.settings = None
            self.dispose()
            return

        self.settings = {
            'workflow': self.workflow,       # ConfigurableIlastikWorkflow instance
            'workflow_name': self.definition.name,
            'images': self.selected_images,
            'show_images': self.show_images_checkbox.isSelected(),
            'force_recalculate': self.force_recalculate_checkbox.isSelected()
        }
        # Merge classifier paths + post-processing options from the definition
        self.settings.update(self.definition.to_run_settings())

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
        self.processed_any = False

    def doInBackground(self):
        """
        Processes each ROI individually after loading all ROIs from the zip file.
        Uses an index to create unique temporary filenames, preventing overwrites.
        """
        # One output folder PER WORKFLOW (run_id = sanitized workflow name), reused
        # across runs. Exported CSVs are timestamp+settings stamped, so they
        # accumulate rather than collide.
        self._definition = getattr(self.settings.get('workflow'), 'definition', None)
        self.run_id = make_run_id(self._definition)

        # Run folder: Runs/{workflow}/Cell_Selections/  (may already exist).
        self.run_folder = os.path.join(self.project.paths['runs'], self.run_id)
        self.cell_selections_folder = os.path.join(self.run_folder, 'Cell_Selections')
        if not os.path.isdir(self.cell_selections_folder):
            os.makedirs(self.cell_selections_folder)

        # Per-workflow probability/label cache; cleared if the workflow's
        # classifiers or append_lab option changed since last time (so edited
        # models never reuse stale predictions).
        self.cache_dir = cache_dir(self.project, self.run_id)
        _invalidate_cache_if_changed(
            self.cache_dir, workflow_cache_signature(self._definition))
        
        # --- Helper class for updating the progress bar on the GUI thread ---
        class UpdateProgressBarTask(Runnable):
            def __init__(self, dialog, value):
                self.dialog = dialog
                self.value = value
            def run(self):
                self.dialog.progress_bar.setValue(self.value)

        # Suppress intermediate image windows during processing. Besides being
        # cleaner, this avoids an ilastik4ij / legacy-ImageJ display error
        # ("Stack argument out of range") that fires when virtual-stack
        # probability images are shown and then closed. Reset in done().
        self._batch_on = not self.settings.get('show_images', False)
        if self._batch_on:
            try:
                IJ.setBatchMode(True)
            except Exception:
                self._batch_on = False

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
                        prob_map_path = os.path.join(self.cache_dir, base_name)
                        IJ.saveAs(imp_cropped, "Tiff", temp_cropped_path)

                        if self.settings.get('show_images', False):
                            imp_cropped.show()

                        # Run only the expensive stages (segmentation + object
                        # classification), which cache the class-label image to
                        # disk. Post-processing, outlines, and CSV export happen
                        # later in the Results Viewer once the user is satisfied.
                        workflow = self.settings.get('workflow')
                        if workflow:
                            result_imp = workflow.process_roi(imp_cropped, temp_cropped_path, prob_map_path, self.settings)

                            if not self.settings.get('show_images', False):
                                if imp_cropped and imp_cropped.isVisible():
                                    imp_cropped.close()
                                if result_imp:
                                    result_imp.changes = False
                                    result_imp.close()

                            self.processed_any = True


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
                
                # No outline saving here - post-processing/export is deferred to
                # the Results Viewer.

                # Close the original image window if it's not meant to be shown
                if not self.settings.get('show_images', False) and imp_original and imp_original.isVisible():
                    imp_original.close()

                image_obj.status = "Segmented"  # classified; awaiting review + export

            except Exception as e:
                IJ.log("ERROR processing '{}': {}".format(image_obj.filename, e))
                image_obj.status = "Failed" # Mark as failed
                continue # Move to the next image

            finally:
                IJ.run("Collect Garbage", "")
                System.gc()

                if not self.settings.get('show_images', False):
                    self._cleanup_stray_windows()

        # Write the run metadata (workflow snapshot + default post params) so the
        # Results Viewer can find this run and recompute from the cached labels.
        self._save_processing_metadata()
        return "Segmentation & classification complete for {} ROIs.".format(roi_counter)
                
    


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
        
        meta = {
            'processed_date': datetime.datetime.now().isoformat(),
            'workflow_name': self.settings.get('workflow_name', 'Unknown'),
            'workflow_settings': serializable_settings,
            'images_processed': [img.filename for img in self.settings.get('images', [])],
            'total_results': len(self.all_results)
        }
        # Snapshot the full workflow definition for reproducibility.
        wf = self.settings.get('workflow')
        defn = getattr(wf, 'definition', None)
        if defn is not None:
            try:
                meta['workflow_definition'] = defn.to_dict()
            except Exception:
                pass
        return meta
    
    def _save_processing_metadata(self):
        """
        Save processing metadata to the run folder as run_metadata.json.
        Each run is self-contained with its own metadata file.
        """
        try:
            metadata_path = os.path.join(self.run_folder, 'run_metadata.json')

            with open(metadata_path, 'w') as f:
                json.dump(self._build_metadata(), f, indent=2)
            
        except Exception as e:
            IJ.log("Warning: Could not save processing metadata: " + str(e))
    
    def done(self):
        """ Runs on GUI thread after background work is finished. """
        final_message = "Processing finished."
        try:
            final_message = self.get()
        except Exception as e:
            IJ.log(traceback.format_exc())
            final_message = "An error occurred during processing:\n" + str(e)
            for image in self.settings['images']:
                if image.status == "Processing":
                    image.status = "Failed"
        finally:
            # Leave batch mode before touching windows / opening the viewer.
            try:
                IJ.setBatchMode(False)
            except Exception:
                pass

            self.progress_dialog.dispose()

            # Close stray temporary windows first (before opening the viewer).
            image_ids = WindowManager.getIDList()
            if image_ids:
                for img_id in list(image_ids):
                    img = WindowManager.getImage(img_id)
                    if img:
                        img.changes = False
                        img.close()

            self.project.sync_project_db()
            self.parent_gui.update_ui_for_project()

            # Inform the user. The Results tab is opened manually (Results button),
            # not automatically, so it doesn't pop up over their work.
            JOptionPane.showMessageDialog(
                self.parent_gui.frame,
                final_message + "\n\nOpen the Results tab to review detections, "
                "adjust post-processing, and export results.",
                "Segmentation complete", JOptionPane.INFORMATION_MESSAGE)
