from pathlib import Path

import pytest

from repo_metadata_cli import crm_upload

REPORT = {
    "success_count": 2,
    "created_count": 1,
    "updated_count": 1,
    "errors": [],
    "total_rows": 2,
}


class FakeResponse:
    def __init__(self, status_code, payload, *, headers=None, reason=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.reason = reason
        self.text = ""

    def json(self):
        return self._payload


def test_upload_retries_server_error_and_reopens_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_bytes(b"repo_id,partner_name\n1,partner\n")
    calls = []
    sleeps = []

    def fake_post(url, *, data, files, timeout):
        filename, file_handle, content_type = files["file"]
        calls.append(
            {
                "url": url,
                "data": data,
                "filename": filename,
                "body": file_handle.read(),
                "content_type": content_type,
                "timeout": timeout,
            }
        )
        if len(calls) == 1:
            return FakeResponse(503, {"error": "temporarily unavailable"})
        return FakeResponse(200, REPORT)

    monkeypatch.setattr(crm_upload.requests, "post", fake_post)
    monkeypatch.setattr(crm_upload.time, "sleep", sleeps.append)

    report = crm_upload.upload_csv(
        csv_path=csv_path,
        login="login",
        password="secret",
        crm_url="https://crm.example/base/",
    )

    assert report == REPORT
    assert len(calls) == 2
    assert calls[0]["url"] == "https://crm.example/base/api/import/"
    assert calls[0]["data"] == {"login": "login", "password": "secret"}
    assert calls[0]["filename"] == "metadata.csv"
    assert calls[0]["body"] == calls[1]["body"] == csv_path.read_bytes()
    assert calls[0]["content_type"] == "text/csv"
    assert calls[0]["timeout"] == 360
    assert sleeps == [2.0]


def test_upload_retries_network_error(tmp_path, monkeypatch):
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text("repo_id,partner_name\n", encoding="utf-8")
    attempts = 0

    def fake_post(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise crm_upload.requests.ConnectionError("connection reset")
        return FakeResponse(200, REPORT)

    monkeypatch.setattr(crm_upload.requests, "post", fake_post)
    monkeypatch.setattr(crm_upload.time, "sleep", lambda delay: None)

    crm_upload.upload_csv(
        csv_path=csv_path,
        login="login",
        password="secret",
        crm_url="https://crm.example/",
    )

    assert attempts == 2


def test_upload_reports_non_retryable_api_error(tmp_path, monkeypatch):
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text("repo_id,partner_name\n", encoding="utf-8")
    monkeypatch.setattr(
        crm_upload.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            401, {"error": "Invalid login or password."}
        ),
    )

    with pytest.raises(crm_upload.CRMUploadError, match="HTTP 401.*Invalid login"):
        crm_upload.upload_csv(
            csv_path=csv_path,
            login="login",
            password="wrong",
            crm_url="https://crm.example/",
        )


def test_upload_requires_existing_csv(tmp_path):
    with pytest.raises(crm_upload.CRMUploadError, match="CSV file not found"):
        crm_upload.upload_csv(
            csv_path=Path(tmp_path / "missing.csv"),
            login="login",
            password="secret",
            crm_url="https://crm.example/",
        )
