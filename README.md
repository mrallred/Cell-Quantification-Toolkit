# Cell Quantification Toolkit

A Fiji plugin for automated cell detection and quantification in microscopy images.

## Features

- **Project-based workflow** — Organize images, ROIs, and results in folder-based projects
- **ROI management** — Draw and edit regions of interest with easy prefilled template names
- **Batch processing** — Automated quantification across multiple images and ROIs
- **Plugin workflows** — Extensible architecture for custom detection algorithms
- **Resume capability** — Intermediate files preserved for interrupted processing
- **Full traceability** — Every result linked to processing settings via JSON metadata

## Installation

1. Copy the `Cell_Quantification_Toolkit` folder to your Fiji `plugins/` directory
2. Restart Fiji
3. Access via **Plugins → Cell Quantification Toolkit**

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
