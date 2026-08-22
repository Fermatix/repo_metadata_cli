"""Upload a generated metadata CSV to the CRM import API."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

_IMPORT_PATH = "api/import/"
_MAX_RETRIES = 5
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_REQUEST_TIMEOUT = 360


class CRMUploadError(RuntimeError):
    """The CSV could not be uploaded or the CRM returned an invalid response."""


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return response.text.strip() or response.reason or "Unknown error"


def _retry_delay(response: requests.Response, fallback: float) -> float:
    value = response.headers.get("Retry-After")
    if value is None:
        return fallback
    try:
        return max(float(value), 0.0)
    except ValueError:
        return fallback


def _http_post(
    *, url: str, csv_path: Path, login: str, password: str
) -> requests.Response:
    delay = 2.0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with csv_path.open("rb") as csv_file:
                response = requests.post(
                    url,
                    data={"login": login, "password": password},
                    files={"file": (csv_path.name, csv_file, "text/csv")},
                    timeout=_REQUEST_TIMEOUT,
                )
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES:
                raise CRMUploadError(
                    f"CRM upload failed after {_MAX_RETRIES} attempts: {exc}"
                ) from exc
            logger.warning(
                "CRM upload error (attempt %d/%d): %s; retrying in %.0fs",
                attempt,
                _MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code in _RETRY_STATUSES:
            if attempt == _MAX_RETRIES:
                raise CRMUploadError(
                    f"CRM upload failed with HTTP {response.status_code} after "
                    f"{_MAX_RETRIES} attempts: {_response_error(response)}"
                )
            retry_after = _retry_delay(response, delay)
            logger.warning(
                "CRM returned HTTP %d (attempt %d/%d); retrying in %.0fs",
                response.status_code,
                attempt,
                _MAX_RETRIES,
                retry_after,
            )
            time.sleep(retry_after)
            delay = max(delay * 2, retry_after)
            continue

        if response.status_code >= 400:
            raise CRMUploadError(
                f"CRM returned HTTP {response.status_code}: {_response_error(response)}"
            )
        return response

    raise CRMUploadError("CRM upload failed without a response.")


def upload_csv(
    *, csv_path: Path, login: str, password: str, crm_url: str
) -> dict[str, Any]:
    """Upload ``csv_path`` and return the CRM import report."""
    if not csv_path.is_file():
        raise CRMUploadError(f"CSV file not found: {csv_path}")

    url = urljoin(crm_url.rstrip("/") + "/", _IMPORT_PATH)
    response = _http_post(url=url, csv_path=csv_path, login=login, password=password)
    try:
        report = response.json()
    except ValueError as exc:
        raise CRMUploadError("CRM returned invalid JSON.") from exc

    required_keys = {
        "success_count",
        "created_count",
        "updated_count",
        "errors",
        "total_rows",
    }
    if not isinstance(report, dict) or not required_keys.issubset(report):
        raise CRMUploadError("CRM returned an incomplete import report.")
    if not isinstance(report["errors"], list):
        raise CRMUploadError("CRM returned an invalid errors list.")
    return report
