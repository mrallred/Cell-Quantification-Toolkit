"""
Bundled workflow definitions, as Python data.

WHY THIS ISN'T JUST workflow_defs/*.json
    The ImageJ updater only checksums a fixed set of extensions under plugins/
    (.jar .class .txt .ijm .py .rb .clj .js .bsh .groovy .gvy). A .json file in
    plugins/Cell_Quantification_Toolkit/workflow_defs/ is invisible to it, so the
    bundled definitions can never travel over the update site as JSON. Carrying
    them as a .py module -- which IS tracked -- and materializing them on first
    run is what gets built-in workflows onto a fresh install.

    WorkflowStore.seed_builtins() writes each definition that has not been seeded
    before and whose file is absent. A '.seeded' marker in workflow_defs/ records
    what has already been written, so a workflow the user deletes stays deleted,
    while definitions added in a later release are still picked up.

These are plain schema-v2 dicts, the same shape WorkflowDefinition.to_dict()
emits -- edit one here and it seeds on the next fresh install. The automated ones
name .ilp files that are NOT shipped with the plugin (classifiers are too large
for an update site); they will fail validation until the user supplies a matching
model in models/ or repoints the workflow. The manual workflows need no models
and work out of the box.
"""

BUILTIN_WORKFLOWS = [
    {
        'schema_version': 2,
        'kind': 'pipeline',
        'name': 'Brightfield cFos',
        'description': 'Single-label cell detection for DAB-stained cFos tissue.',
        'segmentation': {
            'type': 'ilastik_pixel',
            'params': {'project': 'BrightField_cFos_Pixel.ilp', 'append_lab': False},
        },
        'classification': {
            'type': 'ilastik_object',
            'params': {'project': 'BrightField_cFos_Object.ilp'},
        },
        'classes': [
            {'label': 1, 'key': 'cfos', 'display': 'cFos',
             'color': [255, 0, 0], 'include': True},
            {'label': 2, 'key': 'artifact', 'display': 'Artifact',
             'color': [128, 128, 128], 'include': False},
        ],
        'post': {'apply_watershed': True, 'exclude_edges': True,
                 'min_cell_size': 10, 'min_circularity': 0.0},
    },
    {
        'schema_version': 2,
        'kind': 'pipeline',
        'name': 'Brightfield Costained cFos + CtB (RGB+Lab)',
        'description': ("Costain workflow with the pixel stage's 'Append L*a*b*' option ON: "
                        "each crop is expanded to a 6-channel R,G,B,L*,a*,b* image before "
                        "ilastik. Single-class pixel segmentation; multi-class object "
                        "classification. Both .ilp models must be trained on images exported "
                        "with macros/Export_RGB_plus_Lab_for_Training.py."),
        'segmentation': {
            'type': 'ilastik_pixel',
            'params': {'project': 'BrightField_cFos-CtB_RGB-Lab_pixel_classifier_v1.ilp',
                       'append_lab': True},
        },
        'classification': {
            'type': 'ilastik_object',
            'params': {'project': 'BrightField_cFos-CtB_RGB-Lab_object_classifier_v1.ilp'},
        },
        'classes': [
            {'label': 1, 'key': 'cfos', 'display': 'cFos',
             'color': [255, 225, 25], 'include': True},
            {'label': 2, 'key': 'ctb', 'display': 'CtB',
             'color': [0, 130, 200], 'include': True},
            {'label': 3, 'key': 'cfos_ctb', 'display': 'cFos+CtB',
             'color': [230, 25, 75], 'include': True},
            {'label': 4, 'key': 'artifcat_background', 'display': 'Artifcat/background',
             'color': [70, 240, 240], 'include': False},
        ],
        'post': {'apply_watershed': False, 'exclude_edges': False,
                 'min_cell_size': 10, 'min_circularity': 0.0},
    },
    {
        'schema_version': 2,
        'kind': 'manual',
        'name': 'Manual cFos',
        'description': ('Place points on cells per class; points inside each ROI are '
                        'counted and exported.'),
        'classes': [
            {'label': 1, 'key': 'cfos', 'display': 'cFos',
             'color': [255, 0, 0], 'include': True},
        ],
    },
    {
        'schema_version': 2,
        'kind': 'manual',
        'name': 'Manual cFos-CtB costain',
        'description': ('Place points on cells per class; points inside each ROI are '
                        'counted and exported.'),
        'classes': [
            {'label': 1, 'key': 'cfos', 'display': 'cFos',
             'color': [255, 0, 0], 'include': True},
            {'label': 2, 'key': 'ctb', 'display': 'CtB',
             'color': [0, 255, 255], 'include': True},
            {'label': 3, 'key': 'cfos_ctb', 'display': 'cFos+CtB',
             'color': [255, 255, 0], 'include': True},
        ],
    },
]


def builtin_definitions():
    """Deep-ish copies, so a caller mutating a seeded dict can't corrupt the
    module-level originals for the rest of the session."""
    out = []
    for d in BUILTIN_WORKFLOWS:
        c = dict(d)
        c['classes'] = [dict(cl) for cl in d.get('classes', [])]
        for stage in ('segmentation', 'classification'):
            if stage in c:
                c[stage] = {'type': c[stage].get('type'),
                            'params': dict(c[stage].get('params', {}))}
        if 'post' in c:
            c['post'] = dict(c['post'])
        out.append(c)
    return out
