"""primary_language / lang_distribution must reflect hand-written code.

A committed bundle / minified asset / generated stub must not decide the
primary language. Regression guard for the case where a TypeScript app ships a
large committed ``*.bundle.js`` and was previously reported as JavaScript.

Requires scc (skipped otherwise).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metrics.files import (
    FullLangDistributionMetric,
    LangDistributionMetric,
    PrimaryLanguageMetric,
    StackMetric,
)
from repo_metadata_cli.settings import AppSettings, load_app_settings

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML_PATH = _PROJECT_ROOT / "repo_metadata.toml"

pytestmark = pytest.mark.skipif(shutil.which("scc") is None, reason="scc not installed")


def _build_ctx(repo_path: Path, settings: AppSettings) -> RepoContext:
    return RepoContext(
        repo_path=repo_path,
        settings=settings,
        tree_sitter=None,
        allowed_files=AllowedFiles(AllowedFilesConfig(config_file=_TOML_PATH)),
    )


def _settings() -> AppSettings:
    base = load_app_settings(_TOML_PATH)
    base.metrics.dep_dirs = ["vendor", "node_modules"]
    base.metrics.scc_exclude_dirs = ["vendor", "node_modules"]
    base.metrics.autogen_dirs = ["generated", "migrations"]
    return base


@pytest.fixture
def ts_app_with_bundle(tmp_path):
    """TypeScript app (50 hand-written TS lines) shipping a 100-line committed
    JS bundle and a tiny 5-line hand-written .js. Raw JS (105) > TS (50), but
    hand-written TS (50) > hand-written JS (5)."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.ts").write_text("\n".join(f"export const a{i} = {i};" for i in range(30)) + "\n")
    (repo / "src" / "util.ts").write_text("\n".join(f"export function u{i}() {{ return {i}; }}" for i in range(20)) + "\n")
    # committed bundle — matches the *.bundle.js autogen filename pattern
    (repo / "src" / "app.bundle.js").write_text("\n".join(f"var b{i}={i};" for i in range(100)) + "\n")
    (repo / "src" / "index.js").write_text("\n".join(f"const c{i}={i};" for i in range(5)) + "\n")
    run(["git", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_primary_language_ignores_generated_bundle(ts_app_with_bundle):
    ctx = _build_ctx(ts_app_with_bundle, _settings())
    primary = PrimaryLanguageMetric().compute(ctx)
    assert primary == "TypeScript", (
        f"expected TypeScript (hand-written), got {primary!r} — the committed "
        f"app.bundle.js must not decide primary_language"
    )


def test_lang_distribution_excludes_bundle(ts_app_with_bundle):
    import json

    ctx = _build_ctx(ts_app_with_bundle, _settings())
    dist = json.loads(LangDistributionMetric().compute(ctx))
    # TypeScript present and dominant; the generated bundle's JS share must be
    # small (only the 5-line hand-written index.js remains).
    assert dist.get("TypeScript", 0) > dist.get("JavaScript", 0)


def _git_commit_all(repo: Path) -> None:
    run(["git", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def mixed_code_and_data(tmp_path):
    """60 Python + 40 Shell + 20 Dockerfile lines of real code, drowned in
    non-code files (JSON/YAML/Markdown/HTML) that dominate by raw line count."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("\n".join(f"x{i} = {i}" for i in range(60)) + "\n")
    (repo / "run.sh").write_text("\n".join(f"echo line{i}" for i in range(40)) + "\n")
    (repo / "Dockerfile").write_text("FROM python:3.12\n" + "\n".join(f"RUN echo {i}" for i in range(19)) + "\n")
    (repo / "data.json").write_text("[\n" + ",\n".join(f'  {{"k{i}": {i}}}' for i in range(200)) + "\n]\n")
    (repo / "config.yaml").write_text("\n".join(f"key{i}: {i}" for i in range(100)) + "\n")
    (repo / "README.md").write_text("\n".join(f"line {i} of docs" for i in range(100)) + "\n")
    (repo / "page.html").write_text("\n".join(f"<p>row {i}</p>" for i in range(80)) + "\n")
    _git_commit_all(repo)
    return repo


@pytest.fixture
def data_only_repo(tmp_path):
    """No real code at all — a JSON dataset with Markdown docs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.json").write_text("[\n" + ",\n".join(f'  {{"k{i}": {i}}}' for i in range(150)) + "\n]\n")
    (repo / "README.md").write_text("\n".join(f"line {i} of docs" for i in range(50)) + "\n")
    _git_commit_all(repo)
    return repo


def test_lang_distribution_keeps_real_languages_only(mixed_code_and_data):
    import json

    ctx = _build_ctx(mixed_code_and_data, _settings())
    dist = json.loads(LangDistributionMetric().compute(ctx))
    assert not {"JSON", "YAML", "Markdown", "HTML"} & dist.keys(), dist
    # Shell and Dockerfile are real code (sampler parity), Python dominates.
    assert set(dist) == {"Python", "Shell", "Dockerfile"}, dist
    assert abs(sum(dist.values()) - 1.0) < 0.05, "shares must renormalize over real code"
    assert PrimaryLanguageMetric().compute(ctx) == "Python"


def test_full_lang_distribution_is_unfiltered(mixed_code_and_data):
    import json

    ctx = _build_ctx(mixed_code_and_data, _settings())
    full = json.loads(FullLangDistributionMetric().compute(ctx))
    assert {"JSON", "YAML", "Markdown", "HTML", "Python"} <= full.keys(), full
    assert abs(sum(full.values()) - 1.0) < 0.05


def test_stack_keeps_real_languages_only(mixed_code_and_data):
    ctx = _build_ctx(mixed_code_and_data, _settings())
    stack = StackMetric().compute(ctx)
    assert "JSON" not in stack and "YAML" not in stack, stack
    assert stack.startswith("Python"), stack


def test_data_only_repo_has_no_primary_language(data_only_repo):
    import json

    ctx = _build_ctx(data_only_repo, _settings())
    assert json.loads(LangDistributionMetric().compute(ctx)) == {}
    assert PrimaryLanguageMetric().compute(ctx) == ""
    assert StackMetric().compute(ctx) == ""
    # ... while the unfiltered picture is preserved.
    full = json.loads(FullLangDistributionMetric().compute(ctx))
    assert "JSON" in full and "Markdown" in full, full
