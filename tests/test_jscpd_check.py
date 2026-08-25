"""Tests for the fail-fast jscpd presence check."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from repo_metadata_cli import jscpd_check


def _fake_run(returncode: int = 0, stderr: str = "", stdout: str = ""):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    return run


def test_ensure_jscpd_returns_path_when_working(monkeypatch):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    monkeypatch.setattr(jscpd_check.subprocess, "run", _fake_run())
    assert jscpd_check.ensure_jscpd() == Path("/usr/local/bin/jscpd")


def test_ensure_jscpd_exits_when_binary_present_but_broken(monkeypatch):
    """PATH hit says nothing: without Node.js the npm bin script exits 127."""
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    monkeypatch.setattr(
        jscpd_check.subprocess, "run",
        _fake_run(returncode=127, stderr="env: node: No such file or directory"),
    )
    with pytest.raises(SystemExit) as exc:
        jscpd_check.ensure_jscpd()
    message = str(exc.value)
    assert "127" in message
    assert "node" in message
    assert "which node" in message
    assert "--allow-missing-jscpd" in message


def test_ensure_jscpd_reports_other_exit_codes_without_node_hint(monkeypatch):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    monkeypatch.setattr(jscpd_check.subprocess, "run", _fake_run(returncode=1, stderr="boom"))
    with pytest.raises(SystemExit) as exc:
        jscpd_check.ensure_jscpd()
    message = str(exc.value)
    assert "код возврата 1" in message
    assert "boom" in message
    assert "which node" not in message


def test_ensure_jscpd_opt_out_when_binary_broken(monkeypatch, caplog):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    monkeypatch.setattr(jscpd_check.subprocess, "run", _fake_run(returncode=127))
    with caplog.at_level(logging.WARNING):
        assert jscpd_check.ensure_jscpd(allow_missing=True) is None
    assert "duplication_ratio" in caplog.text


def test_ensure_jscpd_exits_when_smoke_run_times_out(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, jscpd_check._VERSION_TIMEOUT_SECONDS)
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    monkeypatch.setattr(jscpd_check.subprocess, "run", timeout)
    with pytest.raises(SystemExit) as exc:
        jscpd_check.ensure_jscpd()
    assert "не ответила" in str(exc.value)


def test_smoke_failure_detects_missing_interpreter_for_real(tmp_path):
    """End-to-end: a script with a shebang to a missing interpreter exits 127."""
    script = tmp_path / "jscpd"
    script.write_text("#!/usr/bin/env definitely-not-an-interpreter\n")
    script.chmod(0o755)
    failure = jscpd_check._smoke_failure(str(script))
    assert failure is not None and "127" in failure


def test_ensure_jscpd_exits_with_instructions_when_missing(monkeypatch):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        jscpd_check.ensure_jscpd()
    message = str(exc.value)
    assert "jscpd" in message
    assert "npm install -g jscpd" in message
    assert "--allow-missing-jscpd" in message


def test_ensure_jscpd_opt_out_returns_none_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: None)
    with caplog.at_level(logging.WARNING):
        assert jscpd_check.ensure_jscpd(allow_missing=True) is None
    assert "duplication_ratio" in caplog.text
