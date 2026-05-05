# Cell Quantification Toolkit

A Fiji plugin for project-based, ROI-specific, and automated cell detection and quantification in microscopy images. 

So far I've only implemented a cell detection workflow for brightfield DAB-stained images, which utilizes Ilastik pixel and object classification. However, the quantification module is designed to be easily extensible to allow development of custom workflows for different types of microscopy images.

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
4. **Run Quantification** — Select images, choose a workflow, and process
5. **View Results** — Results saved to `Results_DB.csv`

## Project Structure

```
MyProject/
├── Images/                 # Source images
├── ROI_Files/              # ROI selections (.zip)
├── Probabilities/          # Workflow intermediate outputs
├── Final_Cell_Selections/  # Detected cell outlines
├── project.json            # Project database
├── Results_DB.csv          # Quantification results
└── processing_log.json     # Processing metadata
```

## Creating Custom Workflows

See [`docs/creating_workflows.md`](docs/creating_workflows.md) for a guide on building your own quantification workflows in a python script.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — System architecture and design
- [`docs/creating_workflows.md`](docs/creating_workflows.md) — Workflow development guide
- [`docs/quantification_overview.md`](docs/quantification_overview.md) — Processing pipeline details
