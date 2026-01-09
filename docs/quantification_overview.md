# Quantification Module Overview

The quantification module (`lib/quantification.py`) performs automated cell detection and counting via a **plugin-based workflow architecture**. Workflows are dynamically discovered from the `workflows/` folder, enabling easy extension without modifying core code.

---

## Architecture

```
QuantificationDialog
        │
        ├──[selects]──► BaseWorkflow Plugin
        │
        └──[starts]──► QuantificationWorker
                              │
                       [for each ROI]
                              │
                              ▼
                    workflow.process_roi()
                              │
                        [returns]
                              │
                              ▼
                      Result ImagePlus
                              │
                       [passed to]
                              │
                              ▼
                  workflow.analyze_results()
                              │
                        [returns]
                              │
                              ▼
                     Measurements Dict
                              │
                              ▼
                        Results CSV
```

| Component | Purpose |
|-----------|---------|
| `_discover_workflows()` | Scans `workflows/` and loads all `BaseWorkflow` subclasses |
| `QuantificationDialog` | Modal dialog with workflow dropdown and dynamic settings panels |
| `QuantificationWorker` | SwingWorker for background batch processing |
| `BaseWorkflow` | Abstract base class defining the plugin interface |

---

## Workflow Plugin System

### Plugin Discovery
At startup, `_discover_workflows()` scans the `workflows/` folder:
1. Loads `base_workflow.py` to get the `BaseWorkflow` class
2. Imports all other `.py` files (excluding files starting with `_`)
3. Instantiates any class that subclasses `BaseWorkflow`
4. Returns a dict of `{display_name: workflow_instance}`

### BaseWorkflow Interface

```
workflows/base_workflow.py
```

| Method | Required | Purpose |
|--------|----------|---------|
| `display_name` | Yes | String shown in workflow dropdown |
| `description` | No | Tooltip text |
| `get_settings_panel(models_dict)` | No | Returns JPanel with custom UI controls |
| `gather_settings(panel)` | No | Extracts settings dict from panel |
| `get_result_columns()` | No | Returns list of custom CSV column names |
| `process_roi(cropped_imp, temp_path, prob_map_path, settings)` | **Yes** | Run detection/classification logic; returns ImagePlus |
| `analyze_results(result_imp, roi, offset_x, offset_y)` | **Yes** | Extract measurements; returns dict with `count`, `total_area`, `outlines`, and custom columns (by convention) |

### Built-in Workflows

| Workflow | File | Description |
|----------|------|-------------|
| **Brightfield cFos** | `brightfield_cfos.py` | Two-step Ilastik classification (pixel → object) with watershed segmentation |
| **Template Workflow** | `template_workflow.py` | Comprehensive template demonstrating all UI controls and processing methods |

---

## Processing Pipeline

### 1. Configuration Phase
`QuantificationDialog` presents:
- **Workflow dropdown** — dynamically populated from discovered plugins
- **Workflow settings panel** — swapped when workflow selection changes
- **Display option** — show/hide intermediate images

### 2. Batch Processing
`QuantificationWorker.doInBackground()` processes each image:

```
For each selected image:
├── Load all ROIs from .zip file
└── For each ROI:
    ├── Crop image region → save temp file
    ├── workflow.process_roi() → classification/detection
    ├── workflow.analyze_results() → measurements
    └── Collect cell outlines
```

### 3. Workflow Delegation
The worker delegates processing to the selected workflow plugin:

```python
# In QuantificationWorker.doInBackground():
result_imp = workflow.process_roi(cropped_imp, temp_path, prob_map_path, settings)
analysis = workflow.analyze_results(result_imp, roi, offset_x, offset_y)
```

### 4. Result Aggregation
In `done()`, results are aggregated by `(filename, roi_name)`:
- ROI areas are summed
- Custom numeric columns are summed
- Bregma values are averaged

---

## Data Flow

```
Input Image + ROI
       ↓
   [Crop to ROI]
       ↓
   [workflow.process_roi()]  →  Intermediate outputs (e.g., *_probabilities.tif)
       ↓
   [workflow.analyze_results()]
       ↓
   Results: count, total_area, outlines, custom columns
```

---

## Output Files

| File | Location | Content |
|------|----------|---------|
| Probability maps | `Ilastik_Probabilities/` | Intermediate classification output |
| Cell outlines | `Final_Cell_Selections/` | ROI zip per image |
| Results database | `Results_DB.csv` | Aggregated quantification data |
| Processing log | `processing_log.json` | JSON metadata linking run IDs to settings |

### Results Schema
Base columns + workflow-specific columns:
```csv
filename, roi_name, roi_area, bregma_value, processing_run_id, [workflow_columns...]
```

> [!TIP]
> **Metadata Coupling**: Each result row contains a `processing_run_id` (e.g., `20231025_143022`). This ID corresponds to an entry in `processing_log.json`, which stores the full snapshot of settings (thresholds, model paths, etc.) used to generate that result.

Example for Brightfield cFos (each workflow defines its own columns via `get_result_columns()`):
```csv
filename, roi_name, roi_area, bregma_value, cell_count, total_cell_area
```

> [!NOTE]
> Multiple ROI sub-regions with the same name are aggregated in the final results, with bregma values averaged.

---

## Creating Custom Workflows

1. Copy `template_workflow.py` to a new file in `workflows/`
2. Rename the class and set `display_name`
3. Implement `process_roi()` with your detection logic
4. Implement `analyze_results()` to extract measurements
5. Optionally add custom settings via `get_settings_panel()` and `gather_settings()`
6. Define custom CSV columns via `get_result_columns()`

The workflow will be automatically discovered on next launch.