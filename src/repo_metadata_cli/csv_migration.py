"""Legacy-CSV schema migration and backfill for late-added metric columns.

The incremental pipeline appends rows aligned to the EXISTING header, so a CSV
produced by an older version would silently drop columns added later.  This
module upgrades such CSVs in place:

* :func:`migrate_csv_schema` appends the missing new columns (empty) while
  preserving every existing column (including unknown extras), every value and
  the row order;
* :func:`update_row_fields` backfills the new columns of one existing row,
  matched by the stable ``(repo_org, repo_name)`` key with legacy fallbacks;
* :func:`warn_unfilled_rows` reports rows whose new columns are still empty
  after a run (source repo gone from the dataset, or a computation error) —
  they are kept, not deleted, and retried on the next run.

Every rewrite is atomic (temp file + ``os.replace``), so a failure cannot leave
a partially written CSV behind.  Cells are read as plain strings
(``dtype=str, keep_default_na=False``) so existing values survive the rewrite
without numeric re-formatting or NaN round-trips.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

# Columns appended to the schema tail after the initial release, in order.
NEW_COLUMNS: tuple = (
    "pr_simple_pct",
    "pr_standard_pct",
    "pr_rich_pct",
    "avg_loc_per_pr",
    "test_coverage_pct",
    "functions_count",
    "classes_count",
    "untested_files_pct",
    "merged_pr_count",
    "clean_logical_loc",
)


def _read_csv_preserving(csv_path: Path) -> pd.DataFrame:
    return pd.read_csv(csv_path, dtype=str, keep_default_na=False)


def _atomic_write(df: pd.DataFrame, csv_path: Path) -> None:
    tmp_path = csv_path.with_name(csv_path.name + ".tmp")
    df.to_csv(tmp_path, index=False)
    os.replace(tmp_path, csv_path)


def _load_for_update(csv_path: Path) -> Optional[pd.DataFrame]:
    if not csv_path.exists():
        return None
    try:
        return _read_csv_preserving(csv_path)
    except Exception as exc:
        logger.warning("Could not read %s for schema migration: %s", csv_path, exc)
        return None


def migrate_csv_schema(csv_path: Path) -> bool:
    """Append missing :data:`NEW_COLUMNS` (empty) to an existing CSV.

    All prior columns, values and row order are preserved; unknown extra
    columns survive.  The write is atomic.  Returns True when a migration was
    performed.
    """
    df = _load_for_update(csv_path)
    if df is None:
        return False
    missing = [c for c in NEW_COLUMNS if c not in df.columns]
    if not missing:
        return False
    for column in missing:
        df[column] = ""
    _atomic_write(df, csv_path)
    logger.info(
        "Migrated %s: appended %d new column(s): %s",
        csv_path, len(missing), ", ".join(missing),
    )
    return True


def _incomplete_mask(df: pd.DataFrame) -> "pd.Series":
    """Rows where at least one new column is empty (unfilled or failed earlier)."""
    mask = pd.Series(False, index=df.index)
    for column in NEW_COLUMNS:
        if column in df.columns:
            mask |= df[column].astype(str).str.strip() == ""
    return mask


def rows_needing_backfill(csv_path: Path) -> Set[str]:
    """Identifiers of rows whose new columns need (re)computation.

    Returns both ``"org\\tname"`` keys and bare names, mirroring the identifier
    sets the pipeline's dedup logic uses, so callers can test membership with
    whichever identifier they have.  Empty new cells count as unfinished — a
    row that previously failed is retried on the next run.
    """
    df = _load_for_update(csv_path)
    if df is None or df.empty or "repo_name" not in df.columns:
        return set()
    if not any(c in df.columns for c in NEW_COLUMNS):
        return set()
    keys: Set[str] = set()
    has_org = "repo_org" in df.columns
    for _, row in df[_incomplete_mask(df)].iterrows():
        name = str(row.get("repo_name", "")).strip()
        if not name:
            continue
        org = str(row.get("repo_org", "")).strip() if has_org else ""
        keys.add(f"{org}\t{name}")
        keys.add(name)
    return keys


def _match_rows(df: pd.DataFrame, org: str, leaf: str, stem: str) -> "pd.Series":
    """Boolean mask of rows matching the stable ``(repo_org, repo_name)`` key.

    Falls back for legacy CSVs: exact (org, leaf) first, then leaf-only, then
    the bundle stem (older CSVs stored the full-path stem as repo_name).
    """
    if "repo_name" not in df.columns:
        return pd.Series(False, index=df.index)
    names = df["repo_name"].astype(str)
    if org and "repo_org" in df.columns:
        mask = (df["repo_org"].astype(str) == org) & (names == leaf)
        if mask.any():
            return mask
    mask = names == leaf
    if mask.any():
        return mask
    return names == stem


def update_row_fields(
    csv_path: Path, org: str, leaf: str, stem: str, values: Dict[str, object]
) -> bool:
    """Backfill the given fields on the matching existing row(s), atomically.

    ``None`` values (a metric that failed to compute) leave the cell as-is, so
    only genuinely computed values are recorded and failures stay retryable.
    Returns True when at least one row was updated.  Never appends rows — the
    caller's append path handles genuinely new repositories.
    """
    df = _load_for_update(csv_path)
    if df is None:
        return False
    mask = _match_rows(df, org, leaf, stem)
    if not mask.any():
        return False
    for field, value in values.items():
        if value is not None and field in df.columns:
            df.loc[mask, field] = str(value)
    _atomic_write(df, csv_path)
    return True


def warn_unfilled_rows(csv_path: Path) -> List[str]:
    """Warn about rows whose new columns are still empty; return their names.

    Such rows are kept untouched: their source bundle/local repo is absent from
    the current dataset, or every computation attempt failed.  The empty cells
    make them eligible for another backfill attempt on the next run.
    """
    df = _load_for_update(csv_path)
    if df is None or df.empty or "repo_name" not in df.columns:
        return []
    if not all(c in df.columns for c in NEW_COLUMNS):
        return []
    names: List[str] = []
    has_org = "repo_org" in df.columns
    for _, row in df[_incomplete_mask(df)].iterrows():
        name = str(row.get("repo_name", "")).strip()
        org = str(row.get("repo_org", "")).strip() if has_org else ""
        label = f"{org}/{name}" if org else name
        names.append(name)
        logger.warning(
            "Row %s: new columns (%s) left empty — source repo missing from the "
            "current dataset or computation failed; row kept, will retry next run.",
            label,
            ", ".join(c for c in NEW_COLUMNS if str(row.get(c, "")).strip() == ""),
        )
    return names
