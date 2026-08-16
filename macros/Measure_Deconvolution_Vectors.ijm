// ============================================================================
// Measure_Deconvolution_Vectors.ijm
//
// Interactive helper for the Cell Quantification Toolkit.
// Measures color-deconvolution stain vectors from your own image so you can
// paste them into Fiji's  Image > Colour Deconvolution  (or Colour
// Deconvolution 2)  >  "User values".
//
// It uses PER-CHANNEL background as I0, which white-balances the optical-
// density math automatically (important when the slide background is tan,
// not white).
//
// HOW TO USE
//   1. Open your RGB brightfield image (24-bit).
//   2. Run this macro (Plugins > Macros > Run..., or drag into the script editor).
//   3. When prompted, use the RECTANGLE tool to draw a box over:
//        - clean, cell-free BACKGROUND (for I0), then
//        - a PURE region of each stain (only cFos, only CtB, ...).
//      Draw the requested number of sample boxes; they are averaged.
//   4. Read the vectors printed to the Log window and paste them into the
//      plugin. Leave the last stain as 0, 0, 0 so the plugin computes the
//      orthogonal residual.
//
// TIP: measure one matrix and reuse the SAME numbers for every image in the
//      dataset, so object-classifier intensities stay comparable.
// ============================================================================

macro "Measure Deconvolution Vectors" {

    if (nImages == 0)
        exit("Open your RGB image first, then run this macro.");
    if (bitDepth() != 24)
        exit("Front image must be RGB (24-bit).");

    srcID = getImageID();

    Dialog.create("Deconvolution vector measuring");
    Dialog.addNumber("Number of stains:", 2);
    Dialog.addNumber("Samples to average per region:", 3);
    Dialog.addMessage("You'll draw one rectangle per sample:\nfirst for background, then for each stain.");
    Dialog.show();
    nStains = Dialog.getNumber();
    nSamp   = maxOf(1, Dialog.getNumber());
    if (nStains < 1) exit("Need at least one stain.");

    // ---- Background (per-channel I0) ----
    I0 = measureAveraged(srcID, nSamp,
        "BACKGROUND: draw a rectangle over clean, cell-free background.");
    for (c = 0; c < 3; c++)
        if (I0[c] < 1) I0[c] = 1;   // guard against log(0)

    results    = "=== Deconvolution stain vectors ===\n";
    results   += "Background I0 (R,G,B): " +
                 d2s(I0[0],2) + ", " + d2s(I0[1],2) + ", " + d2s(I0[2],2) + "\n\n";
    userValues = "";

    // ---- Each stain ----
    for (k = 1; k <= nStains; k++) {
        nm = getString("Name for stain " + k + ":", "stain" + k);
        m  = measureAveraged(srcID, nSamp,
                nm + ": draw a rectangle over a PURE " + nm + " region.");
        v  = odVector(m, I0);
        line = d2s(v[0],5) + ", " + d2s(v[1],5) + ", " + d2s(v[2],5);
        results    += nm + " vector (R,G,B): " + line + "\n";
        userValues += "Stain " + k + "  [" + nm + "] : " + line + "\n";
    }
    userValues += "Stain " + (nStains + 1) +
                  "  [residual]: 0, 0, 0   (leave as zeros)\n";

    // ---- Output ----
    print("\\Clear");
    print(results);
    print("--- Paste into Colour Deconvolution > User values ---");
    print(userValues);

    selectImage(srcID);
}

// Average per-channel RGB mean over nSamp user-drawn rectangles.
function measureAveraged(srcID, nSamp, prompt) {
    sr = 0; sg = 0; sb = 0; got = 0;
    setTool("rectangle");
    while (got < nSamp) {
        selectImage(srcID);
        waitForUser("Sample " + (got + 1) + " of " + nSamp + "\n \n" +
                    prompt + "\n \nDraw a rectangle, then click OK.");
        if (selectionType() != 0) {
            showMessage("Use the RECTANGLE tool and draw a box.");
            continue;
        }
        m = patchMeanRGB(srcID);
        sr += m[0]; sg += m[1]; sb += m[2];
        got++;
    }
    return newArray(sr / nSamp, sg / nSamp, sb / nSamp);
}

// Mean R,G,B inside the current rectangular selection on srcID.
function patchMeanRGB(srcID) {
    selectImage(srcID);
    run("Duplicate...", "title=__patch");
    run("Split Channels");
    selectWindow("__patch (red)");   getStatistics(a, r);
    selectWindow("__patch (green)"); getStatistics(a, g);
    selectWindow("__patch (blue)");  getStatistics(a, b);
    close("__patch (red)"); close("__patch (green)"); close("__patch (blue)");
    return newArray(r, g, b);
}

// Normalized optical-density vector using per-channel I0.
function odVector(mean, I0) {
    od = newArray(3);
    for (c = 0; c < 3; c++) {
        m = mean[c];
        if (m < 1)      m = 1;        // avoid log(0)
        if (m > I0[c])  m = I0[c];    // a stain can't be brighter than background
        od[c] = -log(m / I0[c]) / log(10);
    }
    n = sqrt(od[0]*od[0] + od[1]*od[1] + od[2]*od[2]);
    if (n == 0) n = 1;
    return newArray(od[0]/n, od[1]/n, od[2]/n);
}
