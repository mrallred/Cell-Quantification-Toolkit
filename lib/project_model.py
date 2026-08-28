import os
import csv
import re
import glob
import json
import shutil
import datetime
from ij import IJ
from ij.plugin.frame import RoiManager
from javax.swing import JOptionPane

class ProjectImage(object):
    """ Simple class to hold info about a single image file """
    def __init__(self, filename, project_path):
        self.filename = filename
        self.project_path = project_path  # Store for run-based lookups
        self.full_path = os.path.join(project_path, "Images", filename)

        base_name, _ = os.path.splitext(self.filename)
        self.roi_path = os.path.join(project_path, "ROI_Files", base_name + "_ROIs.zip")

        self.rois = [] # list of dictionaries
        self.status = "In Progress" 
    
    def has_outlines(self):
        """ Check if any run contains cell outlines for this image """
        runs_dir = os.path.join(self.project_path, "Runs")
        if not os.path.exists(runs_dir):
            return False
        
        base_name, _ = os.path.splitext(self.filename)
        outline_name = base_name + "_Outlines.zip"
        
        # Check any run folder for this image's outlines
        for run_id in os.listdir(runs_dir):
            outline_path = os.path.join(runs_dir, run_id, "Cell_Selections", outline_name)
            if os.path.exists(outline_path):
                return True
        return False

    def has_roi(self):
        """ Checks if corrosponding ROI file exists """
        return os.path.exists(self.roi_path)

    def is_linked(self):
        """True if the image in Images/ is a symbolic link rather than a copy."""
        try:
            return os.path.islink(self.full_path)
        except Exception:
            return False

    def location_label(self):
        """Human-readable source: the link target if linked (external, absolute),
        else the internal copy shown as a path relative to the project root."""
        try:
            if os.path.islink(self.full_path):
                return "Link -> " + os.path.realpath(self.full_path)
            try:
                return os.path.relpath(self.full_path, self.project_path)
            except (ValueError, Exception):
                return self.full_path
        except Exception:
            return self.full_path

    def has_cached_objects(self):
        """True if any cached object-classification label image exists for this
        image (i.e. it has been segmented/classified and can be reviewed)."""
        prob_dir = os.path.join(self.project_path, "Probabilities")
        if not os.path.isdir(prob_dir):
            return False
        base = os.path.splitext(self.filename)[0] + "_"
        # Detect cached labels anywhere: per-workflow subfolders, the legacy flat
        # layout, or a migration backup (so pre-migration results still count).
        for root, dirs, files in os.walk(prob_dir):
            for f in files:
                if f.startswith(base) and f.endswith("_objects.tif"):
                    return True
        return False
    
    def add_roi(self, roi_data):
        """ Adds an ROI's data to the image"""
        self.rois.append(roi_data)

    def _load_rois_from_zip(self):
        """
        Loads all ROIs directly from the .zip file, making it the source of truth
        for names and individual bregma values.
        """
        if self.has_roi():
            rm = RoiManager(True)
            try:
                rm.open(self.roi_path)
                rois_array = rm.getRoisAsArray()
                self.rois = [] # Clear any old data
                for roi in rois_array:
                    self.rois.append({
                        'roi_name': roi.getName(),
                        'bregma': roi.getProperty("comment") or 'N/A'
                    })
            finally:
                rm.close()

# ---------------------------------------------------------------------------
# Run-folder migration to the per-workflow layout (inline: no cross-module
# import, which fails in this module's reload context). Non-destructive and
# idempotent -- moves the old Runs/ aside as a backup and rebuilds.
# ---------------------------------------------------------------------------
_MIGRATION_MARKER = '.per_workflow_v1'


def _sanitize_run_key(name):
    """MUST match workflow_config.sanitize_name so migrated folder keys line up
    with the keys make_run_id() produces for future runs."""
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', (name or '').strip())
    return s.strip('_') or 'workflow'


def _run_workflow_name(run_path):
    meta_path = os.path.join(run_path, 'run_metadata.json')
    if not os.path.exists(meta_path):
        legacy = sorted(glob.glob(os.path.join(run_path, '*_run_metadata.json')))
        meta_path = legacy[-1] if legacy else None
    if not meta_path or not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path) as f:
            return json.load(f).get('workflow_name')
    except (IOError, ValueError):
        return None


def _copy_run_into_workflow(src, dst):
    src_cs = os.path.join(src, 'Cell_Selections')
    if os.path.isdir(src_cs):
        dst_cs = os.path.join(dst, 'Cell_Selections')
        if not os.path.isdir(dst_cs):
            os.makedirs(dst_cs)
        for fn in os.listdir(src_cs):
            try:
                shutil.copy2(os.path.join(src_cs, fn), os.path.join(dst_cs, fn))
            except (IOError, OSError):
                pass
    for cand in ['run_metadata.json'] + [os.path.basename(p) for p in
                 glob.glob(os.path.join(src, '*_run_metadata.json'))]:
        sp = os.path.join(src, cand)
        if os.path.exists(sp):
            try:
                shutil.copy2(sp, os.path.join(dst, 'run_metadata.json'))
            except (IOError, OSError):
                pass
    src_run = os.path.basename(src.rstrip(os.sep))
    for csvp in (glob.glob(os.path.join(src, 'results_*.csv')) +
                 glob.glob(os.path.join(src, '*_results.csv'))):
        try:
            shutil.copy2(csvp, os.path.join(
                dst, 'migrated_{}__{}'.format(src_run, os.path.basename(csvp))))
        except (IOError, OSError):
            pass


def _relocate_flat_probabilities(project):
    prob_dir = project.paths.get('probabilities', '')
    if not prob_dir or not os.path.isdir(prob_dir):
        return
    flat = [f for f in os.listdir(prob_dir)
            if os.path.isfile(os.path.join(prob_dir, f)) and f.lower().endswith('.tif')]
    if not flat:
        return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(prob_dir, '_pre_perworkflow_backup_' + ts)
    if not os.path.isdir(backup):
        os.makedirs(backup)
    for f in flat:
        try:
            shutil.move(os.path.join(prob_dir, f), os.path.join(backup, f))
        except (IOError, OSError):
            pass


def _migrate_runs_to_per_workflow(project):
    runs_dir = project.paths.get('runs', '')
    if not runs_dir or not os.path.isdir(runs_dir):
        return
    if os.path.exists(os.path.join(runs_dir, _MIGRATION_MARKER)):
        return
    old_runs = [e for e in os.listdir(runs_dir)
                if os.path.isdir(os.path.join(runs_dir, e))
                and not e.startswith('_') and not e.startswith('.')]
    if not old_runs:
        _write_migration_marker(runs_dir)
        return

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(project.root_dir, 'Runs_pre_perworkflow_' + ts)
    shutil.move(runs_dir, backup)
    os.makedirs(runs_dir)

    workflows, runs, unclassified = 0, 0, 0
    seen = set()
    for run_name in sorted(os.listdir(backup)):
        src = os.path.join(backup, run_name)
        if not os.path.isdir(src):
            continue
        wfname = _run_workflow_name(src)
        if not wfname:
            try:
                shutil.copytree(src, os.path.join(runs_dir, run_name))
            except (IOError, OSError):
                pass
            unclassified += 1
            continue
        key = _sanitize_run_key(wfname)
        dst = os.path.join(runs_dir, key)
        if not os.path.isdir(dst):
            os.makedirs(dst)
        if key not in seen:
            seen.add(key)
            workflows += 1
        _copy_run_into_workflow(src, dst)
        runs += 1

    # NOTE: the legacy flat Probabilities/ cache is left in place; cached_label_path
    # falls back to it so pre-migration results stay viewable/re-tunable.
    _write_migration_marker(runs_dir)
    IJ.log("[CQT] Migrated runs to per-workflow layout: {} workflow(s) from {} run(s), "
           "{} unclassified. Backup: {}".format(workflows, runs, unclassified, backup))


def _write_migration_marker(runs_dir):
    try:
        with open(os.path.join(runs_dir, _MIGRATION_MARKER), 'w') as f:
            f.write('migrated to per-workflow layout\n')
    except IOError:
        pass


class Project(object):
    """ Class representing a project, holding its structure and data once opened from folder """
    
    # Current project.json schema version - 2.0 = run-based structure
    PROJECT_VERSION = "2.0"
    
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.name = os.path.basename(os.path.normpath(root_dir))
        self.paths = self._discover_paths()
        self._verify_and_create_dirs()

        # One-time, non-destructive migration of any old per-execution run folders
        # to the per-workflow layout (moves the old Runs/ aside as a backup and
        # rebuilds). Idempotent via a marker file; safe to call on every open.
        # Defined inline (module-level, below) rather than imported, because a
        # relative import fails in this module's reload context.
        try:
            _migrate_runs_to_per_workflow(self)
        except Exception as _mig_e:
            IJ.log("[CQT] Run migration skipped: " + str(_mig_e))

        self.images = []  # list of ProjectImage objects
        self.roi_templates = []  # List of {'name': str, 'default_bregma': str}
        self.selected_workflow = None  # name of the globally-defined workflow this project last used
        
        # Check for migration to run-based structure
        self._migrate_to_run_based()
        
        # Load from JSON if exists, otherwise try CSV migration
        if os.path.exists(self.paths['project_db']):
            self._load_project_json()
        elif self._has_legacy_csv_files():
            self._migrate_from_csv()
            self._save_project_json()  # Save migrated data
        
        self._scan_for_new_images()
        self.images.sort(key=self._get_natural_sort_key)

    def _get_natural_sort_key(self, image_object):
        """ correctly sorts filenames by extracting leading number """
        try:
            return int(image_object.filename.split('_')[0])
        except (ValueError, IndexError):
            return float('inf')
        
    def _verify_and_create_dirs(self):
        """ Check for essential project directories, verifies with user if missing, then creates if user confirms"""
        missing_dirs = []
        
        # Only check directories, JSON/CSV files are created on-demand
        # Runs folder is created on-demand during processing, not at project open
        dir_keys = ['images', 'rois', 'probabilities', 'temp']
        
        for key in dir_keys:
            path = self.paths.get(key)
            if path and not os.path.exists(path):
                missing_dirs.append(path)
               
        # If missing dirs, prompt user
        if missing_dirs:
            result = JOptionPane.showConfirmDialog(None, "This is not currently a project folder.\nEnsure you opened the correct folder if working on an existing project.\n\nWould like to create a new project here?", "Missing Project Files", JOptionPane.YES_NO_OPTION)
            if result != JOptionPane.YES_OPTION:
                raise Exception("Project Creation Cancelled")
            
            # User confirmed, creating missing items
            for dir_path in missing_dirs:
                try:
                    os.makedirs(dir_path)
                    IJ.log("Created missing project directory: {}".format(dir_path))
                except OSError as e:
                    IJ.log("Error creating directory {}: {}".format(dir_path, e))

    def _discover_paths(self):
        """ Creates dict of essential project components """
        return {
            'images': os.path.join(self.root_dir, 'Images'),
            'rois': os.path.join(self.root_dir, 'ROI_Files'),
            'processed': os.path.join(self.root_dir, 'Processed_Images'),
            'probabilities': os.path.join(self.root_dir, 'Probabilities'),
            'runs': os.path.join(self.root_dir, 'Runs'),
            'temp': os.path.join(self.root_dir, 'temp'),
            'project_db': os.path.join(self.root_dir, 'project.json'),
            # Legacy paths (for migration detection/cleanup)
            'legacy_cell_outlines': os.path.join(self.root_dir, 'Final_Cell_Selections'),
            'legacy_results_db': os.path.join(self.root_dir, 'Results_DB.csv'),
            'legacy_processing_log': os.path.join(self.root_dir, 'processing_log.json'),
            'roi_db': os.path.join(self.root_dir, 'Roi_DB.csv'),
            'image_status_db': os.path.join(self.root_dir, 'Image_Status_DB.csv'),
            'roi_templates_db': os.path.join(self.root_dir, 'ROI_Templates_DB.csv')
        }
    
    def _migrate_to_run_based(self):
        """
        Migrate from old structure (Final_Cell_Selections, Results_DB.csv) to run-based.
        Prompts user before deleting old results. ROIs and images are preserved.
        """
        
        # Check if old structure files exist
        old_files_exist = (
            os.path.exists(self.paths['legacy_cell_outlines']) or
            os.path.exists(self.paths['legacy_results_db']) or
            os.path.exists(self.paths['legacy_processing_log'])
        )
        
        if not old_files_exist:
            return  # Nothing to migrate
        
        # Prompt user
        msg = (
            "This project uses an older results structure.\n\n"
            "To use the new run-based organization, old results files will be deleted:\n"
            "  - Final_Cell_Selections/\n"
            "  - Results_DB.csv\n"
            "  - processing_log.json\n\n"
            "Your ROIs and images will be preserved.\n\n"
            "Migrate to new structure?"
        )
        result = JOptionPane.showConfirmDialog(
            None, msg, "Migrate Project", JOptionPane.YES_NO_OPTION
        )
        
        if result != JOptionPane.YES_OPTION:
            IJ.log("Migration skipped. Old results retained.")
            return
        
        # Delete old files
        try:
            if os.path.exists(self.paths['legacy_cell_outlines']):
                shutil.rmtree(self.paths['legacy_cell_outlines'])
                IJ.log("Deleted: Final_Cell_Selections/")
            
            if os.path.exists(self.paths['legacy_results_db']):
                os.remove(self.paths['legacy_results_db'])
                IJ.log("Deleted: Results_DB.csv")
            
            if os.path.exists(self.paths['legacy_processing_log']):
                os.remove(self.paths['legacy_processing_log'])
                IJ.log("Deleted: processing_log.json")
            
            IJ.log("Migration complete. Project now uses run-based structure.")
            
        except Exception as e:
            IJ.log("Migration error: " + str(e))
            JOptionPane.showMessageDialog(
                None, "Error during migration: " + str(e),
                "Migration Error", JOptionPane.ERROR_MESSAGE
            )
    
    def _has_legacy_csv_files(self):
        """Check if legacy CSV database files exist."""
        return (os.path.exists(self.paths['image_status_db']) or 
                os.path.exists(self.paths['roi_db']) or
                os.path.exists(self.paths['roi_templates_db']))
    
    def _load_project_json(self):
        """Load project state from project.json."""
        try:
            with open(self.paths['project_db'], 'r') as f:
                data = json.load(f)
            
            # Load ROI templates
            self.roi_templates = data.get('roi_templates', [])

            # Load the project's last-selected workflow (may be None)
            self.selected_workflow = data.get('selected_workflow')
            
            # Load images
            images_dir = self.paths['images']
            images_data = data.get('images', {})
            
            for filename, img_data in images_data.items():
                # Only load if image file still exists
                image_path = os.path.join(images_dir, filename)
                if not os.path.exists(image_path):
                    continue
                    
                img = ProjectImage(filename, self.root_dir)
                img.status = img_data.get('status', 'In Progress')
                # ROIs are loaded from zip file as source of truth
                img._load_rois_from_zip()
                self.images.append(img)
                
        except (IOError, ValueError) as e:
            IJ.log("Error loading project.json: {}".format(e))
    
    def _save_project_json(self):
        """Save project state to project.json."""
        try:
            # Build images dict and count total ROIs
            images_data = {}
            total_roi_count = 0
            for img in self.images:
                roi_count = len(img.rois)
                total_roi_count += roi_count
                images_data[img.filename] = {
                    'status': img.status,
                    'roi_count': roi_count,  # Cached count for progress bar
                    'rois': img.rois  # Convenience copy, zip is source of truth
                }
            
            data = {
                'version': self.PROJECT_VERSION,
                'created': datetime.datetime.now().isoformat(),
                'last_modified': datetime.datetime.now().isoformat(),
                'total_roi_count': total_roi_count,  # Cached for progress bar
                'roi_templates': self.roi_templates,
                'selected_workflow': self.selected_workflow,
                'images': images_data
            }
            
            # Preserve original created date if file exists
            if os.path.exists(self.paths['project_db']):
                try:
                    with open(self.paths['project_db'], 'r') as f:
                        existing = json.load(f)
                        data['created'] = existing.get('created', data['created'])
                except (IOError, ValueError):
                    pass
            
            with open(self.paths['project_db'], 'w') as f:
                json.dump(data, f, indent=2)
            return True
            
        except (IOError, ValueError) as e:
            IJ.log("Error saving project.json: {}".format(e))
            return False
    
    def _migrate_from_csv(self):
        """
        Migrate legacy CSV databases to project.json format.
        CSV files are preserved as backup.
        """
        IJ.log("Migrating project from CSV to JSON format...")
        
        # Load from legacy CSV methods
        self._load_project_db()  # Load image status and ROI data
        self._load_roi_templates()  # Load templates
        
        IJ.log("Migration complete. CSV files preserved as backup.")

    def _load_project_db(self):
        """
        Loads and parses both databases, but ONLY for images that currently exist
        in the Images folder, effectively pruning missing entries.
        """
        images_map = {}
        images_dir = self.paths['images']

        # Load Image Status DB
        status_db_path = self.paths['image_status_db']
        if os.path.exists(status_db_path):
            with open(status_db_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    filename = row['filename']
                    
                    # Verify the image file actually exists before processing its DB entry.
                    image_path = os.path.join(images_dir, filename)
                    if not os.path.exists(image_path):
                        continue # Skip this entry if the image file is missing.

                    if filename not in images_map:
                        images_map[filename] = ProjectImage(filename, self.root_dir)
                    images_map[filename].status = row.get('status', 'New')

        # Load ROI DB
        roi_db_path = self.paths['roi_db']
        if os.path.exists(roi_db_path):
            with open(roi_db_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    filename = row['filename']

                    # Also check here to catch images that might only be in the ROI DB.
                    image_path = os.path.join(images_dir, filename)
                    if not os.path.exists(image_path):
                        continue # Skip this entry if the image file is missing.

                    if filename not in images_map:
                        images_map[filename] = ProjectImage(filename, self.root_dir)
                    images_map[filename].add_roi(row)

        # Loop through all loaded images and populate from zip files as before
        for image in images_map.values():
            image._load_rois_from_zip()

        self.images = sorted(images_map.values(), key=lambda img: img.filename)

    def _scan_for_new_images(self):
        """ Scans images folder for any files not already loaded from the DBs. Adds these images into the project list. """
        if not os.path.isdir(self.paths['images']):
            return
        
        existing_filenames = {img.filename for img in self.images}
        for f in sorted(os.listdir(self.paths['images'])):
            if f.lower().endswith(('.tif', '.tiff', '.jpg', '.jpeg', '.png')) and f not in existing_filenames:
                new_image = ProjectImage(f, self.root_dir)
                new_image.status = "In Progress"
                new_image._load_rois_from_zip() # new images
                self.images.append(new_image)

    def remove_images(self, images_to_delete):
        """
        Permanently deletes image files and their associated data (ROIs, outlines)
        from the disk and removes them from the project's internal list.

        Args:
            images_to_delete (list): A list of ProjectImage objects to remove.

        Returns:
            int: The number of images successfully removed.
        """
        deleted_count = 0
        filenames_to_delete = {img.filename for img in images_to_delete}

        for image in images_to_delete:
            try:
                # Delete main image and ROI files
                files_to_remove = [image.full_path, image.roi_path]
                
                for file_path in files_to_remove:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                
                # Delete outline files from all run folders
                base_name, _ = os.path.splitext(image.filename)
                outline_name = base_name + "_Outlines.zip"
                runs_dir = self.paths.get('runs', '')
                
                if os.path.exists(runs_dir):
                    for run_id in os.listdir(runs_dir):
                        outline_path = os.path.join(
                            runs_dir, run_id, 'Cell_Selections', outline_name
                        )
                        if os.path.exists(outline_path):
                            os.remove(outline_path)
                
                deleted_count += 1
            except Exception as e:
                IJ.log("Error deleting files for '{}': {}".format(image.filename, e))

        # Rebuild the project's image list, excluding the deleted ones
        self.images = [img for img in self.images if img.filename not in filenames_to_delete]
        
        return deleted_count

    def sync_project_db(self):
        """ Master save function that saves project state to JSON. """
        return self._save_project_json()

    def _load_roi_templates(self):
        """Loads ROI templates from legacy CSV database (for migration)."""
        db_path = self.paths['roi_templates_db']
        self.roi_templates = []
        if os.path.exists(db_path):
            with open(db_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    self.roi_templates.append({
                        'name': row.get('name', ''),
                        'default_bregma': row.get('default_bregma', '')
                    })