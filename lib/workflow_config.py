"""
Workflow definition model + global store for the configurable multi-class
quantification workflow.

This is a pure data / IO layer (no Swing) so it can be reused and reasoned about
independently of the GUI. A "workflow definition" pairs a specific ilastik pixel
classifier + object classifier with a class map (label value -> display name,
color, role) and post-processing defaults. Definitions live globally in
Cell_Quantification_Toolkit/workflow_defs/ and can be selected by any project.
"""
# Silences Jython's "Parent module 'lib' not found while handling absolute import"
# warning for the lazy Java imports (JHDF5, java.io) used inside this module.
from __future__ import absolute_import

import os
import sys
import json
import re

from ij import IJ
from java.lang import Throwable  # Java exceptions are NOT caught by `except Exception` in Jython

# Sibling modules, imported at module top (relative imports resolve reliably at
# package-load time, unlike function-level imports after the dev-mode reload).
from .step_registry import create_provider
from .postprocess import run_post
from .pipeline_runner import PipelineRunner


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def toolkit_dir():
    plugins_dir = IJ.getDirectory("plugins")
    return os.path.join(plugins_dir, "Cell_Quantification_Toolkit")


def models_dir():
    return os.path.join(toolkit_dir(), "models")


def defs_dir():
    return os.path.join(toolkit_dir(), "workflow_defs")


def sanitize_name(name):
    """Filesystem-safe basename for a workflow definition or class key."""
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', (name or '').strip())
    return s.strip('_') or 'workflow'


DEFAULT_POST = {
    'apply_watershed': True,
    'exclude_edges': True,
    'min_cell_size': 10,
    'min_circularity': 0.0,
}


# ---------------------------------------------------------------------------
# Definition model
# ---------------------------------------------------------------------------
class WorkflowDefinition(object):
    """In-memory representation of a workflow_defs/*.json file."""

    def __init__(self, data=None):
        data = data or {}
        self.schema_version = data.get('schema_version', 2)
        self.kind = data.get('kind', 'pipeline')   # 'pipeline' (ilastik) | 'manual'
        self.name = data.get('name', 'Untitled Workflow')
        self.description = data.get('description', '')
        self.classes = data.get('classes', [])                      # list of dicts
        post = dict(DEFAULT_POST)
        post.update(data.get('post', {}) or {})
        self.post = post

        if self.is_manual():
            # Manual counting has no pipeline stages or classifiers.
            self.segmentation = None
            self.classification = None
            self.pixel_classifier = ''
            self.object_classifier = ''
            return

        # Canonical pipeline stages (schema v2). Derive from the flat v1 fields
        # when absent, so existing v1 definitions load and run unchanged.
        seg = data.get('segmentation')
        cls = data.get('classification')
        if not seg:
            seg = {'type': 'ilastik_pixel', 'params': {'project': data.get('pixel_classifier', '')}}
        if not cls:
            cls = {'type': 'ilastik_object', 'params': {'project': data.get('object_classifier', '')}}
        self.segmentation = seg
        self.classification = cls

        # Derived flat fields kept only for summaries / back-compat display.
        self.pixel_classifier = (seg.get('params', {}) or {}).get('project', '') \
            if seg.get('type') == 'ilastik_pixel' else ''
        self.object_classifier = (cls.get('params', {}) or {}).get('project', '') \
            if cls.get('type') == 'ilastik_object' else ''

    def is_manual(self):
        return self.kind == 'manual'

    # ---- serialization ----
    def to_dict(self):
        d = {
            'schema_version': 2,
            'kind': self.kind,
            'name': self.name,
            'description': self.description,
            'classes': self.classes,
        }
        if not self.is_manual():
            d['segmentation'] = self.segmentation
            d['classification'] = self.classification
            d['post'] = self.post
        return d

    @staticmethod
    def from_json(path):
        with open(path, 'r') as f:
            return WorkflowDefinition(json.load(f))

    def to_json(self, path):
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    # ---- accessors ----
    def cell_classes(self):
        """Included classes (those counted + outlined), in declared order."""
        return [c for c in self.classes if is_included(c)]

    def segmentation_spec(self):
        """{'type', 'params'} for the segmentation stage."""
        return self.segmentation

    def classification_spec(self):
        """{'type', 'params'} for the classification stage."""
        return self.classification

    # ---- validation ----
    def validate(self, md=None):
        """Return a list of human-readable problems ([] means valid). Per-stage
        checks are delegated to the selected providers."""
        problems = []
        if not (self.name or '').strip():
            problems.append("Workflow needs a name.")
        if not self.cell_classes():
            problems.append("At least one class must be included (a 'cell' class).")
        labels = [c.get('label') for c in self.classes]
        if len(labels) != len(set(labels)):
            problems.append("Class label values must be unique.")

        # Manual counting has no pipeline stages to validate.
        if self.is_manual():
            return problems

        for stage, spec in (('segmentation', self.segmentation),
                            ('classification', self.classification)):
            t, params = _resolve_stage(spec)
            prov = create_provider(stage, t, params)
            if prov is None:
                problems.append("Unknown {} provider: {}".format(stage, t))
            else:
                try:
                    problems.extend(prov.validate())
                except Exception as e:
                    problems.append("{} validation error: {}".format(stage, e))
        return problems

    def is_valid(self, md=None):
        return not self.validate(md)

    def to_run_settings(self, md=None):
        """Post-processing params for the settings dict (classifier paths are
        handled by the providers now). The dialog adds images + display flags."""
        s = {}
        s.update(self.post)
        return s


# ---------------------------------------------------------------------------
# Global store
# ---------------------------------------------------------------------------
class WorkflowStore(object):
    """Manages the global workflow_defs/ folder of *.json definitions."""

    def __init__(self, dd=None):
        self.dir = dd or defs_dir()
        if not os.path.isdir(self.dir):
            try:
                os.makedirs(self.dir)
            except OSError:
                pass

    def _path_for(self, name):
        return os.path.join(self.dir, sanitize_name(name) + '.json')

    def list(self):
        """Return [(name, description), ...] for all definitions, sorted by name."""
        out = []
        if os.path.isdir(self.dir):
            for fn in os.listdir(self.dir):
                if fn.lower().endswith('.json'):
                    try:
                        d = WorkflowDefinition.from_json(os.path.join(self.dir, fn))
                        out.append((d.name, d.description))
                    except (IOError, ValueError):
                        continue
        out.sort(key=lambda t: (t[0] or '').lower())
        return out

    def names(self):
        return [n for (n, _d) in self.list()]

    def load(self, name):
        """Load by name; matches the sanitized filename first, then the 'name' field."""
        if not name:
            return None
        p = self._path_for(name)
        if os.path.exists(p):
            return WorkflowDefinition.from_json(p)
        if os.path.isdir(self.dir):
            for fn in os.listdir(self.dir):
                if fn.lower().endswith('.json'):
                    try:
                        d = WorkflowDefinition.from_json(os.path.join(self.dir, fn))
                    except (IOError, ValueError):
                        continue
                    if d.name == name:
                        return d
        return None

    def save(self, defn):
        defn.to_json(self._path_for(defn.name))

    def delete(self, name):
        p = self._path_for(name)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def exists(self, name):
        return self.load(name) is not None


# ---------------------------------------------------------------------------
# Model (.ilp) discovery + introspection
# ---------------------------------------------------------------------------
def list_models():
    """Return {basename: full_path} for every .ilp in the models folder."""
    out = {}
    md = models_dir()
    if os.path.isdir(md):
        for f in os.listdir(md):
            if f.lower().endswith('.ilp'):
                out[f] = os.path.join(md, f)
    return out


def _h5_exists(reader, path):
    """Best-effort HDF5 path existence check across JHDF5 API spellings."""
    try:
        return bool(reader.exists(path))
    except (Exception, Throwable):
        pass
    try:
        return bool(reader.object().exists(path))
    except (Exception, Throwable):
        return False


def read_ilp_metadata(ilp_path):
    """
    Read {'workflow_name', 'label_names', 'label_colors'} from an ilastik .ilp
    (HDF5) using JHDF5 (bundled with the ilastik Fiji update site). Best-effort:
    on any failure the corresponding field stays empty so callers can fall back
    to manual entry.
    """
    result = {'workflow_name': None, 'label_names': [], 'label_colors': []}
    reader = None
    try:
        from ch.systemsx.cisd.hdf5 import HDF5Factory
        from java.io import File
        reader = HDF5Factory.openForReading(File(ilp_path))

        if _h5_exists(reader, "/workflowName"):
            try:
                result['workflow_name'] = reader.readString("/workflowName")
            except (Exception, Throwable):
                pass

        if _h5_exists(reader, "/ObjectClassification/LabelNames"):
            try:
                names = reader.readStringArray("/ObjectClassification/LabelNames")
                result['label_names'] = [str(n) for n in names]
            except (Exception, Throwable):
                pass

        # LabelColors: N x 3 int matrix (try a couple of JHDF5 API spellings)
        if _h5_exists(reader, "/ObjectClassification/LabelColors"):
            colors = None
            try:
                colors = reader.readIntMatrix("/ObjectClassification/LabelColors")
            except (Exception, Throwable):
                try:
                    colors = reader.int32().readMatrix("/ObjectClassification/LabelColors")
                except (Exception, Throwable):
                    colors = None
            if colors is not None:
                result['label_colors'] = [[int(v) for v in row] for row in colors]
    except (Exception, Throwable) as e:
        IJ.log("read_ilp_metadata: could not read {} ({})".format(ilp_path, e))
    finally:
        if reader is not None:
            try:
                reader.close()
            except (Exception, Throwable):
                pass
    return result


def ilp_workflow_name(ilp_path):
    """Convenience: just the ilastik workflowName string (or None)."""
    return read_ilp_metadata(ilp_path).get('workflow_name')


def is_included(c):
    """
    Whether a class is included (counted + outlined). Uses the class's 'include'
    flag; for legacy definitions that still use 'role', a role of 'cell' counts
    as included.
    """
    if 'include' in c:
        return bool(c.get('include'))
    return c.get('role', 'cell') == 'cell'


def default_include(label_name):
    """Default include flag from a class name: exclude obvious artifact/background."""
    n = (label_name or '').strip().lower()
    if 'background' in n or n in ('bg', 'none'):
        return False
    if 'artifact' in n or 'artefact' in n or 'debris' in n:
        return False
    return True


def classes_from_ilp(ilp_path):
    """
    Build a class list (list of dicts) from an object classifier's stored labels.
    The pixel value in ilastik's Object Predictions export is the 1-based class
    index, so label = i + 1.
    """
    meta = read_ilp_metadata(ilp_path)
    names = meta['label_names']
    colors = meta['label_colors']
    classes = []
    for i, nm in enumerate(names):
        if i < len(colors) and len(colors[i]) >= 3:
            color = [int(c) for c in colors[i][:3]]
        else:
            color = [255, 255, 0]
        classes.append({
            'label': i + 1,
            'key': sanitize_name(nm).lower(),
            'display': nm,
            'color': color,
            'include': default_include(nm),
        })
    return classes


# ---------------------------------------------------------------------------
# Runtime factory
# ---------------------------------------------------------------------------
def _resolve_stage(spec):
    """Turn a stage spec {'type','params'} into (type_id, params_with_full_path)."""
    type_id = spec.get('type')
    params = dict(spec.get('params', {}) or {})
    project = params.get('project', '')
    if project:
        params['project_path'] = os.path.join(models_dir(), project)
    return type_id, params


def build_workflow_instance(definition):
    """
    Construct the pipeline runner for a definition: resolve the segmentation and
    classification providers from the step registry, then wrap them in a
    PipelineRunner. Falls back with a clear error if a provider type is unknown.
    """
    seg_type, seg_params = _resolve_stage(definition.segmentation_spec())
    cls_type, cls_params = _resolve_stage(definition.classification_spec())

    seg = create_provider('segmentation', seg_type, seg_params)
    cls = create_provider('classification', cls_type, cls_params)
    if seg is None:
        raise Exception("Unknown segmentation provider: {}".format(seg_type))
    if cls is None:
        raise Exception("Unknown classification provider: {}".format(cls_type))

    return PipelineRunner(definition, seg, cls, run_post)
