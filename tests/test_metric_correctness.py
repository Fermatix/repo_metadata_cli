"""Regression tests for the metric-correctness review.

Each test pins a specific bug found during the cross-platform (git/hg,
GitHub/GitLab) correctness review so it cannot silently regress.

hg integration tests are skipped when the `hg` CLI is absent.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.settings import AppSettings, load_app_settings

requires_hg = pytest.mark.skipif(shutil.which("hg") is None, reason="hg CLI not installed")
requires_jscpd = pytest.mark.skipif(shutil.which("jscpd") is None, reason="jscpd not installed")

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML = _PROJECT_ROOT / "repo_metadata.toml"


def _settings() -> AppSettings:
    s = load_app_settings(_TOML)
    s.metrics.scc_exclude_dirs = ["node_modules", "vendor", "dist", "build", "bower_components"]
    return s


def _ctx(repo: Path, vcs=None, bundle_path=None) -> RepoContext:
    return RepoContext(
        repo_path=repo,
        settings=_settings(),
        tree_sitter=None,
        allowed_files=AllowedFiles(AllowedFilesConfig(config_file=_TOML)),
        bundle_path=bundle_path,
        vcs=vcs,
    )


# --- git/hg builders -------------------------------------------------------

def _init_git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "a@a.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "A"], cwd=repo, check=True, capture_output=True)


def _git_commit(repo: Path, msg: str, date: str | None = None, author: str | None = None) -> None:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    args = ["git", "commit", "-q", "-m", msg]
    if author:
        args += ["--author", author]
    run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    run(args, cwd=repo, check=True, capture_output=True, env=env)


def _hg(repo: Path, *args: str) -> None:
    env = {**os.environ, "HGPLAIN": "1"}
    run(["hg", *args], cwd=str(repo), check=True, capture_output=True, env=env)


# ===========================================================================
# created_at (AF): FIRST commit, parity git vs hg
# ===========================================================================

def test_created_at_git_is_first_commit(tmp_path):
    repo = tmp_path / "g"
    _init_git(repo)
    for d in ("2020-01-01T00:00:00", "2021-06-15T00:00:00", "2022-12-31T00:00:00"):
        (repo / "f.txt").write_text(d)
        _git_commit(repo, f"c {d}", date=d)
    created = _ctx(repo).vcs.created_at(repo)
    assert created.startswith("2020-01-01"), created  # first, not last


@requires_hg
def test_created_at_hg_is_first_commit_and_parity(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    for d in ("2020-01-01 00:00:00", "2021-06-15 00:00:00", "2022-12-31 00:00:00"):
        (repo / "f.txt").write_text(d)
        _hg(repo, "add")
        _hg(repo, "commit", "-d", d, "-u", "A <a@a.com>", "-m", f"c {d}")
    created = _ctx(repo).vcs.created_at(repo)
    assert created.startswith("2020-01-01"), created


@requires_hg
def test_created_at_empty_hg_repo_is_blank(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    _hg(repo, "init", ".")
    assert _ctx(repo).vcs.created_at(repo) == ""  # not the fake 1970 epoch


# ===========================================================================
# commit_count (N): all commits across all branches, INCLUDING merges
# ===========================================================================

def test_commit_count_git_counts_all_including_merges(tmp_path):
    from repo_metadata_cli.metrics.git import CommitCountMetric

    repo = tmp_path / "g"
    _init_git(repo)
    (repo / "a.txt").write_text("1")
    _git_commit(repo, "c1")
    default = run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                  cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    (repo / "b.txt").write_text("2")
    _git_commit(repo, "c2")
    run(["git", "checkout", "-q", default], cwd=repo, check=True, capture_output=True)
    (repo / "c.txt").write_text("3")
    _git_commit(repo, "c3")
    run(["git", "merge", "--no-ff", "-m", "merge feature", "feature"],
        cwd=repo, check=True, capture_output=True)
    # c1, c2, c3 + 1 merge commit = 4 (merge counted, across all branches).
    assert _ctx(repo).vcs.commit_count(repo) == 4
    assert CommitCountMetric().compute(_ctx(repo)) == 4


@requires_hg
def test_commit_count_hg_counts_all_including_merges(tmp_path):
    from repo_metadata_cli.metrics.git import CommitCountMetric

    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    (repo / "a.txt").write_text("1")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "c1")
    _hg(repo, "branch", "feature")
    (repo / "b.txt").write_text("2")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "c2")
    _hg(repo, "update", "default")
    (repo / "c.txt").write_text("3")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "c3")
    _hg(repo, "merge", "feature")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "merge")
    # c1, c2, c3 + 1 merge changeset = 4.
    assert _ctx(repo).vcs.commit_count(repo) == 4
    assert CommitCountMetric().compute(_ctx(repo)) == 4


@requires_hg
def test_hg_latest_branch_is_tip_even_if_closed(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    (repo / "a.txt").write_text("1")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "init")
    _hg(repo, "branch", "feature")
    (repo / "b.txt").write_text("2")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "feat")
    _hg(repo, "commit", "--close-branch", "-u", "A <a@a>", "-m", "close")
    # tip is the close-branch changeset on the (closed) feature branch → analyse it.
    assert _ctx(repo).vcs.latest_branch(repo) == "feature"
    # branch_count still counts only open branches.
    assert _ctx(repo).vcs.branch_count(repo) == 1


# ===========================================================================
# branch_count (AH): hg counts only OPEN branches (parity with git live refs)
# ===========================================================================

@requires_hg
def test_branch_count_hg_excludes_closed(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    (repo / "a.txt").write_text("1")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "init")
    _hg(repo, "branch", "feature")
    (repo / "b.txt").write_text("2")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "feat")
    _hg(repo, "commit", "--close-branch", "-u", "A <a@a>", "-m", "close feature")
    _hg(repo, "update", "default")
    # Only the open 'default' branch should count; the closed 'feature' must not.
    assert _ctx(repo).vcs.branch_count(repo) == 1


# ===========================================================================
# scc / jscpd: VCS metadata dirs (.hg/.git) must never leak into metrics
# ===========================================================================

def _make_tree(repo: Path) -> None:
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("\n".join(f"x_{i} = {i}" for i in range(10)) + "\n")
    (repo / "src" / "util.js").write_text("\n".join(f"const a{i}={i};" for i in range(6)) + "\n")


@requires_hg
def test_scc_metrics_parity_git_vs_hg_identical_tree(tmp_path):
    from repo_metadata_cli.metrics.files import LangDistributionMetric, PrimaryLanguageMetric
    from repo_metadata_cli.metrics.loc import LogicalLocMetric, SymbolsCountMetric

    g = tmp_path / "g"
    _init_git(g)
    _make_tree(g)
    _git_commit(g, "init")

    h = tmp_path / "h"
    h.mkdir()
    _make_tree(h)
    _hg(h, "init", ".")
    _hg(h, "add")
    _hg(h, "commit", "-u", "A <a@a>", "-m", "init")

    gctx, hctx = _ctx(g), _ctx(h)
    # Identical source trees must yield identical scc-derived metrics.
    assert LogicalLocMetric().compute(gctx) == LogicalLocMetric().compute(hctx)
    assert SymbolsCountMetric().compute(gctx) == SymbolsCountMetric().compute(hctx)
    assert PrimaryLanguageMetric().compute(gctx) == PrimaryLanguageMetric().compute(hctx)
    # No VCS-internal "language" (e.g. Plain Text from .hg/last-message.txt) leaks in.
    assert "Plain Text" not in LangDistributionMetric().compute(hctx)


@requires_hg
@requires_jscpd
def test_duplication_parity_git_vs_hg(tmp_path):
    from repo_metadata_cli.metrics.quality import DuplicationMetric

    # Large enough to clear jscpd's --min-tokens 50 / --min-lines 5 thresholds.
    block = "\n".join([
        "def calculate(values, factor, offset):",
        "    total = 0",
        "    for index, value in enumerate(values):",
        "        weighted = value * factor + offset",
        "        if weighted > 0:",
        "            total += weighted",
        "        else:",
        "            total -= weighted * 2",
        "        total = total + index - offset",
        "    average = total / max(len(values), 1)",
        "    return average, total, factor, offset",
    ]) + "\n"

    def populate(p):
        (p / "a.py").write_text(block + "\nA = 1\n")
        (p / "b.py").write_text(block + "\nB = 2\n")

    g = tmp_path / "g"; _init_git(g); populate(g); _git_commit(g, "init")
    h = tmp_path / "h"; h.mkdir(); populate(h)
    _hg(h, "init", "."); _hg(h, "add"); _hg(h, "commit", "-u", "A <a@a>", "-m", "init")

    gdup = DuplicationMetric().compute(_ctx(g))
    hdup = DuplicationMetric().compute(_ctx(h))
    assert gdup > 0.0
    assert gdup == pytest.approx(hdup, abs=1e-6)  # .hg revlog must not skew hg


# ===========================================================================
# dep_dir_loc (AE): nested dependency dirs are counted
# ===========================================================================

def test_dep_dir_loc_counts_nested(tmp_path):
    from repo_metadata_cli.metric_utils import get_dep_dir_loc

    repo = tmp_path / "g"
    _init_git(repo)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("\n".join(f"a{i}={i}" for i in range(3)) + "\n")
    nested = repo / "src" / "vendor"
    nested.mkdir()
    (nested / "lib.py").write_text("\n".join(f"z{i}={i}" for i in range(7)) + "\n")
    _git_commit(repo, "init")
    # Nested src/vendor/lib.py (7 code lines) must be counted in AE.
    assert get_dep_dir_loc(repo) >= 7


# ===========================================================================
# issue_tracker (Z): regex no longer false-positives on lowercase tokens
# ===========================================================================

@pytest.mark.parametrize("subject,matches", [
    ("upgrade to react-18 and node-16", False),
    ("support utf-8 encoding", False),
    ("fix sha-256 hashing", False),
    ("bump python-3.11", False),
    ("fixes #12", True),
    ("resolve #7 in parser", True),
    ("implement ABC-123 feature", True),
    ("JIRA-5 done", True),
    ("just a normal message", False),
])
def test_issue_pattern_no_false_positives(subject, matches):
    from repo_metadata_cli.metric_utils import _ISSUE_PATTERN
    assert bool(_ISSUE_PATTERN.search(subject)) is matches


def test_issue_tracker_benign_commits_git(tmp_path):
    from repo_metadata_cli.metrics.docs import IssueTrackerMetric

    repo = tmp_path / "g"
    _init_git(repo)
    (repo / "f.txt").write_text("x")
    _git_commit(repo, "upgrade to react-18 and utf-8")
    assert IssueTrackerMetric().compute(_ctx(repo)) == "None"


def test_issue_tracker_real_refs_git(tmp_path):
    from repo_metadata_cli.metrics.docs import IssueTrackerMetric

    repo = tmp_path / "g"
    _init_git(repo)
    (repo / "f.txt").write_text("x")
    _git_commit(repo, "fixes #42 broken parser")
    assert IssueTrackerMetric().compute(_ctx(repo)) in ("Linked to Commits", "Full+Design Docs")


# ===========================================================================
# deployment_infra (S): 'ship' word-boundary + vendored files ignored
# ===========================================================================

def _workflow(repo: Path, body: str) -> None:
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(body)


def test_deployment_ship_substring_not_full_cicd(tmp_path):
    from repo_metadata_cli.metric_utils import detect_deployment_infra
    repo = tmp_path / "r"
    repo.mkdir()
    _workflow(repo, "jobs:\n  test:\n    steps:\n      - run: chown runner . # fix ownership\n")
    assert detect_deployment_infra(repo) == "Basic CI"


def test_deployment_real_deploy_keyword(tmp_path):
    from repo_metadata_cli.metric_utils import detect_deployment_infra
    repo = tmp_path / "r"
    repo.mkdir()
    _workflow(repo, "jobs:\n  cd:\n    steps:\n      - run: kubectl deploy app\n")
    assert detect_deployment_infra(repo) == "Full CI-CD"


def test_deployment_vendored_chart_not_enterprise(tmp_path):
    from repo_metadata_cli.metric_utils import detect_deployment_infra
    repo = tmp_path / "r"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "Chart.yaml").write_text("name: x\n")
    (repo / "index.js").write_text("console.log(1)\n")
    assert detect_deployment_infra(repo) == "None"


# ===========================================================================
# containerized (V): Compose v2 / Containerfile names + vendored ignored
# ===========================================================================

@pytest.mark.parametrize("fname", ["compose.yml", "compose.yaml", "Containerfile"])
def test_containerized_modern_names(tmp_path, fname):
    from repo_metadata_cli.metric_utils import detect_containerized
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / fname).write_text("x\n")
    assert detect_containerized(repo) == "Yes"


def test_containerized_vendored_dockerfile_ignored(tmp_path):
    from repo_metadata_cli.metric_utils import detect_containerized
    repo = tmp_path / "r"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "Dockerfile").write_text("FROM scratch\n")
    (repo / "index.js").write_text("x\n")
    assert detect_containerized(repo) == "No"


# ===========================================================================
# test_suite (U): no double-count; React/__tests__ recognised
# ===========================================================================

def test_test_suite_no_double_count(tmp_path):
    from repo_metadata_cli.metric_utils import detect_test_suite
    repo = tmp_path / "r"
    tests = repo / "tests"
    tests.mkdir(parents=True)
    # Each file matches BOTH test_*.py and *_test.py — must be counted once.
    for i in range(5):
        (tests / f"test_m{i}_test.py").write_text("def test_x(): pass\n")
    # 5 real files in 1 dir → Basic, not Comprehensive (the double-count bug hit >=10).
    assert detect_test_suite(repo) == "Basic"


def test_test_suite_react_and_dunder_tests(tmp_path):
    from repo_metadata_cli.metric_utils import detect_test_suite
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Button.test.tsx").write_text("test('x',()=>{})\n")
    assert detect_test_suite(repo) in ("Basic", "Comprehensive")

    repo2 = tmp_path / "r2"
    td = repo2 / "__tests__"
    td.mkdir(parents=True)
    (td / "foo.js").write_text("test('x',()=>{})\n")
    assert detect_test_suite(repo2) in ("Basic", "Comprehensive")


# ===========================================================================
# monitoring (T): .tsx/.jsx sources are scanned
# ===========================================================================

def test_monitoring_detects_tsx(tmp_path):
    from repo_metadata_cli.metric_utils import detect_monitoring
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.tsx").write_text(
        "import * as Sentry from '@sentry/react';\nSentry.init({});\n"
    )
    assert detect_monitoring(repo) in ("APM+Alerting", "Full SRE")


# ===========================================================================
# pr_enricher stem 3-way sync for .atom / .git.git
# ===========================================================================

@pytest.mark.parametrize("url", [
    "https://github.com/foo/bar.atom",
    "https://github.com/foo/repo.git.git",
    "https://gitlab.com/foo/proj.atom.git",
    "https://github.com/foo/normalrepo.git",
    "https://github.com/foo/plain",
    "git@gitlab.com:grp/sub/repo.git",
])
def test_stem_sync_partner_vs_pr_enricher(url):
    from repo_metadata_cli.partner import bundle_stem_from_url
    from repo_metadata_cli.pr_enricher import _repo_only_name
    assert bundle_stem_from_url(url) == _repo_only_name(url), url


# ===========================================================================
# hg fork detection (J): upstream key only counts inside [paths]
# ===========================================================================

@requires_hg
def test_hg_fork_upstream_in_paths_is_fork(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    (repo / "f.txt").write_text("x")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "init")
    (repo / ".hg" / "hgrc").write_text("[paths]\nupstream = https://hg.example.org/parent\n")
    assert _ctx(repo).vcs.detect_fork(repo) == 1.0


@requires_hg
def test_hg_fork_upstream_in_alias_is_not_fork(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    _hg(repo, "init", ".")
    (repo / "f.txt").write_text("x")
    _hg(repo, "add")
    _hg(repo, "commit", "-u", "A <a@a>", "-m", "init")
    # 'upstream' under [alias]/[ui] is unrelated to forking → must be 0.0.
    (repo / ".hg" / "hgrc").write_text("[alias]\nupstream = pull --update\n[ui]\nupstream = whatever\n")
    assert _ctx(repo).vcs.detect_fork(repo) == 0.0


# ===========================================================================
# incremental CSV append survives a column-schema mismatch
# ===========================================================================

def test_incremental_csv_schema_mismatch_does_not_corrupt(tmp_path):
    import pandas as pd
    from repo_metadata_cli.pipeline import run_metadata_pipeline

    # Seed an old, narrower schema.
    csv = tmp_path / "out.csv"
    pd.DataFrame([{"repo_id": "x", "repo_name": "alpha", "commit_count": 3}]).to_csv(csv, index=False)

    dataset = tmp_path / "data"
    (dataset / "beta" / "src").mkdir(parents=True)
    (dataset / "beta" / "src" / "m.py").write_text("a = 1\n")

    run_metadata_pipeline(dataset, csv, _settings(),
                          AllowedFiles(AllowedFilesConfig(config_file=_TOML)), None)

    # File must remain parseable (no ragged rows) and have both repos.
    df = pd.read_csv(csv)
    assert set(df["repo_name"]) == {"alpha", "beta"}
    assert list(df.columns) == ["repo_id", "repo_name", "commit_count"]
