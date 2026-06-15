"""Columns N, O, P, Q, AF, AH — commit/contributor/PR/branch/created-at metrics.

These are VCS-agnostic *column* definitions: each delegates the actual history
read to ``ctx.vcs`` (git or mercurial).  The module name is kept as ``git`` for
import stability (tests and metrics/__init__.py reference it).
"""

from __future__ import annotations

import logging
from typing import Any

from ..base_metric import BaseMetric, RepoContext

logger = logging.getLogger(__name__)

_BOT_NAMES = frozenset({
    "dependabot", "renovate", "renovate-bot", "github-actions",
    "github-actions[bot]", "dependabot[bot]", "snyk-bot",
    "greenkeeper", "semantic-release-bot",
})


class CommitCountMetric(BaseMetric):
    """N: Total commit count across all branches, including merge commits."""

    column = "N"
    field_name = "commit_count"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached("commit_count", lambda: ctx.vcs.commit_count(ctx.repo_path))


class ContributorsMetric(BaseMetric):
    """O: Unique human authors (bots excluded), deduplicated by git shortlog."""

    column = "O"
    field_name = "contributors_count"

    def compute(self, ctx: RepoContext) -> Any:
        names = ctx.vcs.author_names(ctx.repo_path)
        return sum(
            1 for name in names
            if not any(bot in name.lower() for bot in _BOT_NAMES)
        )


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
        total, _ = ctx._cached("pr_counts", lambda: ctx.vcs.count_pull_requests(ctx.repo_path))
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


class BranchCountMetric(BaseMetric):
    """AH: Number of distinct branches in the repository.

    The VCS backend owns the counting: git deduplicates local heads and
    ``origin`` remote branches (avoiding the detached-HEAD / clone-artifact
    inflation of ``git branch -a``); mercurial counts named branches.
    """

    column = "AH"
    field_name = "branch_count"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.vcs.branch_count(ctx.repo_path)


class CreatedAtMetric(BaseMetric):
    """AF: Repository creation timestamp — date of the first commit (ported from v1)."""

    column = "AF"
    field_name = "created_at"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.vcs.created_at(ctx.repo_path)
