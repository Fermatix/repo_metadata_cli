"""End-to-end pipeline tests: an *.hgbundle is globbed, materialized and measured.

Also verifies that git and Mercurial bundles coexist in one dataset directory.

Requires the ``hg`` CLI; skipped otherwise.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from subprocess import run

import pandas as pd
import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.pipeline import run_metadata_pipeline
from repo_metadata_cli.settings import load_app_settings

requires_hg = pytest.mark.skipif(shutil.which("hg") is None, reason="hg CLI not installed")

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML_PATH = _PROJECT_ROOT / "repo_metadata.toml"


def _settings():
    base = load_app_settings(_TOML_PATH)
    base.metrics.scc_exclude_dirs = ["vendor", "node_modules"]
    return base


def _allowed_files():
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML_PATH))


def _hg(args, cwd):
    env = {**os.environ, "HGPLAIN": "1"}
    run(["hg", *args], cwd=str(cwd), check=True, capture_output=True, env=env)


def _make_hg_repo(path: Path) -> None:
    (path / "src").mkdir(parents=True)
    (path / "src" / "main.py").write_text("\n".join(f"x_{i} = {i}" for i in range(10)) + "\n")
    (path / "README.md").write_text("# Title\n\nHello\n")
    _hg(["init", "."], cwd=path)
    _hg(["add"], cwd=path)
    _hg(["commit", "-u", "Test <test@test.com>", "-m", "init"], cwd=path)
    _hg(["branch", "feature"], cwd=path)
    (path / "src" / "extra.py").write_text("y = 1\n")
    _hg(["add"], cwd=path)
    _hg(["commit", "-u", "Dev Two <d2@test.com>", "-m", "feat"], cwd=path)


def _make_git_repo(path: Path) -> None:
    (path / "app").mkdir(parents=True)
    (path / "app" / "core.py").write_text("\n".join(f"v_{i} = {i}" for i in range(8)) + "\n")
    run(["git", "init"], cwd=path, check=True, capture_output=True)
    run(["git", "config", "user.email", "g@test.com"], cwd=path, check=True, capture_output=True)
    run(["git", "config", "user.name", "Git User"], cwd=path, check=True, capture_output=True)
    run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


@requires_hg
def test_hg_bundle_roundtrip(tmp_path):
    src = tmp_path / "src_repo"
    src.mkdir()
    _make_hg_repo(src)

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _hg(["bundle", "--all", str(dataset / "myrepo.hgbundle")], cwd=src)

    csv_path = tmp_path / "out.csv"
    run_metadata_pipeline(dataset, csv_path, _settings(), _allowed_files(), None)

    df = pd.read_csv(csv_path)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["repo_name"] == "myrepo"
    assert row["num_repos"] == 1
    assert row["commit_count"] == 2
    assert row["branch_count"] >= 2
    assert str(row["created_at"]).strip()
    # LOC columns come from scc over the materialized worktree.
    assert row["raw_loc"] > 0


@requires_hg
def test_git_and_hg_bundles_coexist(tmp_path):
    # One git bundle + one hg bundle in the same dataset dir → two rows.
    hg_src = tmp_path / "hg_src"
    hg_src.mkdir()
    _make_hg_repo(hg_src)

    git_src = tmp_path / "git_src"
    git_src.mkdir()
    _make_git_repo(git_src)

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _hg(["bundle", "--all", str(dataset / "hgproj.hgbundle")], cwd=hg_src)
    run(
        ["git", "bundle", "create", str(dataset / "gitproj.bundle"), "--all"],
        cwd=git_src, check=True, capture_output=True,
    )

    csv_path = tmp_path / "out.csv"
    run_metadata_pipeline(dataset, csv_path, _settings(), _allowed_files(), None)

    df = pd.read_csv(csv_path)
    assert len(df) == 2
    names = set(df["repo_name"])
    assert names == {"hgproj", "gitproj"}
    # Both repos have real commit history → non-zero commit counts.
    assert (df["commit_count"] > 0).all()
