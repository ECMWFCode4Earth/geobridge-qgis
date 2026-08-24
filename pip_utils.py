# -*- coding: utf-8 -*-
"""
pip_utils
~~~~~~~~~

Pure subprocess helper for installing packages into QGIS's own Python
interpreter. Zero Qt imports — the QThread wrapper lives in
dependency_installer.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Union


@dataclass
class PipResult:
    ok: bool
    returncode: int
    log: str


def _looks_like_qgis_binary(path: str) -> bool:
    """True if `path` is QGIS itself rather than a real Python interpreter.

    On Windows, `sys.executable` inside QGIS's embedded Python is often
    `qgis-bin.exe` / `qgis.exe` — QGIS embeds Python rather than being
    launched by it. Running that path with `-m pip ...` doesn't invoke
    pip: it launches a whole new QGIS process with those strings as
    command-line arguments, which QGIS's CLI then treats as files to open
    as layers (surfacing as "Invalid Data Source" for "install",
    "--upgrade", and the package name, and popping open a new project).
    """
    name = os.path.basename(path).lower()
    return "qgis" in name


def resolve_python_executable() -> str:
    """Return a real Python interpreter path, not QGIS's own binary.

    `sys.exec_prefix` points at the Python installation's prefix
    regardless of what `sys.executable` reports, so `sys.exec_prefix /
    python(3).exe` reliably finds the actual bundled interpreter on a
    Windows QGIS install. Falls back to `sys.executable` unchanged if it
    doesn't look like QGIS itself (e.g. Linux/Mac, or a Windows install
    where sys.executable already is a real python.exe).
    """
    if not _looks_like_qgis_binary(sys.executable):
        return sys.executable

    candidates = [
        os.path.join(sys.exec_prefix, "python3.exe"),
        os.path.join(sys.exec_prefix, "python.exe"),
        os.path.join(sys.exec_prefix, "bin", "python3"),
        os.path.join(sys.exec_prefix, "bin", "python"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    # Nothing found — return sys.executable anyway so the caller's
    # subprocess call fails visibly (via pip_install's exception handling
    # or QGIS's own log) rather than silently picking something wrong.
    return sys.executable


def pip_install(specs: Union[str, list], timeout: int = 600) -> PipResult:
    """Install/upgrade `specs` into the exact interpreter QGIS is running
    under. `specs` may be a single requirement string or a list of them —
    all installed in one pip invocation (e.g. ``["geobridge[zarr]",
    "aiohttp", "requests"]``).

    Uses `resolve_python_executable() -m pip` rather than a bare `pip` on
    PATH or a naive `sys.executable` — see `_looks_like_qgis_binary`'s
    docstring for why `sys.executable` alone is not safe to use here on
    Windows.

    Returns a PipResult with combined stdout+stderr in `.log`, so pip
    failures (e.g. PermissionError on a non-writable "for all users" QGIS
    install) are visible to the caller instead of silently swallowed.
    """
    if isinstance(specs, str):
        specs = [specs]

    python_exe = resolve_python_executable()
    if _looks_like_qgis_binary(python_exe):
        return PipResult(
            ok=False,
            returncode=-1,
            log=(
                "Could not locate a real Python interpreter distinct from QGIS "
                f"itself (sys.executable={sys.executable!r}, "
                f"sys.exec_prefix={sys.exec_prefix!r}). Please install geobridge "
                "manually into QGIS's Python environment (e.g. via the OSGeo4W "
                "Shell: `python3 -m pip install geobridge`)."
            ),
        )

    cmd = [python_exe, "-m", "pip", "install", "--upgrade", *specs]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # subprocess.SubprocessError, OSError, etc.
        return PipResult(ok=False, returncode=-1, log=str(exc))

    log = (result.stdout or "") + "\n" + (result.stderr or "")
    return PipResult(ok=result.returncode == 0, returncode=result.returncode, log=log)
