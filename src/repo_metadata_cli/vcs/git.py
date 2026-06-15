"""Git backend — mirrors the exact commands the codebase used before the VCS split.

Behaviour is intentionally byte-for-byte identical to the pre-refactor git paths
so existing repositories and tests are unaffected.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple

from ..utils import run_cmd
from .base import BaseVCS

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 720  # 10 min — enough for a 4+ GB bundle on a slow disk


class GitVCS(BaseVCS):
    name: ClassVar[str] = "git"
    history_dirname: ClassVar[str] = ".git"
    default_ref: ClassVar[str] = "HEAD"

    # --- detection -----------------------------------------------------------
    @classmethod
    def matches_url(cls, url: str) -> bool:
        # Git is the default/fallback backend; explicit detection lives in
        # detect.py (a ``.git`` suffix or ``git+`` prefix).  Recognising the
        # suffix here keeps the class self-describing for the registry.
        return url.strip().rstrip("/").lower().endswith(".git")

    @classmethod
    def matches_path(cls, path: Path) -> bool:
        # .exists() (not is_dir): git worktrees/submodules use a ``.git`` file.
        return (path / cls.history_dirname).exists()

    # --- materialization -----------------------------------------------------
    def clone(self, source: Path, dest_dir: Path) -> Optional[Path]:
        repo_dir = dest_dir / source.stem
        env = os.environ.copy()
        env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
        try:
            result = subprocess.run(
                ["git", "clone", str(source), str(repo_dir)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLONE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Clone timed out after %ds: %s", _CLONE_TIMEOUT, source)
            return None
        if result.returncode != 0 or not repo_dir.exists():
            logger.warning("Failed to clone %s", source)
            return None

        # Ensure ALL remote branches are present as refs/remotes/origin/*.
        subprocess.run(
            [
                "git", "-C", str(repo_dir), "fetch", "--quiet", "--force",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CLONE_TIMEOUT,
        )

        logger.debug("Cloned %s into %s", source.name, repo_dir)
        return repo_dir

    def latest_branch(self, repo_dir: Path) -> Optional[str]:
        refs_raw = run_cmd(
            [
                "git", "for-each-ref",
                "--sort=-committerdate",
                "--format=%(refname)|%(committerdate:iso8601)",
                "refs/heads", "refs/remotes",
            ],
            cwd=repo_dir,
        )
        for line in refs_raw.splitlines():
            if "|" not in line:
                continue
            ref, _ = line.split("|", 1)
            ref = ref.strip()
            if not ref or ref.endswith("/HEAD"):
                continue
            return ref
        current = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        return current or None

    def checkout(self, repo_dir: Path, ref: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "--force", "--quiet", "--detach", ref],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    # --- metrics -------------------------------------------------------------
    def commit_count(self, repo_path: Path) -> int:
        # All commits reachable from every ref, INCLUDING merges (column-N
        # contract "across all branches, including merges").
        raw = run_cmd(["git", "rev-list", "--all", "--count"], cwd=repo_path)
        try:
            return int(raw.strip())
        except (ValueError, AttributeError):
            return 0

    def author_names(self, repo_path: Path) -> List[str]:
        out = run_cmd(["git", "shortlog", "-sn", "--no-merges", "--all"], cwd=repo_path)
        names: List[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            names.append(parts[1] if len(parts) == 2 else "")
        return names

    def branch_count(self, repo_path: Path) -> int:
        out = run_cmd(
            ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"],
            cwd=repo_path,
        )
        prefixes = ("refs/heads/", "refs/remotes/origin/")
        names = set()
        for line in out.splitlines():
            ref = line.strip()
            for prefix in prefixes:
                if ref.startswith(prefix):
                    name = ref[len(prefix):]
                    if name and name != "HEAD":
                        names.add(name)
                    break
        return len(names)

    def created_at(self, repo_path: Path) -> str:
        # First commit, NOT last.  `git log --reverse --max-count=1` is a known
        # trap: --max-count is applied during the default newest-first walk
        # BEFORE --reverse, so it returns the most recent commit.  Use the root
        # commit(s) instead (--max-parents=0), taking the earliest (last line,
        # since git lists them newest-first) when a repo has multiple roots.
        raw = run_cmd(
            ["git", "log", "HEAD", "--max-parents=0", "--format=%ai"],
            cwd=repo_path,
        )
        lines = [line for line in raw.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def file_tree(self, repo_path: Path) -> List[str]:
        raw = run_cmd(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_path)
        return raw.splitlines()

    def count_pull_requests(self, repo_path: Path) -> Tuple[int, int]:
        from ..metric_utils import count_pull_requests  # late import: avoid cycle
        return count_pull_requests(repo_path)

    def detect_fork(self, repo_path: Path) -> float:
        from ..metric_utils import detect_fork_pct  # late import: avoid cycle
        return detect_fork_pct(repo_path)

    def recent_commit_subjects(self, repo_path: Path, limit: int = 200) -> str:
        return run_cmd(
            ["git", "log", "--all", "--no-merges", "--format=%s", "-n", str(limit)],
            cwd=repo_path,
        )
