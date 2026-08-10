"""Tests for the fail-fast scc presence check and the --install-scc helper."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from repo_metadata_cli import scc_check


def test_ensure_scc_returns_path_when_present(monkeypatch):
    monkeypatch.setattr(scc_check.shutil, "which", lambda _: "/usr/local/bin/scc")
    assert scc_check.ensure_scc() == Path("/usr/local/bin/scc")


def test_ensure_scc_exits_with_instructions_when_missing(monkeypatch):
    monkeypatch.setattr(scc_check.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        scc_check.ensure_scc()
    message = str(exc.value)
    assert "scc" in message
    assert "brew install scc" in message
    assert "--install-scc" in message


def _fake_release_archive(binary_name: str = "scc") -> bytes:
    payload = b"#!/bin/sh\necho scc\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(binary_name)
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_install_scc_extracts_executable_binary(monkeypatch, tmp_path):
    data = _fake_release_archive()
    monkeypatch.setattr(
        scc_check.urllib.request, "urlretrieve",
        lambda url, dest: Path(dest).write_bytes(data),
    )
    target = scc_check.install_scc(target_dir=tmp_path)
    assert target == tmp_path / "scc"
    assert target.exists()
    assert os.access(target, os.X_OK)


def test_install_scc_fails_on_archive_without_binary(monkeypatch, tmp_path):
    data = _fake_release_archive(binary_name="README.txt")
    monkeypatch.setattr(
        scc_check.urllib.request, "urlretrieve",
        lambda url, dest: Path(dest).write_bytes(data),
    )
    with pytest.raises(RuntimeError):
        scc_check.install_scc(target_dir=tmp_path)


def test_ensure_scc_auto_installs_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(scc_check.shutil, "which", lambda _: None)
    binary = tmp_path / "scc"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(scc_check, "install_scc", lambda target_dir=None: binary)
    assert scc_check.ensure_scc(auto_install=True) == binary


def test_release_url_unsupported_platform(monkeypatch):
    monkeypatch.setattr(scc_check.sys, "platform", "win32")
    with pytest.raises(RuntimeError):
        scc_check._release_url()
