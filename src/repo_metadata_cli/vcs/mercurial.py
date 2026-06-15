"""Mercurial backend — native ``hg`` equivalents of every git operation.

Self-contained: all hg-specific heuristics (fork detection, PR/MR parsing) live
here.  Every read method returns an empty/zero value when ``hg`` is missing or a
command fails, so a system without Mercurial degrades exactly like the no-VCS
local mode.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Set, Tuple

from .base import BaseVCS

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 720  # match GitVCS

# Known Mercurial hosts, anchored to the host segment so a github.com path like
# ``/hgtools/repo`` does NOT falsely match.
_HG_HOST_RE = re.compile(
    r"""^(?:[a-z][a-z0-9+.\-]*://)?    # optional scheme
        (?:[^@/]+@)?                    # optional user@
        (?:
            hg\.[^/:]+                  # hg.* (hg.mozilla.org, hg.python.org, hg.sr.ht)
          | [^/:]*heptapod[^/:]*        # heptapod (foss.heptapod.net, *.heptapod.host)
          | (?:[^/:]+\.)?mercurial-scm\.org
        )
        (?:[:/]|$)                      # host boundary
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Non-merge revset reused across metrics.
_NO_MERGES = "not merge()"
_MERGES = "merge()"


def _hg_env() -> Dict[str, str]:
    """Environment for hermetic, scriptable hg output."""
    env = os.environ.copy()
    env["HGPLAIN"] = "1"           # disable aliases/i18n/pager — stable templated output
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _hg_text(args: List[str], cwd: Optional[Path] = None, timeout: int = 720) -> str:
    """Run ``hg <args>`` and return stripped stdout, or "" on any failure.

    Mirrors utils.run_cmd but injects the Mercurial-plain environment.  Returns
    "" on FileNotFoundError too, so an uninstalled ``hg`` degrades gracefully.
    """
    try:
        result = subprocess.check_output(
            ["hg", *args],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=_hg_env(),
        )
        return result.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        logger.warning("hg command timed out after %ds: %s", timeout, " ".join(args))
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logger.debug("hg command failed: %s (%s)", " ".join(args), exc)
        return ""


class MercurialVCS(BaseVCS):
    name: ClassVar[str] = "hg"
    history_dirname: ClassVar[str] = ".hg"
    default_ref: ClassVar[str] = "tip"

    # --- detection -----------------------------------------------------------
    @classmethod
    def matches_url(cls, url: str) -> bool:
        return bool(_HG_HOST_RE.match(url.strip()))

    @classmethod
    def matches_path(cls, path: Path) -> bool:
        return (path / cls.history_dirname).is_dir()

    # --- materialization -----------------------------------------------------
    def clone(self, source: Path, dest_dir: Path) -> Optional[Path]:
        repo_dir = dest_dir / source.stem
        env = _hg_env()
        try:
            init = subprocess.run(
                ["hg", "init", str(repo_dir)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLONE_TIMEOUT,
            )
            if init.returncode != 0 or not repo_dir.exists():
                logger.warning("Failed to hg init for %s", source)
                return None
            unbundle = subprocess.run(
                ["hg", "-R", str(repo_dir), "unbundle", str(source)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLONE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("hg clone timed out after %ds: %s", _CLONE_TIMEOUT, source)
            return None
        if unbundle.returncode != 0:
            logger.warning("Failed to unbundle %s", source)
            return None
        logger.debug("Materialized %s into %s", source.name, repo_dir)
        return repo_dir

    def latest_branch(self, repo_dir: Path) -> Optional[str]:
        # Branch of the newest changeset (tip) — analyse the most recent state of
        # the repo, even if that branch is closed, mirroring git's "newest ref"
        # selection.  Fall back to the most recent open branch, then None.
        tip = _hg_text(["log", "-r", "tip", "-T", "{branch}"], cwd=repo_dir)
        if tip:
            return tip
        out = _hg_text(["branches", "-T", "{branch}\n"], cwd=repo_dir)
        for line in out.splitlines():
            name = line.strip()
            if name:
                return name
        return None

    def checkout(self, repo_dir: Path, ref: str) -> bool:
        try:
            result = subprocess.run(
                ["hg", "-R", str(repo_dir), "update", "--clean", "--rev", ref],
                env=_hg_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLONE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    # --- metrics -------------------------------------------------------------
    def commit_count(self, repo_path: Path) -> int:
        # Every changeset in the repo, INCLUDING merges (column-N contract).
        raw = _hg_text(["log", "-r", "all()", "-T", ".\n"], cwd=repo_path)
        return sum(1 for line in raw.splitlines() if line)

    def author_names(self, repo_path: Path) -> List[str]:
        raw = _hg_text(
            ["log", "-r", _NO_MERGES, "-T", "{author|person}\n"],
            cwd=repo_path,
        )
        # hg yields one author per commit; dedup preserving first-seen order to
        # approximate ``git shortlog`` (one entry per distinct author).
        seen = (line.strip() for line in raw.splitlines())
        return list(dict.fromkeys(name for name in seen if name))

    def branch_count(self, repo_path: Path) -> int:
        # Open named branches only (no --closed): git's branch_count likewise
        # counts only live refs, so a closed/merged-away branch must not inflate
        # the count for parity.
        out = _hg_text(["branches", "-T", "{branch}\n"], cwd=repo_path)
        names: Set[str] = {line.strip() for line in out.splitlines() if line.strip()}
        return len(names)

    def created_at(self, repo_path: Path) -> str:
        # first(all()) is the genuine root changeset; it yields nothing on an
        # empty repo, so we return "" there (matching git) instead of the fake
        # epoch '1970-01-01 ...' that `-r 0` emits for the null revision.
        # isodatesec ≈ git %ai "2006-01-02 15:04:05 -0700".
        return _hg_text(["log", "-r", "first(all())", "-T", "{date|isodatesec}"], cwd=repo_path)

    def file_tree(self, repo_path: Path) -> List[str]:
        raw = _hg_text(["manifest"], cwd=repo_path)
        return raw.splitlines()

    def count_pull_requests(self, repo_path: Path) -> Tuple[int, int]:
        # Reuse the GitHub/GitLab regexes — hg repos rarely carry these footers,
        # so this usually returns (0, 0), which is the correct answer.
        from ..metric_utils import _GITHUB_PR_RE, _GITHUB_SQUASH_RE, _GITLAB_MR_RE

        merge_bodies = _hg_text(["log", "-r", _MERGES, "-T", "{desc}\n"], cwd=repo_path)
        all_subjects = _hg_text(["log", "-T", "{desc|firstline}\n"], cwd=repo_path)

        prs: Set[str] = set()
        for m in _GITHUB_PR_RE.finditer(merge_bodies):
            prs.add(m.group(1))
        for m in _GITHUB_SQUASH_RE.finditer(all_subjects):
            prs.add(m.group(1))
        mrs: Set[str] = set()
        for m in _GITLAB_MR_RE.finditer(merge_bodies):
            mrs.add(m.group(1))
        return len(prs) + len(mrs), 0

    def detect_fork(self, repo_path: Path) -> float:
        from ..metric_utils import _FORK_MERGE_RE

        # 1. An explicit upstream path in .hg/hgrc *[paths]* section only.  A bare
        # line regex was section-blind and fired on an `upstream =` key under
        # [alias]/[ui]/[hooks] too (false positives); parse and scope to [paths],
        # mirroring git's section-bound [remote "upstream"] check.
        hgrc = repo_path / ".hg" / "hgrc"
        if hgrc.exists():
            import configparser
            cp = configparser.RawConfigParser(strict=False)
            try:
                cp.read_string(hgrc.read_text(encoding="utf-8", errors="ignore"))
                if cp.has_section("paths") and cp.has_option("paths", "upstream"):
                    return 1.0
            except (configparser.Error, OSError):
                pass

        # 2. Merge descriptions referencing an external upstream.
        merge_log = _hg_text(
            ["log", "-r", _MERGES, "-T", "{desc|firstline}\n", "-l", "200"],
            cwd=repo_path,
        )
        if merge_log and _FORK_MERGE_RE.search(merge_log):
            return 1.0
        return 0.0

    def recent_commit_subjects(self, repo_path: Path, limit: int = 200) -> str:
        return _hg_text(
            ["log", "-r", _NO_MERGES, "-T", "{desc|firstline}\n", "-l", str(limit)],
            cwd=repo_path,
        )
