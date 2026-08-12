"""Tests for the conditional Mercurial presence check.

The guard must stay silent for git-only input and refuse to start only when the
run actually contains Mercurial repositories — otherwise every partner without
hg installed would be blocked for no reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_metadata_cli import hg_check


# ---------------------------------------------------------------------------
# Detecting Mercurial in the input
# ---------------------------------------------------------------------------

def test_repos_txt_with_hg_scheme_is_detected(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text(
        "https://gitlab.example.com/group/app.git\n"
        "hg+https://hg.example.com/group/legacy\n"
    )
    assert hg_check.hg_repo_sources(repos) == ["hg+https://hg.example.com/group/legacy"]
    assert hg_check.dataset_needs_hg(repos) is True


def test_repos_txt_git_only_needs_nothing(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text(
        "https://gitlab.example.com/group/app.git\n"
        "# комментарий\n"
        "\n"
        "git@github.com:group/web.git\n"
    )
    assert hg_check.hg_repo_sources(repos) == []
    assert hg_check.dataset_needs_hg(repos) is False


def test_comments_and_blank_lines_are_ignored(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text("# hg+https://hg.example.com/commented-out\n\n")
    assert hg_check.hg_repo_sources(repos) == []


def test_hgbundle_directory_is_detected(tmp_path):
    dataset = tmp_path / "bundles"
    dataset.mkdir()
    (dataset / "team-app.bundle").write_bytes(b"")
    (dataset / "team-legacy.hgbundle").write_bytes(b"")
    assert hg_check.hg_repo_sources(dataset) == ["team-legacy.hgbundle"]


def test_local_working_copy_with_hg_dir_is_detected(tmp_path):
    dataset = tmp_path / "repos"
    (dataset / "app" / ".git").mkdir(parents=True)
    (dataset / "legacy" / ".hg").mkdir(parents=True)
    assert hg_check.hg_repo_sources(dataset) == ["legacy"]


def test_git_only_directory_needs_nothing(tmp_path):
    dataset = tmp_path / "repos"
    (dataset / "app" / ".git").mkdir(parents=True)
    (dataset / "web.bundle").parent.mkdir(parents=True, exist_ok=True)
    (dataset / "web.bundle").write_bytes(b"")
    assert hg_check.dataset_needs_hg(dataset) is False


# ---------------------------------------------------------------------------
# ensure_hg
# ---------------------------------------------------------------------------

def test_ensure_hg_is_noop_without_mercurial_input(tmp_path, monkeypatch):
    """A git-only run must not be blocked even when hg is absent."""
    repos = tmp_path / "repos.txt"
    repos.write_text("https://gitlab.example.com/group/app.git\n")
    monkeypatch.setattr(hg_check.shutil, "which", lambda _: None)
    assert hg_check.ensure_hg(repos) is None


def test_ensure_hg_returns_path_when_present(tmp_path, monkeypatch):
    repos = tmp_path / "repos.txt"
    repos.write_text("hg+https://hg.example.com/group/legacy\n")
    monkeypatch.setattr(hg_check.shutil, "which", lambda _: "/usr/local/bin/hg")
    assert hg_check.ensure_hg(repos) == Path("/usr/local/bin/hg")


def test_ensure_hg_exits_with_instructions_when_needed_and_missing(tmp_path, monkeypatch):
    repos = tmp_path / "repos.txt"
    repos.write_text("hg+https://hg.example.com/group/legacy\n")
    monkeypatch.setattr(hg_check.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        hg_check.ensure_hg(repos)
    message = str(exc.value)
    assert "hg" in message
    assert "pip install mercurial" in message
    assert "--install-hg" in message


def test_ensure_hg_auto_install(tmp_path, monkeypatch):
    repos = tmp_path / "repos.txt"
    repos.write_text("hg+https://hg.example.com/group/legacy\n")
    calls = {"n": 0}

    def fake_which(_):
        calls["n"] += 1
        return None if calls["n"] == 1 else "/venv/bin/hg"

    monkeypatch.setattr(hg_check.shutil, "which", fake_which)
    monkeypatch.setattr(
        hg_check.subprocess, "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stderr": b""})(),
    )
    assert hg_check.ensure_hg(repos, auto_install=True) == Path("/venv/bin/hg")


def test_ensure_hg_still_exits_when_auto_install_fails(tmp_path, monkeypatch):
    repos = tmp_path / "repos.txt"
    repos.write_text("hg+https://hg.example.com/group/legacy\n")
    monkeypatch.setattr(hg_check.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        hg_check.subprocess, "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stderr": b"boom"})(),
    )
    with pytest.raises(SystemExit):
        hg_check.ensure_hg(repos, auto_install=True)
