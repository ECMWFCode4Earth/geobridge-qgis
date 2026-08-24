# -*- coding: utf-8 -*-
"""
dependency_installer
~~~~~~~~~~~~~~~~~~~~

QThread wrapper around pip_utils.pip_install, so a pip install (which can
take anywhere from a few seconds to a couple of minutes) never blocks the
QGIS UI thread. This is the only file besides the dialog that imports both
Qt and pip_utils.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QThread, pyqtSignal

from . import pip_utils


class DependencyInstaller(QThread):
    """Runs `pip install <specs>` off the UI thread. `specs` may be a single
    requirement string or a list of them, installed together in one pip call.

    Connect to `finished_ok`/`finished_err` before calling `.start()`.
    Both signals carry the combined pip stdout+stderr log text.
    """

    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, specs, parent=None):
        super().__init__(parent)
        self.specs = specs

    def run(self):
        result = pip_utils.pip_install(self.specs)
        if result.ok:
            self.finished_ok.emit(result.log)
        else:
            self.finished_err.emit(result.log)
