import sys
import os
from ij import IJ
from javax.swing import SwingUtilities

DEV_MODE = True

def dev_reload_toolkit_modules(base_dir, package_name="toolkit_lib"):
    """
    Safely reloads all modules within a specific package by first unloading
    them from memory and then deleting their compiled .class files.
    """
    modules_to_unload = [name for name in sys.modules if name.startswith(package_name)]
    
    for module_name in modules_to_unload:
        del sys.modules[module_name]
        
    package_path = os.path.join(base_dir, package_name)
    if not os.path.isdir(package_path):
        return # Exit if the package folder doesn't exist.

    for root, dirs, files in os.walk(package_path):
        for name in files:
            if name.endswith(".class"):
                # Jython creates $ for inner classes. This handles files like
                # 'ROIEditor$TextFieldUpdater.class' by checking for 'ROIEditor.py'.
                base_name = os.path.splitext(name)[0].split('$')[0]
                py_equivalent = base_name + ".py"
                
                if py_equivalent in files:
                    class_file_path = os.path.join(root, name)
                    try:
                        os.remove(class_file_path)
                    except OSError as e:
                        print("Error removing file {}: {}".format(class_file_path, e))

plugins_dir = IJ.getDirectory("plugins")
plugin_folder_name = "Cell_Quantification_Toolkit"
script_dir = os.path.join(plugins_dir, plugin_folder_name)
if script_dir not in sys.path:
    sys.path.append(script_dir)

if DEV_MODE:
    dev_reload_toolkit_modules(script_dir, package_name="toolkit_lib")

from toolkit_lib.main_gui import ProjectManagerGUI

def create_and_show_gui():
    """Initializes and displays the main application window."""
    gui = ProjectManagerGUI()
    gui.show()

if __name__ == '__main__':
    SwingUtilities.invokeLater(create_and_show_gui)