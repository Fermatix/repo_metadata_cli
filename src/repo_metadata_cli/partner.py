"""Map fetched repositories to a partner name derived from their source URL.

When the pipeline is launched from a repos.txt URL list (not a directory of
*.bundle files), partner repos follow the pattern:

    https://gitlab.com/doubletapp/data-llm/partner-private-repos/<partner>/<repo>.git

The bundle on disk is named only after <repo> (see fetch_bundles.sh
`repo_only_name`), so the <partner> segment is lost.  This module rebuilds a
{bundle_stem -> partner_name} map straight from the URL list so the partner_name
column can be populated.  URLs that don't match the pattern fall back to
"bundles".
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_PARTNER = "bundles"

# Capture the path segment immediately following the partner-private-repos marker.
_PARTNER_RE = re.compile(r"/partner-private-repos/([^/]+)/")


def parse_partner_name(url: str) -> Optional[str]:
    """Return the partner segment from a partner-private-repos URL, else None."""
    match = _PARTNER_RE.search(url.strip())
    return match.group(1) if match else None


def parse_repo_org(url: str) -> Optional[str]:
    """Return the namespace/org path of a repo URL — everything between the host
    and the final repo segment, joined with '/'.

    Examples:
        git.softlex.pro/softlex/funeral-organizer/devops/logging.git
            -> "softlex/funeral-organizer/devops"
        gitlab.com/doubletapp/data-llm/partner-private-repos/4rome/kapika
            -> "doubletapp/data-llm/partner-private-repos/4rome"

    Returns None when the URL has no namespace (host + repo only) or is unparseable.
    """
    s = url.strip().rstrip("/")
    if not s:
        return None
    if s.endswith(".git"):
        s = s[:-4]
    s = re.sub(r"^[a-zA-Z]+://", "", s)   # strip scheme
    s = s.replace(":", "/", 1)            # scp-like host:path -> host/path
    s = re.sub(r"^[^@/]+@", "", s)        # strip leading user@
    if "/" not in s:
        return None
    path = s.split("/", 1)[1]             # drop host
    segments = [seg for seg in path.split("/") if seg]
    if len(segments) <= 1:                # only the repo segment, no namespace
        return None
    return "/".join(segments[:-1])


def _url_path(url: str) -> str:
    """Strip scheme/user/host from a repo URL, returning the namespace path
    (without a trailing .git). Shared by the stem/leaf/org helpers."""
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    if "://" in s:
        # Scheme URL: urlparse keeps :PORT inside netloc, so the path stays
        # clean — ssh://git@host:10022/group/repo must not leak "10022" into
        # the namespace.
        return urlparse(s).path.lstrip("/")
    s = s.replace(":", "/", 1)            # scp-like host:path → host/path
    s = re.sub(r"^[^@/]+@", "", s)        # strip leading user@
    return s.split("/", 1)[1] if "/" in s else s


def bundle_stem_from_url(url: str) -> str:
    """Reproduce fetch_bundles.sh `safe_name`: the FULL namespace path, sanitized.

    The on-disk *.bundle filename (== RepoContext.bundle_name) is now the full
    path so that repos sharing a leaf under different groups don't collide.
    Must stay in sync with src/repo_metadata_cli/scripts/fetch_bundles.sh.
    """
    path = _url_path(url)
    base = re.sub(r"/+", "--", path)            # path separators → '--'
    base = re.sub(r"[^A-Za-z0-9.-]+", "-", base)  # other chars → '-'
    base = re.sub(r"-+", "-", base)              # collapse runs (also '--' → '-')
    base = re.sub(r"^[.-]+", "", base)
    base = re.sub(r"[.-]+$", "", base)
    if not base:
        base = "repo"
    if base.endswith(".git") or base.endswith(".atom"):
        base = f"{base}-repo"
    return base


def repo_leaf_from_url(url: str) -> str:
    """The sanitized last path segment (the repo's own name, without namespace)."""
    path = _url_path(url)
    base = path.rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9.-]+", "-", base)
    base = re.sub(r"-+", "-", base)
    base = re.sub(r"^[.-]+", "", base)
    base = re.sub(r"[.-]+$", "", base)
    if not base:
        base = "repo"
    if base.endswith(".git") or base.endswith(".atom"):
        base = f"{base}-repo"
    return base


def build_partner_map(repos_file: Path) -> Dict[str, str]:
    """Build {bundle_stem -> partner_name} from a repos.txt URL list.

    Non-matching URLs map to DEFAULT_PARTNER ("bundles").  Blank lines and
    comments (#) are skipped.
    """
    mapping: Dict[str, str] = {}
    try:
        lines = repos_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.warning("Could not read repos file %s: %s", repos_file, exc)
        return mapping

    from .vcs.detect import strip_vcs_scheme  # late import: avoid import cycle

    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        bare = strip_vcs_scheme(url)  # hg+/git+ URLs map to the same stem as the bare URL
        stem = bundle_stem_from_url(bare)
        mapping[stem] = parse_partner_name(bare) or DEFAULT_PARTNER
    return mapping


def build_url_map(repos_file: Path) -> Dict[str, str]:
    """Build {bundle_stem -> source URL/path} from a repos.txt URL list.

    Keeps each entry exactly as written in repos.txt (URL or local path) so the
    output CSV can carry the repo's original location. Blank lines and
    comments (#) are skipped.
    """
    mapping: Dict[str, str] = {}
    try:
        lines = repos_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.warning("Could not read repos file %s: %s", repos_file, exc)
        return mapping

    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        mapping[bundle_stem_from_url(url)] = url
    return mapping


def build_org_map(repos_file: Path) -> Dict[str, str]:
    """Build {bundle_stem -> repo_org} from a repos.txt URL list.

    The org is the full namespace path of each repo URL (see parse_repo_org).
    Bundles are now named by the full path (bundle_stem_from_url), so the stem is
    unique per URL and there are no leaf-name collisions. Blank lines and
    comments (#) are skipped; URLs with no namespace are omitted.
    """
    mapping: Dict[str, str] = {}
    try:
        lines = repos_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.warning("Could not read repos file %s: %s", repos_file, exc)
        return mapping

    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        org = parse_repo_org(url)
        if org is None:
            continue
        mapping[bundle_stem_from_url(url)] = org
    return mapping


def build_name_map(repos_file: Path) -> Dict[str, str]:
    """Build {bundle_stem -> repo_leaf} from a repos.txt URL list.

    Bundle filenames are now the full namespace path; this map recovers the
    repo's own leaf name so the repo_name column stays the short name.
    Blank lines and comments (#) are skipped.
    """
    mapping: Dict[str, str] = {}
    try:
        lines = repos_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.warning("Could not read repos file %s: %s", repos_file, exc)
        return mapping

    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        mapping[bundle_stem_from_url(url)] = repo_leaf_from_url(url)
    return mapping
