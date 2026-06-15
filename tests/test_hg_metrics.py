"""Integration tests for the VCS metrics over a real Mercurial repository.

Builds a synthetic hg repo (two commits across two named branches, two authors)
and verifies the metric classes — which delegate to ``ctx.vcs`` — produce the
same kinds of values they do for git.

Requires the ``hg`` CLI (``pip install mercurial``); skipped otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metrics.docs import DocumentationCountMetric, LicenseTypeMetric
from repo_metadata_cli.metrics.files import ExtensionsMetric, StackMetric
from repo_metadata_cli.metrics.git import (
    BranchCountMetric,
    CommitCountMetric,
    ContributorsMetric,
    CreatedAtMetric,
)
from repo_metadata_cli.metrics.loc import CommentRatioMetric, SymbolsCountMetric
from repo_metadata_cli.metrics.quality import ForkPctMetric
from repo_metadata_cli.metrics.size import GitHistoryMbMetric, WorktreeMbMetric
from repo_metadata_cli.settings import AppSettings, load_app_settings
from repo_metadata_cli.vcs.mercurial import MercurialVCS

requires_hg = pytest.mark.skipif(shutil.which("hg") is None, reason="hg CLI not installed")

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML_PATH = _PROJECT_ROOT / "repo_metadata.toml"


def _hg(args, cwd):
    env = {**os.environ, "HGPLAIN": "1"}
    run(["hg", *args], cwd=str(cwd), check=True, capture_output=True, env=env)


def _make_allowed_files() -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML_PATH))


def _settings() -> AppSettings:
    base = load_app_settings(_TOML_PATH)
    base.metrics.scc_exclude_dirs = ["vendor", "node_modules"]
    return base


def _build_ctx(repo_path: Path, bundle_path=None) -> RepoContext:
    return RepoContext(
        repo_path=repo_path,
        settings=_settings(),
        tree_sitter=None,
        allowed_files=_make_allowed_files(),
        bundle_path=bundle_path,
        vcs=MercurialVCS(),
    )


@pytest.fixture
def synth_hg_repo(tmp_path):
    """Synthetic hg repo: LICENSE, README, two branches, two authors, mixed langs."""
    repo = tmp_path / "hgrepo"
    (repo / "src").mkdir(parents=True)

    py_lines = ["# a comment", "# another comment"] + [f"x_{i} = {i}" for i in range(10)]
    (repo / "src" / "main.py").write_text("\n".join(py_lines) + "\n")
    (repo / "src" / "util.js").write_text("\n".join(f"const a{i} = {i};" for i in range(5)) + "\n")
    (repo / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted, free of charge, to any person...\n"
    )
    (repo / "README.md").write_text("# Title\n\nLine one\nLine two\n")

    _hg(["init", "."], cwd=repo)
    _hg(["add"], cwd=repo)
    _hg(["commit", "-u", "Test <test@test.com>", "-m", "init"], cwd=repo)
    # Second branch + second commit (distinct author) for branch/contributor variety.
    _hg(["branch", "feature"], cwd=repo)
    (repo / "src" / "extra.py").write_text("y = 1\n")
    _hg(["add"], cwd=repo)
    _hg(["commit", "-u", "Second Dev <dev2@test.com>", "-m", "feat"], cwd=repo)
    return repo


@requires_hg
def test_commit_count(synth_hg_repo):
    assert CommitCountMetric().compute(_build_ctx(synth_hg_repo)) == 2


@requires_hg
def test_contributors_count(synth_hg_repo):
    assert ContributorsMetric().compute(_build_ctx(synth_hg_repo)) == 2


@requires_hg
def test_branch_count(synth_hg_repo):
    # default + feature
    assert BranchCountMetric().compute(_build_ctx(synth_hg_repo)) >= 2


@requires_hg
def test_created_at_non_empty(synth_hg_repo):
    created = CreatedAtMetric().compute(_build_ctx(synth_hg_repo))
    assert isinstance(created, str) and created.strip()


@requires_hg
def test_fork_pct_zero(synth_hg_repo):
    assert ForkPctMetric().compute(_build_ctx(synth_hg_repo)) == 0.0


@requires_hg
def test_history_and_worktree_mb(synth_hg_repo):
    ctx = _build_ctx(synth_hg_repo)
    # GitHistoryMbMetric measures the VCS history dir — .hg here — and must be > 0.
    assert GitHistoryMbMetric().compute(ctx) > 0.0
    assert WorktreeMbMetric().compute(ctx) >= 0.0


@requires_hg
def test_history_dir_is_hg(synth_hg_repo):
    assert _build_ctx(synth_hg_repo).vcs.history_dirname == ".hg"
    assert (synth_hg_repo / ".hg").is_dir()


@requires_hg
def test_license_type(synth_hg_repo):
    assert LicenseTypeMetric().compute(_build_ctx(synth_hg_repo)) == "MIT"


@requires_hg
def test_documentation_count(synth_hg_repo):
    # LICENSE + README counted the same way as the git fixture.
    assert DocumentationCountMetric().compute(_build_ctx(synth_hg_repo)) >= 2


@requires_hg
def test_extensions_distribution(synth_hg_repo):
    dist = json.loads(ExtensionsMetric().compute(_build_ctx(synth_hg_repo)))
    assert ".py" in dist and ".js" in dist
    assert abs(sum(dist.values()) - 1.0) < 1e-3


@requires_hg
def test_stack_human_readable(synth_hg_repo):
    stack = StackMetric().compute(_build_ctx(synth_hg_repo))
    assert isinstance(stack, str) and "%" in stack


@requires_hg
def test_comment_ratio_in_range(synth_hg_repo):
    ratio = CommentRatioMetric().compute(_build_ctx(synth_hg_repo))
    assert isinstance(ratio, float) and ratio >= 0.0


@requires_hg
def test_symbols_count(synth_hg_repo):
    symbols = SymbolsCountMetric().compute(_build_ctx(synth_hg_repo))
    assert isinstance(symbols, int)
    py_chars = len((synth_hg_repo / "src" / "main.py").read_text(encoding="utf-8"))
    js_chars = len((synth_hg_repo / "src" / "util.js").read_text(encoding="utf-8"))
    assert symbols >= py_chars + js_chars > 0


@requires_hg
def test_auto_detect_vcs_from_hg_path(synth_hg_repo):
    # When vcs is not passed, RepoContext.__post_init__ must detect Mercurial.
    ctx = RepoContext(
        repo_path=synth_hg_repo,
        settings=_settings(),
        tree_sitter=None,
        allowed_files=_make_allowed_files(),
    )
    assert ctx.vcs.name == "hg"
    assert CommitCountMetric().compute(ctx) == 2
