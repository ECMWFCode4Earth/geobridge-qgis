# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GeoBridge
                                 A QGIS plugin
 Discover, semantically search, and preview Copernicus climate data as WMTS
 layers in QGIS, powered by the geobridge Python library.
                             -------------------
        begin                : 2026-07-05
        copyright            : (C) 2026 by GeoBridge contributors
        email                : iliasmachairas@outlook.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""

import sys


class _NullWriter:
    """No-op file-like object.

    QGIS only wires sys.stdout/sys.stderr up to its Python Console once
    that panel has been opened at least once in the session — until then
    both are None (QGIS's main executable has no attached console).
    Some of geobridge's dependencies (numpy, scikit-learn) write
    deprecation notices straight to sys.stderr instead of going through
    the warnings module, which raises AttributeError when it's None —
    surfaced as noisy (but harmless) WARNING entries in the QGIS log
    panel. Substituting a no-op writer avoids that without requiring the
    user to ever open the console.
    """

    def write(self, *args, **kwargs):
        pass

    def flush(self, *args, **kwargs):
        pass


if sys.stdout is None:
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load GeoBridgePlugin class from file geobridge_plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .geobridge_plugin import GeoBridgePlugin
    return GeoBridgePlugin(iface)
