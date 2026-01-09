# Cell Quantification Toolkit — Architecture Overview

This document covers the high-level architecture and organizational logic of the Cell Quantification Toolkit, a Fiji plugin for managing image analysis projects and running automated cell quantification workflows.

---

## Directory Structure

```
Cell_Quantification_Toolkit/
├── Launch_Toolkit.py       # Entry point script
├── lib/                    # Core application modules
│   ├── main_gui.py         # Main GUI and application controller
│   ├── project_model.py    # Data model for projects and images
│   ├── roi_editor.py       # ROI editing interface
│   ├── quantification.py   # Batch processing orchestration
│   └── results_viewer.py   # Results visualization
├── workflows/              # Pluggable workflow system
│   ├── base_workflow.py    # Abstract base class for workflows
│   ├── brightfield_cfos.py # Example: Ilastik-based cell detection
│   └── template_workflow.py# Reference template for new workflows
├── models/                 # Ilastik classifier models (.ilp files)
└── docs/                   # Documentation
```

---

## Component Overview

```mermaid
graph TD
    subgraph Entry
        A[Launch_Toolkit.py]
    end
    
    subgraph "Core Library (lib/)"
        B[ProjectManagerGUI<br/>main_gui.py]
        C[Project / ProjectImage<br/>project_model.py]
        D[ROIEditor<br/>roi_editor.py]
        E[QuantificationDialog<br/>QuantificationWorker<br/>quantification.py]
        F[ResultsViewer<br/>results_viewer.py]
    end
    
    subgraph "Workflow Plugins (workflows/)"
        G[BaseWorkflow<br/>base_workflow.py]
        H[BrightfieldCfosWorkflow]
        I[Other Workflows...]
    end
    
    A --> B
    B --> C
    B --> D
    B --> E
    B --> F
    E --> G
    G --> H
    G --> I
```

---

## Module Responsibilities

### 1. Entry Point — [`Launch_Toolkit.py`](../Launch_Toolkit.py)

- **Purpose**: Bootstrap script that initializes the plugin
- **Key features**:
  - Development mode (`DEV_MODE = True`) for hot-reloading modules during development
  - Clears compiled `.class` files and reloads Python modules when in dev mode
  - Launches the main GUI on the Swing event thread

---

### 2. Main GUI — [`main_gui.py`](../lib/main_gui.py)

| Class | Purpose |
|-------|---------|
| `ProjectManagerGUI` | Main application window and controller |
| `ImageImportWorker` | Background thread for importing images |

**ProjectManagerGUI responsibilities**:
- File menu: Open/Save project
- Image table: Display and select project images
- Image operations: Import, remove, edit ROIs
- Quantification: Launch batch processing
- ROI templates: Manage predefined ROI names

**Key patterns**:
- Uses `SwingWorker` for background operations
- Maintains `Project` reference as central state
- Coordinates between sub-components (ROI editor, quantification dialog, results viewer)

---

### 3. Data Model — [`project_model.py`](../lib/project_model.py)

| Class | Purpose |
|-------|---------|
| `Project` | Represents an analysis project (folder-based) |
| `ProjectImage` | Represents a single image and its metadata |

**Project structure (on disk)**:
```
MyProject/
├── Images/                 # Source images
├── ROI_Files/              # ROI selection files (.zip)
├── Final_Cell_Selections/  # Detected cell outlines (.zip)
├── Probabilities/          # Workflow intermediate outputs
│   └── {ImageName}_{ROIName}_{index}_probabilities.tif
│   └── {ImageName}_{ROIName}_{index}_objects.tif
├── temp/                   # Temporary processing files (auto-cleaned)
├── Image_Status_DB.csv     # Image processing status
├── Roi_DB.csv              # ROI metadata
├── ROI_Templates_DB.csv    # Predefined ROI names
├── Results_DB.csv          # Quantification results
└── processing_log.json     # Processing run metadata (JSON)
```

**Key methods**:
- `_verify_and_create_dirs()`: Ensures project structure exists
- `_load_project_db()` / `sync_project_db()`: Load/save CSV databases
- `remove_images()`: Delete images and associated files

---

### 4. ROI Editor — [`roi_editor.py`](../lib/roi_editor.py)

| Class | Purpose |
|-------|---------|
| `ROIEditor` | Modal dialog for drawing/editing regions of interest |

**Features**:
- Create, update, delete ROIs
- Hierarchical display with templates as headers
- Store metadata (name, bregma value) in ROI properties
- Commit-on-action editing model
- Save ROIs as Fiji-compatible `.zip` files

---

### 5. Quantification Engine — [`quantification.py`](../lib/quantification.py)

| Class | Purpose |
|-------|---------|
| `QuantificationDialog` | Settings dialog with workflow selection |
| `ProgressDialog` | Progress bar during batch processing |
| `QuantificationWorker` | Background thread executing workflows |

**Processing flow**:
1. User selects images and opens quantification dialog
2. Dialog dynamically loads available workflows from `workflows/` folder
3. User selects workflow and configures settings
4. `QuantificationWorker.doInBackground()`:
   - Loops through each image → each ROI
   - Crops ROI region and saves as temp file
   - Delegates to workflow's `process_roi()` method
   - Calls workflow's `analyze_results()` method
   - Aggregates results by ROI name
5. `QuantificationWorker.done()`:
   - Writes aggregated results to `Results_DB.csv`
   - Saves processing metadata to `processing_log.json`

---

### 6. Results Viewer — [`results_viewer.py`](../lib/results_viewer.py)

| Class | Purpose |
|-------|---------|
| `ResultsViewer` | Dialog for viewing an image with overlays |
| `ImageWindowListener` | Syncs dialog lifecycle with image window |

**Features**:
- Display image with toggleable overlays:
  - Analysis ROIs (user-drawn regions)
  - Cell outlines (detected objects)
- Show processing metadata (workflow used, date, settings)

---

## Workflow Plugin System

### Base Class — [`base_workflow.py`](../workflows/base_workflow.py)

Abstract interface that all workflows must implement:

```python
class BaseWorkflow:
    display_name = "..."        # Shown in dropdown
    description = "..."         # Tooltip
    
    def get_settings_panel(models_dict) -> JPanel    # Custom UI
    def gather_settings(panel) -> dict               # Extract settings
    def get_result_columns() -> list[str]            # Custom CSV columns
    
    # REQUIRED:
    def process_roi(cropped_imp, temp_path, prob_map_path, settings) -> ImagePlus
    def analyze_results(result_imp, roi, offset_x, offset_y) -> dict
```

### Example Implementation — [`brightfield_cfos.py`](../workflows/brightfield_cfos.py)

Implements two-stage Ilastik classification:
1. **Pixel Classification**: Generates probability maps
2. **Object Classification**: Identifies individual cells

Features:
- Resume capability (skips already-processed files)
- Configurable watershed segmentation
- Configurable edge particle exclusion

---

## Data Flow Diagram

```mermaid
flowchart TB
    subgraph Input
        A[Source Images]
        B[User ROIs]
    end
    
    subgraph Processing
        C[Crop ROI Region]
        D[Workflow Plugin<br/>process_roi]
        E[Workflow Plugin<br/>analyze_results]
    end
    
    subgraph Output
        F[Probability Maps]
        G[Cell Outlines]
        H[Results_DB.csv]
        I[processing_log.json]
    end
    
    A --> C
    B --> C
    C --> D
    D --> F
    D --> E
    E --> G
    E --> H
    D --> I
```

---

## Key Design Decisions

### 1. Folder-Based Projects
Projects are simple directories with a defined structure. Data is stored in human-readable formats—CSV files for tabular data, JSON for structured metadata, and standard image formats—for maximum interoperability and easy external analysis.

### 2. Dual Database Strategy: CSV + JSON
- **CSV databases** (`Results_DB.csv`, `Image_Status_DB.csv`, etc.): Store row-oriented data that users may want to analyze in Excel, R, Python, or other tools
- **JSON database** (`processing_log.json`): Stores hierarchical processing metadata, keyed by unique `run_id` timestamps. Each entry contains:
  - `processed_date`: ISO timestamp of when the run occurred
  - `workflow_name`: Which workflow was used
  - `workflow_settings`: Complete configuration (classifiers, options)
  - `images_processed`: List of image filenames in this run
  - `total_results`: Count of results generated

This allows Results_DB.csv rows to reference their processing context via `processing_run_id`, enabling full reproducibility and audit trails.

### 3. Plugin Architecture for Workflows
Workflows are discovered dynamically at runtime by scanning the `workflows/` folder. This allows adding new analysis methods without modifying core code.

### 4. ROI as Source of Truth
ROI geometry and metadata (name, bregma) are stored directly in Fiji's `.zip` ROI format using custom properties, ensuring compatibility with Fiji's built-in ROI Manager.

### 5. Resume-Capable Processing
Intermediate files (probability maps, object classifications) are preserved in `Probabilities/`, allowing processing to resume after interruption. Files are named with image, ROI, and index to prevent collisions.

### 6. Metadata Traceability
Each processing run is logged with a unique `run_id` (timestamp-based) that links results in `Results_DB.csv` to full settings in `processing_log.json`, enabling complete reproducibility.
