#!/usr/bin/env python3
"""Validation script for repo_metadata.csv against Field Definitions spec.

Usage:
    python validate_csv.py repo_metadata.csv
    python validate_csv.py /path/to/output.csv

Exit codes:
    0 — no errors (warnings only)
    1 — at least one error found
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Constants — allowed values for categorical columns
# ---------------------------------------------------------------------------

_CI_CHECKS_VALUES = {"Yes", "No"}
_CONTAINERIZED_VALUES = {"Yes", "No"}
_DEPLOYMENT_VALUES = {"None", "Basic CI", "Full CI-CD", "Enterprise"}
_MONITORING_VALUES = {"None", "Basic", "APM+Alerting", "Full SRE"}
_TEST_SUITE_VALUES = {"None", "Basic", "Comprehensive"}
_HOLDOUT_VALUES = {"Unverified", "Likely Private", "Verified Private", "Verified+Eval-Ready"}
_README_VALUES = {"None", "Basic", "Detailed", "Comprehensive"}
_ISSUE_TRACKER_VALUES = {"None", "Basic", "Linked to Commits", "Full+Design Docs"}

_NON_CODE_PRIMARY_LANGS = {"SVG", "JSON", "Plain Text", "Markdown", "TOML", "YAML", "XML"}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

Violation = Tuple[str, str, str]  # (check_id, repo_name, detail)


class CheckResult:
    def __init__(self, check_id: str, repo_description: str, is_error: bool) -> None:
        self.check_id = check_id
        self.repo_description = repo_description
        self.is_error = is_error
        self.violations: List[Violation] = []

    def add(self, repo_name: str, detail: str) -> None:
        self.violations.append((self.check_id, str(repo_name), detail))

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


# ---------------------------------------------------------------------------
# Helper — safe JSON parse for lang_distribution
# ---------------------------------------------------------------------------

def _parse_lang_dist(value: str) -> dict | None:
    if pd.isna(value) or value == "":
        return {}
    try:
        result = json.loads(str(value))
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Block 1 — Format & allowed values
# ---------------------------------------------------------------------------

def _check_format(df: pd.DataFrame) -> List[CheckResult]:
    results: List[CheckResult] = []

    def col(check_id: str, repo_description: str, is_error: bool = True) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error)
        results.append(r)
        return r

    # repo_id: non-null, non-empty
    r = col("id_notnull", "repo_id is non-null and non-empty")
    for _, row in df.iterrows():
        v = row.get("repo_id", "")
        if pd.isna(v) or str(v).strip() == "":
            r.add(row.get("repo_name", "?"), f"repo_id is empty/null")

    # repo_id: UUID format
    r = col("uuid_format", "repo_id matches UUID format")
    for _, row in df.iterrows():
        v = str(row.get("repo_id", ""))
        if not _UUID_RE.match(v.strip()):
            r.add(row.get("repo_name", "?"), f"'{v}' is not a valid UUID")

    # repo_name: non-null, non-empty
    r = col("name_notnull", "repo_name is non-null and non-empty")
    for _, row in df.iterrows():
        v = row.get("repo_name", "")
        if pd.isna(v) or str(v).strip() == "":
            r.add(str(row.get("repo_id", "?")), "repo_name is empty/null")

    # num_repos >= 1
    r = col("num_repos_ge1", "num_repos >= 1")
    mask = pd.to_numeric(df["num_repos"], errors="coerce").fillna(0) < 1
    for name in df.loc[mask, "repo_name"]:
        r.add(name, f"num_repos={df.loc[df['repo_name']==name, 'num_repos'].values[0]}")

    # Integer non-negative columns
    for col_name, label in [
        ("raw_loc", "F"), ("logical_loc", "G"), ("autogen_loc", "H"),
        ("source_files", "K"), ("commit_count", "N"),
        ("contributors_count", "O"), ("total_pr_count", "P"), ("reviewed_pr_count", "Q"),
    ]:
        r = col(f"{col_name}_ge0", f"{col_name} ({label}) >= 0")
        numeric = pd.to_numeric(df[col_name], errors="coerce")
        bad = df[numeric.isna() | (numeric < 0)]
        for name in bad["repo_name"]:
            val = df.loc[df["repo_name"] == name, col_name].values[0]
            r.add(name, f"{col_name}={val}")

    # dependency_dir_loc (AE) — optional column, present only when dep dirs are committed to git
    if "dependency_dir_loc" in df.columns:
        r = col("dependency_dir_loc_ge0", "dependency_dir_loc (AE) >= 0")
        numeric = pd.to_numeric(df["dependency_dir_loc"], errors="coerce")
        bad = df[numeric.isna() | (numeric < 0)]
        for name in bad["repo_name"]:
            val = df.loc[df["repo_name"] == name, "dependency_dir_loc"].values[0]
            r.add(name, f"dependency_dir_loc={val}")

    # Float [0,1] columns
    for col_name, label in [
        ("duplication_ratio", "I"), ("fork_pct", "J"),
        ("docstring_ratio", "X"),
    ]:
        r = col(f"{col_name}_range", f"{col_name} ({label}) in [0.0, 1.0]")
        numeric = pd.to_numeric(df[col_name], errors="coerce")
        bad = df[numeric.isna() | (numeric < 0) | (numeric > 1)]
        for name in bad["repo_name"]:
            val = df.loc[df["repo_name"] == name, col_name].values[0]
            r.add(name, f"{col_name}={val}")

    # avg_func_length >= 0
    r = col("avg_func_length_ge0", "avg_func_length (AA) >= 0")
    numeric = pd.to_numeric(df["avg_func_length"], errors="coerce")
    bad = df[numeric.isna() | (numeric < 0)]
    for name in bad["repo_name"]:
        r.add(name, f"avg_func_length={df.loc[df['repo_name']==name, 'avg_func_length'].values[0]}")

    # Categorical columns
    for col_name, allowed, label in [
        ("ci_checks", _CI_CHECKS_VALUES, "R"),
        ("containerized", _CONTAINERIZED_VALUES, "V"),
        ("deployment_infra", _DEPLOYMENT_VALUES, "S"),
        ("monitoring", _MONITORING_VALUES, "T"),
        ("test_suite", _TEST_SUITE_VALUES, "U"),
        ("holdout", _HOLDOUT_VALUES, "W"),
        ("readme_quality", _README_VALUES, "Y"),
        ("issue_tracker", _ISSUE_TRACKER_VALUES, "Z"),
    ]:
        r = col(f"{col_name}_values", f"{col_name} ({label}) is one of {sorted(allowed)}")
        bad = df[~df[col_name].isin(allowed)]
        for name in bad["repo_name"]:
            val = df.loc[df["repo_name"] == name, col_name].values[0]
            r.add(name, f"{col_name}='{val}'")

    # lang_distribution: valid JSON
    r = col("lang_dist_json", "lang_distribution (M) is valid JSON")
    for _, row in df.iterrows():
        if _parse_lang_dist(row["lang_distribution"]) is None:
            r.add(row["repo_name"], f"invalid JSON: {str(row['lang_distribution'])[:60]}")

    # lang_distribution: values sum to ~1.0
    r = col("lang_dist_sum", "lang_distribution values sum to ~1.0 (±0.05)")
    for _, row in df.iterrows():
        dist = _parse_lang_dist(row["lang_distribution"])
        if dist is None or not dist:
            continue
        total = sum(dist.values())
        if abs(total - 1.0) > 0.05:
            r.add(row["repo_name"], f"sum={total:.4f}, dist={str(dist)[:80]}")

    # lang_distribution: each value in [0,1]
    r = col("lang_dist_values_range", "lang_distribution values each in [0, 1]")
    for _, row in df.iterrows():
        dist = _parse_lang_dist(row["lang_distribution"])
        if not dist:
            continue
        for lang, pct in dist.items():
            if not isinstance(pct, (int, float)) or pct < 0 or pct > 1:
                r.add(row["repo_name"], f"{lang}={pct}")
                break

    return results


# ---------------------------------------------------------------------------
# Block 2 — Cross-column invariants (from spec)
# ---------------------------------------------------------------------------

def _check_invariants(df: pd.DataFrame) -> Tuple[List[CheckResult], List[CheckResult]]:
    errors: List[CheckResult] = []
    warnings: List[CheckResult] = []

    def err(check_id: str, repo_description: str) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error=True)
        errors.append(r)
        return r

    def warn(check_id: str, repo_description: str) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error=False)
        warnings.append(r)
        return r

    # F >= G
    r = err("F_ge_G", "raw_loc (F) >= logical_loc (G)")
    bad = df[pd.to_numeric(df["raw_loc"], errors="coerce") < pd.to_numeric(df["logical_loc"], errors="coerce")]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"raw_loc={row['raw_loc']}, logical_loc={row['logical_loc']}")

    # G >= H
    r = err("G_ge_H", "logical_loc (G) >= autogen_loc (H)")
    bad = df[pd.to_numeric(df["logical_loc"], errors="coerce") < pd.to_numeric(df["autogen_loc"], errors="coerce")]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"logical_loc={row['logical_loc']}, autogen_loc={row['autogen_loc']}")

    # Q <= P
    r = err("Q_le_P", "reviewed_pr_count (Q) <= total_pr_count (P)")
    bad = df[pd.to_numeric(df["reviewed_pr_count"], errors="coerce") > pd.to_numeric(df["total_pr_count"], errors="coerce")]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"reviewed_pr={row['reviewed_pr_count']}, total_pr={row['total_pr_count']}")

    # reviewed_pr > 0 but total_pr == 0 (logically impossible)
    r = err("reviewed_without_prs", "reviewed_pr_count > 0 requires total_pr_count > 0")
    bad = df[
        (pd.to_numeric(df["reviewed_pr_count"], errors="coerce") > 0) &
        (pd.to_numeric(df["total_pr_count"], errors="coerce") == 0)
    ]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"reviewed_pr={row['reviewed_pr_count']}, total_pr={row['total_pr_count']}")

    # AE <= F: dependency_dir_loc cannot exceed raw_loc
    if "dependency_dir_loc" in df.columns:
        r = err("dep_dir_le_raw", "dependency_dir_loc (AE) <= raw_loc (F)")
        raw = pd.to_numeric(df["raw_loc"], errors="coerce")
        dep = pd.to_numeric(df["dependency_dir_loc"], errors="coerce")
        bad = df[dep > raw]
        for _, row in bad.iterrows():
            r.add(row["repo_name"], f"dependency_dir_loc={row['dependency_dir_loc']}, raw_loc={row['raw_loc']}")

    # F == G warning (no blanks/comments — spec says "likely incorrect")
    r = warn("F_eq_G", "raw_loc == logical_loc with raw_loc > 0 (spec: 'likely incorrect')")
    raw = pd.to_numeric(df["raw_loc"], errors="coerce")
    logical = pd.to_numeric(df["logical_loc"], errors="coerce")
    bad = df[(raw == logical) & (raw > 0)]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"raw_loc=logical_loc={row['raw_loc']}")

    return errors, warnings


# ---------------------------------------------------------------------------
# Block 3 — Business logic
# ---------------------------------------------------------------------------

def _check_business_logic(df: pd.DataFrame) -> Tuple[List[CheckResult], List[CheckResult]]:
    errors: List[CheckResult] = []
    warnings: List[CheckResult] = []

    def err(check_id: str, repo_description: str) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error=True)
        errors.append(r)
        return r

    def warn(check_id: str, repo_description: str) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error=False)
        warnings.append(r)
        return r

    # repo_id uniqueness
    r = err("id_unique", "repo_id is unique across all rows")
    dupes = df[df["repo_id"].duplicated(keep=False)]
    for _, row in dupes.iterrows():
        r.add(row["repo_name"], f"repo_id={row['repo_id']} is duplicated")

    # repo_name uniqueness
    r = err("name_unique", "repo_name is unique across all rows")
    dupes = df[df["repo_name"].duplicated(keep=False)]
    for _, row in dupes.iterrows():
        r.add(row["repo_name"], "repo_name appears more than once")

    # primary_language in lang_distribution
    r = warn("primary_in_dist", "primary_language (L) is present in lang_distribution (M)")
    for _, row in df.iterrows():
        lang = str(row.get("primary_language", "")).strip()
        if not lang:
            continue
        files = pd.to_numeric(row.get("source_files", 0), errors="coerce")
        if files == 0:
            continue
        dist = _parse_lang_dist(row["lang_distribution"])
        if dist is None or not dist:
            continue
        if lang not in dist:
            r.add(row["repo_name"], f"primary='{lang}' not in distribution keys: {list(dist.keys())[:5]}")

    # Full CI-CD / Enterprise without ci_checks=Yes
    r = warn("cicd_needs_ci", "deployment_infra Full CI-CD/Enterprise implies ci_checks=Yes")
    bad = df[
        df["deployment_infra"].isin({"Full CI-CD", "Enterprise"}) &
        (df["ci_checks"] != "Yes")
    ]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"deployment={row['deployment_infra']}, ci_checks={row['ci_checks']}")

    # source_files > 0 but logical_loc == 0
    r = warn("files_but_no_code", "if source_files > 0 then logical_loc > 0")
    bad = df[
        (pd.to_numeric(df["source_files"], errors="coerce") > 0) &
        (pd.to_numeric(df["logical_loc"], errors="coerce") == 0)
    ]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"source_files={row['source_files']}, logical_loc=0")

    # logical_loc == 0 but avg_func_length > 0
    r = warn("no_code_but_functions", "if logical_loc == 0 then avg_func_length == 0")
    bad = df[
        (pd.to_numeric(df["logical_loc"], errors="coerce") == 0) &
        (pd.to_numeric(df["avg_func_length"], errors="coerce") > 0)
    ]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"logical_loc=0, avg_func_length={row['avg_func_length']}")

    return errors, warnings


# ---------------------------------------------------------------------------
# Block 4 — Suspicious values (warnings only)
# ---------------------------------------------------------------------------

def _check_suspicious_values(df: pd.DataFrame) -> List[CheckResult]:
    warnings: List[CheckResult] = []

    def warn(check_id: str, repo_description: str) -> CheckResult:
        r = CheckResult(check_id, repo_description, is_error=False)
        warnings.append(r)
        return r

    # SVG / JSON / Plain Text as primary language
    r = warn("non_code_primary", f"primary_language is a non-code type (SVG, JSON, Plain Text, etc.)")
    bad = df[df["primary_language"].isin(_NON_CODE_PRIMARY_LANGS)]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"primary_language='{row['primary_language']}'")

    # All duplication = 0.0 (> 90% of rows)
    total = len(df)
    zero_dup = (pd.to_numeric(df["duplication_ratio"], errors="coerce") == 0.0).sum()
    if total > 0 and zero_dup / total > 0.90:
        r = warn("all_dup_zero", f">{zero_dup/total*100:.0f}% of rows have duplication_ratio=0.0 (jscpd may not have run)")
        r.add("DATASET-WIDE", f"{zero_dup}/{total} rows have duplication_ratio=0.0")

    # Very high avg_func_length
    r = warn("extreme_func_length", "avg_func_length > 100 (possible tree-sitter parsing error)")
    bad = df[pd.to_numeric(df["avg_func_length"], errors="coerce") > 100]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"avg_func_length={row['avg_func_length']}")

    # Zero commits
    r = warn("zero_commits", "commit_count == 0 (empty or malformed repo)")
    bad = df[pd.to_numeric(df["commit_count"], errors="coerce") == 0]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], "commit_count=0")

    # fork_pct = 1.0 for majority of repos
    fork_one = (pd.to_numeric(df["fork_pct"], errors="coerce") == 1.0).sum()
    if total > 0 and fork_one / total > 0.50:
        r = warn("fork_majority", f">{fork_one/total*100:.0f}% of rows have fork_pct=1.0 (fork detector may be too aggressive)")
        r.add("DATASET-WIDE", f"{fork_one}/{total} repos flagged as forks")

    # raw_loc == 0 but source_files > 0
    r = warn("zero_raw_loc", "raw_loc == 0 with source_files > 0 (scc may have failed)")
    bad = df[
        (pd.to_numeric(df["raw_loc"], errors="coerce") == 0) &
        (pd.to_numeric(df["source_files"], errors="coerce") > 0)
    ]
    for _, row in bad.iterrows():
        r.add(row["repo_name"], f"raw_loc=0, source_files={row['source_files']}")

    return warnings


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(
    format_errors: List[CheckResult],
    invariant_errors: List[CheckResult],
    invariant_warnings: List[CheckResult],
    biz_errors: List[CheckResult],
    biz_warnings: List[CheckResult],
    suspicious_warnings: List[CheckResult],
    total_rows: int,
) -> int:
    all_errors = format_errors + invariant_errors + biz_errors
    all_warnings = invariant_warnings + biz_warnings + suspicious_warnings

    failed_errors = [r for r in all_errors if not r.passed]
    failed_warnings = [r for r in all_warnings if not r.passed]
    passed = [r for r in all_errors + all_warnings if r.passed]

    print(f"\n{'='*60}")
    print(f"  repo_metadata.csv Validation Report")
    print(f"  Rows: {total_rows}")
    print(f"{'='*60}\n")

    if failed_errors:
        print("ERRORS (must fix):")
        for r in failed_errors:
            print(f"  ❌ [{r.check_id}] {r.repo_description}: {len(r.violations)} violation(s)")
            for _, name, detail in r.violations[:10]:
                print(f"       - {name}: {detail}")
            if len(r.violations) > 10:
                print(f"       ... and {len(r.violations) - 10} more")
        print()
    else:
        print("ERRORS: none\n")

    if failed_warnings:
        print("WARNINGS (should review):")
        for r in failed_warnings:
            print(f"  ⚠️  [{r.check_id}] {r.repo_description}: {len(r.violations)} row(s)")
            for _, name, detail in r.violations[:5]:
                print(f"       - {name}: {detail}")
            if len(r.violations) > 5:
                print(f"       ... and {len(r.violations) - 5} more")
        print()
    else:
        print("WARNINGS: none\n")

    if passed:
        print("PASSED:")
        for r in passed:
            total_checked = total_rows
            print(f"  ✅ [{r.check_id}] {r.repo_description} ({total_checked}/{total_checked})")
        print()

    total_error_count = sum(len(r.violations) for r in failed_errors)
    total_warning_count = sum(len(r.violations) for r in failed_warnings)

    print(f"{'='*60}")
    print(f"  Total ERRORS:   {total_error_count}")
    print(f"  Total WARNINGS: {total_warning_count}")
    print(f"  Exit code: {'1 (errors found)' if total_error_count else '0 (clean)'}")
    print(f"{'='*60}\n")

    return 1 if total_error_count else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path/to/repo_metadata.csv>", file=sys.stderr)
        sys.exit(2)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(2)

    df = pd.read_csv(csv_path)
    # Legacy CSVs used the short column name; validate them under the current one.
    df = df.rename(columns={"dep_dir_loc": "dependency_dir_loc"})
    total_rows = len(df)
    print(f"Loaded {total_rows} rows from {csv_path}")

    format_errors = _check_format(df)
    inv_errors, inv_warnings = _check_invariants(df)
    biz_errors, biz_warnings = _check_business_logic(df)
    sus_warnings = _check_suspicious_values(df)

    exit_code = _print_report(
        format_errors, inv_errors, inv_warnings,
        biz_errors, biz_warnings, sus_warnings,
        total_rows,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
