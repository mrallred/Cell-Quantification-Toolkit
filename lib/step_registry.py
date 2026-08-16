"""
Discovery + construction of pipeline step providers from the steps/ folder.

Mirrors the workflow-discovery approach: base_step is loaded first, then each
provider file is executed with StepProvider injected, and StepProvider subclasses
are indexed by (stage, type_id). New providers are added by dropping a file in
steps/ - no registration needed.
"""
import os
import sys

from ij import IJ

_CACHE = None  # {(stage, type_id): provider_class}


def _toolkit_dir():
    return os.path.join(IJ.getDirectory("plugins"), "Cell_Quantification_Toolkit")


def _steps_dir():
    return os.path.join(_toolkit_dir(), "steps")


def discover(force=False):
    """Return {(stage, type_id): class}, importing provider files once (cached)."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE

    registry = {}
    steps_dir = _steps_dir()
    try:
        if not os.path.isdir(steps_dir):
            IJ.log("steps/ folder not found: " + steps_dir)
            _CACHE = registry
            return registry

        if steps_dir not in sys.path:
            sys.path.insert(0, steps_dir)

        base_path = os.path.join(steps_dir, "base_step.py")
        base_ns = {}
        execfile(base_path, base_ns)  # noqa: F821 (Jython builtin)
        StepProvider = base_ns['StepProvider']

        for fn in os.listdir(steps_dir):
            if not fn.endswith('.py') or fn.startswith('_') or fn == 'base_step.py':
                continue
            path = os.path.join(steps_dir, fn)
            try:
                ns = {'StepProvider': StepProvider}
                with open(path, 'r') as f:
                    src = f.read()
                exec(compile(src, path, 'exec'), ns)
                for name, obj in ns.items():
                    if (isinstance(obj, type) and issubclass(obj, StepProvider)
                            and obj is not StepProvider):
                        if obj.stage and obj.type_id:
                            registry[(obj.stage, obj.type_id)] = obj
            except Exception as e:
                IJ.log("Error loading step provider '{}': {}".format(fn, e))
    except Exception as e:
        IJ.log("Error discovering step providers: " + str(e))

    _CACHE = registry
    return registry


def get_class(stage, type_id):
    return discover().get((stage, type_id))


def create_provider(stage, type_id, params):
    """Instantiate a provider, or None if the type is unknown."""
    cls = get_class(stage, type_id)
    if cls is None:
        return None
    return cls(params)


def providers_for(stage):
    """List of provider classes for a stage (for editor dropdowns)."""
    return [cls for (st, _tid), cls in discover().items() if st == stage]
