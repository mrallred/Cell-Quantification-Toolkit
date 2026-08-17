# Creating Workflows

Three ways to work with workflows:

1. **Create an automated workflow** (no code) - combine existing pipeline
   providers into a saved definition.
2. **Create a manual-counting workflow** (no code) - just define the classes.
3. **Add a new pipeline provider** (code) - implement a new segmentation or
   classification method that then becomes selectable in the editor.

Workflows are listed in the main window's **Current Workflow** panel. Click a
workflow to select it, or use **New... / Edit... / Duplicate... / Delete...**.
Every definition is saved to `workflow_defs/<name>.json` and is available to all
projects.

---

## 1. Create an automated workflow (no code)

An automated workflow is three stages - Segmentation -> Classification ->
Post-processing - plus a class map. Post-processing is *not* set here; it is tuned
later in the Results tab.

1. Click **New...**, and set **Workflow type** to *Automated cell classification*.
2. **Name** and describe the workflow.
3. **Segmentation** - pick a provider and set its parameters (for
   `ilastik_pixel`, choose the pixel-classification `.ilp`).
4. **Classification** - pick a provider (only those compatible with the chosen
   segmentation are shown) and set its parameters (for `ilastik_object`, choose
   the object-classification `.ilp`).
5. **Classes** - click **Populate from object classifier** to read the class
   names and colours straight from the `.ilp`, then tick **Include** for the
   classes you want counted (leave artifact/background unticked). You can also add
   rows manually: `Label` is the pixel value in the classification output.
6. **Save.**

Classifiers are validated on save (the file must exist and be the right ilastik
project type - a pixel classifier can't be used in the object slot).

To run it: select the workflow, select images, and click **Run Quantification**.
It runs segmentation + classification and caches the results, then opens the
**Results** tab where you tune post-processing and export.

---

## 2. Create a manual-counting workflow (no code)

1. Click **New...**, and set **Workflow type** to *Manual counting*. The
   segmentation/classification stages disappear - a manual workflow is just a
   class map.
2. **Name** it and add a row per class (display name + colour).
3. **Save.**

To use it: select the workflow, select images, and click **Run Quantification** to
open the counting tool. Pick a class, click on its cells (the active class is the
live multi-point selection; other classes show as a coloured overlay), navigate
between images, then **Save & Close**. Open the **Results** tab and
**Export counts (all images)** to count the points inside each ROI and write the
CSV.

---

## 3. Add a new pipeline provider (code)

Providers live in `steps/` and are discovered automatically. Each subclasses
`StepProvider` (`steps/base_step.py`) and declares which stage it fills and what
it produces/consumes so the editor can gate compatible combinations.

```python
# steps/my_segmenter.py
import os
from ij import IJ

try:                       # StepProvider is injected by the registry loader
    StepProvider
except NameError:
    import sys
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    from base_step import StepProvider


class MySegmenter(StepProvider):
    stage = "segmentation"                 # or "classification"
    type_id = "my_segmenter"               # stable id stored in the JSON
    display_name = "My Segmenter"
    produces = ["instance_labels"]         # contract tokens
    consumes = []

    def available(self):
        return True                        # e.g. check a required plugin exists

    def default_params(self):
        return {"threshold": 0.5}

    def build_panel(self, params):
        # return a Swing JPanel of controls (store references for gather_params)
        ...

    def gather_params(self, panel=None):
        return {"threshold": float(self._spin.getValue())}

    def validate(self):
        return []                          # list of problem strings ([] == OK)

    def run(self, ctx):
        # ctx has: temp_path, prob_map_path, show_images, force_recalculate
        # produce your output and set it on ctx, e.g.:
        # ctx["instance_labels_path"] = ...  /  ctx["class_labels_imp"] = imp
        return ctx
```

### Contract tokens

| Stage | Typical `produces` | Typical `consumes` |
|-------|--------------------|--------------------|
| segmentation | `probability_map`, `instance_labels` | (none) |
| classification | `class_labels` | `probability_map`, `instance_labels` |

A classification provider is offered in the editor only if its `consumes`
intersects the chosen segmentation's `produces` (or it consumes nothing). The
final post-processing stage is shared (`postprocess.run_post`) and consumes the
`class_labels` image the classification provider returns as
`ctx["class_labels_imp"]`.

### Helpers available on `StepProvider`

- `self._list_models()` -> `{basename: full_path}` of `.ilp` files in `models/`.
- `self._ilp_workflow_name(path)` -> the ilastik `workflowName` (for type checks).
- `self._materialize(imp)` -> convert a virtual-stack output (e.g. ilastik's) to a
  real in-memory image, avoiding an ImageJ2 display crash when it is closed.
- `self._close_transient_windows(tokens)` -> close stray display windows.

### Tips

- **DEV_MODE** in `Launch_Toolkit.py` reloads `lib/` and `steps/` code without
  restarting Fiji.
- Cache expensive outputs under `ctx["prob_map_path"]` and skip work if the file
  already exists (this is what enables resume + fast re-export).
- Use `IJ.log("...")` for debugging.
