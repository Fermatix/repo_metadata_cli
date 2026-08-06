"""Static test-coverage estimate (test_coverage_pct) and the tracked-files API.

Covers test-file recognition across languages and layouts, exclusion of
vendored/generated/binary/oversized/non-code files, the zero and cap
semantics, the full tracked-file list for Git and Mercurial, and the
plain-directory filesystem fallback.
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
from repo_metadata_cli.coverage_estimate import (
    MAX_FILE_BYTES,
    coverage_stats,
    is_code_file,
    is_test_file,
    is_vendored_path,
)
from repo_metadata_cli.metrics.testing import TestCoveragePctMetric
from repo_metadata_cli.settings import load_app_settings
from repo_metadata_cli.vcs.git import GitVCS
from repo_metadata_cli.vcs.mercurial import MercurialVCS

requires_hg = pytest.mark.skipif(shutil.which("hg") is None, reason="hg CLI not installed")

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML = _PROJECT_ROOT / "repo_metadata.toml"


def _ctx(repo: Path, vcs=None) -> RepoContext:
    return RepoContext(
        repo_path=repo,
        settings=load_app_settings(_TOML),
        tree_sitter=None,
        allowed_files=AllowedFiles(AllowedFilesConfig(config_file=_TOML)),
        vcs=vcs,
    )


# --- test-file recognition ---------------------------------------------------

@pytest.mark.parametrize("path", [
    # directory markers (whole segment, any depth, case-insensitive)
    "tests/test_app.py",
    "src/test/java/FooBar.java",
    "spec/models/user.rb",
    "__tests__/widget.js",
    "e2e/login.ts",
    "src/androidTest/kotlin/A.kt",
    "cypress/integration/x.js",
    "features/checkout.rb",
    # filename conventions (delimiter required)
    "src/test_utils.py",
    "src/utils_test.go",
    "src/widget.spec.ts",
    "src/widget.test.jsx",
    "conftest.py",
    "src/checkout.feature",
    # CamelCase class conventions (case-sensitive suffix)
    "src/FooTest.java",
    "src/BarTests.cs",
    "src/BazSpec.scala",
    "src/QuxSuite.scala",
    "src/AppTestCase.py",
    "src/FooIT.java",
])
def test_is_test_file_positive(path):
    assert is_test_file(path)


@pytest.mark.parametrize("path", [
    "src/latest.js",          # no delimiter before "test"
    "src/greatest.py",
    "contest/entry.py",       # "contest" is not a test dir segment
    "src/protest_march.md",
    "src/attest.go",
    "src/detestable.rb",
    "src/main.py",
    "src/testing_utils.py.bak",  # no recognised convention match
])
def test_is_test_file_negative(path):
    assert not is_test_file(path)


# --- code-file classification ------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("src/app.py", True),
    ("src/App.kt", True),
    ("web/index.html", True),
    ("styles/site.scss", True),
    ("features/pay.feature", True),
    ("README.md", False),                # not a code extension
    ("data/config.json", False),
    ("assets/logo.svg", False),
    ("Makefile", False),                 # no extension
    ("dist/app.min.js", False),          # generated
    ("app.min.css", False),
    ("types/index.d.ts", False),
    ("proto/msg_pb2.py", False),
    ("proto/msg_pb2_grpc.py", False),
    ("api/service.pb.go", False),
    ("lib/model.g.dart", False),
    ("lib/model.freezed.dart", False),
    ("poetry.lock", False),
    ("bundle.js.map", False),
    ("snap/__snapshots__/x.snap", False),
])
def test_is_code_file(path, expected):
    assert is_code_file(path) is expected


@pytest.mark.parametrize("path,expected", [
    ("node_modules/lib/index.js", True),
    ("a/b/vendor/pkg/x.go", True),
    (".venv/lib/site.py", True),
    ("Pods/SDK/x.m", True),
    ("dist/main.js", True),
    ("build/out.py", True),
    ("src/vendor_utils.py", False),   # segment match, not substring
    ("src/app.py", False),
])
def test_is_vendored_path(path, expected):
    assert is_vendored_path(path) is expected


# --- coverage_stats ----------------------------------------------------------

def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_coverage_stats_mixed_repo(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "src/app.py", "x = 1\n" * 60)
    _write(repo, "tests/test_app.py", "t = 1\n" * 30)
    _write(repo, "widget.spec.ts", "s\n" * 10)
    _write(repo, "node_modules/lib.js", "v\n" * 500)   # vendored: excluded
    _write(repo, "bundle.min.js", "m\n" * 200)         # generated: excluded
    _write(repo, "README.md", "doc\n" * 40)            # non-code: excluded
    (repo / "logo.bin").write_bytes(b"\0\1\2" * 100)   # binary: excluded
    files = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()]
    stats = coverage_stats(repo, files)
    # Golden values from the reference implementation on this exact layout.
    assert stats == {"test_coverage_pct": 40, "total_code_lines": 100, "test_code_lines": 40}


def test_coverage_stats_skips_binary_code_file(tmp_path):
    repo = tmp_path / "r"
    (repo).mkdir()
    (repo / "blob.py").write_bytes(b"\0" + b"x = 1\n" * 10)
    _write(repo, "app.py", "x = 1\n" * 10)
    stats = coverage_stats(repo, ["blob.py", "app.py"])
    assert stats["total_code_lines"] == 10


def test_coverage_stats_skips_oversized_file(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    big = "x = 1\n" * (MAX_FILE_BYTES // 6 + 10)
    (repo / "big.py").write_text(big)
    _write(repo, "app.py", "x = 1\n" * 5)
    stats = coverage_stats(repo, ["big.py", "app.py"])
    assert stats["total_code_lines"] == 5


def test_coverage_stats_missing_file_ignored(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _write(repo, "app.py", "x = 1\n" * 5)
    stats = coverage_stats(repo, ["app.py", "gone.py"])
    assert stats["total_code_lines"] == 5


def test_coverage_capped_at_100(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "tests/test_all.py", "t = 1\n" * 50)
    stats = coverage_stats(repo, ["tests/test_all.py"])
    assert stats["test_coverage_pct"] == 100


def test_no_tests_yields_zero(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "src/app.py", "x = 1\n" * 50)
    stats = coverage_stats(repo, ["src/app.py"])
    assert stats == {"test_coverage_pct": 0, "total_code_lines": 50, "test_code_lines": 0}


def test_no_code_files_yields_zero(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "README.md", "doc\n" * 30)
    _write(repo, "data.json", "{}\n")
    stats = coverage_stats(repo, ["README.md", "data.json"])
    assert stats == {"test_coverage_pct": 0, "total_code_lines": 0, "test_code_lines": 0}


def test_coverage_rounding(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "src/app.py", "x = 1\n" * 110)
    _write(repo, "tests/test_app.py", "t = 1\n" * 20)
    stats = coverage_stats(repo, ["src/app.py", "tests/test_app.py"])
    # 20/130 = 15.38 -> round() -> 15 (same rounding as the reference)
    assert stats["test_coverage_pct"] == 15


# --- tracked-files API -------------------------------------------------------

_GIT_ENV = dict(os.environ)


def _git(repo: Path, *args: str) -> None:
    run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_GIT_ENV)


def _init_git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "a@a.com")
    _git(repo, "config", "user.name", "A")


def test_git_tracked_files_full_list_not_truncated(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    for i in range(60):  # more than the 40-path diagnostic file_tree cap
        _write(repo, f"src/f{i:02d}.py", "x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    ctx = _ctx(repo, vcs=GitVCS())
    assert len(ctx.tracked_files) == 60
    assert len(ctx.file_tree) == 40  # the diagnostic view stays truncated
    assert "src/f59.py" in ctx.tracked_files


def test_git_tracked_files_ignores_untracked(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _write(repo, "a.py", "x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _write(repo, "untracked.py", "y = 2\n")
    ctx = _ctx(repo, vcs=GitVCS())
    assert ctx.tracked_files == ["a.py"]


@requires_hg
def test_hg_tracked_files_full_list(tmp_path):
    env = dict(os.environ, HGPLAIN="1", HGUSER="A <a@a.com>")
    repo = tmp_path / "r"
    repo.mkdir()
    run(["hg", "init"], cwd=str(repo), check=True, capture_output=True, env=env)
    for i in range(45):
        _write(repo, f"src/f{i:02d}.py", "x = 1\n")
    run(["hg", "add", "."], cwd=str(repo), check=True, capture_output=True, env=env)
    run(["hg", "commit", "-m", "init", "-d", "0 0"], cwd=str(repo),
        check=True, capture_output=True, env=env)
    ctx = _ctx(repo, vcs=MercurialVCS())
    assert len(ctx.tracked_files) == 45


def test_plain_directory_filesystem_fallback(tmp_path):
    repo = tmp_path / "r"
    _write(repo, "src/app.py", "x = 1\n" * 8)
    _write(repo, "tests/test_app.py", "t = 1\n" * 2)
    _write(repo, ".hidden/secret.py", "s = 1\n")   # dot-dirs skipped
    ctx = _ctx(repo)  # no VCS on disk -> filesystem fallback
    assert ctx.tracked_files == ["src/app.py", "tests/test_app.py"]
    assert TestCoveragePctMetric().compute(ctx) == 20  # 2 / 10 lines


def test_coverage_metric_uses_vcs_tracked_files(tmp_path):
    repo = tmp_path / "r"
    _init_git(repo)
    _write(repo, "src/app.py", "x = 1\n" * 80)
    _write(repo, "tests/test_app.py", "t = 1\n" * 20)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    # untracked noise must not affect the ratio
    _write(repo, "scratch.py", "n = 1\n" * 1000)
    ctx = _ctx(repo, vcs=GitVCS())
    assert TestCoveragePctMetric().compute(ctx) == 20
    assert ctx._cache["test_coverage_pct"] == 20  # cached for reuse
