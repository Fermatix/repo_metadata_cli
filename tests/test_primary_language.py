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
from repo_metadata_cli.metrics.files import LangDistributionMetric, PrimaryLanguageMetric
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
