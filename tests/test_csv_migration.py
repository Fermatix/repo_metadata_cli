"""Legacy-CSV migration and backfill of the late-added metric columns.

Covers: schema migration (columns appended, everything else preserved, atomic
write), fresh-CSV schema, backfill of existing rows through the pipeline
without duplicate rows, preservation of unknown extra columns, rows whose
source repo is gone, retry after a computation error, and idempotent re-runs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from subprocess import run

import pandas as pd
import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.csv_migration import (
    NEW_COLUMNS,
    migrate_csv_schema,
    rows_needing_backfill,
    update_row_fields,
    warn_unfilled_rows,
)
from repo_metadata_cli.pipeline import TRAILING_METRICS, run_metadata_pipeline
from repo_metadata_cli.settings import AppSettings, load_app_settings

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML = _PROJECT_ROOT / "repo_metadata.toml"


def _settings() -> AppSettings:
    s = load_app_settings(_TOML)
    s.metrics.scc_exclude_dirs = ["node_modules", "vendor", "dist", "build"]
    return s


def _allowed() -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML))


def _run(dataset: Path, csv: Path) -> None:
    run_metadata_pipeline(dataset, csv, _settings(), _allowed(), None)


def _git(repo: Path, *args: str) -> None:
    run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(dataset: Path, name: str) -> Path:
    """Local-mode git repo with a squash-PR fingerprint and a test file."""
    repo = dataset / name
    (repo / "src").mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "a@a.com")
    _git(repo, "config", "user.name", "A")
    (repo / "src" / "app.py").write_text("x = 1\n" * 80)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("t = 1\n" * 20)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "src" / "extra.py").write_text("y = 2\n" * 30)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Add extra (#1)")
    return repo


# Expected trailing values for _make_repo: one squash PR unit of 30 lines
# (fingerprint -> merged_pr_count=1), 20 test lines vs 130 total, 1 test file
# out of 3; functions/classes are 0 because the pipeline runs with
# ts_manager=None; all 130 lines are Python code, so clean_logical_loc counts
# them all and none of them is generated (BH == BG, BI == 0).
_EXPECTED = {
    "pr_simple_pct": 100, "pr_standard_pct": 0, "pr_rich_pct": 0,
    "avg_loc_per_pr": 30, "test_coverage_pct": 15,
    "functions_count": 0, "classes_count": 0,
    "untested_files_pct": 67, "merged_pr_count": 1,
    "clean_logical_loc": 130,
    "clean_handwritten_loc": 130, "autogen_in_clean_loc": 0,
    "meta_logical_loc": 0, "meta_non_authored_loc": 0,
    "meta_duplication_ratio": 0, "meta_non_merge_commit_count": 0,
}

assert set(_EXPECTED) == set(NEW_COLUMNS)
assert [m.field_name for m in TRAILING_METRICS] == list(NEW_COLUMNS)


@pytest.fixture(autouse=True)
def _stable_external_metrics(monkeypatch):
    """Schema tests do not depend on locally installed scc/jscpd/git tools."""
    from repo_metadata_cli.metrics import external

    monkeypatch.setattr(external, "get_meta_logical_loc", lambda repo: 0)
    monkeypatch.setattr(external, "get_meta_non_authored_loc", lambda repo: 0)
    monkeypatch.setattr(external, "get_meta_duplication_ratio", lambda repo: 0.0)
    monkeypatch.setattr(external, "get_meta_non_merge_commit_count", lambda repo: 0)


# --- unit level --------------------------------------------------------------

def test_migrate_adds_columns_preserving_everything(tmp_path):
    csv = tmp_path / "meta.csv"
    pd.DataFrame([
        {"repo_org": "org/a", "repo_name": "alpha", "raw_loc": 100, "custom_col": "keep me"},
        {"repo_org": "org/b", "repo_name": "beta", "raw_loc": "0.500", "custom_col": ""},
    ]).to_csv(csv, index=False)

    assert migrate_csv_schema(csv) is True

    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert list(df.columns) == ["repo_org", "repo_name", "raw_loc", "custom_col", *NEW_COLUMNS]
    assert list(df["repo_name"]) == ["alpha", "beta"]      # row order preserved
    assert df.loc[0, "custom_col"] == "keep me"            # unknown column preserved
    assert df.loc[1, "raw_loc"] == "0.500"                 # no numeric re-formatting
    assert all(df.loc[i, c] == "" for i in (0, 1) for c in NEW_COLUMNS)
    assert not csv.with_name(csv.name + ".tmp").exists()   # atomic write cleaned up


def test_backfill_refreshes_pr_columns_for_consistency(tmp_path):
    # A legacy row's total/reviewed may predate the current PR source; writing
    # a fresh merged_pr_count next to them would violate merged <= total, so
    # the backfill refreshes the whole PR triple from the same source.
    dataset = tmp_path / "data"
    _make_repo(dataset, "alpha")
    csv = tmp_path / "meta.csv"
    _run(dataset, csv)

    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    df = df.drop(columns=list(NEW_COLUMNS))
    df["total_pr_count"] = "999"      # stale value from an older era
    df["reviewed_pr_count"] = "500"
    df.to_csv(csv, index=False)

    _run(dataset, csv)

    out = pd.read_csv(csv, dtype=str, keep_default_na=False)
    row = out.iloc[0]
    assert row["merged_pr_count"] == "1"       # squash fingerprint
    assert row["total_pr_count"] == "1"        # refreshed, not left at 999
    assert row["reviewed_pr_count"] == "0"     # refreshed (no cache -> 0)


def test_migrate_noop_on_current_schema(tmp_path):
    csv = tmp_path / "meta.csv"
    pd.DataFrame([{"repo_name": "a", **{c: 1 for c in NEW_COLUMNS}}]).to_csv(csv, index=False)
    assert migrate_csv_schema(csv) is False


def test_migrate_noop_on_missing_file(tmp_path):
    assert migrate_csv_schema(tmp_path / "absent.csv") is False


def test_rows_needing_backfill_keys(tmp_path):
    csv = tmp_path / "meta.csv"
    filled = {c: "5" for c in NEW_COLUMNS}
    empty = {c: "" for c in NEW_COLUMNS}
    partial = {**filled, "test_coverage_pct": ""}
    pd.DataFrame([
        {"repo_org": "org/a", "repo_name": "alpha", **empty},
        {"repo_org": "org/b", "repo_name": "beta", **filled},
        {"repo_org": "", "repo_name": "gamma", **partial},
    ]).to_csv(csv, index=False)
    keys = rows_needing_backfill(csv)
    assert "org/a\talpha" in keys and "alpha" in keys
    assert "\tgamma" in keys and "gamma" in keys
    assert "beta" not in keys and "org/b\tbeta" not in keys


def test_update_row_fields_matches_and_writes(tmp_path):
    csv = tmp_path / "meta.csv"
    empty = {c: "" for c in NEW_COLUMNS}
    pd.DataFrame([
        {"repo_org": "org/a", "repo_name": "app", **empty},
        {"repo_org": "org/b", "repo_name": "app", **empty},
    ]).to_csv(csv, index=False)
    values = dict.fromkeys(NEW_COLUMNS, 7)
    assert update_row_fields(csv, "org/b", "app", "org_b_app", values) is True
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    # Only the (org/b, app) row is updated — the same leaf under org/a is not.
    assert all(df.loc[0, c] == "" for c in NEW_COLUMNS)
    assert all(df.loc[1, c] == "7" for c in NEW_COLUMNS)


def test_update_row_fields_leaf_and_stem_fallbacks(tmp_path):
    csv = tmp_path / "meta.csv"
    empty = {c: "" for c in NEW_COLUMNS}
    pd.DataFrame([{"repo_name": "full-path-stem", **empty}]).to_csv(csv, index=False)
    # legacy CSV without repo_org, repo_name kept as the bundle stem
    assert update_row_fields(csv, "org/a", "leaf", "full-path-stem",
                             dict.fromkeys(NEW_COLUMNS, 3)) is True
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert all(df.loc[0, c] == "3" for c in NEW_COLUMNS)


def test_update_row_fields_none_values_leave_cells(tmp_path):
    csv = tmp_path / "meta.csv"
    empty = {c: "" for c in NEW_COLUMNS}
    pd.DataFrame([{"repo_name": "a", **empty}]).to_csv(csv, index=False)
    values = dict.fromkeys(NEW_COLUMNS, 4)
    values["test_coverage_pct"] = None  # this metric failed
    assert update_row_fields(csv, "", "a", "a", values) is True
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert df.loc[0, "pr_simple_pct"] == "4"
    assert df.loc[0, "test_coverage_pct"] == ""  # left empty -> retried later


def test_update_row_fields_no_match_returns_false(tmp_path):
    csv = tmp_path / "meta.csv"
    pd.DataFrame([{"repo_name": "a", **{c: "" for c in NEW_COLUMNS}}]).to_csv(csv, index=False)
    assert update_row_fields(csv, "", "zzz", "zzz", dict.fromkeys(NEW_COLUMNS, 1)) is False


# --- pipeline level ----------------------------------------------------------

def test_fresh_csv_has_new_columns_at_schema_tail(tmp_path):
    dataset = tmp_path / "data"
    _make_repo(dataset, "alpha")
    csv = tmp_path / "meta.csv"
    _run(dataset, csv)
    df = pd.read_csv(csv)
    assert list(df.columns)[-len(NEW_COLUMNS):] == list(NEW_COLUMNS)
    row = df.iloc[0]
    for field, expected in _EXPECTED.items():
        assert float(row[field]) == expected, field


def test_legacy_csv_migrated_and_backfilled_without_duplicates(tmp_path, caplog):
    dataset = tmp_path / "data"
    _make_repo(dataset, "alpha")
    csv = tmp_path / "meta.csv"
    _run(dataset, csv)

    # Simulate a CSV written by the previous version: no new columns, plus an
    # unknown extra column and a row whose source repo no longer exists.
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    df = df.drop(columns=list(NEW_COLUMNS))
    df["custom_note"] = "keep"
    ghost = {c: "" for c in df.columns}
    ghost.update({"repo_name": "ghost", "custom_note": "keep too"})
    df = pd.concat([df, pd.DataFrame([ghost])], ignore_index=True)
    df.to_csv(csv, index=False)

    with caplog.at_level(logging.WARNING):
        _run(dataset, csv)

    out = pd.read_csv(csv, dtype=str, keep_default_na=False)
    # no duplicate rows; order preserved; extra column intact
    assert list(out["repo_name"]) == ["alpha", "ghost"]
    assert list(out["custom_note"]) == ["keep", "keep too"]
    assert list(out.columns)[-len(NEW_COLUMNS):] == list(NEW_COLUMNS)
    alpha = out[out["repo_name"] == "alpha"].iloc[0]
    for field, expected in _EXPECTED.items():
        assert float(alpha[field]) == expected, field
    # the ghost row is kept, its new cells stay empty, and a warning names it
    ghost_row = out[out["repo_name"] == "ghost"].iloc[0]
    assert all(ghost_row[c] == "" for c in NEW_COLUMNS)
    assert "ghost" in caplog.text


def test_backfill_error_leaves_cell_empty_then_retries(tmp_path, monkeypatch, caplog):
    dataset = tmp_path / "data"
    _make_repo(dataset, "alpha")
    csv = tmp_path / "meta.csv"
    _run(dataset, csv)

    # Strip the new columns -> next run must migrate and backfill.
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    df.drop(columns=list(NEW_COLUMNS)).to_csv(csv, index=False)

    # First backfill attempt: the coverage computation raises.
    import repo_metadata_cli.metrics.testing as testing_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(testing_mod, "coverage_stats", _boom)
    with caplog.at_level(logging.WARNING):
        _run(dataset, csv)
    out = pd.read_csv(csv, dtype=str, keep_default_na=False)
    row = out.iloc[0]
    assert row["test_coverage_pct"] == ""              # failed metric: empty cell
    assert int(row["pr_simple_pct"]) == 100            # others still computed
    # the warning names both the metric and the repository
    assert "test_coverage_pct" in caplog.text and "alpha" in caplog.text

    # Second run without the failure: the empty cell is recomputed.
    monkeypatch.undo()
    _run(dataset, csv)
    out = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert int(out.iloc[0]["test_coverage_pct"]) == _EXPECTED["test_coverage_pct"]
    assert len(out) == 1                               # still no duplicates


def test_rerun_is_idempotent(tmp_path):
    dataset = tmp_path / "data"
    _make_repo(dataset, "alpha")
    _make_repo(dataset, "beta")
    csv = tmp_path / "meta.csv"
    _run(dataset, csv)
    first = pd.read_csv(csv, dtype=str, keep_default_na=False)
    _run(dataset, csv)
    second = pd.read_csv(csv, dtype=str, keep_default_na=False)
    assert len(second) == 2
    pd.testing.assert_frame_equal(first, second)


def test_warn_unfilled_rows_lists_missing(tmp_path, caplog):
    csv = tmp_path / "meta.csv"
    filled = {c: "1" for c in NEW_COLUMNS}
    empty = {c: "" for c in NEW_COLUMNS}
    pd.DataFrame([
        {"repo_org": "o", "repo_name": "done", **filled},
        {"repo_org": "o", "repo_name": "pending", **empty},
    ]).to_csv(csv, index=False)
    with caplog.at_level(logging.WARNING):
        names = warn_unfilled_rows(csv)
    assert names == ["pending"]
    assert "o/pending" in caplog.text
