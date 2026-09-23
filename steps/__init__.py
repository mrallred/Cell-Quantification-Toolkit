"""
Pipeline step providers.

Files in this folder are discovered and loaded by lib/step_registry.py, which
puts this directory on sys.path and execfile()s them with StepProvider injected.
Nothing imports `steps` as a package, so this file is only a marker.

DO NOT LEAVE THIS FILE EMPTY. The ImageJ updater stages downloads in the
`update/` folder, and Installer.moveUpdatedIntoPlace() treats a staged file of
length 0 as an instruction to DELETE the target:

    if (file.length() == 0) {
        if (targetFile.exists()) deleteOrThrowException(targetFile);
        deleteOrThrowException(file);
    }

A zero-byte file therefore cannot be shipped over an update site: it is either
silently never installed, or the delete fails and the user gets
"Could not remove '<path>'" on the next Fiji restart. Keeping a docstring here
keeps the file non-empty and the install clean.
"""
