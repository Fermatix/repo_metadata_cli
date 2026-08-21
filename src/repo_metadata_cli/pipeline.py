"""Metadata pipeline: clone → build context → run all metrics → write CSV."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Set, Type

import pandas as pd
from tqdm import tqdm

from .allowed_files import AllowedFiles
from .base_metric import BaseMetric, RepoContext
from .csv_migration import (
    migrate_csv_schema,
    rows_needing_backfill,
    update_row_fields,
    warn_unfilled_rows,
)
from .metrics import (  # metrics/ package — all metric classes via __init__.py
    AutoGenLocMetric,
    AutogenInCleanLocMetric,
    AvgFuncLengthMetric,
    AvgLocPerPRMetric,
    BranchCountMetric,
    ClassesCountMetric,
    CleanHandwrittenLocMetric,
    CleanLogicalLocMetric,
    CommentRatioMetric,
    DepDirLocMetric,
    CIChecksMetric,
    CommitCountMetric,
    CommitMinhashMetric,
    ContainerizedMetric,
    ContributorsMetric,
    CreatedAtMetric,
    DatasetIdMetric,
    EarlyCommitHashesMetric,
    DatasetNameMetric,
    DeploymentMetric,
    DocstringRatioMetric,
    DocumentationCountMetric,
    DuplicationMetric,
    ExtensionsMetric,
    FirstCommitHashMetric,
    ForkPctMetric,
    FullLangDistributionMetric,
    FunctionsCountMetric,
    GitHistoryMbMetric,
    HoldoutMetric,
    IssueTrackerMetric,
    LangDistributionMetric,
    LicenseTypeMetric,
    LogicalLocMetric,
    MetaDuplicationRatioMetric,
    MetaLocWithGeneratedMetric,
    MetaLogicalLocMetric,
    MetaNonAuthoredLocMetric,
    MetaNonMergeCommitCountMetric,
    MergedPRMetric,
    MetadataBranchNameMetric,
    MetadataCommitHashMetric,
    MonitoringMetric,
    NumReposMetric,
    PrimaryLanguageMetric,
    PRRichPctMetric,
    PRSimplePctMetric,
    PRStandardPctMetric,
    RawLocMetric,
    ReadmeQualityMetric,
    RepoBundleMbMetric,
    RepoOrgMetric,
    RepoUrlMetric,
    ReviewedPRMetric,
    SourceFilesMetric,
    StackMetric,
    SymbolsCountMetric,
    TestCoveragePctMetric,
    TestSuiteMetric,
    TotalPRMetric,
    UntestedFilesPctMetric,
    VendorNameMetric,
    WorktreeMbMetric,
)
from .settings import AppSettings
from .tree_sitter_support import TreeSitterManager
from .vcs import GitVCS, MercurialVCS
from .vcs.base import BaseVCS

logger = logging.getLogger(__name__)

# Pipeline order: Quote Form columns A → AA, AE, then v1-ported AF → AO,
# and finally the empty pricing placeholders AB/AC/AD (appended last in run_pipeline).
METRICS: list[Type[BaseMetric]] = [
    DatasetIdMetric,       # A
    VendorNameMetric,      # B
    RepoOrgMetric,         # B2 — full namespace path from source URL
    DatasetNameMetric,     # C
    NumReposMetric,        # E
    RawLocMetric,          # F
    LogicalLocMetric,      # G
    AutoGenLocMetric,      # H
    DuplicationMetric,     # I
    ForkPctMetric,         # J
    SourceFilesMetric,     # K
    PrimaryLanguageMetric, # L
    LangDistributionMetric,# M
    CommitCountMetric,     # N
    ContributorsMetric,    # O
    TotalPRMetric,         # P
    ReviewedPRMetric,      # Q
    CIChecksMetric,        # R
    DeploymentMetric,      # S
    MonitoringMetric,      # T
    TestSuiteMetric,       # U
    ContainerizedMetric,   # V
    HoldoutMetric,         # W
    DocstringRatioMetric,  # X
    ReadmeQualityMetric,   # Y
    IssueTrackerMetric,    # Z
    AvgFuncLengthMetric,   # AA
    DepDirLocMetric,       # AE (optional)
    # v1-ported metrics (AF → AO), kept before the pricing placeholders.
    CreatedAtMetric,         # AF
    LicenseTypeMetric,       # AG
    BranchCountMetric,       # AH
    RepoBundleMbMetric,      # AI
    GitHistoryMbMetric,      # AJ
    WorktreeMbMetric,        # AK
    ExtensionsMetric,        # AL
    StackMetric,             # AM
    DocumentationCountMetric,# AN
    CommentRatioMetric,      # AO
    SymbolsCountMetric,      # AP
    # git identity / provenance fingerprint (cross-recollection matching)
    FirstCommitHashMetric,     # AQ
    MetadataCommitHashMetric,  # AR
    MetadataBranchNameMetric,  # AS
    EarlyCommitHashesMetric,   # AT
    CommitMinhashMetric,       # AU
    # Unfiltered language distribution — diagnostic counterpart of M, which
    # keeps real programming languages only. Appended last so the spec letters
    # A → AU above stay stable.
    FullLangDistributionMetric,  # AV
    # Source URL/path exactly as written in repos.txt (origin remote in
    # directory mode). Appended last so the spec letters above stay stable.
    RepoUrlMetric,               # AW
]

# Empty placeholder columns per spec.
_EMPTY_COLUMNS: Dict[str, str] = {
    "quoted_price": "",   # AB
    "pricing_unit": "",   # AC
    "unit_rate": "",      # AD
}

# Trailing metrics — the stable schema TAIL.  Appended after the pricing
# placeholders so a freshly written CSV and a migrated legacy CSV (which gets
# these columns appended after its last column) end with the same tail columns.
# Field names must stay in sync with csv_migration.NEW_COLUMNS.
TRAILING_METRICS: list[Type[BaseMetric]] = [
    PRSimplePctMetric,       # AX
    PRStandardPctMetric,     # AY
    PRRichPctMetric,         # AZ
    AvgLocPerPRMetric,       # BA
    TestCoveragePctMetric,   # BB
    FunctionsCountMetric,    # BC
    ClassesCountMetric,      # BD
    UntestedFilesPctMetric,  # BE
    MergedPRMetric,          # BF
    CleanLogicalLocMetric,   # BG
    CleanHandwrittenLocMetric,  # BH
    AutogenInCleanLocMetric,    # BI
    MetaLogicalLocMetric,       # BJ
    MetaNonAuthoredLocMetric,   # BK
    MetaDuplicationRatioMetric, # BL
    MetaNonMergeCommitCountMetric,  # BM
    MetaLocWithGeneratedMetric,  # BN
]


def _compute_metric_values(
    ctx: RepoContext,
    metric_classes: list[Type[BaseMetric]],
    result: Dict[str, Any],
) -> None:
    """Compute the given metrics into ``result``; a failure warns (with the
    repository and metric name) and yields None for that value only."""
    for MetricClass in metric_classes:
        metric = MetricClass()
        try:
            result[MetricClass.field_name] = metric.compute(ctx)
        except Exception as exc:
            logger.warning(
                "Metric %s failed for %s: %s",
                MetricClass.field_name, ctx.bundle_name, exc,
            )
            result[MetricClass.field_name] = None


def run_pipeline(ctx: RepoContext) -> Dict[str, Any]:
    """Compute all metrics for a single repository context."""
    result: Dict[str, Any] = {}
    _compute_metric_values(ctx, METRICS, result)
    result.update(_EMPTY_COLUMNS)
    _compute_metric_values(ctx, TRAILING_METRICS, result)
    return result


# ---------------------------------------------------------------------------
# VCS materialization
# ---------------------------------------------------------------------------

# Bundle extension → VCS backend.  Git keeps the plain ``*.bundle`` it always
# used; Mercurial bundles use a distinct ``*.hgbundle`` suffix so a bundle's VCS
# can be determined unambiguously at materialization time.
_BUNDLE_GLOBS: tuple[str, ...] = ("*.bundle", "*.hgbundle")
_BUNDLE_VCS = {".hgbundle": MercurialVCS, ".bundle": GitVCS}


def vcs_for_bundle(bundle_path: Path) -> BaseVCS:
    """Return the VCS backend for a bundle, keyed by file extension (git default)."""
    return _BUNDLE_VCS.get(bundle_path.suffix, GitVCS)()


# Thin backward-compatible wrappers (the git implementations now live in GitVCS).
def clone_bundle(bundle_path: Path, dest_dir: Path) -> Optional[Path]:
    return GitVCS().clone(bundle_path, dest_dir)


def latest_branch_by_commit(repo_dir: Path) -> Optional[str]:
    return GitVCS().latest_branch(repo_dir)


def _short_branch(ref: Optional[str]) -> str:
    """Normalize a full ref (refs/heads/x, refs/remotes/origin/x) to a branch name.

    Returns "HEAD" for a bare commit-hash fallback (repo with no branch refs).
    """
    if not ref:
        return ""
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    if ref.startswith("refs/remotes/"):
        rest = ref[len("refs/remotes/"):]
        parts = rest.split("/", 1)  # drop the remote name (origin/…)
        return parts[1] if len(parts) == 2 else parts[0]
    return "HEAD"


def checkout_ref(repo_dir: Path, ref: str) -> bool:
    return GitVCS().checkout(repo_dir, ref)


def build_repo_context(
    bundle_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    tmpdir: Path,
) -> Optional[RepoContext]:
    vcs = vcs_for_bundle(bundle_path)
    repo_dir = vcs.clone(bundle_path, tmpdir)
    if repo_dir is None:
        return None
    branch_ref = vcs.latest_branch(repo_dir) or vcs.default_ref
    if not vcs.checkout(repo_dir, branch_ref):
        logger.debug("Failed to checkout %s; staying on default ref", branch_ref)
    return RepoContext(
        repo_path=repo_dir,
        settings=settings,
        tree_sitter=ts_manager,
        allowed_files=allowed_files,
        bundle_path=bundle_path,
        metadata_branch=_short_branch(branch_ref),
        vcs=vcs,
    )


def build_local_repo_context(
    local_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> RepoContext:
    # LOCAL PATCH: when the directory is a real git repo, select the branch with
    # the most recent commit (same rule as bundle mode) and check it out in
    # place, so LOC/git metrics are computed on the latest-commit branch rather
    # than whatever HEAD happens to be checked out. No-op for non-git folders.
    branch_ref: Optional[str] = None
    if (local_path / ".git").exists():
        branch_ref = latest_branch_by_commit(local_path)
        if branch_ref and not checkout_ref(local_path, branch_ref):
            logger.debug(
                "Failed to checkout %s in %s; staying on HEAD", branch_ref, local_path
            )
    return RepoContext(
        repo_path=local_path,
        settings=settings,
        tree_sitter=ts_manager,
        allowed_files=allowed_files,
        metadata_branch=_short_branch(branch_ref),
    )


# ---------------------------------------------------------------------------
# Incremental CSV pipeline
# ---------------------------------------------------------------------------

def _processed_repos(csv_path: Path) -> Set[str]:
    """Return the set of already-processed bundle stems.

    repo_name is the leaf (not unique across namespaces), so dedup keys on
    (repo_org, repo_name) — unique per repo — joined as "org\trepo". Falls back
    to repo_name alone for legacy CSVs without a repo_org column.
    """
    if not csv_path.exists():
        return set()
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        logger.warning("Could not read %s (%s); recomputing all.", csv_path, exc)
        return set()
    if df.empty or "repo_name" not in df.columns:
        return set()
    if "repo_org" in df.columns:
        processed = {
            f"{org}\t{name}"
            for org, name in zip(df["repo_org"].astype(str), df["repo_name"].astype(str))
        }
        # LOCAL PATCH: also index by bare repo_name so directory/local-mode runs
        # (where the dedup key's org segment is empty but the CSV's repo_org is
        # populated from the git remote) still resume via the `stem in processed`
        # fallback instead of re-appending every already-processed repo.
        processed |= set(df["repo_name"].astype(str))
    else:
        processed = set(df["repo_name"].astype(str))
    logger.info("%s already contains %d repositories.", csv_path, len(processed))
    return processed


def _backfill_repo_row(
    item: Path,
    local_mode: bool,
    csv_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    org: str,
    leaf: str,
    stem: str,
) -> None:
    """Recompute the trailing metrics for an already-processed repo and update
    its existing CSV row in place (no new row is appended).

    A metric that raises leaves its cell empty (retried next run); other
    metrics and repositories continue unaffected.

    The PR columns P/Q are refreshed TOGETHER with merged_pr_count (BF): a
    legacy row's total_pr_count may predate the current pr_cache (or even the
    enricher itself), and writing a fresh merged count next to a stale total
    would make the row internally contradictory (merged > total, flagged by
    validate_csv).  All three come from the same cache-or-fingerprints source,
    so refreshing them as a unit keeps the invariant.
    """
    # Recomputed alongside TRAILING_METRICS so the PR triple stays consistent.
    _PR_CONSISTENCY_METRICS = [TotalPRMetric, ReviewedPRMetric]

    values: Dict[str, Any] = {}
    if local_mode:
        ctx = build_local_repo_context(item, settings, allowed_files, ts_manager)
        _compute_metric_values(ctx, TRAILING_METRICS + _PR_CONSISTENCY_METRICS, values)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = build_repo_context(item, settings, allowed_files, ts_manager, Path(tmpdir))
            if ctx is None:
                logger.warning(
                    "Backfill of %s skipped: failed to materialize repository", stem
                )
                return
            _compute_metric_values(ctx, TRAILING_METRICS + _PR_CONSISTENCY_METRICS, values)
    if not update_row_fields(csv_path, org, leaf, stem, values):
        logger.warning(
            "Backfill: no CSV row matched %s (org=%r, name=%r); nothing updated.",
            stem, org, leaf,
        )


def _log_run_summary(skipped: list[str], degraded: list[str]) -> None:
    """Report repositories that produced no row / an incomplete working tree.

    Without this a failed materialization was only a single line in the middle
    of the log and the repository silently missing from the CSV.
    """
    if skipped:
        logger.warning(
            "%d repository/repositories produced NO row (materialization failed): %s",
            len(skipped), ", ".join(skipped),
        )
    if degraded:
        logger.warning(
            "%d repository/repositories were measured from a repaired working tree "
            "(some paths the OS rejects were restored under sanitized names): %s",
            len(degraded), ", ".join(degraded),
        )


def run_metadata_pipeline(
    dataset_dir: Path,
    csv_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> Dict[str, list]:
    bundle_files = sorted(
        p for glob in _BUNDLE_GLOBS for p in dataset_dir.rglob(glob)
    )

    if bundle_files:
        logger.info("Found %d bundle files under %s", len(bundle_files), dataset_dir)
        items = bundle_files
        local_mode = False
    else:
        local_dirs = sorted(
            p for p in dataset_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
        if not local_dirs:
            logger.warning(
                "No *.bundle files and no subdirectories found under %s; nothing to process.",
                dataset_dir,
            )
            return {"skipped": [], "degraded": []}
        logger.info(
            "Found %d local repo directories under %s (no-VCS mode)",
            len(local_dirs), dataset_dir,
        )
        items = local_dirs
        local_mode = True

    # Upgrade a legacy CSV in place: append the late-added columns (atomic,
    # preserving all prior columns/values/rows), then plan the backfill of
    # rows whose new cells are empty (fresh migration or a prior failure).
    migrate_csv_schema(csv_path)
    backfill_keys = rows_needing_backfill(csv_path)

    processed = _processed_repos(csv_path)
    skipped: list[str] = []
    degraded: list[str] = []

    for item in tqdm(items, desc="Metadata"):
        stem = item.name if local_mode else item.stem
        # Dedup key mirrors _processed_repos: (repo_org, repo_name-leaf). The
        # stem is the unique full-path bundle name; org/leaf come from the maps.
        org = settings.org_map.get(stem, "")
        leaf = settings.name_map.get(stem, stem)
        key = f"{org}\t{leaf}"
        if key in processed or stem in processed:
            if key in backfill_keys or leaf in backfill_keys or stem in backfill_keys:
                logger.info("Backfilling new columns for %s", stem)
                try:
                    _backfill_repo_row(
                        item, local_mode, csv_path, settings, allowed_files,
                        ts_manager, org, leaf, stem,
                    )
                except Exception as exc:
                    # One broken repo must not block the rest of the run.
                    logger.warning("Backfill of %s failed: %s", stem, exc)
            else:
                logger.debug("Skipping %s (already processed)", stem)
            continue

        if local_mode:
            ctx = build_local_repo_context(item, settings, allowed_files, ts_manager)
            row = run_pipeline(ctx)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                ctx = build_repo_context(item, settings, allowed_files, ts_manager, Path(tmpdir))
                if ctx is None:
                    logger.error("Skipping %s: failed to materialize repository", item.name)
                    skipped.append(stem)
                    continue
                # A repaired working tree still yields a full row; record it so
                # the end-of-run summary can flag the repository as degraded.
                if getattr(ctx.vcs, "last_repair", None) is not None:
                    degraded.append(stem)
                row = run_pipeline(ctx)

        row_df = pd.DataFrame([row])
        if csv_path.exists():
            # Align to the existing header before appending: pandas appends in the
            # row's own column order and writes no header on append, so a column-set
            # mismatch would silently corrupt the file.  reindex keeps it valid.
            existing_cols = pd.read_csv(csv_path, nrows=0).columns.tolist()
            if existing_cols and set(existing_cols) != set(row_df.columns):
                logger.warning(
                    "CSV %s header differs from current metric schema; aligning row to existing columns.",
                    csv_path,
                )
            row_df = row_df.reindex(columns=existing_cols) if existing_cols else row_df
            row_df.to_csv(csv_path, mode="a", header=False, index=False)
        else:
            row_df.to_csv(csv_path, mode="w", header=True, index=False)
        # Mark done using the same identifiers the skip-check consults above, so a
        # duplicate item within this run is not reprocessed.
        processed.add(key)
        processed.add(stem)

    # Rows whose new columns are still empty (source repo absent from this
    # dataset, or computation failed) are kept as-is and reported; the empty
    # cells make them retryable on the next run.
    warn_unfilled_rows(csv_path)
    _log_run_summary(skipped, degraded)

    logger.info("Metadata pipeline finished; %d repositories processed.", len(processed))
    return {"skipped": skipped, "degraded": degraded}
