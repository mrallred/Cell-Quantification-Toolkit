# Quantification Module Overview

The quantification module (`lib/quantification.py`) performs automated cell detection and counting via a **plugin-based workflow architecture**. Workflows are dynamically discovered from the `workflows/` folder, enabling easy extension without modifying core code.

---

## Architecture

```mermaid
flowchart TD
    QD[QuantificationDialog]
    QD -->|selects| BWP[BaseWorkflow Plugin]
    QD -->|starts| QW[QuantificationWorker]
    QW -->|for each ROI| PR["workflow.process_roi()"]
    PR -->|returns| RIP[Result ImagePlus]
    RIP -->|passed to| AR["workflow.analyze_results()"]
    AR -->|returns| MD[Measurements Dict]
    MD --> CSV[Results CSV]
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

```mermaid
flowchart TD
    A[For each selected image] --> B[Load all ROIs from .zip file]
    B --> C[For each ROI]
    C --> D["Crop image region → save temp file"]
    D --> E["workflow.process_roi() → classification/detection"]
    E --> F["workflow.analyze_results() → measurements"]
    F --> G[Collect cell outlines]
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

```mermaid
flowchart TD
    A[Input Image + ROI] --> B[Crop to ROI]
    B --> C["workflow.process_roi()"]
    C -->|produces| D["Intermediate outputs (e.g., *_probabilities.tif)"]
    C --> E["workflow.analyze_results()"]
    E --> F["Results: count, total_area, outlines, custom columns"]
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

> [!NOTE]
> **Metadata Coupling**: Each result row contains a `processing_run_id` (e.g., `20231025_143022`). This ID corresponds to an entry in `processing_log.json`, which stores the full snapshot of settings (thresholds, model paths, etc.) used to generate that result.

Example for Brightfield cFos (each workflow defines its own columns via `get_result_columns()`):
```csv
filename, roi_name, roi_area, bregma_value, cell_count, total_cell_area
```

> [!NOTE]
> Multiple ROI sub-regions with the same name are aggregated in the final results, with bregma values averaged.

---