"""Base class for all repository metrics and the shared computation context."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional

from .allowed_files import AllowedFiles
from .settings import AppSettings
from .tree_sitter_support import TreeSitterManager
from .utils import run_cmd  # used by the repo_org property (local-mode git remote)

if TYPE_CHECKING:
    from .vcs.base import BaseVCS

logger = logging.getLogger(__name__)


@dataclass
class RepoContext:
    """Shared state for a single repository analysis pass. Expensive computations are cached."""

    repo_path: Path
    settings: AppSettings
    tree_sitter: Optional[TreeSitterManager]
    allowed_files: AllowedFiles
    bundle_path: Optional[Path] = None
    # Short name of the branch selected for metadata collection (latest-commit
    # branch). Set by the pipeline at checkout time; consumed by the
    # metadata_branch_name metric (HEAD is detached, so it can't be read back).
    metadata_branch: Optional[str] = None
    vcs: Optional["BaseVCS"] = None
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Auto-detect the VCS from the working copy when not provided, so every
        # existing RepoContext(...) call site keeps working.  A plain directory
        # with neither .git nor .hg defaults to git (commands return "" → metrics
        # yield 0), preserving the no-VCS local-mode behaviour.
        if self.vcs is None:
            from .vcs.detect import detect_vcs_from_path  # late import: avoid cycle
            self.vcs = detect_vcs_from_path(self.repo_path)

    @property
    def bundle_name(self) -> str:
        if self.bundle_path is not None:
            return self.bundle_path.stem
        return self.repo_path.name

    @property
    def partner_name(self) -> str:
        # When launched from a repos.txt URL list, partner_map resolves the
        # bundle to a partner_name parsed from its source URL (or "bundles").
        mapped = self.settings.partner_map.get(self.bundle_name)
        if mapped is not None:
            return mapped
        if self.bundle_path is not None:
            return self.bundle_path.parent.name
        return self.repo_path.parent.name

    @property
    def repo_org(self) -> str:
        # When launched from a repos.txt URL list, org_map resolves the bundle
        # to the full namespace path parsed from its source URL.
        mapped = self.settings.org_map.get(self.bundle_name)
        if mapped:
            return mapped
        # LOCAL PATCH: in directory/local mode there is no URL list, so derive
        # the org from the repo's `origin` git remote (same parser as the URL
        # path) instead of leaving it empty.
        if self.bundle_path is None:
            def _compute() -> str:
                from .partner import parse_repo_org  # late import avoids cycle
                url = run_cmd(["git", "remote", "get-url", "origin"], cwd=self.repo_path)
                return (parse_repo_org(url) or "") if url else ""
            return self._cached("repo_org_remote", _compute)
        return ""

    @property
    def repo_url(self) -> str:
        # When launched from a repos.txt URL list, url_map carries the entry
        # exactly as written there (URL or local path).
        mapped = self.settings.url_map.get(self.bundle_name)
        if mapped:
            return mapped
        # Directory/local mode has no URL list — fall back to the working
        # copy's `origin` remote (raw URL, no sanitization); empty if none.
        if self.bundle_path is None:
            def _compute() -> str:
                return run_cmd(["git", "remote", "get-url", "origin"], cwd=self.repo_path) or ""
            return self._cached("repo_url_remote", _compute)
        return ""

    @property
    def repo_name(self) -> str:
        # Bundles are named by the full path (bundle_name); name_map recovers the
        # short leaf name. Falls back to bundle_name when no map (directory mode).
        return self.settings.name_map.get(self.bundle_name, self.bundle_name)

    def _cached(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def scc_stats(self) -> dict:
        """scc stats on ALL files (no dir exclusions) — used for Raw LOC (F) and language distribution."""
        from .metric_utils import get_scc_stats  # late import to avoid circularity
        return self._cached("scc_stats", lambda: get_scc_stats(self.repo_path))

    @property
    def scc_stats_no_deps(self) -> dict:
        """scc stats excluding dependency/build dirs — used for Logical LOC (G)."""
        from .metric_utils import get_scc_stats  # late import to avoid circularity
        exclude_dirs = list(self.settings.metrics.scc_exclude_dirs)
        return self._cached(
            "scc_stats_no_deps",
            lambda: get_scc_stats(self.repo_path, exclude_dirs=exclude_dirs),
        )

    @property
    def tracked_files(self) -> list[str]:
        """FULL tracked-file list (repo-relative, '/'-separated), cached.

        VCS-tracked paths when available; in plain-directory mode (no VCS
        history) falls back to a filesystem walk that skips dot-directories.
        Consumed by test_coverage_pct, which must see every file — unlike the
        diagnostic ``file_tree`` below, which truncates to 40 paths.
        """
        def _compute() -> list[str]:
            tracked = self.vcs.tracked_files(self.repo_path)
            if tracked:
                return tracked
            return sorted(
                p.relative_to(self.repo_path).as_posix()
                for p in self.repo_path.rglob("*")
                if p.is_file()
                and not any(
                    part.startswith(".")
                    for part in p.relative_to(self.repo_path).parts
                )
            )
        return self._cached("tracked_files", _compute)

    @property
    def file_tree(self) -> list[str]:
        """Up to 40 file paths (VCS-tracked when available, filesystem walk otherwise)."""
        def _compute() -> list[str]:
            tracked = self.vcs.file_tree(self.repo_path)
            if tracked:
                return tracked[:40]
            files = sorted(
                str(p.relative_to(self.repo_path))
                for p in self.repo_path.rglob("*")
                if p.is_file() and not any(part.startswith(".") for part in p.parts)
            )
            return files[:40]
        return self._cached("file_tree", _compute)


class BaseMetric(ABC):
    """Abstract base for all metric implementations. One subclass per CSV column."""

    column: ClassVar[str]      # e.g. "A", "B", "AA"
    field_name: ClassVar[str]  # CSV column header

    @abstractmethod
    def compute(self, ctx: RepoContext) -> Any:
        """Compute the metric value given the repository context."""
        ...
