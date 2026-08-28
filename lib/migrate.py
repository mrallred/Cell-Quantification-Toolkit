"""
One-time, non-destructive migration of the Runs/ layout.

Old layout: one folder per *execution* (timestamped), e.g.
    Runs/20260728_141502_123456/Cell_Selections/*.zip

New layout: one folder per *workflow* (stable key = sanitized workflow name),
with exported CSVs stamped by timestamp+settings so they accumulate:
    Runs/<workflow-name>/Cell_Selections/*.zip
    Runs/<workflow-name>/results_<ts>__<stamp>.csv

Migration strategy (safe by construction):
  1. If a marker file is present, do nothing (idempotent).
  2. Move the whole existing Runs/ aside to Runs_pre_perworkflow_<ts>/ (a backup;
     nothing is ever deleted).
  3. Rebuild Runs/ by grouping the old runs by their workflow name (read from each
     run's run_metadata.json). Oldest->newest so the newest detections/metadata
     win; every old results CSV is copied in, renamed with its source run so none
     are lost. Runs with no metadata are copied verbatim so they stay viewable.
  4. Relocate any legacy flat Probabilities/*.tif cache into a backup subfolder
     (the cache is now per-workflow; flat files can't be attributed to a workflow).

Called from Project.__init__; failures are caught there and logged.
"""
import os
import glob
import json
import shutil
import datetime

from ij import IJ

from .workflow_config import sanitize_name

MARKER = '.per_workflow_v1'


def _run_workflow_name(run_path):
    """Read the workflow name from a run's metadata, or None."""
    meta_path = os.path.join(run_path, 'run_metadata.json')
    if not os.path.exists(meta_path):
        legacy = sorted(glob.glob(os.path.join(run_path, '*_run_metadata.json')))
        meta_path = legacy[-1] if legacy else None
    if not meta_path or not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path) as f:
            return json.load(f).get('workflow_name')
    except (IOError, ValueError):
        return None


def _copy_into_workflow(src, dst):
    """Merge one old run folder `src` into the workflow folder `dst`."""
    # Cell_Selections: newest wins (caller iterates oldest->newest).
    src_cs = os.path.join(src, 'Cell_Selections')
    if os.path.isdir(src_cs):
        dst_cs = os.path.join(dst, 'Cell_Selections')
        if not os.path.isdir(dst_cs):
            os.makedirs(dst_cs)
        for fn in os.listdir(src_cs):
            try:
                shutil.copy2(os.path.join(src_cs, fn), os.path.join(dst_cs, fn))
            except (IOError, OSError):
                pass
    # Metadata: newest wins.
    for cand in ['run_metadata.json'] + [os.path.basename(p) for p in
                 glob.glob(os.path.join(src, '*_run_metadata.json'))]:
        sp = os.path.join(src, cand)
        if os.path.exists(sp):
            try:
                shutil.copy2(sp, os.path.join(dst, 'run_metadata.json'))
            except (IOError, OSError):
                pass
    # CSVs: preserve every one, tagged by the source run name.
    src_run = os.path.basename(src.rstrip(os.sep))
    for csvp in (glob.glob(os.path.join(src, 'results_*.csv')) +
                 glob.glob(os.path.join(src, '*_results.csv'))):
        base = os.path.basename(csvp)
        try:
            shutil.copy2(csvp, os.path.join(dst, 'migrated_{}__{}'.format(src_run, base)))
        except (IOError, OSError):
            pass


def _relocate_flat_probabilities(project):
    """Move legacy top-level Probabilities/*.tif into a backup subfolder so the
    per-workflow cache lookup isn't confused by unattributable flat files."""
    prob_dir = project.paths.get('probabilities', '')
    if not prob_dir or not os.path.isdir(prob_dir):
        return
    flat = [f for f in os.listdir(prob_dir)
            if os.path.isfile(os.path.join(prob_dir, f)) and f.lower().endswith('.tif')]
    if not flat:
        return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(prob_dir, '_pre_perworkflow_backup_' + ts)
    if not os.path.isdir(backup):
        os.makedirs(backup)
    for f in flat:
        try:
            shutil.move(os.path.join(prob_dir, f), os.path.join(backup, f))
        except (IOError, OSError):
            pass


def migrate_runs_to_per_workflow(project):
    """Idempotent, non-destructive. Returns a summary dict or None if nothing done."""
    runs_dir = project.paths.get('runs', '')
    if not runs_dir or not os.path.isdir(runs_dir):
        return None
    if os.path.exists(os.path.join(runs_dir, MARKER)):
        return None  # already migrated

    old_runs = [e for e in os.listdir(runs_dir)
                if os.path.isdir(os.path.join(runs_dir, e))
                and not e.startswith('_') and not e.startswith('.')]
    if not old_runs:
        _write_marker(runs_dir)
        return None

    # Move the existing Runs/ aside as a full backup, then rebuild.
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(project.root_dir, 'Runs_pre_perworkflow_' + ts)
    shutil.move(runs_dir, backup)
    os.makedirs(runs_dir)

    summary = {'workflows': 0, 'runs': 0, 'unclassified': 0, 'backup': backup}
    seen_keys = set()
    for run_name in sorted(os.listdir(backup)):          # oldest -> newest
        src = os.path.join(backup, run_name)
        if not os.path.isdir(src):
            continue
        wfname = _run_workflow_name(src)
        if not wfname:
            # Can't attribute to a workflow -> copy verbatim so it stays viewable.
            try:
                shutil.copytree(src, os.path.join(runs_dir, run_name))
            except (IOError, OSError):
                pass
            summary['unclassified'] += 1
            continue
        key = sanitize_name(wfname) or 'workflow'
        dst = os.path.join(runs_dir, key)
        if not os.path.isdir(dst):
            os.makedirs(dst)
        if key not in seen_keys:
            seen_keys.add(key)
            summary['workflows'] += 1
        _copy_into_workflow(src, dst)
        summary['runs'] += 1

    _relocate_flat_probabilities(project)
    _write_marker(runs_dir)
    IJ.log("[CQT] Migrated runs to per-workflow layout: {} workflow(s) from {} run(s), "
           "{} unclassified. Original runs backed up to: {}".format(
               summary['workflows'], summary['runs'], summary['unclassified'], backup))
    return summary


def _write_marker(runs_dir):
    try:
        with open(os.path.join(runs_dir, MARKER), 'w') as f:
            f.write('migrated to per-workflow layout\n')
    except IOError:
        pass
