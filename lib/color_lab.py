"""
Shared RGB -> (R, G, B, L*, a*, b*) conversion for the RGB+Lab workflow.

ONE canonical implementation, imported by BOTH:
  - steps/ilastik_pixel_lab.py                        (pipeline segmentation provider)
  - macros/Export_RGB_plus_Lab_for_Training.py        (training-image export)

so the channel layout used to TRAIN the ilastik models is byte-for-byte the same
as the layout produced at PREDICT time. If these ever diverge, the classifier
silently degrades -- keep this file as the single source of truth.

The L*a*b* channels come from ImageJ's ij.process.ColorSpaceConverter (the same
CIELAB conversion behind Image > Type > "Lab Stack", but called as a method that
returns a new ImagePlus -- the menu command itself acts on the *active* image and
no-ops on an off-screen duplicate).

Output: a 6-channel 8-BIT composite, channels in this FIXED order/encoding:
    1  R     0..255   (native)
    2  G     0..255   (native)
    3  B     0..255   (native)
    4  L*    L*(0..100) * 2.55  -> 0..255
    5  a*    a* + 128           -> ~0..255   green(-) / red(+)
    6  b*    b* + 128           -> ~0..255   blue(-)  / yellow(+)

The Lab->8-bit mapping is a FIXED linear transform (multiply/add then cast WITHOUT
auto-scaling), identical for every image -- not a per-image contrast stretch,
which would be non-stationary across slides. 8-bit is ~1 unit/level in a*/b*
(about the CIELAB perceptual JND), so effectively no usable information is lost,
and it keeps files at ~2x the RGB size instead of ~8x for 32-bit float.
"""
from ij import IJ, ImagePlus, ImageStack, CompositeImage
from ij.process import ColorSpaceConverter

CHANNEL_LABELS = ["R", "G", "B", "L*", "a*", "b*"]


def rgb_to_rgblab_imp(imp, title=None):
    """Return a NEW 6-channel 8-bit composite (R,G,B,L*,a*,b*) from an RGB imp.
    The input imp is not modified."""
    w = imp.getWidth()
    h = imp.getHeight()

    # Work on a copy; ensure it is RGB so the conversions apply.
    dup = imp.duplicate()
    if dup.getBitDepth() != 24:
        IJ.run(dup, "RGB Color", "")

    # R, G, B as a 3-slice 8-bit stack.
    rgb = dup.duplicate()
    IJ.run(rgb, "RGB Stack", "")
    rs = rgb.getStack()

    # L*, a*, b* as a 3-slice 32-bit stack via ImageJ's ColorSpaceConverter
    # (returns a new ImagePlus -- no active-image dependency).
    lab = ColorSpaceConverter().RGBToLab(dup)
    ls = lab.getStack()

    # Fixed linear map to 8-bit, done with native processor math + cast WITHOUT
    # auto-scaling (convertToByte(False) clamps to 0..255), so the encoding is
    # deterministic and identical across images.
    lfp = ls.getProcessor(1).convertToFloat(); lfp.multiply(2.55)   # L* 0..100 -> 0..255
    afp = ls.getProcessor(2).convertToFloat(); afp.add(128.0)       # a* -> +128
    bfp = ls.getProcessor(3).convertToFloat(); bfp.add(128.0)       # b* -> +128

    out_stack = ImageStack(w, h)
    out_stack.addSlice("R",  rs.getProcessor(1).duplicate())
    out_stack.addSlice("G",  rs.getProcessor(2).duplicate())
    out_stack.addSlice("B",  rs.getProcessor(3).duplicate())
    out_stack.addSlice("L*", lfp.convertToByte(False))
    out_stack.addSlice("a*", afp.convertToByte(False))
    out_stack.addSlice("b*", bfp.convertToByte(False))

    dup.close()
    rgb.close()
    lab.close()

    out = ImagePlus(title or "RGBLab", out_stack)
    out.setDimensions(6, 1, 1)                 # 6 channels, 1 z, 1 t
    return CompositeImage(out, CompositeImage.GRAYSCALE)


def rgb_to_rgblab_file(in_path, out_path):
    """Open an RGB image, convert, save the 6-channel stack as a TIFF.
    Returns out_path."""
    imp = IJ.openImage(in_path)
    if imp is None:
        raise Exception("Could not open image: " + str(in_path))
    try:
        comp = rgb_to_rgblab_imp(imp, title="RGBLab")
        IJ.saveAsTiff(comp, out_path)
    finally:
        imp.close()
    return out_path
