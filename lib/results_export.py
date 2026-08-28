"""
Recompute + write results from cached class-label images.

Post-processing reads the cached `<base>_objects.tif` per ROI, so results can be
re-derived with new post params without re-running ilastik. Shared by the
interactive results viewer's "Save (this image)" and "Apply to all & save"
buttons; mirrors the batch runner's outline/CSV/metadata output so runs stay
consistent.
"""
import os
import csv
import glob
import json
import datetime

from ij import IJ
from ij.plugin.frame import RoiManager

from .postprocess import run_post
from .quantification import _sanitize_filename, _ensure_closed_area_roi
from .workflow_config import cache_dir


def _run_folder(project, run_id):
    return os.path.join(project.paths['runs'], run_id)


def _post_stamp(post_params):
    """Compact, filename-safe encoding of the post-processing settings."""
    p = post_params or {}
    try:
        circ = ("%.2f" % float(p.get('min_circularity', 0.0))).replace('.', 'p')
        return "ws{}_edge{}_min{}_circ{}".format(
            1 if p.get('apply_watershed') else 0,
            1 if p.get('exclude_edges') else 0,
            int(p.get('min_cell_size', 0)),
            circ)
    except Exception:
        return "post"


def stamped_csv_path(project, run_id, stamp):
    """A fresh, non-colliding results CSV path: results_<timestamp>__<stamp>.csv."""
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    return os.path.join(_run_folder(project, run_id),
                        "results_{}__{}.csv".format(ts, stamp))


def result_columns(cell_classes):
    cols = []
    for c in cell_classes:
        cols.append(c.get('key') + '_count')
        cols.append(c.get('key') + '_total_area')
    return cols


def cached_label_path(project, image_obj, safe_roi_name, i, run_id):
    # Scoped to THIS workflow only. We deliberately do NOT fall back to the shared
    # legacy flat cache: it can't be attributed to a workflow, so falling back
    # would make different automated workflows recompute to identical results.
    # Display of old results instead uses each workflow's own saved outlines.
    stem = os.path.splitext(image_obj.filename)[0]
    base = "{}_{}_{}".format(stem, safe_roi_name, i)
    return os.path.join(cache_dir(project, run_id), base + "_objects.tif")


def recompute_image(project, image_obj, post_params, cell_classes, run_id):
    """
    Re-run post-processing for every ROI of an image from its cached label
    images. Returns (absolute_outlines, rows, missing_count).
    """
    outlines = []
    rows = []
    missing = 0

    if not image_obj.has_roi():
        return outlines, rows, missing

    rm = RoiManager(True)
    rm.open(image_obj.roi_path)
    rois = rm.getRoisAsArray()
    rm.close()

    for i, roi in enumerate(rois):
        crop_roi = _ensure_closed_area_roi(roi)
        if crop_roi is None:
            continue
        safe = _sanitize_filename(roi.getName())
        label_path = cached_label_path(project, image_obj, safe, i, run_id)
        if not os.path.exists(label_path):
            missing += 1
            continue
        imp = IJ.openImage(label_path)
        if not imp:
            missing += 1
            continue

        res = run_post(imp, crop_roi, post_params, cell_classes)
        imp.changes = False
        imp.close()

        rx = crop_roi.getBounds().x
        ry = crop_roi.getBounds().y
        for o in res.get('outlines', []):
            b = o.getBounds()
            o.setLocation(b.x + rx, b.y + ry)
            outlines.append(o)

        bregma_str = roi.getProperty("comment")
        try:
            bregma = float(bregma_str) if bregma_str else 0.0
        except (ValueError, TypeError):
            bregma = 0.0

        row = {
            'filename': image_obj.filename,
            'roi_name': roi.getName(),
            'roi_area': roi.getStatistics().area,
            'bregma_value': bregma,
        }
        for c in cell_classes:
            k = c.get('key')
            row[k + '_count'] = res.get(k + '_count', 0)
            row[k + '_total_area'] = res.get(k + '_total_area', 0)
        rows.append(row)

    return outlines, rows, missing


def write_image_outlines(project, run_id, image_obj, outlines):
    cs = os.path.join(_run_folder(project, run_id), 'Cell_Selections')
    if not os.path.isdir(cs):
        os.makedirs(cs)
    stem = os.path.splitext(image_obj.filename)[0]
    path = os.path.join(cs, stem + "_Outlines.zip")
    rm = RoiManager(True)
    if outlines:
        for o in outlines:
            rm.addRoi(o)
        rm.runCommand("Save", path)
    elif os.path.exists(path):
        try:
            os.remove(path)  # no outlines now: drop the stale zip
        except OSError:
            pass
    rm.close()
    return path


def _aggregate(rows, custom_columns):
    """Group by (filename, roi_name): sum area + numeric columns, average bregma."""
    aggregated = {}
    bregma = {}
    for r in rows:
        key = (r['filename'], r['roi_name'])
        if key not in aggregated:
            aggregated[key] = dict(r)
            bregma[key] = {'sum': r['bregma_value'], 'count': 1}
        else:
            aggregated[key]['roi_area'] += r['roi_area']
            for col in custom_columns:
                if col in r and col in aggregated[key]:
                    try:
                        aggregated[key][col] += r[col]
                    except TypeError:
                        pass
            bregma[key]['sum'] += r['bregma_value']
            bregma[key]['count'] += 1
    out = []
    for key, agg in aggregated.items():
        c = bregma[key]['count']
        s = bregma[key]['sum']
        agg['bregma_value'] = "{:.3f}".format((s / c) if c else 0)
        out.append(agg)
    return out


def _results_csv_path(project, run_id):
    """Newest existing results CSV in the run folder (for in-place per-image
    splicing). New exports use stamped_csv_path() instead."""
    rf = _run_folder(project, run_id)
    existing = sorted(glob.glob(os.path.join(rf, 'results_*.csv')) +
                      glob.glob(os.path.join(rf, '*_results.csv')))
    if existing:
        return existing[-1]
    date = datetime.datetime.now().strftime('%Y%m%d')
    return os.path.join(rf, date + '_results.csv')


def write_run_csv(project, run_id, rows, cell_classes, post_params=None):
    custom = result_columns(cell_classes)
    headers = ['filename', 'roi_name', 'roi_area', 'bregma_value'] + custom
    final = _aggregate(rows, custom)
    path = stamped_csv_path(project, run_id, _post_stamp(post_params))
    with open(path, 'w') as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        w.writeheader()
        w.writerows(final)
    return path


def splice_image_into_csv(project, run_id, image_obj, new_rows, cell_classes):
    """Replace just this image's rows in the run CSV, keeping the rest."""
    custom = result_columns(cell_classes)
    headers = ['filename', 'roi_name', 'roi_area', 'bregma_value'] + custom
    path = _results_csv_path(project, run_id)

    kept = []
    if os.path.exists(path):
        with open(path, 'r') as f:
            for row in csv.DictReader(f):
                if row.get('filename') != image_obj.filename:
                    kept.append(row)

    all_rows = kept + _aggregate(new_rows, custom)
    with open(path, 'w') as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        w.writeheader()
        w.writerows(all_rows)
    return path


def _metadata_path(project, run_id):
    rf = _run_folder(project, run_id)
    p = os.path.join(rf, 'run_metadata.json')
    if os.path.exists(p):
        return p
    legacy = sorted(glob.glob(os.path.join(rf, '*_run_metadata.json')))
    return legacy[0] if legacy else p


def update_run_post(project, run_id, post_params):
    """Persist the tuned post params into the run's metadata."""
    p = _metadata_path(project, run_id)
    data = {}
    if os.path.exists(p):
        try:
            with open(p, 'r') as f:
                data = json.load(f)
        except (IOError, ValueError):
            data = {}
    wd = data.get('workflow_definition')
    if isinstance(wd, dict):
        wd.setdefault('post', {})
        wd['post'].update(post_params)
        data['workflow_definition'] = wd
    ws = data.get('workflow_settings')
    if isinstance(ws, dict):
        ws.update(post_params)
    data.setdefault('post_overrides', {})
    data['post_overrides'].update(post_params)
    try:
        with open(p, 'w') as f:
            json.dump(data, f, indent=2)
    except IOError:
        pass
    return p


def reexport_all(project, run_id, post_params, cell_classes):
    """
    Re-post every image that has cached labels and rewrite the run's outlines +
    CSV + metadata. Returns (images_done, missing_filenames).
    """
    all_rows = []
    images_done = 0
    missing = []
    for image_obj in project.images:
        if not image_obj.has_roi():
            continue
        outlines, rows, _m = recompute_image(project, image_obj, post_params, cell_classes, run_id)
        if rows:
            write_image_outlines(project, run_id, image_obj, outlines)
            all_rows.extend(rows)
            images_done += 1
        else:
            missing.append(image_obj.filename)
    write_run_csv(project, run_id, all_rows, cell_classes, post_params=post_params)
    update_run_post(project, run_id, post_params)
    return images_done, missing
