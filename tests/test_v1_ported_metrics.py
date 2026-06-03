"""Integration tests for the v1-ported metrics (AF → AO).

Builds a synthetic git repository with a LICENSE, README, an extra branch and a
mix of languages, then verifies the ported metrics produce sensible values.

Requires: git (always available), scc (for extensions/stack/comment_ratio).
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metrics.docs import DocumentationCountMetric, LicenseTypeMetric
from repo_metadata_cli.metrics.files import ExtensionsMetric, StackMetric
from repo_metadata_cli.metrics.git import BranchCountMetric, CreatedAtMetric
from repo_metadata_cli.metrics.loc import CommentRatioMetric, SymbolsCountMetric
from repo_metadata_cli.metrics.size import (
    GitHistoryMbMetric,
    RepoBundleMbMetric,
    WorktreeMbMetric,
)
from repo_metadata_cli.settings import AppSettings, load_app_settings

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML_PATH = _PROJECT_ROOT / "repo_metadata.toml"


def _make_allowed_files() -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML_PATH))


def _build_ctx(repo_path: Path, settings: AppSettings, bundle_path=None) -> RepoContext:
    return RepoContext(
        repo_path=repo_path,
        settings=settings,
        tree_sitter=None,
        allowed_files=_make_allowed_files(),
        bundle_path=bundle_path,
    )


def _settings() -> AppSettings:
    base = load_app_settings(_TOML_PATH)
    base.metrics.scc_exclude_dirs = ["vendor", "node_modules"]
    return base


@pytest.fixture
def synth_repo(tmp_path):
    """Synthetic git repo with LICENSE, README, two branches and mixed languages."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    py_lines = ["# a comment", "# another comment"] + [f"x_{i} = {i}" for i in range(10)]
    (repo / "src" / "main.py").write_text("\n".join(py_lines) + "\n")
    (repo / "src" / "util.js").write_text("\n".join(f"const a{i} = {i};" for i in range(5)) + "\n")

    (repo / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted, free of charge, to any person...\n"
    )
    (repo / "README.md").write_text("# Title\n\nLine one\nLine two\n")

    run(["git", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "branch", "feature"], cwd=repo, check=True, capture_output=True)

    return repo


def test_license_type(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    assert LicenseTypeMetric().compute(ctx) == "MIT"


def test_documentation_cnt(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    assert DocumentationCountMetric().compute(ctx) == 4


def test_branch_count(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    # main/master + feature → at least 2 branches.
    assert BranchCountMetric().compute(ctx) >= 2


def test_created_at_non_empty(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    created = CreatedAtMetric().compute(ctx)
    assert isinstance(created, str) and created.strip()


def test_git_history_and_worktree_mb(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    assert GitHistoryMbMetric().compute(ctx) >= 0.0
    assert WorktreeMbMetric().compute(ctx) >= 0.0


def test_repo_bundle_mb_zero_in_local_mode(synth_repo):
    ctx = _build_ctx(synth_repo, _settings(), bundle_path=None)
    assert RepoBundleMbMetric().compute(ctx) == 0.0


def test_repo_bundle_mb_with_bundle(synth_repo, tmp_path):
    bundle = tmp_path / "repo.bundle"
    run(
        ["git", "bundle", "create", str(bundle), "--all"],
        cwd=synth_repo, check=True, capture_output=True,
    )
    ctx = _build_ctx(synth_repo, _settings(), bundle_path=bundle)
    assert RepoBundleMbMetric().compute(ctx) > 0.0


def test_extensions_distribution(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    dist = json.loads(ExtensionsMetric().compute(ctx))
    assert ".py" in dist and ".js" in dist
    assert abs(sum(dist.values()) - 1.0) < 1e-3


def test_stack_human_readable(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    stack = StackMetric().compute(ctx)
    assert isinstance(stack, str) and "%" in stack


def test_comment_ratio_in_range(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    ratio = CommentRatioMetric().compute(ctx)
    assert isinstance(ratio, float)
    assert ratio >= 0.0


def test_symbols_count(synth_repo):
    ctx = _build_ctx(synth_repo, _settings())
    symbols = SymbolsCountMetric().compute(ctx)
    assert isinstance(symbols, int)
    # The two source files are definitely part of the logical_loc set, so the
    # total character count must be at least their combined length.
    py_chars = len((synth_repo / "src" / "main.py").read_text(encoding="utf-8"))
    js_chars = len((synth_repo / "src" / "util.js").read_text(encoding="utf-8"))
    assert symbols >= py_chars + js_chars > 0
