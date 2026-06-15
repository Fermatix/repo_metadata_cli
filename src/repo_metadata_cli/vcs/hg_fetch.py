"""Fetch Mercurial repositories and create ``*.hgbundle`` files.

Python-native parallel to ``scripts/fetch_bundles.sh`` (git), invoked only for
URLs detected as Mercurial.  The git fetch path / bash script is untouched.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from ..partner import bundle_stem_from_url
from .detect import strip_vcs_scheme
from .mercurial import _hg_env

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 1800  # 30 min per repo


def _safe_name(url: str) -> str:
    """Sanitized full-path name for the mirror dir (mirrors fetch_bundles.sh `safe_name`)."""
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    s = re.sub(r"^[a-zA-Z]+://", "", s)   # strip scheme
    s = s.replace(":", "/", 1)            # scp-like host:path → host/path
    s = re.sub(r"^[^@/]+@", "", s)        # strip leading user@
    path = s.split("/", 1)[1] if "/" in s else s
    path = path.lstrip("/")
    path = re.sub(r"/+", "--", path)
    path = re.sub(r"[^A-Za-z0-9.-]+", "-", path)
    path = re.sub(r"-+", "-", path)
    path = re.sub(r"^[.-]+", "", path)
    path = re.sub(r"[.-]+$", "", path)
    return path or "repo"


def inject_hg_token(
    url: str,
    gitlab_token: Optional[str] = None,
    github_token: Optional[str] = None,
) -> str:
    """Embed an auth token into an HTTPS URL, mirroring fetch_bundles.sh `auth_url`."""
    u = strip_vcs_scheme(url)
    if not u.startswith("https://"):
        return u  # ssh / local paths: leave untouched
    rest = u[len("https://"):]
    host = rest.split("/", 1)[0]
    if "github.com" in host and github_token:
        return f"https://x-access-token:{github_token}@{rest}"
    token = gitlab_token or github_token  # heptapod / GitLab use oauth2 style
    if token:
        return f"https://oauth2:{token}@{rest}"
    return u


def fetch_hg_bundles(
    urls: List[str],
    bundles_dir: Path,
    mirrors_dir: Path,
    ok_file: Path,
    gitlab_token: Optional[str] = None,
    github_token: Optional[str] = None,
) -> None:
    """Mirror each Mercurial repo and write a ``<stem>.hgbundle`` per URL."""
    if not urls:
        return
    if shutil.which("hg") is None:
        logger.warning(
            "Found %d Mercurial URL(s) but `hg` is not installed — skipping them. "
            "Install Mercurial with `pip install mercurial` to enable hg support.",
            len(urls),
        )
        return

    bundles_dir.mkdir(parents=True, exist_ok=True)
    mirrors_dir.mkdir(parents=True, exist_ok=True)
    ok_file.parent.mkdir(parents=True, exist_ok=True)
    env = _hg_env()

    for raw_url in urls:
        bare = strip_vcs_scheme(raw_url)
        stem = bundle_stem_from_url(bare)
        bundle_path = bundles_dir / f"{stem}.hgbundle"
        if bundle_path.exists() and bundle_path.stat().st_size > 0:
            logger.info("[%s] already exists, skipping", stem)
            continue

        mirror = mirrors_dir / f"{_safe_name(bare)}.hg"
        auth_url = inject_hg_token(bare, gitlab_token, github_token)

        if mirror.exists():
            fetch_cmd = ["hg", "-R", str(mirror), "pull", auth_url]
        else:
            fetch_cmd = ["hg", "clone", "-U", auth_url, str(mirror)]

        logger.info("[%s] fetching…", stem)
        try:
            fetch = subprocess.run(
                fetch_cmd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_FETCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[%s] hg fetch timed out, skipping", stem)
            continue
        if fetch.returncode != 0:
            logger.warning("[%s] hg fetch failed (private/empty/no access?), skipping", stem)
            continue

        try:
            made = subprocess.run(
                ["hg", "-R", str(mirror), "bundle", "--all", str(bundle_path)],
                env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_FETCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[%s] hg bundle timed out, skipping", stem)
            continue
        if made.returncode == 0 and bundle_path.exists():
            logger.info("[%s] done", stem)
            with ok_file.open("a", encoding="utf-8") as fh:
                fh.write(raw_url + "\n")
        else:
            # `hg bundle --all` returns non-zero for an empty repo ("no changes found").
            logger.warning("[%s] bundle create failed (empty repo?), skipping", stem)
