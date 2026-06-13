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


def bundle_stem_from_url(url: str) -> str:
    """Reproduce fetch_bundles.sh `repo_only_name`: the sanitized last path segment.

    Must stay in sync with src/repo_metadata_cli/scripts/fetch_bundles.sh so the
    stem matches the on-disk *.bundle filename (== RepoContext.bundle_name).
    """
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    s = re.sub(r"^[a-zA-Z]+://", "", s)   # strip scheme
    s = s.replace(":", "/", 1)            # scp-like host:path → host/path
    s = re.sub(r"^[^@/]+@", "", s)        # strip leading user@
    path = s.split("/", 1)[1] if "/" in s else s
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

    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        stem = bundle_stem_from_url(url)
        mapping[stem] = parse_partner_name(url) or DEFAULT_PARTNER
    return mapping


def build_org_map(repos_file: Path) -> Dict[str, str]:
    """Build {bundle_stem -> repo_org} from a repos.txt URL list.

    The org is the full namespace path of each repo URL (see parse_repo_org).
    Blank lines and comments (#) are skipped; URLs with no namespace are omitted.

    NOTE: bundles are named after the leaf segment only (see bundle_stem_from_url),
    so when two URLs share a leaf but live under different namespaces (e.g.
    ".../prima-finance/backend" and ".../ultima-finance/backend") the stem
    collides and only the last org wins. Such collisions are logged as warnings:
    repo_org is unreliable for those repos unless bundles are named full-path.
    """
    mapping: Dict[str, str] = {}
    collisions: Dict[str, set] = {}
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
        stem = bundle_stem_from_url(url)
        if stem in mapping and mapping[stem] != org:
            collisions.setdefault(stem, {mapping[stem]}).add(org)
        mapping[stem] = org

    if collisions:
        logger.warning(
            "repo_org: %d bundle name(s) map to multiple namespaces (leaf-name "
            "collision) — repo_org is unreliable for these. Examples: %s",
            len(collisions),
            "; ".join(f"{k} -> {sorted(v)}" for k, v in list(collisions.items())[:5]),
        )
    return mapping
