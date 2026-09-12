# -*- coding: utf-8 -*-
"""
gdal_native.http_utils
~~~~~~~~~~~~~~~~~~~~~~

One shared entry point for every urllib.request.urlopen() call in this
package. Bandit's B310 flags a bare urlopen() unconditionally, since a
URL whose scheme is something other than http/https (e.g. "file://" or
"ftp://") would make it read a local file or otherwise behave
unexpectedly instead of making the plain network request the code
actually intends. Every URL this plugin opens ultimately comes from
either a hardcoded https:// constant or a CDS/ARCO API response (job
status links, discovery download URLs) - normally safe, but validating
the scheme here closes off that whole class of surprise explicitly,
rather than relying on "the server we already trust wouldn't do that."
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def safe_urlopen(url_or_request, timeout=None):
    """urllib.request.urlopen(), refusing anything whose scheme isn't
    http/https instead of opening it."""
    url = (
        url_or_request.full_url
        if isinstance(url_or_request, urllib.request.Request)
        else url_or_request
    )
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Refusing to open URL with scheme {scheme!r} (only http/https "
            f"allowed): {url!r}"
        )
    return urllib.request.urlopen(url_or_request, timeout=timeout)  # nosec B310
