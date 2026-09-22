# ============================================================================
# Confusion_Matrix_Manual_vs_Automated.py
#
# Compares two runs of the SAME project cell-by-cell and reports a confusion
# matrix, instead of the net per-class counts a Bland-Altman plot shows.
#
# WHY
#   Net counts conflate two different failure modes: a cell the automated
#   pipeline never detected, and a cell it detected but put in the wrong class.
#   Two errors in opposite directions look like agreement. This script matches
#   individual reference cells to individual test objects, so you can see
#   exactly where the counts come from -- e.g. how many true double-labelled
#   cells were called CtB-only, versus how many extra objects were detected
#   that no human ever marked.
#
# HOW TO RUN
#   Fiji > File > New > Script...  (Language menu -> Python), open this file, Run.
#   Pick the project folder, then pick the two runs to compare.
#
#   Typical use:  reference = a manual counting run, test = an automated run.
#   Both sides may also be manual runs -- comparing two counters (or the same
#   counter twice) gives the manual repeatability envelope, which is what tells
#   you whether an automated bias is worth chasing.
#
# WHAT IT READS  (nothing is modified)
#   <project>/Runs/<run>/run_metadata.json                    -- kind + class map
#   <project>/Runs/<run>/Cell_Selections/<image>_Outlines.zip -- points or outlines
#   <project>/ROI_Files/<image>_ROIs.zip                      -- analysis regions
#
# OUTPUT
#   <project>/Validation/confusion_<ref>_vs_<test>_<timestamp>.csv
#   <project>/Validation/confusion_<ref>_vs_<test>_<timestamp>_per_image.csv
#   plus a readable summary in the Fiji Log.
#
#   Optionally, one ROI zip per image holding only the DISAGREEMENTS, so you can
#   look at them on the image instead of guessing from the totals:
#     <project>/Validation/confusion_..._<timestamp>_overlays/<image>_Disagreements.zip
#       red     = detected, nothing marked there   (spurious)
#       magenta = marked, nothing detected there   (missed)
#       cyan    = matched but put in the wrong class
#
# This script is self-contained: it does not import lib/, so it also runs on a
# project folder copied off this machine.
# ============================================================================
import os
import csv
import json
import math
import datetime

from ij import IJ
from ij.gui import GenericDialog, PointRoi, Roi
from ij.io import DirectoryChooser
from ij.plugin.frame import RoiManager
from java.awt import Color

# A reference cell and a test object are matched when the test object CONTAINS
# the reference point, or (failing that) when their centres are within this many
# pixels. Set it to roughly one cell radius for your magnification. Point-vs-
# point comparisons (manual vs manual) can only use the distance rule.
DEFAULT_MATCH_RADIUS = 10.0

# Spatial-index cell size in pixels. Only affects speed, not results.
GRID = 64

MISSED = '__missed__'      # reference cell with no matching test object
SPURIOUS = '__spurious__'  # test object with no matching reference cell

# Overlay colours, picked to stay legible over brown DAB and blue/grey tracer.
COLOR_SPURIOUS = Color(255, 0, 0)      # red
COLOR_MISSED = Color(255, 0, 255)      # magenta
COLOR_WRONG_CLASS = Color(0, 255, 255) # cyan


# ---------------------------------------------------------------------------
# Reading a run
# ---------------------------------------------------------------------------
def read_metadata(run_path):
    """Load a run's run_metadata.json (or the legacy *_run_metadata.json)."""
    p = os.path.join(run_path, 'run_metadata.json')
    if not os.path.exists(p):
        cands = sorted(f for f in os.listdir(run_path)
                       if f.endswith('_run_metadata.json'))
        if not cands:
            return None
        p = os.path.join(run_path, cands[-1])
    try:
        with open(p) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def run_classes(meta):
    """[(key, display), ...] for the run's included classes, in declared order."""
    defn = (meta or {}).get('workflow_definition') or {}
    out = []
    for c in defn.get('classes', []):
        included = c.get('include', c.get('role', 'cell') == 'cell')
        if included:
            out.append((c.get('key'), c.get('display', c.get('key'))))
    return out


def list_runs(runs_dir):
    """[(run_name, kind, [(key, display), ...]), ...] for every run with metadata."""
    out = []
    if not os.path.isdir(runs_dir):
        return out
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name)
        if not os.path.isdir(path) or name.startswith('.') or name.startswith('_'):
            continue
        meta = read_metadata(path)
        if meta is None:
            continue
        out.append((name, meta.get('kind', 'pipeline'), run_classes(meta)))
    return out


def rois_from_zip(path):
    """Every Roi in a saved zip, or [] if there is none."""
    if not path or not os.path.exists(path):
        return []
    rm = RoiManager(True)
    try:
        rm.open(path)
        rois = rm.getRoisAsArray()
    finally:
        rm.close()
    return list(rois) if rois is not None else []


def _class_of(roi):
    try:
        return roi.getProperty("cell_class")
    except Exception:
        return None


def _points_of(roi):
    """Absolute (x, y) vertices of a PointRoi."""
    try:
        poly = roi.getFloatPolygon()
        return [(float(poly.xpoints[i]), float(poly.ypoints[i]))
                for i in range(poly.npoints)]
    except Exception:
        poly = roi.getPolygon()
        return [(float(poly.xpoints[i]), float(poly.ypoints[i]))
                for i in range(poly.npoints)]


def load_side(zip_path):
    """
    Read one run's saved zip for one image into a flat list of cells:

        [{'key':..., 'x':..., 'y':..., 'roi': Roi or None}, ...]

    A manual run stores one multi-point PointRoi per class, so each vertex
    becomes one cell with roi=None (distance matching only). An automated run
    stores one area Roi per detected cell, kept so containment can be tested;
    its bounding-box centre is used as the cell's position.
    """
    cells = []
    for roi in rois_from_zip(zip_path):
        key = _class_of(roi)
        if not key:
            continue                      # not written by this toolkit; skip
        if roi.getType() == Roi.POINT:
            for (x, y) in _points_of(roi):
                cells.append({'key': key, 'x': x, 'y': y, 'roi': None})
        else:
            b = roi.getBounds()
            cells.append({'key': key,
                          'x': b.x + b.width / 2.0,
                          'y': b.y + b.height / 2.0,
                          'roi': roi})
    return cells


def inside_any(rois, x, y):
    xi, yi = int(round(x)), int(round(y))
    for r in rois:
        if r.contains(xi, yi):
            return True
    return False


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _index(cells, radius):
    """Grid index of test cells by bounds expanded with `radius`, for lookup."""
    buckets = {}
    for i, c in enumerate(cells):
        if c['roi'] is not None:
            b = c['roi'].getBounds()
            x0, y0, x1, y1 = b.x, b.y, b.x + b.width, b.y + b.height
        else:
            x0, y0, x1, y1 = c['x'], c['y'], c['x'], c['y']
        gx0 = int(math.floor((x0 - radius) / GRID))
        gx1 = int(math.floor((x1 + radius) / GRID))
        gy0 = int(math.floor((y0 - radius) / GRID))
        gy1 = int(math.floor((y1 + radius) / GRID))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                buckets.setdefault((gx, gy), []).append(i)
    return buckets


def match(ref_cells, test_cells, radius):
    """
    One-to-one greedy match of reference cells to test objects.

    Containment wins over proximity, then the closest pair wins; each cell on
    either side is used at most once. The one-to-one rule matters: without it a
    single merged blob covering three marked cells would score as three correct
    detections instead of one detection and two misses.

    Returns (pairs, missed_ref_idx, spurious_test_idx, diagnostics) where pairs
    is [(ref_idx, test_idx), ...].
    """
    buckets = _index(test_cells, radius)
    cands = []                    # (0=contained/1=near, distance, ref_i, test_i)
    contains_count = {}           # test_i -> how many reference cells it contains
    near_any = set()              # test cells that had at least one candidate

    for ri, rc in enumerate(ref_cells):
        gx = int(math.floor(rc['x'] / GRID))
        gy = int(math.floor(rc['y'] / GRID))
        for ti in buckets.get((gx, gy), ()):
            tc = test_cells[ti]
            troi = tc['roi']
            if troi is not None and troi.contains(int(round(rc['x'])),
                                                  int(round(rc['y']))):
                cands.append((0, 0.0, ri, ti))
                contains_count[ti] = contains_count.get(ti, 0) + 1
                near_any.add(ti)
            else:
                d = math.hypot(tc['x'] - rc['x'], tc['y'] - rc['y'])
                if d <= radius:
                    cands.append((1, d, ri, ti))
                    near_any.add(ti)

    cands.sort()                  # containment first, then nearest
    ref_taken, test_taken = {}, {}
    for _rank, _d, ri, ti in cands:
        if ri in ref_taken or ti in test_taken:
            continue
        ref_taken[ri] = ti
        test_taken[ti] = ri

    pairs = sorted(ref_taken.items())
    missed = [i for i in range(len(ref_cells)) if i not in ref_taken]
    spurious = [i for i in range(len(test_cells)) if i not in test_taken]

    diagnostics = {
        # One test object covering several marked cells: under-segmentation.
        'merged': sum(1 for ti, n in contains_count.items() if n >= 2),
        # Unmatched test objects that still sit on a marked cell -- most likely
        # a real cell split into pieces, or a duplicate detection.
        'split_or_duplicate': sum(1 for ti in spurious if ti in near_any),
        # Unmatched test objects nowhere near any marked cell -- either a false
        # positive, or a real cell the reference counter missed.
        'unmarked': sum(1 for ti in spurious if ti not in near_any),
    }
    return pairs, missed, spurious, diagnostics


# ---------------------------------------------------------------------------
# Disagreement overlay
# ---------------------------------------------------------------------------
def _marker(cell, name, color):
    """A drawable ROI for one cell: its own outline if it has one, else a point."""
    roi = cell['roi']
    if roi is None:
        roi = PointRoi(float(cell['x']), float(cell['y']))
        try:
            roi.setSize(3)          # large hollow dot; ignored by older ImageJ
        except Exception:
            pass
    else:
        roi = roi.clone()           # never mutate the loaded outline
    roi.setName(name)
    roi.setStrokeColor(color)
    try:
        roi.setProperty("disagreement", name.split('_')[0])
    except Exception:
        pass
    return roi


def disagreement_rois(ref_cells, test_cells, pairs, missed, spurious):
    """
    Build the review overlay: only the cells the two runs disagree about.

    Correct matches are deliberately left out -- on a section with 4000 cells
    they would bury the ~1000 that actually need looking at.
    """
    rois = []
    for ti in spurious:
        c = test_cells[ti]
        rois.append(_marker(c, "spurious_{}_{}".format(c['key'], ti),
                            COLOR_SPURIOUS))
    for ri in missed:
        c = ref_cells[ri]
        rois.append(_marker(c, "missed_{}_{}".format(c['key'], ri), COLOR_MISSED))
    for ri, ti in pairs:
        rk, tk = ref_cells[ri]['key'], test_cells[ti]['key']
        if rk != tk:
            # Draw what the test run drew, labelled with the call it got wrong.
            rois.append(_marker(test_cells[ti],
                                "wrongclass_{}-as-{}_{}".format(rk, tk, ti),
                                COLOR_WRONG_CLASS))
    return rois


def write_rois(path, rois):
    """Save ROIs to a zip. Returns True if anything was written."""
    if not rois:
        return False
    rm = RoiManager(True)
    try:
        for r in rois:
            rm.addRoi(r)
        rm.runCommand("Save", path)
    finally:
        rm.close()
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    line = "  ".join(h.rjust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for r in rows:
        out.append("  ".join(str(v).rjust(widths[i]) for i, v in enumerate(r)))
    return "\n".join(out)


def log_report(ref_run, test_run, ref_keys, test_keys, labels,
               matrix, missed, spurious, diag, n_images, radius):
    IJ.log("")
    IJ.log("=" * 72)
    IJ.log("Confusion matrix: reference '{}'  vs  test '{}'".format(ref_run, test_run))
    IJ.log("{} image(s), match radius {:.1f} px".format(n_images, radius))
    IJ.log("=" * 72)
    IJ.log("Rows = reference class, columns = what the test run called it.")

    headers = ["reference \\ test"] + [labels.get(k, k) for k in test_keys] + \
              ["missed", "total", "recall"]
    rows = []
    for rk in ref_keys:
        cells = [matrix.get((rk, tk), 0) for tk in test_keys]
        m = missed.get(rk, 0)
        total = sum(cells) + m
        correct = matrix.get((rk, rk), 0)
        recall = (100.0 * correct / total) if total else 0.0
        rows.append([labels.get(rk, rk)] + cells + [m, total, "{:.1f}%".format(recall)])

    sp = [spurious.get(tk, 0) for tk in test_keys]
    rows.append(["spurious"] + sp + ["", sum(sp), ""])

    tot_row = []
    for i, tk in enumerate(test_keys):
        tot_row.append(sum(matrix.get((rk, tk), 0) for rk in ref_keys) + sp[i])
    rows.append(["test total"] + tot_row + [sum(missed.values()), "", ""])

    for line in _fmt_table(rows, headers).split("\n"):
        IJ.log(line)

    IJ.log("")
    IJ.log("Per-class agreement")
    rows = []
    for k in ref_keys:
        ref_total = sum(matrix.get((k, tk), 0) for tk in test_keys) + missed.get(k, 0)
        test_total = sum(matrix.get((rk, k), 0) for rk in ref_keys) + spurious.get(k, 0)
        correct = matrix.get((k, k), 0)
        recall = (100.0 * correct / ref_total) if ref_total else 0.0
        prec = (100.0 * correct / test_total) if test_total else 0.0
        rows.append([labels.get(k, k), ref_total, test_total,
                     test_total - ref_total, correct,
                     "{:.1f}%".format(recall), "{:.1f}%".format(prec)])
    for line in _fmt_table(
            rows, ["class", "reference", "test", "test-ref", "correct",
                   "recall", "precision"]).split("\n"):
        IJ.log(line)
    IJ.log("('test-ref' is the net count difference -- the quantity a "
           "Bland-Altman bias summarises.)")

    IJ.log("")
    IJ.log("Detection diagnostics")
    IJ.log("  test objects covering >1 reference cell (merged): {}".format(diag['merged']))
    IJ.log("  unmatched test objects ON a reference cell (split/duplicate): {}".format(
        diag['split_or_duplicate']))
    IJ.log("  unmatched test objects away from any reference cell: {}".format(
        diag['unmarked']))
    IJ.log("  reference cells with no test object (missed): {}".format(sum(missed.values())))
    IJ.log("")


def write_matrix_csv(path, ref_keys, test_keys, labels, matrix, missed, spurious):
    with open(path, 'w') as f:
        w = csv.writer(f)
        w.writerow(['reference_class'] + [labels.get(k, k) for k in test_keys] +
                   ['missed', 'reference_total'])
        for rk in ref_keys:
            cells = [matrix.get((rk, tk), 0) for tk in test_keys]
            m = missed.get(rk, 0)
            w.writerow([labels.get(rk, rk)] + cells + [m, sum(cells) + m])
        w.writerow(['spurious'] + [spurious.get(tk, 0) for tk in test_keys] + ['', ''])


def write_per_image_csv(path, per_image):
    with open(path, 'w') as f:
        w = csv.writer(f)
        w.writerow(['filename', 'reference_class', 'test_class', 'n'])
        for fname in sorted(per_image.keys()):
            for (rk, tk), n in sorted(per_image[fname].items()):
                w.writerow([fname, rk, tk, n])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    project = DirectoryChooser("Choose the PROJECT folder").getDirectory()
    if not project:
        return
    runs_dir = os.path.join(project, 'Runs')
    runs = list_runs(runs_dir)
    if len(runs) < 2:
        IJ.error("Confusion matrix",
                 "Need at least two runs with a run_metadata.json in:\n" + runs_dir)
        return

    names = [r[0] for r in runs]
    kinds = dict((r[0], r[1]) for r in runs)
    classes = dict((r[0], r[2]) for r in runs)
    # Default the reference to a manual run if there is one.
    manual_first = [n for n in names if kinds[n] == 'manual'] or names
    auto_first = [n for n in names if kinds[n] != 'manual'] or names

    gd = GenericDialog("Confusion matrix: reference vs test")
    gd.addChoice("Reference run (rows):", names, manual_first[0])
    gd.addChoice("Test run (columns):", names, auto_first[0])
    gd.addNumericField("Match radius (px):", DEFAULT_MATCH_RADIUS, 1)
    gd.addCheckbox("Restrict to analysis ROIs", True)
    gd.addCheckbox("Write disagreement overlays (one ROI zip per image)", True)
    gd.addMessage("Reference cells are matched one-to-one to test objects:\n"
                  "containment first, then nearest within the match radius.\n\n"
                  "'Restrict to analysis ROIs' keeps only cells inside the\n"
                  "image's ROI_Files regions. Leave it ticked when comparing a\n"
                  "manual run against an automated one -- the counting tool lets\n"
                  "you click anywhere, but the pipeline only ever looks inside\n"
                  "the ROIs, so points outside them would count as false misses.")
    gd.showDialog()
    if gd.wasCanceled():
        return

    ref_run = gd.getNextChoice()
    test_run = gd.getNextChoice()
    radius = float(gd.getNextNumber())
    restrict = gd.getNextBoolean()
    want_overlays = gd.getNextBoolean()

    if ref_run == test_run:
        IJ.error("Confusion matrix", "Pick two different runs.")
        return

    ref_keys = [k for (k, _d) in classes[ref_run]]
    test_keys = [k for (k, _d) in classes[test_run]]
    labels = {}
    for run in (ref_run, test_run):
        for (k, d) in classes[run]:
            labels[k] = d
    if not ref_keys or not test_keys:
        IJ.error("Confusion matrix",
                 "A run has no included classes in its metadata snapshot.")
        return

    ref_cs = os.path.join(runs_dir, ref_run, 'Cell_Selections')
    test_cs = os.path.join(runs_dir, test_run, 'Cell_Selections')
    if not (os.path.isdir(ref_cs) and os.path.isdir(test_cs)):
        IJ.error("Confusion matrix", "One of the runs has no Cell_Selections folder.")
        return

    stems = sorted(set(f[:-len('_Outlines.zip')]
                       for f in os.listdir(ref_cs) if f.endswith('_Outlines.zip')) &
                   set(f[:-len('_Outlines.zip')]
                       for f in os.listdir(test_cs) if f.endswith('_Outlines.zip')))
    if not stems:
        IJ.error("Confusion matrix",
                 "No image has saved results in BOTH runs.\n\n"
                 "An automated run only writes its outlines on Export, so export "
                 "the run from the Results viewer first.")
        return

    outdir = os.path.join(project, 'Validation')
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    stem_out = "confusion_{}_vs_{}_{}".format(ref_run, test_run, ts)
    overlay_dir = os.path.join(outdir, stem_out + '_overlays')
    if want_overlays and not os.path.isdir(overlay_dir):
        os.makedirs(overlay_dir)
    overlays_written = 0

    matrix = {}
    missed = {}
    spurious = {}
    diag_total = {'merged': 0, 'split_or_duplicate': 0, 'unmarked': 0}
    per_image = {}
    used_images = 0

    for stem in stems:
        ref_cells = load_side(os.path.join(ref_cs, stem + '_Outlines.zip'))
        test_cells = load_side(os.path.join(test_cs, stem + '_Outlines.zip'))

        if restrict:
            arois = rois_from_zip(os.path.join(project, 'ROI_Files',
                                               stem + '_ROIs.zip'))
            if arois:
                ref_cells = [c for c in ref_cells if inside_any(arois, c['x'], c['y'])]
                test_cells = [c for c in test_cells if inside_any(arois, c['x'], c['y'])]
            else:
                IJ.log("[{}] no ROI file found - using all cells for this image".format(stem))

        if not ref_cells and not test_cells:
            continue
        used_images += 1

        pairs, miss, spur, diag = match(ref_cells, test_cells, radius)
        counts = {}
        for ri, ti in pairs:
            k = (ref_cells[ri]['key'], test_cells[ti]['key'])
            counts[k] = counts.get(k, 0) + 1
        for ri in miss:
            k = (ref_cells[ri]['key'], MISSED)
            counts[k] = counts.get(k, 0) + 1
        for ti in spur:
            k = (SPURIOUS, test_cells[ti]['key'])
            counts[k] = counts.get(k, 0) + 1

        per_image[stem] = counts
        for (rk, tk), n in counts.items():
            if rk == SPURIOUS:
                spurious[tk] = spurious.get(tk, 0) + n
            elif tk == MISSED:
                missed[rk] = missed.get(rk, 0) + n
            else:
                matrix[(rk, tk)] = matrix.get((rk, tk), 0) + n
        for k in diag_total:
            diag_total[k] += diag[k]

        if want_overlays:
            rois = disagreement_rois(ref_cells, test_cells, pairs, miss, spur)
            if write_rois(os.path.join(overlay_dir, stem + '_Disagreements.zip'),
                          rois):
                overlays_written += 1

        IJ.log("[{}] reference {}, test {}, matched {}".format(
            stem, len(ref_cells), len(test_cells), len(pairs)))

    if not used_images:
        IJ.error("Confusion matrix", "No cells found in either run.")
        return

    log_report(ref_run, test_run, ref_keys, test_keys, labels,
               matrix, missed, spurious, diag_total, used_images, radius)

    m_path = os.path.join(outdir, stem_out + '.csv')
    i_path = os.path.join(outdir, stem_out + '_per_image.csv')
    write_matrix_csv(m_path, ref_keys, test_keys, labels, matrix, missed, spurious)
    write_per_image_csv(i_path, per_image)
    IJ.log("Wrote " + m_path)
    IJ.log("Wrote " + i_path)
    if want_overlays:
        IJ.log("Wrote {} disagreement overlay(s) to {}".format(
            overlays_written, overlay_dir))
        IJ.log("  To review: open the image, then drag its "
               "*_Disagreements.zip onto the Fiji window")
        IJ.log("  (or ROI Manager > More >> Open), and tick 'Show All'.")
        IJ.log("  red = spurious (detected, nothing marked)   "
               "magenta = missed   cyan = wrong class")


main()
