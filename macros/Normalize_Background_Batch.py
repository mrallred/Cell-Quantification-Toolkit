# ============================================================================
# Normalize_Background_Batch.py
#
# Equalises the illumination/white-balance differences between brightfield
# sections, so one classifier can span images acquired on different days.
#
# WHY
#   In transmission brightfield, I = I0 * exp(-OD). I0 is the ILLUMINATION -- an
#   instrument property, not a property of the tissue. If the white balance or
#   lamp changes between sessions, I0 changes and every downstream measurement
#   moves with it, even though the specimen is identical. Dividing by I0 removes
#   a term that was never part of the sample.
#
#   This is standard stain-normalisation practice (cf. Reinhard 2001, Macenko
#   2009, Vahadane 2016), not a cosmetic adjustment.
#
# WHAT IT DOES
#   1. Estimates each image's background from a single spatial population of
#      "unstained tissue" pixels, and averages R,G,B over exactly those pixels.
#   2. Scales each channel so that background lands on ONE common target value,
#      identical for every image in the batch.
#
#   After this, unstained tissue reads neutral grey everywhere, so any colour
#   left in the image is stain. The stains themselves are untouched.
#
# WHY THE BACKGROUND IS FOUND AUTOMATICALLY, NOT BY A DRAWN ROI
#   A hand-picked reference is what causes this problem in the first place: an
#   operator clicking auto-white-balance on an operator-chosen spot is exactly
#   why two slides from one session can disagree. The estimate must follow the
#   same procedure for every image with no human input, or the correction
#   inherits the variability it is meant to remove.
#
# WHY NOT THE BLANK SLIDE
#   The off-tissue slide is usually clipped at 255,255,255 and carries no colour
#   information, so it cannot serve as I0. The reference here is the lighter,
#   least-stained TISSUE instead. Note what that means: this normalises for
#   COMPARABILITY between sections, it is not an absolute I0 calibration. For
#   true optical density you need an unclipped blank field, which means dropping
#   the exposure slightly at acquisition.
#
# HOW TO RUN
#   Fiji > File > New > Script...  (Language menu -> Python), open this file, Run.
#   Point it at a folder of RGB TIFFs and an EMPTY output folder.
#
#   Leave "Target background" at 0 to run two passes: survey every image, take
#   the median background as the target, then apply. That keeps the target
#   inside the data's own range so corrections stay small and nothing clips.
#   Set it explicitly to reuse a previous batch's target -- do that when adding
#   new sections to a dataset you have already processed, so old and new images
#   land on the same scale.
#
# OUTPUT
#   <output>/<name>.tif                     -- normalised, same size and bit depth
#   <output>/normalisation_report.csv       -- per image: background, gains,
#                                              colour cast, predicted clipping
#
#   Read the report before trusting the result. Two things to check:
#     - gains near 1.0 for images you believe were fine (this one should barely
#       touch them; if it does, the recipe is too aggressive)
#     - clip% at or near zero (a channel pushed past 255 is unrecoverable)
#
# AFTERWARDS -- two things that WILL bite otherwise
#   1. Every .ilp trained on un-normalised pixels is now invalid. Retrain.
#   2. The toolkit's prediction cache will NOT invalidate itself: the cache
#      signature covers classifier names and append_lab, not image content.
#      Delete Probabilities/ or tick "Force recalculate" on the first run.
#
#   Keep the originals. Normalisation is not reversible once you overwrite.
# ============================================================================
import os
import csv
import math

from ij import IJ, ImagePlus
from ij.gui import GenericDialog
from ij.io import DirectoryChooser

# Pixels this bright are off-tissue blank slide (clipped white), not background.
BLANK_LUMA = 250

# Sample every Nth pixel in x and y when estimating. 24 gives ~500k samples on a
# 15k x 19k section -- far more than needed, and fast.
DEFAULT_STRIDE = 24

# The background population: this luminance percentile band of the tissue.
# Below 0.60 you start averaging in stained cells; above 0.85 you drift into the
# tissue edge and blank slide.
BAND_LO, BAND_HI = 0.60, 0.85

_EXTS = ('.tif', '.tiff')


def _luma_hist(px, w, h, stride):
    """Luminance histogram over sampled tissue pixels (blank slide excluded)."""
    hist = [0] * 256
    n = 0
    for y in range(0, h, stride):
        base = y * w
        for x in range(0, w, stride):
            v = px[base + x]
            L = (((v >> 16) & 255) + ((v >> 8) & 255) + (v & 255)) // 3
            if L >= BLANK_LUMA:
                continue
            hist[L] += 1
            n += 1
    return hist, n


def _band_limits(hist, n, lo, hi):
    """Luminance cutoffs bounding the [lo, hi] percentile band of the tissue."""
    lo_t, hi_t = lo * n, hi * n
    run = 0
    l_lo, l_hi = 0, 255
    for v in range(256):
        run += hist[v]
        if run <= lo_t:
            l_lo = v
        if run <= hi_t:
            l_hi = v
    return l_lo, l_hi


def estimate_background(imp, stride):
    """
    Mean R,G,B over one spatial population of background-tissue pixels.

    Taking a per-channel statistic independently does NOT work here: on an image
    with a strong colour cast the channels' histograms peak on different pixels,
    and the resulting triplet describes no real population. Selecting the pixels
    once by luminance and then averaging keeps the channels coupled.
    """
    ip = imp.getProcessor()
    w, h = imp.getWidth(), imp.getHeight()
    px = ip.getPixels()

    hist, n = _luma_hist(px, w, h, stride)
    if n == 0:
        return None
    l_lo, l_hi = _band_limits(hist, n, BAND_LO, BAND_HI)

    sr = sg = sb = 0
    cnt = 0
    for y in range(0, h, stride):
        base = y * w
        for x in range(0, w, stride):
            v = px[base + x]
            r = (v >> 16) & 255
            g = (v >> 8) & 255
            b = v & 255
            L = (r + g + b) // 3
            if L < l_lo or L > l_hi:
                continue
            sr += r
            sg += g
            sb += b
            cnt += 1
    if cnt == 0:
        return None
    return [sr / float(cnt), sg / float(cnt), sb / float(cnt)]


def predicted_clip(imp, gains, stride):
    """Percent of tissue pixels each channel would push past 255."""
    ip = imp.getProcessor()
    w, h = imp.getWidth(), imp.getHeight()
    px = ip.getPixels()
    over = [0, 0, 0]
    tot = 0
    step = stride * 2
    for y in range(0, h, step):
        base = y * w
        for x in range(0, w, step):
            v = px[base + x]
            c = [(v >> 16) & 255, (v >> 8) & 255, v & 255]
            if (c[0] + c[1] + c[2]) // 3 >= BLANK_LUMA:
                continue
            tot += 1
            for i in range(3):
                if c[i] * gains[i] > 255.5:
                    over[i] += 1
    return [100.0 * o / max(1, tot) for o in over]


def apply_gains(imp, gains):
    """Scale each channel in place. Done through ImageJ's own RGB-stack ops --
    a per-pixel loop in Jython on a 290-megapixel image would take minutes."""
    cal = imp.getCalibration().copy()
    IJ.run(imp, "RGB Stack", "")
    for i, g in enumerate(gains):
        imp.setSlice(i + 1)
        IJ.run(imp, "Multiply...", "value={:.6f} slice".format(g))
    imp.setSlice(1)
    IJ.run(imp, "RGB Color", "")
    imp.setCalibration(cal)
    return imp


def _cast(bg):
    """Red-minus-blue in optical density: >0 is a blue cast, <0 is warm/tan."""
    od = [-math.log10((v + 1.0) / 256.0) for v in bg]
    return od[0] - od[2]


def main():
    ind = DirectoryChooser("Choose the INPUT folder (RGB TIFFs)").getDirectory()
    if not ind:
        return
    outd = DirectoryChooser("Choose the OUTPUT folder (must be empty)").getDirectory()
    if not outd:
        return
    if os.path.normpath(ind) == os.path.normpath(outd):
        IJ.error("Normalise background",
                 "Output folder must differ from the input folder.\n"
                 "Keep your originals -- this is not reversible.")
        return

    gd = GenericDialog("Normalise background")
    gd.addNumericField("Target background (0 = median of this batch):", 0, 0)
    gd.addNumericField("Sampling stride (px):", DEFAULT_STRIDE, 0)
    gd.addMessage(
        "Every image is scaled so its unstained tissue lands on the SAME\n"
        "target. Use one target for a whole dataset -- if you later add\n"
        "sections, set the target explicitly to the value in the report\n"
        "rather than letting it be recomputed, or old and new images end\n"
        "up on different scales.\n\n"
        "The background is found automatically and identically for every\n"
        "image. That is deliberate: a hand-picked reference reintroduces\n"
        "the operator variability this is meant to remove.")
    gd.showDialog()
    if gd.wasCanceled():
        return
    target = float(gd.getNextNumber())
    stride = max(1, int(gd.getNextNumber()))

    files = sorted(f for f in os.listdir(ind) if f.lower().endswith(_EXTS))
    if not files:
        IJ.error("Normalise background", "No TIFFs in " + ind)
        return

    # ---- pass 1: survey ----
    IJ.log("")
    IJ.log("Normalise background: surveying {} image(s)...".format(len(files)))
    bgs = {}
    for f in files:
        imp = IJ.openImage(os.path.join(ind, f))
        if imp is None:
            IJ.log("  skip (unreadable): " + f)
            continue
        if imp.getType() != ImagePlus.COLOR_RGB:
            IJ.log("  skip (not RGB): " + f)
            imp.close()
            continue
        bg = estimate_background(imp, stride)
        imp.close()
        if bg is None:
            IJ.log("  skip (no tissue found): " + f)
            continue
        bgs[f] = bg
        IJ.log("  {}  bg = {:.0f},{:.0f},{:.0f}   cast = {:+.3f}".format(
            f, bg[0], bg[1], bg[2], _cast(bg)))

    if not bgs:
        IJ.error("Normalise background", "No usable RGB images found.")
        return

    if target <= 0:
        lumas = sorted(sum(b) / 3.0 for b in bgs.values())
        target = lumas[len(lumas) // 2]
        IJ.log("Target = {:.1f} (median background of this batch)".format(target))
    else:
        IJ.log("Target = {:.1f} (specified)".format(target))

    # ---- pass 2: apply ----
    rows = []
    for f in sorted(bgs.keys()):
        bg = bgs[f]
        gains = [target / v for v in bg]
        imp = IJ.openImage(os.path.join(ind, f))
        if imp is None:
            continue
        clip = predicted_clip(imp, gains, stride)
        apply_gains(imp, gains)
        outp = os.path.join(outd, f)
        IJ.saveAsTiff(imp, outp)
        imp.close()
        rows.append({
            'filename': f,
            'bg_r': round(bg[0], 1), 'bg_g': round(bg[1], 1), 'bg_b': round(bg[2], 1),
            'gain_r': round(gains[0], 4), 'gain_g': round(gains[1], 4),
            'gain_b': round(gains[2], 4),
            'cast_R_minus_B_OD': round(_cast(bg), 4),
            'clip_pct_r': round(clip[0], 3), 'clip_pct_g': round(clip[1], 3),
            'clip_pct_b': round(clip[2], 3),
            'target': round(target, 1),
        })
        IJ.log("  wrote {}   gains {:.3f},{:.3f},{:.3f}   clip% {:.2f},{:.2f},{:.2f}".format(
            f, gains[0], gains[1], gains[2], clip[0], clip[1], clip[2]))

    report = os.path.join(outd, 'normalisation_report.csv')
    headers = ['filename', 'bg_r', 'bg_g', 'bg_b', 'gain_r', 'gain_g', 'gain_b',
               'cast_R_minus_B_OD', 'clip_pct_r', 'clip_pct_g', 'clip_pct_b', 'target']
    with open(report, 'w') as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)

    worst = max((max(r['clip_pct_r'], r['clip_pct_g'], r['clip_pct_b']), r['filename'])
                for r in rows) if rows else (0, '')
    IJ.log("")
    IJ.log("Done: {} image(s) -> {}".format(len(rows), outd))
    IJ.log("Report: " + report)
    IJ.log("Worst clipping: {:.2f}% ({})".format(worst[0], worst[1]))
    IJ.log("Check the report: gains should be near 1.0 for images that were "
           "already well balanced, and clip% should be ~0.")
    IJ.log("Remember: retrain the .ilp models, and clear Probabilities/ "
           "(the cache signature does not track image content).")


main()
