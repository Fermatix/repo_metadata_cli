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


class FirstCommitHashMetric(BaseMetric):
    """AQ: Root commit hash(es) — the parentless commit(s) of the history.

    Stable across re-collections (the root SHA never changes unless history is
    rewritten), so it serves as a cross-run identity fingerprint for matching a
    re-collected repo to its previous metadata. Comma-joined when a repo has
    multiple root commits (merged unrelated histories); empty for an empty repo.
    """

    column = "AQ"
    field_name = "first_commit_hash"

    def compute(self, ctx: RepoContext) -> Any:
        return ",".join(ctx.vcs.root_commit_hashes(ctx.repo_path))


class MetadataCommitHashMetric(BaseMetric):
    """AR: The commit HEAD pointed at when this metadata was collected."""

    column = "AR"
    field_name = "metadata_commit_hash"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.vcs.head_commit_hash(ctx.repo_path)


class MetadataBranchNameMetric(BaseMetric):
    """AS: The branch this metadata was collected from (latest-commit branch).

    HEAD is checked out detached at that branch's tip, so the name is captured by
    the pipeline at checkout time and stashed on the context.
    """

    column = "AS"
    field_name = "metadata_branch_name"

    def compute(self, ctx: RepoContext) -> Any:
        return getattr(ctx, "metadata_branch", None) or ""


class EarlyCommitHashesMetric(BaseMetric):
    """AT: The first N commit hashes (oldest-first) of the collected branch.

    Disambiguates repos that share a root commit (forks/templates) but diverge
    early: the beginning of history is fixed, so these hashes are stable across
    re-collections yet differ between forks. Comma-joined; fewer than N for short
    histories, empty for an empty repo.
    """

    column = "AT"
    field_name = "early_commit_hashes"
    N = 10

    def compute(self, ctx: RepoContext) -> Any:
        return ",".join(ctx.vcs.early_commit_hashes(ctx.repo_path, self.N))


# --- MinHash over the commit-hash set (Jaccard-based repo identity) ----------
# Fixed universal-hash constants (deterministic, derived from the perm index) so
# signatures are reproducible across repos and runs. Commit hashes are already
# uniformly random, so a linear hash a*x+b over a 64-bit prefix is plenty.
_MINHASH_PERMS = 32
_MASK64 = (1 << 64) - 1


def _minhash_consts(i: int) -> tuple[int, int]:
    import hashlib

    d = hashlib.blake2b(str(i).encode(), digest_size=16).digest()
    a = int.from_bytes(d[:8], "big") | 1  # must be odd
    b = int.from_bytes(d[8:], "big")
    return a, b


_MINHASH_AB = [_minhash_consts(i) for i in range(_MINHASH_PERMS)]


class CommitMinhashMetric(BaseMetric):
    """AU: MinHash signature over ALL commit hashes (across refs).

    Enables Jaccard-similarity matching of a re-collected repo to its prior
    metadata even as HEAD advances (same repo -> nearly identical commit set ->
    high signature agreement; forks/unrelated -> low). 32 perms, hex-joined;
    empty for an empty repo.
    """

    column = "AU"
    field_name = "commit_minhash"

    def compute(self, ctx: RepoContext) -> Any:
        xs = [int(h[:16], 16) for h in ctx.vcs.all_commit_hashes(ctx.repo_path) if len(h) >= 16]
        if not xs:
            return ""
        sig = [min((a * x + b) & _MASK64 for x in xs) for a, b in _MINHASH_AB]
        return ",".join(format(s, "016x") for s in sig)
