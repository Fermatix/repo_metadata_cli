"""Columns AI, AJ, AK — repository disk-size metrics (ported from v1)."""

from __future__ import annotations

import logging
from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import _parse_du_kb, get_dir_size_mb
from ..utils import run_cmd

logger = logging.getLogger(__name__)


class RepoBundleMbMetric(BaseMetric):
    """AI: Size of the source *.bundle file in megabytes (0.0 in local no-VCS mode)."""

    column = "AI"
    field_name = "repo_bundle_mb"

    def compute(self, ctx: RepoContext) -> Any:
        if ctx.bundle_path is None:
            return 0.0
        try:
            return round(ctx.bundle_path.stat().st_size / (1024 * 1024), 3)
        except OSError:
            logger.debug("Unable to stat bundle %s", ctx.bundle_path)
            return 0.0


class GitHistoryMbMetric(BaseMetric):
    """AJ: Size of the VCS history directory (.git or .hg) in megabytes."""

    column = "AJ"
    field_name = "repo_git_history_mb"

    def compute(self, ctx: RepoContext) -> Any:
        hist_dir = ctx.repo_path / ctx.vcs.history_dirname
        if not hist_dir.exists():
            return 0.0
        return get_dir_size_mb(hist_dir)


class WorktreeMbMetric(BaseMetric):
    """AK: Size of the worktree (total minus the VCS history dir) in megabytes."""

    column = "AK"
    field_name = "repo_worktree_mb"

    def compute(self, ctx: RepoContext) -> Any:
        total_kb = _parse_du_kb(run_cmd(["du", "-sk", str(ctx.repo_path)]))
        hist_dir = ctx.repo_path / ctx.vcs.history_dirname
        hist_kb = _parse_du_kb(run_cmd(["du", "-sk", str(hist_dir)])) if hist_dir.exists() else 0
        worktree_kb = max(total_kb - hist_kb, 0)
        return round(worktree_kb / 1024, 3)
