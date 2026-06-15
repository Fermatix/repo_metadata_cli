"""Version-control abstraction: a stable backend interface plus auto-detection.

``BaseVCS`` defines the operations the pipeline and metrics need; ``GitVCS`` and
``MercurialVCS`` implement them.  ``detect_vcs_from_url`` / ``detect_vcs_from_path``
choose a backend automatically (git is always the safe default).
"""

from .base import BaseVCS
from .detect import (
    detect_vcs_from_path,
    detect_vcs_from_url,
    get_vcs,
    strip_vcs_scheme,
)
from .git import GitVCS
from .mercurial import MercurialVCS

__all__ = [
    "BaseVCS",
    "GitVCS",
    "MercurialVCS",
    "detect_vcs_from_url",
    "detect_vcs_from_path",
    "get_vcs",
    "strip_vcs_scheme",
]
