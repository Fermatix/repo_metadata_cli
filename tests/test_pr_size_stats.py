"""PR size-distribution metrics (pr_simple_pct / pr_standard_pct / pr_rich_pct /
avg_loc_per_pr).

Covers fingerprint detection (GitHub merge, GitHub squash, GitLab MR),
deduplication, the merge-commit and plain-commit fallbacks, the size
thresholds, the unit cap, rounding, the agreed zero semantics, and the
Mercurial equivalent.  The golden tests pin the exact values produced by the
reference implementation (the partner-side CRM autofill metrics script) on the
same synthetic repositories — the reference project is NOT required at runtime.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli import pr_size_stats
from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metrics.git import (
    AvgLocPerPRMetric,
    PRRichPctMetric,
    PRSimplePctMetric,
    PRStandardPctMetric,
)
from repo_metadata_cli.pr_size_stats import (
    MAX_PR_UNITS,
    collect_pr_size_stats,
    parse_changed_lines,
    zero_pr_size_stats,
)
from repo_metadata_cli.settings import load_app_settings
from repo_metadata_cli.vcs.git import GitVCS
from repo_metadata_cli.vcs.mercurial import MercurialVCS

requires_hg = pytest.mark.skipif(shutil.which("hg") is None, reason="hg CLI not installed")

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML = _PROJECT_ROOT / "repo_metadata.toml"

_GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_DATE="2026-01-01T00:00:00 +0000",
    GIT_COMMITTER_DATE="2026-01-01T00:00:00 +0000",
)


# --- builders --------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_GIT_ENV)


def _init_git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True, env=_GIT_ENV)
    _git(repo, "config", "user.email", "a@a.com")
    _git(repo, "config", "user.name", "A")


def _commit_file(repo: Path, name: str, n_lines: int, msg: str) -> None:
    (repo / name).write_text("\n".join(f"line{i} = {i}" for i in range(n_lines)) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", msg)


def _merge_branch(repo: Path, branch: str, files: dict, msg: str) -> None:
    """Create ``branch`` off main with the given {name: n_lines} files and merge it."""
    _git(repo, "checkout", "-q", "-b", branch)
    for name, n_lines in files.items():
        (repo / name).write_text("\n".join(f"{name}{i} = {i}" for i in range(n_lines)) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"work on {branch}")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", msg, branch)


def _ctx(repo: Path, vcs=None, pr_cache=None) -> RepoContext:
    settings = load_app_settings(_TOML)
    if pr_cache is not None:
        settings.pr_cache = pr_cache
    return RepoContext(
        repo_path=repo,
        settings=settings,
        tree_sitter=None,
        allowed_files=AllowedFiles(AllowedFilesConfig(config_file=_TOML)),
        vcs=vcs,
    )


# --- parse_changed_lines ---------------------------------------------------

def test_parse_changed_lines():
    assert parse_changed_lines(" 2 files changed, 10 insertions(+), 3 deletions(-)") == 13
    assert parse_changed_lines(" 1 file changed, 1 insertion(+)") == 1
    assert parse_changed_lines(" 1 file changed, 4 deletions(-)") == 4
    assert parse_changed_lines("") == 0


# --- fingerprint unit detection (git) --------------------------------------

def test_github_merge_pr_unit(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _merge_branch(repo, "f1", {"b.py": 70}, "Merge pull request #1 from org/f1")
    units = GitVCS().pr_fingerprint_units(repo)
    assert [kind for _, kind in units] == ["merge"]
    # merge unit measured as the diff to the first parent: only b.py (+70)
    rev, kind = units[0]
    assert GitVCS().unit_changed_lines(repo, rev, kind) == 70


def test_github_squash_pr_unit(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _commit_file(repo, "b.py", 30, "Add feature (#12)")
    units = GitVCS().pr_fingerprint_units(repo)
    assert [kind for _, kind in units] == ["commit"]
    rev, kind = units[0]
    # squash unit measured as the commit's own diff (+30)
    assert GitVCS().unit_changed_lines(repo, rev, kind) == 30


def test_squash_number_must_end_subject(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _commit_file(repo, "b.py", 3, "Add feature (#12) and more")  # not at end
    assert GitVCS().pr_fingerprint_units(repo) == []


def test_gitlab_mr_unit(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _merge_branch(
        repo, "f1", {"b.py": 20},
        "Merge branch 'f1' into 'main'\n\nfeature\n\nSee merge request org/proj!33",
    )
    units = GitVCS().pr_fingerprint_units(repo)
    assert [kind for _, kind in units] == ["merge"]


def test_fingerprint_dedup_same_platform_and_number(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _commit_file(repo, "b.py", 30, "Add feature (#7)")
    _commit_file(repo, "c.py", 4, "Fix feature (#7)")
    units = GitVCS().pr_fingerprint_units(repo)
    assert len(units) == 1
    # newest-first log order: the dedup keeps the most recent #7 commit
    rev, kind = units[0]
    assert GitVCS().unit_changed_lines(repo, rev, kind) == 4


def test_no_dedup_across_platforms(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _merge_branch(repo, "f1", {"b.py": 20}, "Merge pull request #5 from org/f1")
    _merge_branch(repo, "f2", {"c.py": 21}, "Merge branch 'f2'\n\nSee merge request org/x!5")
    # same number 5 on different platforms -> two distinct units
    assert len(GitVCS().pr_fingerprint_units(repo)) == 2


def test_local_merge_without_fingerprint_ignored(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _merge_branch(repo, "f1", {"b.py": 20}, "Merge branch 'f1'")
    assert GitVCS().pr_fingerprint_units(repo) == []


# --- fallbacks -------------------------------------------------------------

def test_fallback_merge_commits(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _merge_branch(repo, "f1", {"b.py": 40}, "Merge branch 'f1'")
    _merge_branch(repo, "f2", {"c.py": 320}, "Merge branch 'f2'")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=2)
    # two merge units: 40 (simple) and 320 (rich); plain commits not counted
    assert stats == {
        "pr_simple_pct": 50, "pr_standard_pct": 0, "pr_rich_pct": 50,
        "avg_loc_per_pr": 180,
    }


def test_fallback_plain_commits(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    _commit_file(repo, "b.py", 200, "second")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    assert stats == {
        "pr_simple_pct": 50, "pr_standard_pct": 50, "pr_rich_pct": 0,
        "avg_loc_per_pr": 105,
    }


# --- thresholds, cap, rounding ---------------------------------------------

def test_size_bucket_boundaries_50_51_300_301(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "root.py", 7, "init")     # 7   -> simple
    _commit_file(repo, "a.py", 50, "c50")        # 50  -> simple (boundary)
    _commit_file(repo, "b.py", 51, "c51")        # 51  -> standard (boundary)
    _commit_file(repo, "c.py", 300, "c300")      # 300 -> standard (boundary)
    _commit_file(repo, "d.py", 301, "c301")      # 301 -> rich (boundary)
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    assert stats == {
        "pr_simple_pct": 40, "pr_standard_pct": 40, "pr_rich_pct": 20,
        "avg_loc_per_pr": 142,  # round(709 / 5)
    }


def test_max_units_cap(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "root.py", 400, "init")  # oldest: would be rich
    for i in range(3):
        _commit_file(repo, f"f{i}.py", 10, f"small {i}")
    monkeypatch.setattr(pr_size_stats, "MAX_PR_UNITS", 3)
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    # newest-first: the cap keeps the three 10-line commits, dropping the root
    assert stats["pr_simple_pct"] == 100
    assert stats["pr_rich_pct"] == 0
    assert stats["avg_loc_per_pr"] == 10
    assert MAX_PR_UNITS == 400  # module default untouched


def test_percentage_and_average_rounding(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "root.py", 10, "init")
    _commit_file(repo, "a.py", 100, "mid")
    _commit_file(repo, "b.py", 500, "big")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    # 1/3 each: round(33.33) == 33 everywhere; avg round(610/3) == round(203.33)
    assert stats == {
        "pr_simple_pct": 33, "pr_standard_pct": 33, "pr_rich_pct": 33,
        "avg_loc_per_pr": 203,
    }


def test_average_uses_bankers_rounding_like_reference(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "root.py", 1, "init")
    _commit_file(repo, "a.py", 4, "second")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    # avg = 5/2 = 2.5 -> Python round() gives 2 (same as the reference)
    assert stats["avg_loc_per_pr"] == 2


# --- zero semantics --------------------------------------------------------

def test_total_pr_count_zero_gates_to_zeros(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    _commit_file(repo, "b.py", 200, "second")
    assert collect_pr_size_stats(GitVCS(), repo, total_pr_count=0) == zero_pr_size_stats()


def test_single_commit_yields_zeros(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "only")
    assert collect_pr_size_stats(GitVCS(), repo, total_pr_count=1) == zero_pr_size_stats()


def test_empty_history_yields_zeros(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    assert collect_pr_size_stats(GitVCS(), repo, total_pr_count=1) == zero_pr_size_stats()


def test_plain_directory_yields_zeros(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    ctx = _ctx(repo)  # auto-detects: no VCS -> git backend over a plain dir
    for metric in (PRSimplePctMetric(), PRStandardPctMetric(), PRRichPctMetric(), AvgLocPerPRMetric()):
        assert metric.compute(ctx) == 0


# --- metric classes / pipeline gating --------------------------------------

def test_metric_classes_use_history_fingerprints(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 5, "init")
    _commit_file(repo, "b.py", 30, "Add feature (#2)")
    ctx = _ctx(repo)
    # total_pr_count fallback finds the squash fingerprint -> gate open
    assert PRSimplePctMetric().compute(ctx) == 100
    assert PRStandardPctMetric().compute(ctx) == 0
    assert PRRichPctMetric().compute(ctx) == 0
    assert AvgLocPerPRMetric().compute(ctx) == 30


def test_metric_classes_respect_pr_cache_gate(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    _commit_file(repo, "b.py", 200, "plain commit")
    # No fingerprints in history (git-log total would be 0), but the PR cache
    # says the project has PRs -> same effective total as column P -> fallback
    # commit basis is used.
    ctx = _ctx(repo, pr_cache={repo.name: {"total_pr": 4, "reviewed_pr": 1}})
    assert PRSimplePctMetric().compute(ctx) == 50
    assert PRStandardPctMetric().compute(ctx) == 50
    assert AvgLocPerPRMetric().compute(ctx) == 105


def test_pr_size_stats_cached_once(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    _commit_file(repo, "b.py", 20, "second")
    ctx = _ctx(repo)
    PRSimplePctMetric().compute(ctx)
    assert "pr_size_stats" in ctx._cache
    cached = ctx._cache["pr_size_stats"]
    PRRichPctMetric().compute(ctx)
    assert ctx._cache["pr_size_stats"] is cached


# --- golden parity with the reference implementation ------------------------
# Expected values below were produced by running the reference implementation
# (partner-side CRM autofill metrics script) on these exact synthetic repos.

def _build_reference_github_mix(repo: Path) -> None:
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    # merge PR #1: rewrite a.py (10+10) + add b.py (70) = 90 changed
    _git(repo, "checkout", "-q", "-b", "f1")
    (repo / "a.py").write_text("\n".join(f"line{i} = {i + 1}" for i in range(10)) + "\n")
    (repo / "b.py").write_text("\n".join(f"b{i} = {i}" for i in range(70)) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feature 1")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from org/f1", "f1")
    # squash PR #2 (30 lines) then a duplicate #2 (5 lines): newest wins dedup
    _commit_file(repo, "c.py", 30, "Add c (#2)")
    _commit_file(repo, "c2.py", 5, "Fix c again (#2)")
    # GitLab MR !7: 400 added lines -> rich
    _merge_branch(
        repo, "f2", {"d.py": 400},
        "Merge branch 'f2' into 'main'\n\nbig\n\nSee merge request org/proj!7",
    )
    # local merge without fingerprint: excluded from the PR basis
    _merge_branch(repo, "f3", {"e.py": 12}, "Merge branch 'f3'")


def test_golden_parity_github_mix(tmp_path):
    repo = tmp_path / "github_mix"
    _build_reference_github_mix(repo)
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=3)
    # Reference: pr_count=3 (merge #1 = 90, squash #2 = 5, MR !7 = 400)
    assert stats == {
        "pr_simple_pct": 33, "pr_standard_pct": 33, "pr_rich_pct": 33,
        "avg_loc_per_pr": 165,  # round(495 / 3)
    }


def test_golden_parity_fallback_merges(tmp_path):
    repo = tmp_path / "fallback_merges"
    _init_git(repo)
    _commit_file(repo, "a.py", 10, "init")
    for i, size in enumerate((40, 250, 320)):
        _merge_branch(repo, f"br{i}", {f"f{i}.py": size}, f"Merge branch 'br{i}'")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=3)
    # Reference: basis=merge, sizes 40/250/320
    assert stats == {
        "pr_simple_pct": 33, "pr_standard_pct": 33, "pr_rich_pct": 33,
        "avg_loc_per_pr": 203,  # round(610 / 3)
    }


def test_golden_parity_fallback_commits(tmp_path):
    repo = tmp_path / "fallback_commits"
    _init_git(repo)
    _commit_file(repo, "base.py", 7, "init")
    for name, size in (("s50.py", 50), ("s51.py", 51), ("s300.py", 300), ("s301.py", 301)):
        _commit_file(repo, name, size, f"c{size}")
    stats = collect_pr_size_stats(GitVCS(), repo, total_pr_count=1)
    # Reference: basis=commit, sizes 7/50/51/300/301
    assert stats == {
        "pr_simple_pct": 40, "pr_standard_pct": 40, "pr_rich_pct": 20,
        "avg_loc_per_pr": 142,  # round(709 / 5)
    }


# --- Mercurial equivalent ---------------------------------------------------

_HG_ENV = dict(os.environ, HGPLAIN="1", HGUSER="A <a@a.com>")


def _hg(repo: Path, *args: str) -> None:
    run(["hg", *args], cwd=str(repo), check=True, capture_output=True, env=_HG_ENV)


def _hg_commit_file(repo: Path, name: str, n_lines: int, msg: str) -> None:
    (repo / name).write_text("\n".join(f"{name}{i} = {i}" for i in range(n_lines)) + "\n")
    _hg(repo, "add", name)
    _hg(repo, "commit", "-m", msg, "-d", "0 0")


@requires_hg
def test_hg_fingerprint_units_and_sizes(tmp_path):
    repo = tmp_path / "hgrepo"
    repo.mkdir()
    _hg(repo, "init")
    _hg_commit_file(repo, "a.py", 10, "init")
    # merge changeset with a GitHub-style fingerprint (+70 to first parent)
    _hg(repo, "branch", "f1")
    _hg_commit_file(repo, "b.py", 70, "feature")
    _hg(repo, "update", "default")
    _hg(repo, "merge", "f1")
    _hg(repo, "commit", "-m", "Merge pull request #1 from org/f1", "-d", "0 0")
    # squash-style fingerprint (+30)
    _hg_commit_file(repo, "c.py", 30, "Add c (#2)")

    vcs = MercurialVCS()
    units = vcs.pr_fingerprint_units(repo)
    assert [kind for _, kind in units] == ["merge", "commit"]
    stats = collect_pr_size_stats(vcs, repo, total_pr_count=2)
    assert stats == {
        "pr_simple_pct": 50, "pr_standard_pct": 50, "pr_rich_pct": 0,
        "avg_loc_per_pr": 50,  # round((70 + 30) / 2)
    }


@requires_hg
def test_hg_fallback_merge_then_commits(tmp_path):
    repo = tmp_path / "hgrepo"
    repo.mkdir()
    _hg(repo, "init")
    _hg_commit_file(repo, "a.py", 10, "init")
    _hg(repo, "branch", "f1")
    _hg_commit_file(repo, "b.py", 40, "feature")
    _hg(repo, "update", "default")
    _hg(repo, "merge", "f1")
    _hg(repo, "commit", "-m", "merged f1", "-d", "0 0")  # no fingerprint

    vcs = MercurialVCS()
    assert vcs.pr_fingerprint_units(repo) == []
    assert len(vcs.merge_unit_revs(repo)) == 1
    stats = collect_pr_size_stats(vcs, repo, total_pr_count=1)
    # single merge unit: +40 to first parent -> simple
    assert stats == {
        "pr_simple_pct": 100, "pr_standard_pct": 0, "pr_rich_pct": 0,
        "avg_loc_per_pr": 40,
    }


@requires_hg
def test_hg_plain_commit_fallback_and_zero_semantics(tmp_path):
    repo = tmp_path / "hgrepo"
    repo.mkdir()
    _hg(repo, "init")
    _hg_commit_file(repo, "a.py", 10, "init")
    vcs = MercurialVCS()
    # single changeset -> zeros
    assert collect_pr_size_stats(vcs, repo, total_pr_count=1) == zero_pr_size_stats()
    _hg_commit_file(repo, "b.py", 400, "big")
    stats = collect_pr_size_stats(vcs, repo, total_pr_count=1)
    assert stats == {
        "pr_simple_pct": 50, "pr_standard_pct": 0, "pr_rich_pct": 50,
        "avg_loc_per_pr": 205,  # round((10 + 400) / 2)
    }


@requires_hg
def test_git_and_hg_equivalent_history_agree(tmp_path):
    """The same logical history yields identical buckets on both VCSes."""
    git_repo = tmp_path / "g"
    _init_git(git_repo)
    _commit_file(git_repo, "a.py", 10, "init")
    _commit_file(git_repo, "b.py", 30, "Add b (#2)")
    _commit_file(git_repo, "c.py", 301, "Add c (#3)")

    hg_repo = tmp_path / "h"
    hg_repo.mkdir()
    _hg(hg_repo, "init")
    _hg_commit_file(hg_repo, "a.py", 10, "init")
    _hg_commit_file(hg_repo, "b.py", 30, "Add b (#2)")
    _hg_commit_file(hg_repo, "c.py", 301, "Add c (#3)")

    git_stats = collect_pr_size_stats(GitVCS(), git_repo, total_pr_count=2)
    hg_stats = collect_pr_size_stats(MercurialVCS(), hg_repo, total_pr_count=2)
    assert git_stats == hg_stats == {
        "pr_simple_pct": 50, "pr_standard_pct": 0, "pr_rich_pct": 50,
        "avg_loc_per_pr": 166,  # round((30 + 301) / 2)
    }
