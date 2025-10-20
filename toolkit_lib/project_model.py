import os
import csv
from ij import IJ
from ij.plugin.frame import RoiManager

class ProjectImage(object):
    """ Simple class to hold info about a single image file """
    def __init__(self, filename, project_path):
        self.filename = filename
        self.full_path = os.path.join(project_path, "Images", filename)

        base_name, _ = os.path.splitext(self.filename)
        self.roi_path = os.path.join(project_path, "ROI_Files", base_name + "_ROIs.zip")
        self.outline_path = os.path.join(project_path, "Final_Cell_Selections", base_name + "_Outlines.zip")

        self.rois = [] # list of dictionaries
        self.status = "In Progress" 
    
    def has_outlines(self):
        """ Check if image has corrosponding cell outline selections file """
        return os.path.exists(self.outline_path)

    def has_roi(self):
        """ Checks if corrosponding ROI file exists """
        return os.path.exists(self.roi_path)
    
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

class Project(object):
    """ Class representing a project, holding its structure and data once opened from folder """
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.name = os.path.basename(os.path.normpath(root_dir))
        self.paths = self._discover_paths()
        self._verify_and_create_dirs()
        self.images = [] # list of ProjectImage objects
        self._load_project_db()
        self._scan_for_new_images()
        self.images.sort(key=self._get_natural_sort_key)

    def _get_natural_sort_key(self, image_object):
        """ correctly sorts filenames by extracting leading number """
        try:
            return int(image_object.filename.split('_')[0])
        except (ValueError, IndexError):
            return float('inf')
        
    def _verify_and_create_dirs(self):
        """ Check for essential project files and creates them if missing"""
        for key, path in self.paths.items():
            if not os.path.exists(path):
                try:
                    # For csv databases
                    if path.endswith(".csv"):
                        headers = []
                        if key == 'roi_db':
                            headers = ['filename', 'roi_name', 'bregma', 'status']
                        elif key == 'image_status_db': 
                            headers = ['filename', 'status']
                        elif key == 'results_db':
                            headers = ['filename', 'roi_name', 'roi_area', 'bregma_value', 'cell_count', 'total_cell_area' ]

                        if headers:
                            with open(path, 'wb') as csvfile:
                                writer = csv.writer(csvfile)
                                writer.writerow(headers)
                                IJ.log("Created missing project database: {}".format(path))
                    else:
                        os.makedirs(path)
                        IJ.log("Created missing project directory: {}".format(path))
                except OSError as e:
                    IJ.log("Error creating directory {}: {}".format(path, e))

    def _discover_paths(self):
        """ Creates dict of essential project components """
        return {
            'images': os.path.join(self.root_dir, 'Images'),
            'rois': os.path.join(self.root_dir, 'ROI_Files'),
            'processed': os.path.join(self.root_dir, 'Processed_Images'),
            'probabilities': os.path.join(self.root_dir, 'Ilastik_Probabilites'),
            'cell_outlines': os.path.join(self.root_dir, 'Final_Cell_Selections'),
            'temp': os.path.join(self.root_dir, 'temp'),
            'roi_db': os.path.join(self.root_dir, 'Roi_DB.csv'),
            'image_status_db': os.path.join(self.root_dir, 'Image_Status_DB.csv'),
            'results_db': os.path.join(self.root_dir, 'Results_DB.csv')
        }

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
                    
                    # --- NEW CHECK ---
                    # Verify the image file actually exists before processing its DB entry.
                    image_path = os.path.join(images_dir, filename)
                    if not os.path.exists(image_path):
                        continue # Skip this entry if the image file is missing.
                    # --- END NEW CHECK ---

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

                    # --- NEW CHECK ---
                    # Also check here to catch images that might only be in the ROI DB.
                    image_path = os.path.join(images_dir, filename)
                    if not os.path.exists(image_path):
                        continue # Skip this entry if the image file is missing.
                    # --- END NEW CHECK ---

                    if filename not in images_map:
                        images_map[filename] = ProjectImage(filename, self.root_dir)
                    images_map[filename].add_roi(row)

        # Loop through all loaded images and populate from zip files as before
        for image in images_map.values():
            image._load_rois_from_zip()

        self.images = sorted(images_map.values(), key=lambda img: img.filename)

    def _scan_for_new_images(self):
        """ Scans images folder for any files not already loaded from the DBs. """
        if not os.path.isdir(self.paths['images']):
            return
        
        existing_filenames = {img.filename for img in self.images}
        for f in sorted(os.listdir(self.paths['images'])):
            if f.lower().endswith(('.tif', '.tiff', 'jpg', 'jpeg')) and f not in existing_filenames:
                new_image = ProjectImage(f, self.root_dir)
                new_image.status = "In Progress"
                new_image._load_rois_from_zip() # new images
                self.images.append(new_image)

    def sync_project_db(self):
        """ Master save function that syncs both databases. """
        roi_success = self._sync_roi_db()
        status_success = self._sync_image_status_db()
        return roi_success and status_success

    def _sync_roi_db(self):
        """ Rewrites the Roi_DB.csv (ROI data) from memory. """
        db_path = self.paths['roi_db']
        headers = ['filename', 'roi_name', 'bregma', 'status']
        try:
            with open(db_path, 'wb') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=headers)
                writer.writeheader()
                for image in self.images:
                    if not image.rois:
                        continue # Skip images with no ROIs
                    for roi_data in image.rois:
                        row = {
                            'filename': image.filename,
                            'roi_name': roi_data.get('roi_name', 'N/A'),
                            'bregma': roi_data.get('bregma', 'N/A'),
                            'status': roi_data.get('status', 'Pending')
                        }
                        writer.writerow(row)
            return True
        except IOError as e:
            IJ.log("Error syncing ROI DB: {}".format(e))
            return False

    def _sync_image_status_db(self):
        """ Rewrites the Image_Status_DB.csv from memory. """
        db_path = self.paths['image_status_db']
        headers = ['filename', 'status']
        try:
            with open(db_path, 'wb') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=headers)
                writer.writeheader()
                for image in self.images:
                    writer.writerow({'filename': image.filename, 'status': image.status})
            return True
        except IOError as e:
            IJ.log("Error syncing Image Status DB: {}".format(e))
            return False

    