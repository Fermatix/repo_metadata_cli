"""Base class for all repository metrics and the shared computation context."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from .allowed_files import AllowedFiles
from .settings import AppSettings
from .tree_sitter_support import TreeSitterManager
from .utils import run_cmd

logger = logging.getLogger(__name__)


@dataclass
class RepoContext:
    """Shared state for a single repository analysis pass. Expensive computations are cached."""

    repo_path: Path
    settings: AppSettings
    tree_sitter: Optional[TreeSitterManager]
    allowed_files: AllowedFiles
    bundle_path: Optional[Path] = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def bundle_name(self) -> str:
        if self.bundle_path is not None:
            return self.bundle_path.stem
        return self.repo_path.name

    @property
    def partner_name(self) -> str:
        if self.bundle_path is not None:
            return self.bundle_path.parent.name
        return self.repo_path.parent.name

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
    def git_log_no_merges(self) -> list[str]:
        """Non-merge, non-revert commits on the default branch (spec column N)."""
        def _compute() -> list[str]:
            raw = run_cmd(["git", "log", "--no-merges", "--oneline"], cwd=self.repo_path)
            return [
                line for line in raw.splitlines()
                if line.strip() and "revert" not in line.lower()
            ]
        return self._cached("git_log_no_merges", _compute)

    @property
    def file_tree(self) -> list[str]:
        """Up to 40 file paths (git-tracked when available, filesystem walk otherwise)."""
        def _compute() -> list[str]:
            raw = run_cmd(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=self.repo_path)
            if raw:
                return raw.splitlines()[:40]
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
