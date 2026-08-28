# ============================================================================
# Export_RGB_plus_Lab_for_Training.py
#
# Produces the SAME 6-channel (R, G, B, L*, a*, b*) images that the
# "... (RGB+Lab)" workflow feeds ilastik at PREDICT time. Use these to TRAIN the
# pixel and object classifiers, so training and prediction channel layouts match
# exactly. (A layout mismatch silently degrades the classifier.)
#
# HOW TO RUN
#   Fiji > File > New > Script...  (Language menu -> Python), open this file, Run.
#   You'll be asked whether to convert the active image or a whole folder.
#
# OUTPUT
#   <name>_RGBLab.tif  -- a 6-channel 32-bit composite, per input image.
#
# The conversion is the shared, canonical one in lib/color_lab.py -- the exact
# same code the pipeline provider uses. Do not reimplement it here.
# ============================================================================
import os
import sys

from ij import IJ
from ij.io import DirectoryChooser
from javax.swing import JOptionPane

# Make lib/color_lab.py importable no matter where the Script Editor runs from.
_ROOT = os.path.join(IJ.getDirectory("plugins"), "Cell_Quantification_Toolkit")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from lib.color_lab import rgb_to_rgblab_imp, rgb_to_rgblab_file  # noqa: E402

_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp")


def _export_active():
    imp = IJ.getImage()
    if imp is None:
        IJ.error("Open an RGB image first.")
        return
    base = os.path.splitext(imp.getTitle())[0]
    outdir = DirectoryChooser("Choose output folder for the RGB+Lab image").getDirectory()
    if not outdir:
        return
    comp = rgb_to_rgblab_imp(imp, title=base + "_RGBLab")
    outpath = os.path.join(outdir, base + "_RGBLab.tif")
    IJ.saveAsTiff(comp, outpath)
    IJ.log("Wrote " + outpath)


def _export_folder():
    ind = DirectoryChooser("Choose INPUT folder (RGB images)").getDirectory()
    if not ind:
        return
    outd = DirectoryChooser("Choose OUTPUT folder").getDirectory()
    if not outd:
        return
    n = 0
    for f in sorted(os.listdir(ind)):
        if not f.lower().endswith(_EXTS):
            continue
        base = os.path.splitext(f)[0]
        outp = os.path.join(outd, base + "_RGBLab.tif")
        try:
            rgb_to_rgblab_file(os.path.join(ind, f), outp)
            n += 1
        except Exception as e:
            IJ.log("Skip {}: {}".format(f, e))
    IJ.log("Done. Wrote {} RGB+Lab image(s) to {}".format(n, outd))


_opts = ["Active image", "Whole folder (batch)"]
_choice = JOptionPane.showOptionDialog(
    None, "Export RGB+Lab images for ilastik training",
    "RGB+Lab Export", JOptionPane.DEFAULT_OPTION, JOptionPane.QUESTION_MESSAGE,
    None, _opts, _opts[0])

if _choice == 0:
    _export_active()
elif _choice == 1:
    _export_folder()
