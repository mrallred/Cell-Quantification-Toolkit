# Creating Workflows

There are two levels to this:

1. **Create a workflow** (no code) — combine existing pipeline providers into a
   saved workflow definition using the editor.
2. **Add a new pipeline provider** (code) — implement a new segmentation or
   classification method that then becomes selectable in the editor.

---

## 1. Create a workflow in the editor (no code)

A workflow is three stages — Segmentation → Classification → Post-processing —
plus a class map. Post-processing is *not* set here; it is tuned later in the
Results Viewer.

1. In the main window's **Current Workflow** panel, click **New…** (or
   **Edit…** / **Duplicate…**).
2. **Name** and describe the workflow.
3. **Segmentation** — pick a provider and set its parameters (for
   `ilastik_pixel`, choose the pixel-classification `.ilp`).
4. **Classification** — pick a provider (only those compatible with the chosen
   segmentation are shown) and set its parameters (for `ilastik_object`, choose
   the object-classification `.ilp`).
5. **Classes** — click **Populate from object classifier** to read the class
   names and colours straight from the `.ilp`, then tick **Include** for the
   classes you want counted (leave artifact/background unticked). You can also add
   rows manually: `Label` is the pixel value in the classification output.
6. **Save.** The definition is written to `workflow_defs/<name>.json` and is
   available to every project.

Classifiers are validated on save (the file must exist and be the right ilastik
project type — a pixel classifier can't be used in the object slot).

To run it: select the workflow in the Current Workflow panel, select images, and
click **Run Quantification**. Review and export from the Results Viewer.

---

## 2. Add a new pipeline provider (code)

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

- `self._list_models()` → `{basename: full_path}` of `.ilp` files in `models/`.
- `self._ilp_workflow_name(path)` → the ilastik `workflowName` (for type checks).
- `self._close_transient_windows(tokens)` → close stray ilastik display windows.

### Tips

- **DEV_MODE** in `Launch_Toolkit.py` reloads `lib/`, `workflows/`, and provider
  code without restarting Fiji.
- Cache expensive outputs under `ctx["prob_map_path"]` and skip work if the file
  already exists (this is what enables resume + fast re-export).
- Use `IJ.log("...")` for debugging.

> **Legacy note:** the old `workflows/` `BaseWorkflow` plugins
> (`template_workflow.py`, `brightfield_cfos.py`, …) are retained but hidden and
> superseded by the provider pipeline. New work should be a `StepProvider` in
> `steps/`, not a `BaseWorkflow` in `workflows/`.
