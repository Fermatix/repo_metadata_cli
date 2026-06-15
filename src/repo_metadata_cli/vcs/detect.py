"""VCS detection — from a source URL (fetch time) or a working copy (disk).

Detection is deliberately conservative: git is the default, and a URL only flips
to Mercurial on a strong positive signal, so every existing GitHub/GitLab URL
keeps its current git behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple, Type

from .base import BaseVCS
from .git import GitVCS
from .mercurial import MercurialVCS

# Order matters: first match wins.  Git is the fallback default.
_REGISTRY: Tuple[Type[BaseVCS], ...] = (MercurialVCS, GitVCS)

# pip / PEP 440 VCS scheme prefixes — explicit author intent, checked first.
_HG_SCHEME_RE = re.compile(r"^hg\+", re.IGNORECASE)
_GIT_SCHEME_RE = re.compile(r"^git\+", re.IGNORECASE)
_ANY_SCHEME_RE = re.compile(r"^(?:hg|git)\+", re.IGNORECASE)


def strip_vcs_scheme(url: str) -> str:
    """Remove a leading ``hg+`` / ``git+`` prefix (the bare URL for clone/stem)."""
    return _ANY_SCHEME_RE.sub("", url.strip(), count=1)


def detect_vcs_from_url(url: str) -> BaseVCS:
    """Pick a VCS backend for a source URL.  Defaults to GitVCS."""
    s = url.strip()
    # 1. Explicit scheme prefix wins (strongest, unambiguous signal).
    if _HG_SCHEME_RE.match(s):
        return MercurialVCS()
    if _GIT_SCHEME_RE.match(s):
        return GitVCS()
    # 2. A .git suffix is a positive git signal.
    if s.rstrip("/").lower().endswith(".git"):
        return GitVCS()
    # 3. Known-host / suffix heuristics from each backend.
    for cls in _REGISTRY:
        if cls.matches_url(s):
            return cls()
    # 4. Default: git (preserves all current github.com / gitlab.com behaviour).
    return GitVCS()


def detect_vcs_from_path(path: Path) -> BaseVCS:
    """Pick a VCS backend for a working copy on disk.  Defaults to GitVCS."""
    for cls in _REGISTRY:
        if cls.matches_path(path):
            return cls()
    return GitVCS()


def get_vcs(name: str) -> BaseVCS:
    """Return a backend instance by name ("git" / "hg"); defaults to GitVCS."""
    for cls in _REGISTRY:
        if cls.name == name:
            return cls()
    return GitVCS()
