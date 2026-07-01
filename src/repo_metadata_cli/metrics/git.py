"""Columns N, O, P, Q — commit count, contributors_count, PR counts."""

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
    field_name = "contributors_count"

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


class BranchCountMetric(BaseMetric):
    """AH: Number of distinct branches in the repository.

    Counts unique branch names across both local heads and ``origin`` remote
    branches (a bundle is materialized through the ``origin`` remote, so its
    branches live under ``refs/remotes/origin/*``).  Deduplicating by name and
    reading refs directly avoids the artifacts that inflated the old
    ``git branch -a`` line count by a fixed +3: the detached-HEAD pseudo-entry,
    the extra local branch ``git clone`` leaves behind, and the symbolic
    ``origin/HEAD`` alias.
    """

    column = "AH"
    field_name = "branch_count"

    _PREFIXES = ("refs/heads/", "refs/remotes/origin/")

    def compute(self, ctx: RepoContext) -> Any:
        out = run_cmd(
            ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"],
            cwd=ctx.repo_path,
        )
        names = set()
        for line in out.splitlines():
            ref = line.strip()
            for prefix in self._PREFIXES:
                if ref.startswith(prefix):
                    name = ref[len(prefix):]
                    if name and name != "HEAD":
                        names.add(name)
                    break
        return len(names)


class CreatedAtMetric(BaseMetric):
    """AF: Repository creation timestamp — date of the first commit (ported from v1)."""

    column = "AF"
    field_name = "created_at"

    def compute(self, ctx: RepoContext) -> Any:
        return run_cmd(
            ["git", "log", "HEAD", "--reverse", "--format=%ai", "--max-count=1"],
            cwd=ctx.repo_path,
        )


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
        out = run_cmd(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=ctx.repo_path)
        roots = [h.strip() for h in (out or "").splitlines() if h.strip()]
        return ",".join(roots)


class MetadataCommitHashMetric(BaseMetric):
    """AR: The commit HEAD pointed at when this metadata was collected."""

    column = "AR"
    field_name = "metadata_commit_hash"

    def compute(self, ctx: RepoContext) -> Any:
        return (run_cmd(["git", "rev-parse", "HEAD"], cwd=ctx.repo_path) or "").strip()


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
        # `git log --reverse --max-count=N` returns the N *newest* commits
        # oldest-first, not the first N — so list oldest-first and slice.
        out = run_cmd(["git", "rev-list", "--reverse", "HEAD"], cwd=ctx.repo_path)
        hashes = [h.strip() for h in (out or "").splitlines() if h.strip()]
        return ",".join(hashes[: self.N])


# --- MinHash over the commit-hash set (Jaccard-based repo identity) ----------
# Fixed universal-hash constants (deterministic, derived from the perm index) so
# signatures are reproducible across repos and runs. Commit SHAs are already
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
        out = run_cmd(["git", "rev-list", "--all"], cwd=ctx.repo_path)
        xs = [int(h[:16], 16) for h in (out or "").split() if len(h) >= 16]
        if not xs:
            return ""
        sig = [min((a * x + b) & _MASK64 for x in xs) for a, b in _MINHASH_AB]
        return ",".join(format(s, "016x") for s in sig)
