// ============================================================================
// RGB_to_RGB_plus_Saturation.ijm
//
// Builds a 4-channel image  (channel 1=R, 2=G, 3=B, 4=Saturation)  from an RGB
// brightfield image, to feed ilastik pixel/object classification. The extra
// Saturation channel exposes the brown-vs-gray difference (saturated DAB cFos
// vs desaturated CtB) that a near-degenerate color deconvolution misses, while
// keeping the original R/G/B so the classifier still has full color+texture.
//
// TWO COMMANDS (Plugins > Macros > run this file, or install it):
//   "RGB to RGB+Saturation Stack"        - processes the active RGB image
//   "Batch: RGB to RGB+Saturation Stack" - processes every RGB image in a folder
//
// NOTE: train AND predict the classifier on the same 4-channel layout, and use
// this on every image so channels stay consistent across the dataset.
//
// INPUT TYPES: RGB (24-bit) and 8-bit COLOR (indexed) both work -- indexed
// images are converted to RGB automatically. A true 8-bit GRAYSCALE image has
// no color, so R=G=B and the Saturation channel comes out ~blank; if you see
// that, you need the original color scans rather than this macro.
// ============================================================================

macro "RGB to RGB+Saturation Stack" {
    if (nImages == 0)
        exit("Open an image first.");

    base = stripExt(getTitle());
    id = makeRGBS(base, false);   // keep the user's original image open
    selectImage(id);

    if (getBoolean("Save the RGB+Saturation stack to disk now?")) {
        outDir = getDirectory("Choose output folder");
        saveAs("Tiff", outDir + base + "_RGBS.tif");
    }
}

macro "Batch: RGB to RGB+Saturation Stack" {
    inDir  = getDirectory("Choose INPUT folder (RGB images)");
    outDir = getDirectory("Choose OUTPUT folder");
    list = getFileList(inDir);

    setBatchMode(true);
    n = 0;
    for (i = 0; i < list.length; i++) {
        f = list[i];
        if (endsWith(f, "/")) continue;      // skip subfolders
        if (!isImageFile(f))  continue;

        open(inDir + f);
        base = stripExt(f);
        id = makeRGBS(base, true);           // close source in batch
        selectImage(id);
        saveAs("Tiff", outDir + base + "_RGBS.tif");
        close();
        n++;
    }
    setBatchMode(false);
    showMessage("Done. Processed " + n + " image(s) to:\n" + outDir);
}

// ---------------------------------------------------------------------------
// Build the R,G,B + Saturation composite from the active RGB image.
// Leaves the merged 4-channel image active and returns its imageID.
// closeSource=true closes the original RGB image (for batch use).
// ---------------------------------------------------------------------------
function makeRGBS(base, closeSource) {
    origID = getImageID();

    // Work from an RGB copy. 8-bit COLOR (indexed) and other non-RGB types are
    // converted with "RGB Color" so HSB Stack / Split Channels will run. (A true
    // 8-bit grayscale source becomes R=G=B here, with ~blank Saturation.)
    selectImage(origID);
    run("Duplicate...", "title=__rgbsrc");
    if (bitDepth() != 24)
        run("RGB Color");
    workID = getImageID();

    if (closeSource) { selectImage(origID); close(); }

    // R, G, B from a duplicate (Split Channels consumes the duplicate)
    selectImage(workID);
    run("Duplicate...", "title=__rgb");
    run("Split Channels");               // -> "__rgb (red/green/blue)"

    // Saturation from an HSB duplicate (slice 2 of the HSB stack)
    selectImage(workID);
    run("Duplicate...", "title=__hsb");
    run("HSB Stack");                    // slices: 1=Hue, 2=Saturation, 3=Brightness
    setSlice(2);
    run("Duplicate...", "title=__sat");  // grab Saturation slice only
    selectWindow("__hsb"); close();

    selectImage(workID); close();        // done with the RGB work copy

    // Combine into a 4-channel composite hyperstack
    run("Merge Channels...",
        "c1=[__rgb (red)] c2=[__rgb (green)] c3=[__rgb (blue)] c4=[__sat] create");
    rename(base + "_RGBS");
    return getImageID();
}

function stripExt(name) {
    dot = lastIndexOf(name, ".");
    if (dot >= 0) return substring(name, 0, dot);
    return name;
}

function isImageFile(name) {
    lc = toLowerCase(name);
    return endsWith(lc, ".tif")  || endsWith(lc, ".tiff") ||
           endsWith(lc, ".jpg")  || endsWith(lc, ".jpeg") ||
           endsWith(lc, ".png")  || endsWith(lc, ".bmp");
}
