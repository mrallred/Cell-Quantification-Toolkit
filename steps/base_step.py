"""
Base class for pipeline step providers.

A pipeline is three fixed stages - segmentation, classification, post-processing.
Each stage is filled by a swappable provider that declares what it produces and
consumes (contract tokens), so the editor can offer only compatible combinations.
New methods (StarDist, z-score, ...) are added by dropping a provider file into
this folder; the step registry discovers it automatically.
"""


class StepProvider(object):
    """Interface every step provider implements."""

    stage = None          # "segmentation" | "classification"
    type_id = None        # stable id stored in the definition JSON
    display_name = "Step"
    produces = []         # contract tokens this step outputs, e.g. ["probability_map"]
    consumes = []         # contract tokens this step requires from upstream

    def __init__(self, params=None):
        self.params = params or {}

    def available(self):
        """Whether the backing plugin/command is installed and usable."""
        return True

    def validate(self):
        """Return a list of human-readable problems ([] means OK)."""
        return []

    def run(self, ctx):
        """
        Execute the step. `ctx` is a shared dict carrying at least:
            temp_path, prob_map_path, show_images, force_recalculate
        and accumulating outputs (probability_map_path, class_labels_imp, ...).
        Mutate and/or return ctx.
        """
        raise NotImplementedError("Step providers must implement run(ctx)")

    # ---- editor support (optional to override) ----
    def default_params(self):
        """Default parameter dict for a fresh instance of this provider."""
        return {}

    def build_panel(self, params):
        """Return a Swing JPanel for editing this provider's params, or None."""
        return None

    def gather_params(self, panel=None):
        """Read the editor panel back into a params dict."""
        return dict(self.params)

    # ---- shared helpers (self-contained: only ij / java) ----
    def _list_models(self):
        """{basename: full_path} for every .ilp in the plugin models folder."""
        import os
        from ij import IJ
        md = os.path.join(IJ.getDirectory("plugins"),
                          "Cell_Quantification_Toolkit", "models")
        out = {}
        if os.path.isdir(md):
            for f in os.listdir(md):
                if f.lower().endswith('.ilp'):
                    out[f] = os.path.join(md, f)
        return out

    def _close_transient_windows(self, tokens):
        """Close any open image windows whose title contains one of `tokens`.
        ilastik4ij displays its output (e.g. 'predictions.h5/exported_data')
        regardless of IJ1 batch mode; this removes those stray windows."""
        from ij import WindowManager
        ids = WindowManager.getIDList()
        if not ids:
            return
        toks = [t.lower() for t in tokens]
        for i in list(ids):
            img = WindowManager.getImage(i)
            if img is None:
                continue
            title = (img.getTitle() or "").lower()
            if any(t in title for t in toks):
                img.changes = False
                img.close()

    def _ilp_workflow_name(self, path):
        """Read /workflowName from an ilastik .ilp, or None (best-effort)."""
        from java.lang import Throwable
        reader = None
        try:
            from ch.systemsx.cisd.hdf5 import HDF5Factory
            from java.io import File
            reader = HDF5Factory.openForReading(File(path))
            try:
                return reader.readString("/workflowName")
            except (Exception, Throwable):
                return None
        except (Exception, Throwable):
            return None
        finally:
            if reader is not None:
                try:
                    reader.close()
                except (Exception, Throwable):
                    pass
