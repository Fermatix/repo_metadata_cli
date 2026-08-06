"""Abstract VCS interface.

Each backend (Git, Mercurial) wraps its subprocess calls behind a stable API so
the rest of the codebase (RepoContext, metrics, pipeline) never references a
concrete VCS command.  All *read* methods MUST degrade gracefully — return ``""``,
``0``, ``0.0`` or ``[]`` — when the VCS binary is absent or a command fails.
``run_cmd`` already returns ``""`` on ``FileNotFoundError``/``OSError``, so most
methods get this for free (e.g. when ``hg`` is not installed).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple


class BaseVCS(ABC):
    """Stable interface implemented by GitVCS and MercurialVCS."""

    # --- identity / registry -------------------------------------------------
    name: ClassVar[str]             # "git" | "hg"
    history_dirname: ClassVar[str]  # ".git" | ".hg"
    default_ref: ClassVar[str]      # "HEAD" | "tip"

    @classmethod
    @abstractmethod
    def matches_url(cls, url: str) -> bool:
        """True if this backend should handle the given source URL.

        Used at fetch / partner-mapping time, before anything is on disk.
        """

    @classmethod
    @abstractmethod
    def matches_path(cls, path: Path) -> bool:
        """True if ``path`` is a working copy of this VCS (history dir present)."""

    # --- materialization (pipeline) -----------------------------------------
    @abstractmethod
    def clone(self, source: Path, dest_dir: Path) -> Optional[Path]:
        """Materialize a bundle/source into ``dest_dir``; return the repo dir or None.

        Owns the FULL story: clone + make every branch locally inspectable.
        """

    @abstractmethod
    def latest_branch(self, repo_dir: Path) -> Optional[str]:
        """Ref/branch name with the most recent commit, or None."""

    @abstractmethod
    def checkout(self, repo_dir: Path, ref: str) -> bool:
        """Check out ``ref`` (discarding local changes); return success."""

    # --- metrics (per-column data) ------------------------------------------
    @abstractmethod
    def commit_count(self, repo_path: Path) -> int:
        """Total number of commits across all branches, including merges (column N)."""

    @abstractmethod
    def author_names(self, repo_path: Path) -> List[str]:
        """One entry per distinct author across all history (column O, pre-bot-filter)."""

    @abstractmethod
    def branch_count(self, repo_path: Path) -> int:
        """Number of distinct branches (column AH)."""

    @abstractmethod
    def created_at(self, repo_path: Path) -> str:
        """Timestamp of the first commit as a string (column AF)."""

    @abstractmethod
    def file_tree(self, repo_path: Path) -> List[str]:
        """Tracked file paths (caller truncates to 40; falls back to FS walk on [])."""

    @abstractmethod
    def count_pull_requests(self, repo_path: Path) -> Tuple[int, int]:
        """(total_pr, reviewed_pr); reviewed always 0 from history alone (cols P/Q)."""

    @abstractmethod
    def detect_fork(self, repo_path: Path) -> float:
        """1.0 if the repo appears to be a fork, else 0.0 (column J)."""

    def recent_commit_subjects(self, repo_path: Path, limit: int = 200) -> str:
        """Recent non-merge commit subjects, newline-joined (column Z input).

        Default returns "" so the caller falls back to filesystem-only detection.
        Git/Mercurial override this.
        """
        return ""

    # --- full tracked-file list (test_coverage_pct input) --------------------
    def tracked_files(self, repo_path: Path) -> List[str]:
        """COMPLETE list of tracked file paths (repo-relative, '/'-separated).

        Unlike the diagnostic ``RepoContext.file_tree`` (truncated to 40 paths),
        consumers of this method get every tracked file.  The default delegates
        to ``file_tree``, which both backends already return in full; the
        caller (``RepoContext.tracked_files``) adds a filesystem fallback for
        plain-directory mode, where this returns [].
        """
        return self.file_tree(repo_path)

    # --- PR size units (columns AX-BA: pr_*_pct / avg_loc_per_pr) ------------
    # Defaults return empty/zero so a plain directory (no VCS) degrades to the
    # agreed all-zero PR size distribution.
    def pr_fingerprint_units(self, repo_path: Path) -> List[Tuple[str, str]]:
        """Real platform PRs/MRs detected from history fingerprints.

        Returns ``[(revision, kind)]`` where kind is ``"merge"`` (GitHub
        "Merge pull request #N" / GitLab "See merge request ...!N" merge
        commits — measured as the diff to the first parent) or ``"commit"``
        (GitHub squash merges, subject ending in "(#N)" — measured as the
        commit's own diff).  Deduplicated by (platform, PR number), merge
        fingerprints first.  [] when the history carries no fingerprints.
        """
        return []

    def merge_unit_revs(self, repo_path: Path) -> List[str]:
        """Merge revisions on the checked-out line, newest first (first fallback)."""
        return []

    def commit_unit_revs(self, repo_path: Path) -> List[str]:
        """Non-merge revisions on the checked-out line, newest first (last fallback)."""
        return []

    def unit_changed_lines(self, repo_path: Path, rev: str, kind: str) -> int:
        """Changed lines (additions + deletions) of one PR unit; 0 on failure."""
        return 0

    # --- commit-hash provenance / identity fingerprints (cols AQ-AU) ---------
    # Default to empty so a backend that can't produce stable commit ids just
    # yields blank fingerprints. GitVCS overrides all four.
    def root_commit_hashes(self, repo_path: Path) -> List[str]:
        """Parentless (root) commit hashes — the identity anchor (column AQ)."""
        return []

    def early_commit_hashes(self, repo_path: Path, n: int) -> List[str]:
        """First N commit hashes, oldest-first (column AT)."""
        return []

    def head_commit_hash(self, repo_path: Path) -> str:
        """The commit HEAD/tip currently points at (column AR)."""
        return ""

    def all_commit_hashes(self, repo_path: Path) -> List[str]:
        """Every commit hash across refs — input to the commit MinHash (column AU)."""
        return []
