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

> [!NOTE]
> **Classifiers are not bundled.** The ImageJ updater only ships a fixed set of
> file extensions, which excludes `.ilp`, and the models run to tens of MB each.
> The bundled *automated* workflows name classifiers you have to put in `models/`
> yourself — until then they'll report "classifier file not found" when you try to
> run them. Point them at your own `.ilp` via **Edit...**, or build a new
> workflow. The bundled *manual counting* workflows need no models and work
> immediately.
>
> The bundled definitions themselves are seeded into `workflow_defs/` on first
> run from `lib/builtin_workflows.py`. Editing or deleting one is permanent — it
> won't be rewritten or resurrected on the next launch.

---

## 1. Create an automated workflow (no code)

An automated workflow is three stages - Segmentation -> Classification ->
Post-processing - plus a class map. Post-processing is *not* set here; it is tuned
later in the Results viewer.

1. Click **New...**, and set **Workflow type** to *Automated cell classification*.
2. **Name** and describe the workflow.
3. **Segmentation** - pick a provider and set its parameters (for
   `ilastik_pixel`, choose the pixel-classification `.ilp`, and optionally tick
   **Append L\*a\*b\* channels** - see [below](#optional-append-lab-channels);
   both classifiers must then be trained on 6-channel RGB+Lab images).
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
It runs segmentation + classification and caches the results; then open the
**Results** viewer, where you tune post-processing and export.

Output lands in `Runs/<workflow-name>/` and the prediction cache in
`Probabilities/<workflow-name>/`, both keyed by the workflow's (sanitized) name.
Renaming a workflow therefore starts a fresh folder and a fresh cache; the old
one is left untouched.

### Optional: Append L\*a\*b\* channels

The `ilastik_pixel` provider has an **Append L\*a\*b\* channels** checkbox (param
`append_lab`, off by default). When ticked, each ROI crop is expanded to a
6-channel 8-bit image before ilastik sees it - R, G, B native, then `L*` x 2.55,
`a*` + 128, `b*` + 128 - and that image replaces the input for the **object**
stage too, so both classifiers see the same layout. The conversion is
`lib/color_lab.py`, the single source of truth shared with the training-export
macro.

The point is the two chroma axes: `b*` (blue<->yellow) separates DAB brown from
bluish CtB, and `a*` adds DAB's red component - signal that raw RGB buries.
Whether it helps your weak classes is empirical; the checkbox is there so you can
A/B it against plain RGB. The pixel classifier stays single-class (foreground vs.
background) either way; the multi-class decision stays in the object classifier.

Both `.ilp` models must be trained on matching 6-channel images:

1. Export training images with `macros/Export_RGB_plus_Lab_for_Training.py`
   (active image, or a whole folder) -> one `*_RGBLab.tif` per input.
2. Train the **pixel** classifier on those (one foreground class + background).
   Save the `.ilp` into `models/`.
3. Train the **object** classifier as *Object Classification (from prediction
   image)*, with the `*_RGBLab.tif` as Raw Data and the pixel probabilities as the
   prediction input. Save into `models/`.
4. In the editor, tick the box and select those two `.ilp` files.

Keep training and prediction matched - an RGB+Lab classifier must run with the box
ticked, a plain-RGB one with it unticked, or it silently degrades. `append_lab` is
part of the cache signature, so toggling it invalidates that workflow's cached
predictions instead of mixing layouts.
`workflow_defs/Brightfield_Costained_cFos_CtB_RGB_Lab.json` is a working example.

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
between images, then **Save & Close** (points also autosave every minute, so an
accidental close doesn't lose them). Open the **Results** viewer and
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

### The `ctx` dict

| Key | Set by | Meaning |
|-----|--------|---------|
| `temp_path` | runner | The cropped ROI image on disk (the stage input) |
| `prob_map_path` | runner | Cache path *prefix* for this ROI, inside `Probabilities/<workflow>/` - append your own suffix |
| `show_images` / `force_recalculate` | runner | Per-run display / cache-busting flags |
| `probability_map_path` | segmentation | Written probability map (what `ilastik_object` reads) |
| `class_labels_imp` / `class_labels_path` | classification | The class-label image (post-processing reads this) |

A stage may **rewrite `ctx['temp_path']`** to change the input seen by later
stages - that is how `ilastik_pixel`'s `append_lab` option feeds the same
6-channel image to the object classifier. If you do this, do it *before* your
cache-hit early return, or a cached run will leave the downstream stage with the
original input.

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
  already exists (this is what enables resume + fast re-export). Honour
  `force_recalculate` by deleting your cached file first.
- **If you add a parameter that changes what the cache contains, add it to
  `workflow_config.workflow_cache_signature()`.** That signature (currently the
  two classifier names + `append_lab`) is stored as `.signature` beside the cache
  and invalidates it when it changes; a parameter missing from it means edited
  settings silently reuse stale predictions.
- Use `IJ.log("...")` for debugging.
