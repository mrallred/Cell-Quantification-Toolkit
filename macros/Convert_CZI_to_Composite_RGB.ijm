// ============================================================================
// Convert_CZI_to_Composite_RGB.ijm
//
// Batch-converts Zeiss .czi images to composite RGB TIFFs for the Cell
// Quantification Toolkit. Each .czi is opened with Bio-Formats as a composite
// and flattened to a 24-bit RGB with Fiji's built-in "Stack to RGB".
//
//   "Batch: CZI to Composite RGB TIFF" - pick an INPUT and an OUTPUT folder;
//                                        every .czi is written as <name>.tif
// ============================================================================

macro "Batch: CZI to Composite RGB TIFF" {
    showMessage("Step 1 of 2",
        "Next, choose the INPUT folder that contains your .czi images.");
    inDir  = getDirectory("Choose INPUT folder (.czi images)");

    showMessage("Step 2 of 2",
        "Now choose the OUTPUT folder where the RGB .tif files will be saved.");
    outDir = getDirectory("Choose OUTPUT folder");

    list = getFileList(inDir);

    setBatchMode(true);
    n = 0;
    for (i = 0; i < list.length; i++) {
        f = list[i];
        if (!endsWith(toLowerCase(f), ".czi")) continue;

        run("Bio-Formats Importer", "open=[" + inDir + f +
            "] color_mode=Composite view=Hyperstack stack_order=XYCZT");
        run("Stack to RGB");

        base = substring(f, 0, lastIndexOf(f, "."));
        saveAs("Tiff", outDir + base + ".tif");
        close("*");
        n++;
    }
    setBatchMode(false);
    showMessage("Done. Converted " + n + " .czi file(s) to:\n" + outDir);
}
