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
from .metrics import (  # metrics/ package — all metric classes via __init__.py
    AutoGenLocMetric,
    AvgFuncLengthMetric,
    BranchCountMetric,
    CommentRatioMetric,
    DepDirLocMetric,
    CIChecksMetric,
    CommitCountMetric,
    ContainerizedMetric,
    ContributorsMetric,
    CreatedAtMetric,
    DatasetIdMetric,
    DatasetNameMetric,
    DeploymentMetric,
    DescriptionMetric,
    DocstringRatioMetric,
    DocumentationCountMetric,
    DuplicationMetric,
    ExtensionsMetric,
    ForkPctMetric,
    GitHistoryMbMetric,
    HoldoutMetric,
    IssueTrackerMetric,
    LangDistributionMetric,
    LicenseTypeMetric,
    LogicalLocMetric,
    MonitoringMetric,
    NumReposMetric,
    PrimaryLanguageMetric,
    RawLocMetric,
    ReadmeQualityMetric,
    RepoBundleMbMetric,
    ReviewedPRMetric,
    SourceFilesMetric,
    StackMetric,
    SymbolsCountMetric,
    TestSuiteMetric,
    TotalPRMetric,
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
    DatasetNameMetric,     # C
    DescriptionMetric,     # D
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
]

# Empty placeholder columns per spec.
_EMPTY_COLUMNS: Dict[str, str] = {
    "quoted_price": "",   # AB
    "pricing_unit": "",   # AC
    "unit_rate": "",      # AD
}


def run_pipeline(ctx: RepoContext) -> Dict[str, Any]:
    """Compute all metrics for a single repository context."""
    result: Dict[str, Any] = {}
    for MetricClass in METRICS:
        metric = MetricClass()
        try:
            result[MetricClass.field_name] = metric.compute(ctx)
        except Exception as exc:
            logger.warning("Metric %s failed: %s", MetricClass.field_name, exc)
            result[MetricClass.field_name] = None
    result.update(_EMPTY_COLUMNS)
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
        vcs=vcs,
    )


def build_local_repo_context(
    local_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> RepoContext:
    return RepoContext(
        repo_path=local_path,
        settings=settings,
        tree_sitter=ts_manager,
        allowed_files=allowed_files,
    )


# ---------------------------------------------------------------------------
# Incremental CSV pipeline
# ---------------------------------------------------------------------------

def _processed_repos(csv_path: Path) -> Set[str]:
    if not csv_path.exists():
        return set()
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        logger.warning("Could not read %s (%s); recomputing all.", csv_path, exc)
        return set()
    if df.empty or "repo_name" not in df.columns:
        return set()
    processed = set(df["repo_name"].astype(str))
    logger.info("%s already contains %d repositories.", csv_path, len(processed))
    return processed


def run_metadata_pipeline(
    dataset_dir: Path,
    csv_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> None:
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
            return
        logger.info(
            "Found %d local repo directories under %s (no-VCS mode)",
            len(local_dirs), dataset_dir,
        )
        items = local_dirs
        local_mode = True

    processed = _processed_repos(csv_path)

    for item in tqdm(items, desc="Metadata"):
        repo_name = item.name if local_mode else item.stem
        if repo_name in processed:
            logger.debug("Skipping %s (already processed)", repo_name)
            continue

        if local_mode:
            ctx = build_local_repo_context(item, settings, allowed_files, ts_manager)
            row = run_pipeline(ctx)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                ctx = build_repo_context(item, settings, allowed_files, ts_manager, Path(tmpdir))
                if ctx is None:
                    logger.error("Skipping %s: failed to materialize repository", item.name)
                    continue
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
        processed.add(repo_name)

    logger.info("Metadata pipeline finished; %d repositories processed.", len(processed))
