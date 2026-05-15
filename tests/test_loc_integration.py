"""Integration tests for LOC metrics.

Creates a synthetic git repository with a known file structure and verifies
that raw_loc, logical_loc, autogen_loc, dep_dir_loc satisfy their invariants.

Requires: git (always available), scc (optional — invariant checks only).
"""

from __future__ import annotations

from pathlib import Path
from subprocess import run

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.metrics.loc import (
    AutoGenLocMetric,
    DepDirLocMetric,
    LogicalLocMetric,
    RawLocMetric,
)
from repo_metadata_cli.settings import AppSettings, MetricsSettings

# Use the project's TOML for AllowedFiles (only needed for tree-sitter metrics,
# not for LOC metrics, but RepoContext requires the field).
_PROJECT_ROOT = Path(__file__).parent.parent
_TOML_PATH = _PROJECT_ROOT / "repo_metadata.toml"


def _make_allowed_files() -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML_PATH))


def _build_ctx(repo_path: Path, settings: AppSettings) -> RepoContext:
    return RepoContext(
        repo_path=repo_path,
        settings=settings,
        tree_sitter=None,
        allowed_files=_make_allowed_files(),
    )


@pytest.fixture
def synth_repo(tmp_path):
    """Synthetic git repo with known structure:

    src/main.py       — 10 code lines, 2 blank, 1 comment  (logical LOC, not autogen)
    generated/api.py  — 5 code lines                        (autogen_loc)
    vendor/lib.py     — 20 code lines                       (dep_dir_loc, excluded from logical)
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "generated").mkdir()
    (repo / "vendor").mkdir()

    main_lines = ["# header comment"] + [""] * 2 + [f"x_{i} = {i}" for i in range(10)]
    (repo / "src" / "main.py").write_text("\n".join(main_lines) + "\n")

    api_lines = [f"y_{i} = {i}" for i in range(5)]
    (repo / "generated" / "api.py").write_text("\n".join(api_lines) + "\n")

    lib_lines = [f"z_{i} = {i}" for i in range(20)]
    (repo / "vendor" / "lib.py").write_text("\n".join(lib_lines) + "\n")

    run(["git", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    return repo


def _make_settings(dep_dirs=("vendor",), scc_exclude=("vendor",), autogen=("generated",)):
    from repo_metadata_cli.settings import load_app_settings
    base = load_app_settings(_TOML_PATH)
    base.metrics.dep_dirs = list(dep_dirs)
    base.metrics.scc_exclude_dirs = list(scc_exclude)
    base.metrics.autogen_dirs = list(autogen)
    return base


def test_raw_loc_includes_all_files(synth_repo):
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)
    raw = RawLocMetric().compute(ctx)
    assert isinstance(raw, int)
    assert raw > 0


def test_logical_loc_less_than_raw_loc(synth_repo):
    """vendor/ is excluded from logical_loc → logical < raw."""
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)
    raw = RawLocMetric().compute(ctx)
    logical = LogicalLocMetric().compute(ctx)
    assert logical < raw, (
        f"logical_loc ({logical}) should be < raw_loc ({raw}) because vendor/ is excluded"
    )


def test_autogen_loc_le_logical_loc(synth_repo):
    """autogen_loc is capped at logical_loc."""
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)
    logical = LogicalLocMetric().compute(ctx)
    autogen = AutoGenLocMetric().compute(ctx)
    assert autogen <= logical, (
        f"autogen_loc ({autogen}) must not exceed logical_loc ({logical})"
    )


def test_dep_dir_loc_non_negative(synth_repo):
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)
    dep = DepDirLocMetric().compute(ctx)
    assert isinstance(dep, int)
    assert dep >= 0


def test_loc_invariant_chain(synth_repo):
    """Core invariant: raw >= logical >= autogen >= 0 and dep >= 0."""
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)

    raw = RawLocMetric().compute(ctx)
    logical = LogicalLocMetric().compute(ctx)
    autogen = AutoGenLocMetric().compute(ctx)
    dep = DepDirLocMetric().compute(ctx)

    assert raw >= logical >= autogen >= 0, (
        f"Invariant broken: raw={raw}, logical={logical}, autogen={autogen}"
    )
    assert dep >= 0, f"dep_dir_loc must be non-negative, got {dep}"


def test_empty_dep_dirs_logical_equals_raw(synth_repo):
    """When no dirs are excluded, logical_loc should match raw_loc code lines."""
    settings = _make_settings(dep_dirs=(), scc_exclude=(), autogen=())
    ctx = _build_ctx(synth_repo, settings)

    raw = RawLocMetric().compute(ctx)
    logical = LogicalLocMetric().compute(ctx)
    dep = DepDirLocMetric().compute(ctx)

    assert raw >= logical
    assert dep == 0


def test_validate_metrics_config_warns_on_overlap(caplog):
    """_validate_metrics_config should log a warning when autogen_dirs ∩ scc_exclude_dirs ≠ ∅."""
    import logging
    from repo_metadata_cli.settings import MetricsSettings, _validate_metrics_config

    bad = MetricsSettings(
        dep_dirs=["vendor"],
        scc_exclude_dirs=["vendor", "generated"],
        autogen_dirs=["generated"],  # "generated" is also in scc_exclude_dirs
    )
    with caplog.at_level(logging.WARNING, logger="repo_metadata_cli.settings"):
        _validate_metrics_config(bad)

    assert any("autogen_dirs" in msg and "scc_exclude_dirs" in msg for msg in caplog.messages), (
        "Expected a warning about autogen_dirs ∩ scc_exclude_dirs overlap"
    )


def test_validate_metrics_config_warns_on_missing_dep_in_exclude(caplog):
    """_validate_metrics_config should log a warning when dep_dirs ⊄ scc_exclude_dirs."""
    import logging
    from repo_metadata_cli.settings import MetricsSettings, _validate_metrics_config

    bad = MetricsSettings(
        dep_dirs=["vendor", "Pods"],
        scc_exclude_dirs=["vendor"],  # "Pods" is not in scc_exclude_dirs
        autogen_dirs=["generated"],
    )
    with caplog.at_level(logging.WARNING, logger="repo_metadata_cli.settings"):
        _validate_metrics_config(bad)

    assert any("dep_dirs" in msg and "scc_exclude_dirs" in msg for msg in caplog.messages), (
        "Expected a warning about dep_dirs not being subset of scc_exclude_dirs"
    )


def test_validate_metrics_config_no_warnings_for_valid_config(caplog):
    import logging
    from repo_metadata_cli.settings import MetricsSettings, _validate_metrics_config

    good = MetricsSettings(
        dep_dirs=["vendor"],
        scc_exclude_dirs=["vendor", "dist"],
        autogen_dirs=["generated"],
    )
    with caplog.at_level(logging.WARNING, logger="repo_metadata_cli.settings"):
        _validate_metrics_config(good)

    assert caplog.messages == [], f"Expected no warnings, got: {caplog.messages}"
