"""Fetch repositories and create bundle files from a repos list.

Git URLs go through the unchanged ``fetch_bundles.sh`` script; Mercurial URLs go
through the Python ``fetch_hg_bundles`` path.  The VCS for each URL is detected
automatically, so a pure-git repos list behaves exactly as before.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import List, Optional, Tuple

from .vcs.detect import detect_vcs_from_url, strip_vcs_scheme
from .vcs.hg_fetch import fetch_hg_bundles

logger = logging.getLogger(__name__)

_SCRIPT_NAME = "fetch_bundles.sh"


def _script_path() -> Path:
    """Return the path to the bundled fetch_bundles.sh script."""
    pkg_scripts = files("repo_metadata_cli") / "scripts" / _SCRIPT_NAME
    # importlib.resources may return a traversal object; resolve to a real path.
    try:
        return Path(str(pkg_scripts))
    except Exception:
        # Fallback: locate relative to this file (editable installs)
        return Path(__file__).parent / "scripts" / _SCRIPT_NAME


def _partition_urls(repos_file: Path) -> Tuple[List[str], List[str], bool]:
    """Split a repos file into (git_urls, hg_urls, needs_norm) by auto-detecting each URL.

    git URLs are returned with any ``git+`` prefix stripped (the bash script /
    git expect a bare URL); hg URLs are returned verbatim.  ``needs_norm`` is True
    if any line carried an ``hg+``/``git+`` scheme prefix, CR line endings or
    surrounding whitespace — in those cases the original file cannot be passed
    to the bash script as-is.
    """
    git_urls: List[str] = []
    hg_urls: List[str] = []
    # read_bytes, not read_text: universal-newline decoding would hide the \r of
    # CRLF endings. splitlines() consumes it too, so it never shows up in the
    # lines below — but the bash script reads the file raw and would pass the \r
    # to git, which rejects the URL ("Malformed input to a URL function").
    text = repos_file.read_bytes().decode("utf-8", errors="ignore")
    needs_norm = "\r" in text
    for line in text.splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if url != line:
            needs_norm = True
        bare = strip_vcs_scheme(url)
        if bare != url:
            needs_norm = True
        if detect_vcs_from_url(url).name == "hg":
            hg_urls.append(url)
        else:
            git_urls.append(bare)
    return git_urls, hg_urls, needs_norm


def _run_git_script(
    repos_file: Path,
    bundles_dir: Path,
    mirrors_dir: Path,
    ok_file: Path,
    env: dict,
) -> None:
    script = _script_path()
    if not script.exists():
        raise FileNotFoundError(f"fetch_bundles.sh not found at {script}")
    cmd = ["bash", str(script), str(repos_file), str(mirrors_dir), str(bundles_dir), str(ok_file)]
    logger.info("Running fetch script: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"fetch_bundles.sh exited with code {result.returncode}. "
            "Check the output above for details."
        )


def fetch_bundles(
    repos_file: Path,
    bundles_dir: Path,
    mirrors_dir: Path,
    ok_file: Path,
    gitlab_token: Optional[str] = None,
    github_token: Optional[str] = None,
) -> None:
    """
    Mirror each repo and create bundle files (git → *.bundle, hg → *.hgbundle).

    Args:
        repos_file:   Text file with one repository URL per line.
        bundles_dir:  Directory where bundle files will be written.
        mirrors_dir:  Directory used for bare-mirror clones (intermediate state).
        ok_file:      File that will receive successfully processed repo URLs.
        gitlab_token: Optional GitLab personal access token (GITLAB_TOKEN env var).
        github_token: Optional GitHub personal access token (GITHUB_TOKEN env var).
    """
    bundles_dir.mkdir(parents=True, exist_ok=True)
    mirrors_dir.mkdir(parents=True, exist_ok=True)
    ok_file.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if gitlab_token:
        env["GITLAB_TOKEN"] = gitlab_token
    if github_token:
        env["GITHUB_TOKEN"] = github_token

    git_urls, hg_urls, needs_norm = _partition_urls(repos_file)

    # Git: hand the ORIGINAL file to the script only when there are no hg URLs AND
    # no scheme prefixes to strip — that keeps the common all-git case byte-for-byte.
    # Otherwise (hg present, or any git+/hg+ prefix) write a normalized temp file.
    if not hg_urls and not needs_norm:
        _run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env)
    elif git_urls:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="git_repos_", delete=False, encoding="utf-8"
        )
        try:
            tmp.write("\n".join(git_urls) + "\n")
            tmp.close()
            _run_git_script(Path(tmp.name), bundles_dir, mirrors_dir, ok_file, env)
        finally:
            os.unlink(tmp.name)

    # Mercurial: Python-native path (skips itself with a warning if `hg` is absent).
    if hg_urls:
        logger.info("Fetching %d Mercurial repo(s)…", len(hg_urls))
        fetch_hg_bundles(
            urls=hg_urls,
            bundles_dir=bundles_dir,
            mirrors_dir=mirrors_dir,
            ok_file=ok_file,
            gitlab_token=gitlab_token,
            github_token=github_token,
        )

    logger.info("Bundle fetch complete. Bundles written to %s", bundles_dir)
