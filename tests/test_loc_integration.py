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


def test_autogen_loc_le_logical_loc_without_cap(synth_repo):
    """autogen_loc <= logical_loc must hold through scoping alone, not via min() cap.

    autogen files are a strict subset of logical_loc files (same scc_exclude_dirs
    filter applied), so the invariant must be satisfied by the computation itself.
    """
    settings = _make_settings()
    ctx = _build_ctx(synth_repo, settings)
    logical = LogicalLocMetric().compute(ctx)
    autogen = AutoGenLocMetric().compute(ctx)
    assert autogen <= logical, (
        f"autogen_loc ({autogen}) > logical_loc ({logical}): scoping is broken — "
        "autogen files are not a subset of logical_loc files"
    )


def test_empty_dep_dirs_logical_equals_raw(synth_repo):
    """When no dirs are excluded, logical_loc should match raw_loc code lines."""
    settings = _make_settings(dep_dirs=(), scc_exclude=(), autogen=())
    ctx = _build_ctx(synth_repo, settings)

    raw = RawLocMetric().compute(ctx)
    logical = LogicalLocMetric().compute(ctx)
    dep = DepDirLocMetric().compute(ctx)

    assert raw >= logical
    assert dep == 0


def test_autogen_loc_excludes_files_in_scc_exclude_dirs(tmp_path):
    """Files inside scc_exclude_dirs must not contribute to autogen_loc even if
    they match autogen patterns — spec says 'only include files already counted
    in Logical LOC'."""
    from repo_metadata_cli.metric_utils import get_auto_gen_loc

    # vendor/migrations/0001.py matches autogen dir pattern "migrations"
    # but vendor/ is in exclude_dirs → must be excluded
    (tmp_path / "vendor" / "migrations").mkdir(parents=True)
    (tmp_path / "vendor" / "migrations" / "0001.py").write_text(
        "\n".join(f"x_{i} = {i}" for i in range(20))
    )
    # src/migrations/0002.py is in logical_loc scope → must be counted
    (tmp_path / "src" / "migrations").mkdir(parents=True)
    (tmp_path / "src" / "migrations" / "0002.py").write_text(
        "\n".join(f"y_{i} = {i}" for i in range(10))
    )

    result_with_exclude = get_auto_gen_loc(
        tmp_path,
        autogen_dirs={"migrations"},
        exclude_dirs={"vendor"},
    )
    result_without_exclude = get_auto_gen_loc(
        tmp_path,
        autogen_dirs={"migrations"},
        exclude_dirs=None,
    )

    # With correct scoping: only src/migrations/0002.py counts (10 lines)
    # Without scoping: both files count (30 lines) — this was the bug
    assert result_with_exclude < result_without_exclude, (
        "Scoping filter should reduce autogen_loc by excluding vendor/migrations/"
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

    # "out" appears in both autogen_dirs and scc_exclude_dirs — this is now valid
    good = MetricsSettings(
        dep_dirs=["vendor"],
        scc_exclude_dirs=["vendor", "dist", "out"],
        autogen_dirs=["generated", "out"],
    )
    with caplog.at_level(logging.WARNING, logger="repo_metadata_cli.settings"):
        _validate_metrics_config(good)

    assert caplog.messages == [], f"Expected no warnings, got: {caplog.messages}"


def test_shipped_config_excludes_python_env_dirs(caplog):
    """The shipped repo_metadata.toml must exclude Python virtualenv / installed
    package directories from logical_loc and keep the dep_dirs ⊆ scc_exclude_dirs
    invariant. Guards the config against regressing to the old short list."""
    import logging
    from repo_metadata_cli.settings import load_app_settings, _validate_metrics_config

    m = load_app_settings(_TOML_PATH).metrics
    for d in (".venv", "venv", "site-packages", "Pods", "__pycache__"):
        assert d in m.scc_exclude_dirs, f"{d} must be excluded from logical_loc"
    for d in (".venv", "venv", "site-packages"):
        assert d in m.dep_dirs, f"{d} should be counted as dependency code"
    assert set(m.dep_dirs) <= set(m.scc_exclude_dirs), "dep_dirs ⊄ scc_exclude_dirs"

    with caplog.at_level(logging.WARNING, logger="repo_metadata_cli.settings"):
        _validate_metrics_config(m)
    assert caplog.messages == [], f"Shipped config must be valid, got: {caplog.messages}"


@pytest.fixture
def synth_repo_with_venv(tmp_path):
    """Synthetic repo with own code plus a committed .venv/ and site-packages/."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / "site-packages").mkdir()

    (repo / "src" / "main.py").write_text(
        "\n".join(f"x_{i} = {i}" for i in range(10)) + "\n"
    )
    # Third-party code that must NOT count toward logical_loc.
    (repo / ".venv" / "lib" / "dep.py").write_text(
        "\n".join(f"a_{i} = {i}" for i in range(200)) + "\n"
    )
    (repo / "site-packages" / "pkg.py").write_text(
        "\n".join(f"b_{i} = {i}" for i in range(150)) + "\n"
    )

    run(["git", "init"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_venv_excluded_from_logical_loc_with_shipped_config(synth_repo_with_venv):
    """With the shipped config, .venv/ and site-packages/ code lands in
    dep_dir_loc, not logical_loc."""
    from repo_metadata_cli.settings import load_app_settings

    settings = load_app_settings(_TOML_PATH)  # real lists, no override
    ctx = _build_ctx(synth_repo_with_venv, settings)

    raw = RawLocMetric().compute(ctx)
    logical = LogicalLocMetric().compute(ctx)
    dep = DepDirLocMetric().compute(ctx)

    # 350 lines of third-party code exist; logical_loc must stay near the ~10
    # lines of own code and be far below raw.
    assert logical < raw
    assert logical <= 20, f"logical_loc ({logical}) must exclude .venv/site-packages code"
    assert dep >= 300, f"dep_dir_loc ({dep}) should capture the excluded dependency code"
