"""Columns N, O, P, Q — commit count, contributors, PR counts."""

from __future__ import annotations

import logging
from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import count_pull_requests
from ..utils import run_cmd

logger = logging.getLogger(__name__)

_BOT_NAMES = frozenset({
    "dependabot", "renovate", "renovate-bot", "github-actions",
    "github-actions[bot]", "dependabot[bot]", "snyk-bot",
    "greenkeeper", "semantic-release-bot",
})


class CommitCountMetric(BaseMetric):
    """N: Total commit count across all branches (including merges)."""

    column = "N"
    field_name = "commit_count"

    def compute(self, ctx: RepoContext) -> Any:
        return len(ctx.git_log_no_merges)


class ContributorsMetric(BaseMetric):
    """O: Unique human authors (bots excluded), deduplicated by git shortlog."""

    column = "O"
    field_name = "contributors"

    def compute(self, ctx: RepoContext) -> Any:
        out = run_cmd(["git", "shortlog", "-sn", "--no-merges", "--all"], cwd=ctx.repo_path)
        if not out:
            return 0
        count = 0
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            name = parts[1].lower() if len(parts) == 2 else ""
            if not any(bot in name for bot in _BOT_NAMES):
                count += 1
        return count


class TotalPRMetric(BaseMetric):
    """P: Total merged PRs/MRs.

    Uses pr_cache (from enrich-prs command) when available — more accurate than
    git log pattern matching.  Falls back to git log detection otherwise.
    """

    column = "P"
    field_name = "total_pr_count"

    def compute(self, ctx: RepoContext) -> Any:
        entry = ctx.settings.pr_cache.get(ctx.bundle_name)
        # Only trust the cache when it returned a non-zero count.  A zero means
        # either the API was queried against a mirror URL (no MRs on the mirror)
        # or the project was genuinely empty — fall back to git log detection so
        # we don't silently report 0 for active repos.
        if entry is not None and entry.get("total_pr", 0) > 0:
            return entry["total_pr"]
        total, _ = ctx._cached("pr_counts", lambda: count_pull_requests(ctx.repo_path))
        return total


class ReviewedPRMetric(BaseMetric):
    """Q: Merged PRs with at least one review.

    Populated from pr_cache (enrich-prs command).  Returns 0 when cache is absent
    because git history alone cannot determine review status.
    """

    column = "Q"
    field_name = "reviewed_pr_count"

    def compute(self, ctx: RepoContext) -> Any:
        entry = ctx.settings.pr_cache.get(ctx.bundle_name)
        if entry is not None:
            return entry.get("reviewed_pr", 0)
        return 0
