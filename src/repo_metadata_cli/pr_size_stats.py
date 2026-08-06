"""PR size-distribution statistics (columns AX-BA).

Deterministic, history-only computation — no LLM, no network calls.

The unit of analysis is one merged PR/MR when platform fingerprints are present
in the history: GitHub merge commits ("Merge pull request #N"), GitHub squash
merges (subject ending in "(#N)") and GitLab MR merge commits ("See merge
request ...!N"), deduplicated by (platform, PR number).  Merge units are
measured as the diff to their first parent; squash units as the commit's own
diff.  When no fingerprints exist (rewritten history, mirrors, non-standard
messages) the fallback bases are merge commits, then plain commits.

The size of a unit is additions + deletions.  Buckets:

* ``simple``   — up to :data:`PR_SIMPLE_MAX` changed lines (inclusive);
* ``standard`` — :data:`PR_SIMPLE_MAX`+1 .. :data:`PR_STANDARD_MAX`;
* ``rich``     — more than :data:`PR_STANDARD_MAX`.

At most :data:`MAX_PR_UNITS` units are analysed.  Percentages and the average
are rounded with Python's :func:`round` (banker's rounding).

Zero semantics (agreed, not an error): when the pipeline's effective
``total_pr_count`` is 0, when history is too short (fewer than two commits and
no merges), or when no unit can be formed, all four columns are 0.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .vcs.base import BaseVCS

# PR size thresholds (changed lines = additions + deletions).
PR_SIMPLE_MAX = 50      # <=50 -> simple
PR_STANDARD_MAX = 300   # 51..300 -> standard; >300 -> rich
# Cap on analysed PR/commit units per repository.
MAX_PR_UNITS = 400

# Parsers for git/hg diffstat summary lines ("N insertions(+), M deletions(-)").
_INSERTIONS_RE = re.compile(r"(\d+) insertion")
_DELETIONS_RE = re.compile(r"(\d+) deletion")

PR_SIZE_FIELDS = ("pr_simple_pct", "pr_standard_pct", "pr_rich_pct", "avg_loc_per_pr")


def parse_changed_lines(shortstat_text: str) -> int:
    """Sum additions + deletions from a git/hg diffstat summary text."""
    return sum(int(x) for x in _INSERTIONS_RE.findall(shortstat_text)) + sum(
        int(x) for x in _DELETIONS_RE.findall(shortstat_text)
    )


def zero_pr_size_stats() -> Dict[str, int]:
    """The agreed all-zero result: no PRs / not enough history is not an error."""
    return {field: 0 for field in PR_SIZE_FIELDS}


def collect_pr_size_stats(
    vcs: "BaseVCS", repo_path: Path, total_pr_count: int
) -> Dict[str, int]:
    """Compute the PR size distribution for one repository.

    ``total_pr_count`` is the pipeline's effective column-P value (PR cache or
    history-fingerprint fallback).  The distribution describes PRs, so when the
    repository has none the four fields are 0 — a commit-size distribution is
    not passed off as PR statistics.
    """
    if total_pr_count <= 0:
        return zero_pr_size_stats()

    units = vcs.pr_fingerprint_units(repo_path)[:MAX_PR_UNITS]
    if not units:
        merges = vcs.merge_unit_revs(repo_path)[:MAX_PR_UNITS]
        if merges:
            units = [(rev, "merge") for rev in merges]
        else:
            commits = vcs.commit_unit_revs(repo_path)
            # A single commit (or an empty history) gives no meaningful
            # distribution — the agreed result is zeros.
            if len(commits) <= 1:
                return zero_pr_size_stats()
            units = [(rev, "commit") for rev in commits[:MAX_PR_UNITS]]

    simple = standard = rich = 0
    total_changed = 0
    for rev, kind in units:
        changed = vcs.unit_changed_lines(repo_path, rev, kind)
        total_changed += changed
        if changed <= PR_SIMPLE_MAX:
            simple += 1
        elif changed <= PR_STANDARD_MAX:
            standard += 1
        else:
            rich += 1

    n = simple + standard + rich
    if n == 0:
        return zero_pr_size_stats()
    return {
        "pr_simple_pct": round(100 * simple / n),
        "pr_standard_pct": round(100 * standard / n),
        "pr_rich_pct": round(100 * rich / n),
        "avg_loc_per_pr": round(total_changed / n),
    }
