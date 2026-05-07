"""Metadata pipeline: clone → build context → run all metrics → write CSV."""

from __future__ import annotations

import logging
import os
import subprocess
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
    CIChecksMetric,
    CommitCountMetric,
    ContainerizedMetric,
    ContributorsMetric,
    DatasetIdMetric,
    DatasetNameMetric,
    DeploymentMetric,
    DescriptionMetric,
    DocstringRatioMetric,
    DuplicationMetric,
    ForkPctMetric,
    HoldoutMetric,
    IssueTrackerMetric,
    LangDistributionMetric,
    LogicalLocMetric,
    MonitoringMetric,
    NumReposMetric,
    PrimaryLanguageMetric,
    RawLocMetric,
    ReadmeQualityMetric,
    ReviewedPRMetric,
    SourceFilesMetric,
    TestSuiteMetric,
    TotalPRMetric,
    VendorNameMetric,
)
from .settings import AppSettings
from .tree_sitter_support import TreeSitterManager
from .utils import run_cmd

logger = logging.getLogger(__name__)

# Pipeline order matches Quote Form columns A → AA, then empty AB/AC/AD.
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
# Git helpers
# ---------------------------------------------------------------------------

def clone_bundle(bundle_path: Path, dest_dir: Path) -> Optional[Path]:
    repo_dir = dest_dir / bundle_path.stem
    env = os.environ.copy()
    env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    result = subprocess.run(
        ["git", "clone", str(bundle_path), str(repo_dir)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0 or not repo_dir.exists():
        logger.warning("Failed to clone %s", bundle_path)
        return None

    # Ensure ALL remote branches are present as refs/remotes/origin/*.
    # git clone only maps refs/heads/* by default; bundles created with --all
    # may contain additional refs. A second fetch with an explicit refspec
    # guarantees nothing is missed.
    subprocess.run(
        [
            "git", "-C", str(repo_dir), "fetch", "--quiet", "--force",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    logger.debug("Cloned %s into %s", bundle_path.name, repo_dir)
    return repo_dir


def latest_branch_by_commit(repo_dir: Path) -> Optional[str]:
    refs_raw = run_cmd(
        [
            "git", "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)|%(committerdate:iso8601)",
            "refs/heads", "refs/remotes",
        ],
        cwd=repo_dir,
    )
    for line in refs_raw.splitlines():
        if "|" not in line:
            continue
        name, _ = line.split("|", 1)
        name = name.strip()
        if not name or name.endswith("/HEAD") or name == "HEAD":
            continue
        return name
    current = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    return current or None


def checkout_ref(repo_dir: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "--force", "--quiet", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def build_repo_context(
    bundle_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    tmpdir: Path,
) -> Optional[RepoContext]:
    repo_dir = clone_bundle(bundle_path, tmpdir)
    if repo_dir is None:
        return None
    branch_ref = latest_branch_by_commit(repo_dir) or "HEAD"
    if not checkout_ref(repo_dir, branch_ref):
        logger.debug("Failed to checkout %s; staying on HEAD", branch_ref)
    return RepoContext(
        repo_path=repo_dir,
        bundle_path=bundle_path,
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
    if df.empty or "dataset_name" not in df.columns:
        return set()
    processed = set(df["dataset_name"].astype(str))
    logger.info("%s already contains %d repositories.", csv_path, len(processed))
    return processed


def run_metadata_pipeline(
    dataset_dir: Path,
    csv_path: Path,
    settings: AppSettings,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> None:
    bundle_files = sorted(dataset_dir.rglob("*.bundle"))
    logger.info("Found %d bundle files under %s", len(bundle_files), dataset_dir)
    if not bundle_files:
        logger.warning("No *.bundle files found under %s; nothing to process.", dataset_dir)
        return

    processed = _processed_repos(csv_path)

    for bundle_path in tqdm(bundle_files, desc="Metadata"):
        repo_name = bundle_path.stem
        if repo_name in processed:
            logger.debug("Skipping %s (already processed)", repo_name)
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = build_repo_context(
                bundle_path, settings, allowed_files, ts_manager, Path(tmpdir)
            )
            if ctx is None:
                logger.error("Skipping %s: failed to materialize repository", bundle_path.name)
                continue
            row = run_pipeline(ctx)

        pd.DataFrame([row]).to_csv(
            csv_path,
            mode="a" if csv_path.exists() else "w",
            header=not csv_path.exists(),
            index=False,
        )
        processed.add(repo_name)

    logger.info("Metadata pipeline finished; %d repositories processed.", len(processed))
