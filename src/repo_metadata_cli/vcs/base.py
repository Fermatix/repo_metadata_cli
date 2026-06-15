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
