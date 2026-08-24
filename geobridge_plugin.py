# -*- coding: utf-8 -*-
"""
geobridge_plugin
~~~~~~~~~~~~~~~~

Main plugin class: registers the toolbar/menu entry and owns the single
GeoBridgePluginDialog instance. Structure mirrors test_temporal.py (a
Plugin-Builder-generated scaffold already in use in this QGIS profile).
"""

from __future__ import annotations

import os.path

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QTranslator
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .geobridge_plugin_dialog import GeoBridgePluginDialog


class GeoBridgePlugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = QSettings().value("locale/userLocale")
        locale = locale[0:2] if locale else "en"
        locale_path = os.path.join(self.plugin_dir, "i18n", f"GeoBridge_{locale}.qm")
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&GeoBridge")
        self.first_start = None
        self.dlg = None

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        return QCoreApplication.translate("GeoBridgePlugin", message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.iface.addToolBarIcon(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(
            icon_path,
            text=self.tr("GeoBridge"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )
        self.first_start = True

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&GeoBridge"), action)
            self.iface.removeToolBarIcon(action)
        if self.dlg is not None:
            self.dlg.cleanup()

    def run(self):
        if self.first_start:
            self.first_start = False
            self.dlg = GeoBridgePluginDialog(self.iface)

        # Non-modal: dialog stays open while the user interacts with the map.
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
