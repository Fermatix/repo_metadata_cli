"""Extra run-level exclusions (--exclude-dir) and the shared path matcher.

The point of the option is that ONE rule governs every metric: scc-based LOC
and language columns, the tree-sitter walk, and the tracked-file set behind the
test estimates.  A rule honoured by scc but not by the Python walks would
produce a CSV whose columns disagree about what the repository contains.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metric_utils import compile_exclude_matcher, get_scc_stats, iter_code_files
from repo_metadata_cli.settings import load_app_settings

_TOML = Path(__file__).parent.parent / "repo_metadata.toml"


# ---------------------------------------------------------------------------
# compile_exclude_matcher — scc-compatible semantics
# ---------------------------------------------------------------------------

def test_bare_name_matches_at_any_depth():
    m = compile_exclude_matcher(["node_modules"])
    assert m(["node_modules"]) is True
    assert m(["app", "node_modules", "lib"]) is True
    assert m(["app", "src"]) is False


def test_path_entry_matches_segments_in_sequence():
    m = compile_exclude_matcher(["bitrix/modules"])
    assert m(["bitrix", "modules"]) is True
    assert m(["www", "bitrix", "modules", "main"]) is True      # install under www/
    assert m(["backup", "2023", "bitrix", "modules"]) is True   # backup copy
    assert m(["bitrix", "templates"]) is False                  # partner's own code
    assert m(["modules"]) is False                              # bare segment alone


def test_path_entry_requires_adjacent_segments():
    m = compile_exclude_matcher(["bitrix/modules"])
    assert m(["bitrix", "x", "modules"]) is False


def test_matching_is_case_sensitive_like_scc():
    m = compile_exclude_matcher(["Plugins"])
    assert m(["Plugins"]) is True
    assert m(["plugins"]) is False


def test_blank_and_slash_padded_entries_are_normalised():
    m = compile_exclude_matcher(["", "  ", "/vendor/", "bitrix/modules/"])
    assert m(["vendor"]) is True
    assert m(["bitrix", "modules"]) is True


def test_empty_list_excludes_nothing():
    m = compile_exclude_matcher([])
    assert m(["node_modules"]) is False


# ---------------------------------------------------------------------------
# The same rule reaches scc, the AST walk and the tracked-file set
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def cms_repo(tmp_path) -> Path:
    """A CMS-like tree: vendor kernel nested under www/, partner code beside it."""
    repo = tmp_path / "site"
    (repo / "www" / "bitrix" / "modules" / "main").mkdir(parents=True)
    (repo / "www" / "bitrix" / "templates" / "custom").mkdir(parents=True)
    (repo / "local" / "components").mkdir(parents=True)
    (repo / "www" / "bitrix" / "modules" / "main" / "kernel.php").write_text(
        "<?php\n" + "\n".join(f"$k{i} = {i};" for i in range(40)) + "\n"
    )
    (repo / "www" / "bitrix" / "templates" / "custom" / "header.php").write_text(
        "<?php\nfunction render_header() {\n  return 1;\n}\n"
    )
    (repo / "local" / "components" / "widget.php").write_text(
        "<?php\nfunction widget() {\n  return 2;\n}\n"
    )
    run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "a@a.com")
    _git(repo, "config", "user.name", "A")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _ctx(repo: Path, extra: list[str] | None = None) -> RepoContext:
    settings = load_app_settings(_TOML)
    if extra:
        settings.metrics.extra_exclude_dirs = list(extra)
        settings.metrics.scc_exclude_dirs.extend(extra)
    return RepoContext(
        repo_path=repo,
        settings=settings,
        tree_sitter=None,
        allowed_files=AllowedFiles(AllowedFilesConfig(config_file=_TOML)),
    )


def test_scc_honours_path_exclusion(cms_repo):
    full = get_scc_stats(cms_repo)["total"]["code"]
    without_kernel = get_scc_stats(cms_repo, exclude_dirs=["bitrix/modules"])["total"]["code"]
    assert without_kernel < full
    # the two hand-written files survive
    assert without_kernel >= 6


def test_ast_walk_honours_path_exclusion(cms_repo):
    allowed = AllowedFiles(AllowedFilesConfig(config_file=_TOML))
    all_files = {p.name for p in iter_code_files(cms_repo, allowed)}
    assert "kernel.php" in all_files

    kept = {p.name for p in iter_code_files(cms_repo, allowed, ["bitrix/modules"])}
    assert "kernel.php" not in kept          # vendor kernel, nested under www/
    assert {"header.php", "widget.php"} <= kept   # partner code untouched


def test_tracked_files_honour_extra_exclusions(cms_repo):
    assert any("kernel.php" in p for p in _ctx(cms_repo).tracked_files)

    kept = _ctx(cms_repo, ["bitrix/modules"]).tracked_files
    assert not any("kernel.php" in p for p in kept)
    assert any("header.php" in p for p in kept)


def test_default_run_is_unchanged(cms_repo):
    """Without the option the tracked-file set must stay exactly as before."""
    assert _ctx(cms_repo).tracked_files == _ctx(cms_repo, []).tracked_files
