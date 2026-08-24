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


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load GeoBridgePlugin class from file geobridge_plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .geobridge_plugin import GeoBridgePlugin
    return GeoBridgePlugin(iface)
