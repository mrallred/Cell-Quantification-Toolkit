"""
Manual counting export: turn per-class points into a points overlay + counts CSV.

Reuses results_export for the run-folder layout, CSV aggregation, and the
outline-zip writer (points are saved as PointRois into the same
Cell_Selections/{Image}_Outlines.zip the Results Viewer already reads).
"""
import os
import csv
import json
import datetime

from ij import IJ
from ij.gui import PointRoi
from ij.plugin.frame import RoiManager
from java.awt import Color

from . import results_export as rexport


def _color(rgb):
    try:
        return Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return Color.YELLOW


def pointroi_for_class(points, cls):
    """Build a single PointRoi holding all points of one class (absolute coords)."""
    if not points:
        return None
    pr = PointRoi(float(points[0][0]), float(points[0][1]))
    for (x, y) in points[1:]:
        pr.addPoint(float(x), float(y))
    pr.setName(cls.get('display', cls.get('key')))
    pr.setStrokeColor(_color(cls.get('color')))
    try:
        pr.setProperty("cell_class", cls.get('key'))
    except Exception:
        pass
    return pr


def count_rows(image_obj, points_by_class, classes):
    """Count each class's points inside each analysis ROI of an image."""
    rows = []
    if not image_obj.has_roi():
        return rows
    rm = RoiManager(True)
    rm.open(image_obj.roi_path)
    analysis_rois = rm.getRoisAsArray()
    rm.close()

    for aroi in analysis_rois:
        bregma_str = aroi.getProperty("comment")
        try:
            bregma = float(bregma_str) if bregma_str else 0.0
        except (ValueError, TypeError):
            bregma = 0.0
        row = {
            'filename': image_obj.filename,
            'roi_name': aroi.getName(),
            'roi_area': aroi.getStatistics().area,
            'bregma_value': bregma,
        }
        for cls in classes:
            key = cls.get('key')
            pts = points_by_class.get(key, [])
            # A point is counted in every ROI that contains it.
            row[key + '_count'] = sum(
                1 for (x, y) in pts if aroi.contains(int(round(x)), int(round(y))))
        rows.append(row)
    return rows


def _manual_columns(classes):
    return [c.get('key') + '_count' for c in classes]


def _write_csv(project, run_id, rows, classes):
    custom = _manual_columns(classes)
    headers = ['filename', 'roi_name', 'roi_area', 'bregma_value'] + custom
    final = rexport._aggregate(rows, custom)
    path = rexport._results_csv_path(project, run_id)
    with open(path, 'w') as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        w.writeheader()
        w.writerows(final)
    return path


def _write_metadata(project, run_id, definition, image_filenames, rows):
    meta = {
        'processed_date': datetime.datetime.now().isoformat(),
        'workflow_name': definition.name,
        'kind': 'manual',
        'workflow_definition': definition.to_dict(),
        'images_processed': list(image_filenames),
        'total_results': len(rows),
    }
    path = os.path.join(rexport._run_folder(project, run_id), 'run_metadata.json')
    with open(path, 'w') as f:
        json.dump(meta, f, indent=2)
    return path


def save_points_run(project, run_id, definition, per_image_points):
    """
    Save placed points only (no counting/CSV). Writes a points zip per image and
    the run metadata. Counting + CSV export happens later from the Results viewer.

    per_image_points: list of (ProjectImage, {class_key: [(x, y), ...]}).
    Returns images_done.
    """
    run_folder = rexport._run_folder(project, run_id)
    if not os.path.isdir(run_folder):
        os.makedirs(run_folder)

    classes = definition.cell_classes()
    images_done = 0
    for image_obj, points_by_class in per_image_points:
        prois = []
        for cls in classes:
            pr = pointroi_for_class(points_by_class.get(cls.get('key'), []), cls)
            if pr is not None:
                prois.append(pr)
        rexport.write_image_outlines(project, run_id, image_obj, prois)
        images_done += 1

    _write_metadata(project, run_id, definition,
                    [img.filename for img, _ in per_image_points], [])
    return images_done


def _points_zip_path(project, run_id, image_obj):
    stem = os.path.splitext(image_obj.filename)[0]
    return os.path.join(rexport._run_folder(project, run_id),
                        'Cell_Selections', stem + '_Outlines.zip')


def points_from_zip(zip_path):
    """Read a saved points zip into {class_key: [(x, y), ...]}."""
    out = {}
    if not os.path.exists(zip_path):
        return out
    rm = RoiManager(True)
    rm.open(zip_path)
    rois = rm.getRoisAsArray()
    rm.close()
    for r in rois:
        try:
            key = r.getProperty("cell_class")
        except Exception:
            key = None
        if not key:
            continue
        try:
            poly = r.getPolygon()
            out.setdefault(key, []).extend(
                (poly.xpoints[i], poly.ypoints[i]) for i in range(poly.npoints))
        except Exception:
            pass
    return out


def _run_kind(run_path):
    """Read the 'kind' from a run's metadata (run_metadata.json or legacy name)."""
    p = os.path.join(run_path, 'run_metadata.json')
    if not os.path.exists(p):
        for f in os.listdir(run_path):
            if f.endswith('_run_metadata.json'):
                p = os.path.join(run_path, f)
                break
    if not os.path.exists(p):
        return None
    try:
        with open(p) as fh:
            return json.load(fh).get('kind')
    except (IOError, ValueError):
        return None


def latest_points_for_image(project, image_obj):
    """
    Return {class_key: [(x, y), ...]} from the most recent *manual* run that has a
    saved points zip for this image, or {} if there is none. Used to reopen
    previously drawn dots so counting can be continued/edited.
    """
    runs_dir = project.paths.get('runs', '')
    if not os.path.isdir(runs_dir):
        return {}
    stem = os.path.splitext(image_obj.filename)[0]
    for run_id in sorted(os.listdir(runs_dir), reverse=True):   # newest first
        run_path = os.path.join(runs_dir, run_id)
        if not os.path.isdir(run_path):
            continue
        if _run_kind(run_path) != 'manual':
            continue   # only manual runs store points (automated zips are outlines)
        zp = os.path.join(run_path, 'Cell_Selections', stem + '_Outlines.zip')
        if os.path.exists(zp):
            return points_from_zip(zp)
    return {}


def export_counts_for_run(project, run_id, definition):
    """
    Count the saved points inside each ROI for every image in the run and write
    the aggregated CSV (+ refresh metadata). Returns (images_done, total_rows).
    """
    classes = definition.cell_classes()
    all_rows = []
    images = []
    for image_obj in project.images:
        zp = _points_zip_path(project, run_id, image_obj)
        if not os.path.exists(zp):
            continue
        pts = points_from_zip(zp)
        rows = count_rows(image_obj, pts, classes)
        if rows:
            all_rows.extend(rows)
            images.append(image_obj.filename)
    _write_csv(project, run_id, all_rows, classes)
    _write_metadata(project, run_id, definition, images, all_rows)
    return len(images), len(all_rows)
