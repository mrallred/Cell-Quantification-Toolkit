"""
Standalone post-processing step.

`run_post` turns a class-label image (ilastik "Object Predictions" or any
instance/class-label image) into per-class outlines + counts, applying the
tunable post-processing (ROI mask, per-class threshold, optional watershed,
particle analysis with min area / circularity / edge exclusion).

It is deliberately free of `self`/`settings` so it can be called by both the
batch pipeline runner and the interactive results viewer with the same code.
Outlines are returned in CROP coordinates (the caller translates by the ROI
offset). The input image is NOT closed here - the caller owns its lifecycle.
"""
from ij import IJ
from ij.measure import ResultsTable, Measurements
from ij.plugin import ImageCalculator
from ij.plugin.filter import ParticleAnalyzer
from ij.plugin.frame import RoiManager

from java.lang import System
from java.awt import Color


DEFAULT_POST = {
    'apply_watershed': True,
    'exclude_edges': True,
    'min_cell_size': 10,
    'min_circularity': 0.0,
}


def awt_color(rgb):
    """[r, g, b] list -> java.awt.Color (defaults to yellow)."""
    try:
        return Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return Color.YELLOW


def run_post(result_imp, roi, post_params, cell_classes):
    """
    Args:
        result_imp:  ImagePlus of the class-label image (cropped to the ROI).
        roi:         the ROI (area) used for the crop; masked at origin here.
        post_params: dict with apply_watershed, exclude_edges, min_cell_size,
                     min_circularity (missing keys fall back to DEFAULT_POST).
        cell_classes: list of {label, key, display, color} dicts to count.

    Returns:
        dict with 'outlines' (list of Roi in crop coords) plus, per class,
        '<key>_count' and '<key>_total_area'.
    """
    p = dict(DEFAULT_POST)
    p.update(post_params or {})

    width = result_imp.getWidth()
    height = result_imp.getHeight()

    # ROI mask (at origin) AND-ed into the label image so only ROI pixels remain.
    # Fill the mask explicitly with 255 via the processor rather than IJ "Fill",
    # which uses the global foreground colour: if that colour isn't 255, the
    # bitwise AND corrupts multi-bit class labels (e.g. label 2 -> 0, label 3 ->
    # 1), which silently drops CtB / cFos+CtB and inflates cFos.
    mask_title = "mask_" + str(System.nanoTime())
    mask_imp = IJ.createImage(mask_title, "8-bit black", width, height, 1)
    roi_clone = roi.clone()
    roi_clone.setLocation(0, 0)
    mp = mask_imp.getProcessor()
    mp.setColor(255)
    mp.fill(roi_clone)  # fill the ROI region with 255 regardless of global state

    ic = ImageCalculator()
    ic.run("AND", result_imp, mask_imp)
    mask_imp.changes = False
    mask_imp.close()

    apply_watershed = p.get('apply_watershed', True)
    exclude_edges = p.get('exclude_edges', True)
    min_circularity = p.get('min_circularity', 0.0)
    min_cell_size = p.get('min_cell_size', 10)

    # ADD_TO_MANAGER collects outlines into the (hidden) RoiManager without
    # opening a "Drawing of..." window per class that the user must close.
    options = ParticleAnalyzer.ADD_TO_MANAGER
    if exclude_edges:
        options |= ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
    measurements = Measurements.AREA

    results = {'outlines': []}

    for cls in cell_classes:
        label = cls.get('label')
        key = cls.get('key')
        display = cls.get('display', key)
        color = awt_color(cls.get('color'))

        class_imp = result_imp.duplicate()
        class_imp.setTitle("class_{}_{}".format(key, System.nanoTime()))

        IJ.setThreshold(class_imp, label, label)
        IJ.run(class_imp, "Convert to Mask", "")

        if apply_watershed:
            IJ.run(class_imp, "Watershed", "")

        rm = RoiManager(True)
        rt = ResultsTable()
        pa = ParticleAnalyzer(options, measurements, rt, min_cell_size, float('inf'), min_circularity, 1.0)
        pa.setRoiManager(rm)
        pa.analyze(class_imp)

        count = rt.getCounter()
        total_area = 0
        if count > 0:
            area_col_index = rt.getColumnIndex("Area")
            if area_col_index != -1:
                area_col = rt.getColumn(area_col_index)
                if area_col is not None:
                    total_area = sum(area_col)

        class_outlines = rm.getRoisAsArray()
        rm.reset()
        rm.close()
        if class_outlines is None:
            class_outlines = []

        for idx, outline in enumerate(class_outlines):
            outline.setName("{}_{}".format(display, idx + 1))
            outline.setProperty("cell_class", key)
            outline.setStrokeColor(color)
            results['outlines'].append(outline)

        results[key + '_count'] = count
        results[key + '_total_area'] = total_area

        class_imp.changes = False
        class_imp.close()

    return results
