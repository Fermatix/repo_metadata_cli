"""Recovery of working trees the OS refuses to check out.

Covers the three outcomes a materialization can have — complete, repaired
(some paths restored under sanitized names) and impossible — plus the
end-of-run summary that makes a missing row visible.

The failure itself is platform-specific (a backslash in a file name is legal on
Linux, fatal on Windows), so the tests reproduce its EFFECT — tracked files
absent from the working tree — in a platform-independent way: check out part of
the tree and let the repair pass restore the rest.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.pipeline import run_metadata_pipeline
from repo_metadata_cli.settings import load_app_settings
from repo_metadata_cli.vcs.checkout_repair import (
    restore_rejected_files,
    sanitize_rel_path,
)
from repo_metadata_cli.vcs.git import GitVCS

_TOML = Path(__file__).parent.parent / "repo_metadata.toml"


def _git(repo: Path, *args: str) -> None:
    run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def bundle(tmp_path) -> Path:
    """Bundle of a small repo with three tracked files."""
    src = tmp_path / "src"
    (src / "Sources").mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main", str(src)], check=True, capture_output=True)
    _git(src, "config", "user.email", "a@a.com")
    _git(src, "config", "user.name", "A")
    (src / "Sources" / "View.swift").write_text("let a = 1\nlet b = 2\n")
    (src / "app.py").write_text("x = 1\ny = 2\nz = 3\n")
    (src / "README.md").write_text("# demo\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    out = tmp_path / "repo.bundle"
    _git(src, "bundle", "create", "-q", str(out), "--all")
    return out


# ---------------------------------------------------------------------------
# sanitize_rel_path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Sources\\View.swift", "Sources_View.swift"),   # backslash: the real case
        ("a/b\\c.kt", "a/b_c.kt"),                        # only the segment is fixed
        ('weird:name?.js', "weird_name_.js"),             # other forbidden chars
        ("trailing. ", "trailing"),                       # trailing dot/space
        ("dir/CON.txt", "dir/_CON.txt"),                  # reserved device name
        ("nul", "_nul"),                                   # reserved, no extension
        ("plain/file.py", "plain/file.py"),               # untouched
    ],
)
def test_sanitize_rel_path(raw, expected):
    assert sanitize_rel_path(raw) == expected


def test_sanitize_keeps_extension_for_language_detection():
    assert sanitize_rel_path("Sources\\Model.swift").endswith(".swift")


def test_sanitize_drops_parent_traversal():
    # git never tracks such a path, but sanitization must not produce an escape.
    assert ".." not in sanitize_rel_path("../../etc/passwd")


# ---------------------------------------------------------------------------
# restore_rejected_files
# ---------------------------------------------------------------------------

def _clone_without_worktree(bundle: Path, dest: Path) -> Path:
    run(["git", "clone", "-q", "--no-checkout", str(bundle), str(dest)],
        check=True, capture_output=True)
    # Populate the index exactly like the tolerant checkout does, then remove a
    # file to emulate a path the OS refused to create.
    _git(dest, "checkout", "--force", "HEAD", "--", ".")
    return dest


def test_restore_brings_back_missing_file(bundle, tmp_path):
    repo = _clone_without_worktree(bundle, tmp_path / "repo")
    (repo / "Sources" / "View.swift").unlink()

    report = restore_rejected_files(repo)

    assert report.restored == 1
    assert report.failed == []
    assert (repo / "Sources" / "View.swift").read_text() == "let a = 1\nlet b = 2\n"


def test_restore_is_noop_on_complete_tree(bundle, tmp_path):
    repo = _clone_without_worktree(bundle, tmp_path / "repo")
    report = restore_rejected_files(repo)
    assert report.restored == 0
    assert report.renamed == []
    assert report.touched is False


def test_restore_reports_rename_when_name_changes(bundle, tmp_path):
    """A path whose sanitized form differs is reported as renamed."""
    src = tmp_path / "src2"
    src.mkdir()
    run(["git", "init", "-q", "-b", "main", str(src)], check=True, capture_output=True)
    _git(src, "config", "user.email", "a@a.com")
    _git(src, "config", "user.name", "A")
    # A file whose NAME contains a backslash — legal here, impossible on Windows.
    (src / "Sources\\View.swift").write_text("let hidden = 1\n")
    (src / "ok.py").write_text("x = 1\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    b = tmp_path / "b2.bundle"
    _git(src, "bundle", "create", "-q", str(b), "--all")

    repo = _clone_without_worktree(b, tmp_path / "repo2")
    (repo / "Sources\\View.swift").unlink()

    report = restore_rejected_files(repo)

    assert report.restored == 1
    assert report.renamed == [("Sources\\View.swift", "Sources_View.swift")]
    assert (repo / "Sources_View.swift").read_text() == "let hidden = 1\n"


def test_restore_avoids_collision(bundle, tmp_path):
    """Two paths collapsing to one sanitized name must not overwrite each other."""
    src = tmp_path / "src3"
    src.mkdir()
    run(["git", "init", "-q", "-b", "main", str(src)], check=True, capture_output=True)
    _git(src, "config", "user.email", "a@a.com")
    _git(src, "config", "user.name", "A")
    (src / "a\\b.py").write_text("first = 1\n")
    (src / "a_b.py").write_text("second = 2\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    b = tmp_path / "b3.bundle"
    _git(src, "bundle", "create", "-q", str(b), "--all")

    repo = _clone_without_worktree(b, tmp_path / "repo3")
    (repo / "a\\b.py").unlink()

    report = restore_rejected_files(repo)

    assert report.restored == 1
    assert (repo / "a_b.py").read_text() == "second = 2\n"   # untouched
    assert (repo / "a_b_1.py").read_text() == "first = 1\n"  # restored alongside


# ---------------------------------------------------------------------------
# GitVCS.clone — the three outcomes
# ---------------------------------------------------------------------------

def test_clone_complete_tree_reports_no_repair(bundle, tmp_path):
    vcs = GitVCS()
    repo = vcs.clone(bundle, tmp_path / "dest")
    assert repo is not None
    assert vcs.last_repair is None
    assert (repo / "app.py").exists()


def test_degraded_path_materializes_everything(bundle, tmp_path):
    """The fallback used when a checkout fails yields a COMPLETE working tree."""
    dest = tmp_path / "dest2"
    dest.mkdir()
    vcs = GitVCS()
    import os

    repo_dir = dest / bundle.stem
    result = vcs._materialize_degraded(bundle, repo_dir, os.environ.copy())

    assert result == repo_dir
    assert (repo_dir / "app.py").read_text() == "x = 1\ny = 2\nz = 3\n"
    assert (repo_dir / "Sources" / "View.swift").exists()
    assert vcs.last_repair is not None and vcs.last_repair.failed == []


def test_clone_returns_none_for_unusable_source(tmp_path):
    """A corrupt bundle cannot be salvaged — the caller must see None."""
    broken = tmp_path / "broken.bundle"
    broken.write_bytes(b"not a git bundle at all")
    vcs = GitVCS()
    assert vcs.clone(broken, tmp_path / "dest3") is None


# ---------------------------------------------------------------------------
# Pipeline summary — a missing row must be visible
# ---------------------------------------------------------------------------

def test_pipeline_reports_skipped_repository(bundle, tmp_path, caplog):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "good.bundle").write_bytes(bundle.read_bytes())
    (dataset / "broken.bundle").write_bytes(b"not a git bundle at all")
    csv_path = tmp_path / "out.csv"

    settings = load_app_settings(_TOML)
    allowed = AllowedFiles(AllowedFilesConfig(config_file=_TOML))

    with caplog.at_level("WARNING"):
        summary = run_metadata_pipeline(dataset, csv_path, settings, allowed, None)

    assert summary["skipped"] == ["broken"]
    assert "produced NO row" in caplog.text
    # The healthy repository is still measured and written.
    import pandas as pd
    assert list(pd.read_csv(csv_path)["repo_name"]) == ["good"]
