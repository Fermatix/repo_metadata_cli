"""Tests for the fail-fast jscpd presence check."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from repo_metadata_cli import jscpd_check


def test_ensure_jscpd_returns_path_when_present(monkeypatch):
    monkeypatch.setattr(jscpd_check.shutil, "which", lambda _: "/usr/local/bin/jscpd")
    assert jscpd_check.ensure_jscpd() == Path("/usr/local/bin/jscpd")


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
