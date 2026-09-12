# -*- coding: utf-8 -*-
"""
gdal_native.auth
~~~~~~~~~~~~~~~~

CDS API key handling — ported near-verbatim from geobridge/auth.py
(already pure stdlib there: os, pathlib). Credentials resolved in
priority order: explicit key argument, CDS_API_KEY env var, ~/.cdsapirc.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://cds.climate.copernicus.eu/api"
_CONFIG_FILE = pathlib.Path.home() / ".cdsapirc"

_session: Optional["_AuthSession"] = None


class AuthenticationError(RuntimeError):
    """Raised when credentials are missing or improperly formatted."""


class _AuthSession:
    def __init__(self, api_key: str, api_url: str) -> None:
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")

    def get_token(self) -> str:
        return self.api_key

    @property
    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}


def authenticate(key: Optional[str] = None, url: str = _DEFAULT_API_URL) -> None:
    global _session
    resolved_key = _resolve_key(key)
    _session = _AuthSession(api_key=resolved_key, api_url=url)
    logger.info("gdal_native: authenticated (key ending ...%s)", resolved_key[-6:])


def get_token() -> str:
    return _require_session().get_token()


def auth_header() -> dict:
    """Return the HTTP Authorization header dict for CDS/ARCO requests —
    pass straight to `requests` or GDAL's GDAL_HTTP_HEADERS config option."""
    return _require_session().auth_header


def is_authenticated() -> bool:
    return _session is not None


def _require_session() -> _AuthSession:
    if _session is None:
        raise AuthenticationError(
            "Not authenticated. Call gdal_native.authenticate() first."
        )
    return _session


def _resolve_key(explicit_key: Optional[str]) -> str:
    if explicit_key:
        return explicit_key.strip()

    env_key = os.environ.get("CDS_API_KEY")
    if env_key:
        return env_key.strip()

    if _CONFIG_FILE.exists():
        for line in _CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("key"):
                separator = ":" if ":" in line else "="
                _, _, value = line.partition(separator)
                value = value.strip().strip('"').strip("'")
                if value:
                    return value

    raise AuthenticationError(
        "No CDS API key found. Provide one via:\n"
        "  1. authenticate(key='your-key')\n"
        "  2. export CDS_API_KEY='your-key'\n"  # pragma: allowlist secret - placeholder text, not a real key
        "  3. echo 'key: your-key' >> ~/.cdsapirc\n\n"
        "Get your key at: https://cds.climate.copernicus.eu/profile"
    )
