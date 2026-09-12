# -*- coding: utf-8 -*-
"""
gdal_native.cds_download
~~~~~~~~~~~~~~~~~~~~~~~~

CDS API download path (OGC API Processes: submit, poll, download) for
datasets not available as ARCO Zarr — job submission/polling/format
sniffing ported near-verbatim from geobridge/modules/cds_download.py
(already pure stdlib there: urllib.request, json, time, re — no
`requests` even needed for this part, so it stays that way).

What's NOT ported as-is: the NetCDF/GRIB -> GeoTIFF conversion. The
original used xarray/rioxarray to hand-detect lat/lon dimensions and
stack non-spatial dimensions (time, level, ensemble) into bands. This
version uses GDAL's own NetCDF/GRIB drivers instead, which already do
that dimension-to-band unrolling natively (each driver exposes one band
per time/level combination, with descriptive band metadata) — see
extract_gdal.py.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import auth

logger = logging.getLogger(__name__)

CDS_API_BASE = "https://cds.climate.copernicus.eu/api/retrieve/v1"

_POLL_INITIAL = 5.0
_POLL_MAX = 60.0
_POLL_BACKOFF = 15.0


class CdsApiError(Exception):
    """Raised when the CDS API returns an error."""


class CdsJobTimeout(Exception):
    """Raised when a CDS job does not complete within the timeout."""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    return {
        "PRIVATE-TOKEN": auth.get_token(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_auth_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _download_file(url: str, dest_file, timeout: int = 300) -> None:
    """Stream the response straight into an already-open file object.

    Deliberately doesn't take a path and open/write/close it separately:
    on Windows, closing the just-created temp file and immediately
    reopening it for writing left a gap where antivirus real-time
    scanning could grab the file first, failing the reopen with
    "[WinError 32] The process cannot access the file because it is
    being used by another process." Writing through the handle the
    caller already has open (from NamedTemporaryFile) avoids that gap.
    """
    import shutil

    req = urllib.request.Request(url, headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        shutil.copyfileobj(resp, dest_file)


# ---------------------------------------------------------------------------
# Job submission and polling
# ---------------------------------------------------------------------------

def _submit_job(dataset_id: str, request: dict) -> str:
    cds_id = dataset_id.replace("_", "-")
    url = f"{CDS_API_BASE}/processes/{cds_id}/execution"
    payload = {"inputs": request}

    try:
        resp = _post_json(url, payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 401:
            dataset_page = f"https://cds.climate.copernicus.eu/datasets/{cds_id}"
            raise CdsApiError(
                f"CDS API rejected the request for '{cds_id}' (HTTP 401 - permission denied).\n\n"
                "Most likely cause: you need to accept the dataset's licence on the CDS portal.\n\n"
                f"  1. Open: {dataset_page}\n"
                "  2. Scroll to the bottom and click 'Accept Terms'\n"
                "  3. Re-run this request\n\n"
                "If you have already accepted the licence, verify your API key.\n\n"
                f"Raw response:\n{body}"
            ) from exc
        raise CdsApiError(
            f"CDS API rejected the request for '{cds_id}' (HTTP {exc.code}):\n{body}\n\n"
            "Common causes: invalid parameter combination, licence not accepted, "
            "or dataset not available via this API endpoint."
        ) from exc

    job_id = resp.get("jobID") or resp.get("id")
    if not job_id:
        raise CdsApiError(f"No job ID in response: {resp}")

    status_url = f"{CDS_API_BASE}/jobs/{job_id}"
    logger.info("CDS job submitted: %s", job_id)
    return status_url


def _poll_job(status_url: str, timeout_seconds: float = 3600, progress_callback=None) -> str:
    """Poll the job until it succeeds or fails. Returns the download URL."""
    interval = _POLL_INITIAL
    start = time.monotonic()

    while True:
        try:
            status = _get_json(status_url)
        except Exception as exc:
            logger.warning("Poll failed: %s - retrying", exc)
            time.sleep(interval)
            continue

        job_status = status.get("status", "")
        message = status.get("message", "")

        if progress_callback:
            progress_callback(f"CDS job status: {job_status} - {message}")

        if job_status in ("successful", "finished"):
            for link in status.get("links", []):
                if link.get("rel") in ("result", "download"):
                    return link["href"]
            results_url = status_url + "/results"
            results = _get_json(results_url)
            try:
                return results["asset"]["value"]["href"]
            except (KeyError, TypeError) as exc:
                raise CdsApiError(
                    f"Could not find a download URL in results response: {results}"
                ) from exc

        if job_status in ("failed", "dismissed", "error"):
            detail = status.get("detail") or status.get("message") or str(status)
            raise CdsApiError(
                f"CDS job failed:\n{detail}\n\n"
                "If the error mentions 'licence', visit the dataset page on "
                "https://cds.climate.copernicus.eu and accept the terms of use."
            )

        elapsed = time.monotonic() - start
        if elapsed > timeout_seconds:
            raise CdsJobTimeout(
                f"CDS job did not complete within {timeout_seconds/60:.0f} minutes.\n"
                f"Status URL: {status_url}\n"
                "The job is still running on the CDS server - you can check its "
                "status at https://cds.climate.copernicus.eu/requests"
            )

        logger.debug("CDS job %s after %.0fs - waiting %.0fs", job_status, elapsed, interval)
        time.sleep(interval)
        interval = min(interval * _POLL_BACKOFF, _POLL_MAX)


# ---------------------------------------------------------------------------
# Format sniffing (trust the bytes on disk, not the requested format - the
# CDS API sometimes returns GRIB or a ZIP-of-files regardless of what was
# requested)
# ---------------------------------------------------------------------------

_MAGIC = {
    b"CDF": "netcdf",
    b"\x89HDF\r\n\x1a\n": "netcdf",
    b"GRIB": "grib",
    b"PK\x03\x04": "zip",
}


def sniff_format(path: Path) -> str:
    # This is the very first read of a file this process just finished
    # writing and closed (in cds_to_geotiff, right before calling here) -
    # on Windows, that's exactly the window where Defender's real-time
    # scan can grab a freshly-written file for a moment, so a plain
    # open() here intermittently failed with "[WinError 32] The process
    # cannot access the file because it is being used by another
    # process" even after the earlier download-side race (closing then
    # reopening our *own* handle) was already fixed - this is a second,
    # different race, with an external process rather than ourselves.
    # Defender's lock is brief, so a few short retries clear it.
    attempts, delay = 5, 0.2
    last_exc = None
    for _ in range(attempts):
        try:
            with open(path, "rb") as fh:
                head = fh.read(8)
            break
        except (PermissionError, OSError) as exc:
            if getattr(exc, "winerror", None) != 32:
                raise
            last_exc = exc
            time.sleep(delay)
    else:
        raise last_exc

    for magic, fmt in _MAGIC.items():
        if head.startswith(magic):
            return fmt
    return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cds_to_geotiff(
    dataset: str,
    request: dict,
    variable: Optional[str] = None,
    bbox: Optional[tuple] = None,
    output_path: Optional[str] = None,
    timeout: float = 3600,
    progress_callback=None,
    cog: bool = True,
    allow_unsupported: bool = False,
) -> Path:
    """Download a dataset from the CDS API and convert to GeoTIFF via GDAL.

    Submits a CDS API job, polls until it completes, downloads the result,
    and converts it with GDAL (see extract_gdal.raster_file_to_geotiff).
    """
    import tempfile

    from . import discover as discover_mod
    from . import extract_gdal

    if not allow_unsupported:
        try:
            desc = discover_mod.discover_one(dataset)
        except Exception:
            desc = None
        if desc is not None and desc.has_cds_retrieve and not desc.cds_download_supported:
            raise CdsApiError(
                f"'{dataset}' is not on the validated cds_to_geotiff list.\n"
                "Only a few CDS datasets are confirmed to convert correctly "
                "(see discover(cds_download_only=True)).\n\n"
                "To try this dataset anyway, pass allow_unsupported=True."
            )

    if "data_format" not in request:
        request = dict(request)
        request["data_format"] = "netcdf"

    if variable is None:
        vars_in_request = request.get("variable", [])
        if isinstance(vars_in_request, str):
            variable = vars_in_request
        elif isinstance(vars_in_request, list) and len(vars_in_request) == 1:
            variable = vars_in_request[0]

    if progress_callback:
        progress_callback(f"Submitting CDS request for {dataset}...")

    status_url = _submit_job(dataset, request)

    if progress_callback:
        progress_callback("Job queued - waiting for CDS server...")

    download_url = _poll_job(status_url, timeout_seconds=timeout, progress_callback=progress_callback)

    if progress_callback:
        progress_callback("Downloading result file...")

    download_error = None
    with tempfile.NamedTemporaryFile(suffix=".download", delete=False) as f:
        raw_path = Path(f.name)
        try:
            _download_file(download_url, f)
        except Exception as exc:
            download_error = exc

    if download_error is not None:
        raw_path.unlink(missing_ok=True)
        raise CdsApiError(f"Download failed: {download_error}") from download_error

    if progress_callback:
        progress_callback("Converting to GeoTIFF...")

    if output_path is None:
        output_path = raw_path.with_suffix(".tif")
    else:
        output_path = Path(output_path)

    try:
        result = extract_gdal.raster_file_to_geotiff(
            raw_path, variable or "", bbox, output_path, cog=cog,
        )
    finally:
        raw_path.unlink(missing_ok=True)

    if progress_callback:
        progress_callback(f"Done: {result.name}")

    return result
