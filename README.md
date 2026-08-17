# Cell Quantification Toolkit

A Fiji plugin for project-based, ROI-specific, and automated cell detection and quantification in microscopy images. 

A **workflow** is a saved, reusable definition that describes how to quantify cells. There are two kinds:

- **Automated cell classification** — a pluggable pipeline of **segmentation -> classification -> post-processing**, where each stage is filled by a swappable provider. Two ilastik-based providers ship today (pixel classification for segmentation, object classification for classification), and the built-in workflows cover single-label brightfield DAB-cFos and two-colour costained cFos + CtB detection. New methods are added by dropping a provider into `steps/` (no core changes).
- **Manual counting** — you define classes and click on the cells of each class; points inside each ROI are counted and exported.

Workflows are global and reusable across projects, chosen from a list in the main window.

## Installation

The best way to install the Cell Quantification Toolkit is from the Fiji update site. 

1. If you dont have Fiji and Ilastik installed you must install them first:

    - **Fiji**: Download and install the correct version from [Fiji](https://imagej.net/software/fiji/downloads)

    - **Ilastik**: Ilastik is a machine learning-based image analysis tool that is used to train and apply pixel and object classification models. Download and install the correct version from [Ilastik](https://www.ilastik.org/download).

2. Once you have Fiji and Ilastik installed you can install the update sites:

    - **Ilastik ImageJ Plugin**: [Ilastik ImageJ Plugin](https://www.ilastik.org/documentation/fiji_export/plugin) allows for easy integration of some Ilastik workflows into Fiji. This is how the ilastik pixel and object classification workflows are used in the Cell Quantification Toolkit. Install it through their Fiji update site.

    - **Cell Quantification Toolkit**: The main update site for the toolkit.

    1. Open Fiji
    2. Go to `Help > Update... > Manage Update Sites`
    3. Search for `ilastik` 
        - URL: `https://sites.imagej.net/ilastik/`
    4. Press the check box next to `ilastik` 
    5. Press `Add Unlisted Site` and enter the details:
        - Name: `Cell Quantification Toolkit`
        - URL: `https://sites.imagej.net/cell-quantifier-workflows/`
    6. Press `Apply and Close` 
    7. Press `Apply Changes` and restart Fiji
    8. Configure the Ilastik excutable location:
        - Select `Plugins > ilastik > Configure ilastik executable location`
        - Enter the path to the Ilastik executable file (e.g. `/Applications/ilastik-1.4.1.post1-arm64-OSX.app/Contents/MacOS/ilastik` or `C:\Program Files\ilastik\bin\ilastik.exe`)
        - Press `OK`
    
    Now you're ready to go!



## Quick Start

1. **Create/Open Project** — Select a folder (will create project structure if new)
2. **Import Images** — Add images to the `Images/` folder
3. **Define ROIs** — Use the ROI Editor to draw analysis regions
4. **Select/Create a Workflow** — The **Current Workflow** panel shows a list of all workflows; click one to select it, or use **New... / Edit... / Duplicate... / Delete...**. When creating one, choose the type: *Automated cell classification* or *Manual counting*.
5. **Run Quantification** — Select images and click **Run Quantification**:
    - *Automated:* runs segmentation + classification only and caches the results (no CSV yet), then opens the **Results** tab.
    - *Manual:* opens the counting tool — pick a class, click the cells, then **Save & Close**.
6. **Review & Export (Results tab)** —
    - *Automated:* adjust post-processing (watershed, min size, etc.) with live preview, then **Export results (all images)** to write the outlines and CSV. One setting applies to every image in the run.
    - *Manual:* review the points and click **Export counts (all images)** to write the counts CSV.

## Project Structure

Each quantification run is self-contained: its results, cell outlines, and settings live together in a timestamped folder under `Runs/`. Re-running an analysis never overwrites a previous one, so you can compare runs side by side.

```
MyProject/
├── Images/                 # Source images
├── ROI_Files/              # ROI selections (.zip)
├── Probabilities/          # Workflow intermediate outputs (shared across runs)
├── Runs/                   # One folder per quantification run
│   └── 20260728_143022_871000/
│       ├── Cell_Selections/    # Detected cell outlines (.zip)
│       ├── 20260728_results.csv
│       └── run_metadata.json   # Workflow, date, and settings for this run
├── temp/                   # Temporary processing files (auto-cleaned)
└── project.json            # Project database (images, ROIs, templates)
```

> [!NOTE]
> Projects created by earlier versions (with `Final_Cell_Selections/`, `Results_DB.csv`, and `processing_log.json` at the project root) are detected on open. The toolkit offers to migrate them to the run-based layout, which removes those old result files — your images and ROIs are preserved.

## Creating Workflows

You can build a workflow in the editor with no code (New... in the Current
Workflow panel): choose the type, then either pick a segmentation + classification
provider and a class map (automated), or just define the classes to count
(manual). Developers can add entirely new automated methods (e.g. StarDist,
intensity-cutoff classification) by dropping a provider into `steps/`.

See [`docs/creating_workflows.md`](docs/creating_workflows.md) for both.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — System architecture and design
- [`docs/creating_workflows.md`](docs/creating_workflows.md) — Workflow development guide
- [`docs/quantification_overview.md`](docs/quantification_overview.md) — Processing pipeline details
