# Creating Custom Workflows

This guide explains how to create your own quantification workflow for the Cell Quantification Toolkit.

---

## Quick Start

1. Copy `workflows/template_workflow.py` to a new file (e.g., `my_workflow.py`)
2. Rename the class and set `display_name`
3. Implement `process_roi()` with your detection logic
4. Implement `analyze_results()` to extract measurements
5. Restart the toolkit — your workflow appears in the dropdown

---

## Class Attributes

```python
class MyWorkflow(BaseWorkflow):
    display_name = "My Workflow"      # Shown in dropdown
    description = "Brief tooltip"     # Shown on hover
```

---

## Required Methods

Your class must inherit from `BaseWorkflow` and implement:

### `process_roi(cropped_imp, temp_path, prob_map_path, settings) → ImagePlus`

Main processing logic. Receives a cropped ROI image, returns a result image (e.g., binary mask).

```python
def process_roi(self, cropped_imp, temp_path, prob_map_path, settings):
    # Your detection/classification logic here
    result = cropped_imp.duplicate()
    IJ.run(result, "8-bit", "")
    IJ.setThreshold(result, 128, 255)
    IJ.run(result, "Convert to Mask", "")
    return result
```

### `analyze_results(result_imp, roi, offset_x, offset_y) → dict`

Extract measurements from the result image. Return a dict with keys matching `get_result_columns()`.

- Include `'outlines'` (list of ROIs) if you want cell selections saved for visualization

```python
def analyze_results(self, result_imp, roi, offset_x, offset_y):
    rm = RoiManager(True)
    rt = ResultsTable()
    pa = ParticleAnalyzer(ParticleAnalyzer.SHOW_OUTLINES, Measurements.AREA, rt, 20, float('inf'))
    pa.setRoiManager(rm)
    pa.analyze(result_imp)
    
    outlines = rm.getRoisAsArray() or []
    rm.close()
    
    return {
        'outlines': outlines,  # Base code translates to absolute coordinates
        'my_count': rt.getCounter(),
        'my_area': sum(rt.getColumn(rt.getColumnIndex("Area")) or [0])
    }
```

> **Note**: Outlines are returned in cropped image coordinates. The base code automatically translates them to full image coordinates.

---

## Optional Methods

### `get_settings_panel(models_dict) → JPanel`

Return a Swing panel with UI controls. Common components:

| Control | Use Case |
|---------|----------|
| `JComboBox` | Model selection, dropdowns |
| `JSpinner` | Numeric values with bounds |
| `JCheckBox` | Boolean options |
| `JTextField` | Text input |

### `gather_settings(panel) → dict`

Extract values from your panel into a settings dictionary.

### `get_result_columns() → list[str]`

Custom CSV columns beyond `filename`, `roi_name`, `roi_area`, `bregma_value`.

### `get_log_metadata(settings) → dict`

Custom metadata for `processing_log.json` (e.g., model names).

---

## File Structure

```
workflows/
├── base_workflow.py      # Don't modify - defines interface
├── template_workflow.py  # Copy and customize
├── brightfield_cfos.py   # Example implementation
└── my_workflow.py        # Your custom workflow
```

---

## Tips

- **DEV_MODE**: Set `DEV_MODE = True` in `Launch_Toolkit.py` to reload workflows without restarting Fiji
- **Debugging**: Use `IJ.log("message")` for logging
- **External tools**: Use `temp_path` to pass images to Ilastik or other tools
- **Resume capability**: Save intermediate files to `prob_map_path` and check if they exist before reprocessing
