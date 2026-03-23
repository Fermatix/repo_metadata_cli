"""Fetch git repositories and create bundle files from a repos list."""

from __future__ import annotations

import logging
import os
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Optional

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


def fetch_bundles(
    repos_file: Path,
    bundles_dir: Path,
    mirrors_dir: Path,
    ok_file: Path,
    gitlab_token: Optional[str] = None,
) -> None:
    """
    Run the fetch_bundles.sh script to mirror each repo and create *.bundle files.

    Args:
        repos_file:   Text file with one repository URL per line.
        bundles_dir:  Directory where *.bundle files will be written.
        mirrors_dir:  Directory used for bare-mirror clones (intermediate state).
        ok_file:      File that will receive successfully processed repo URLs.
        gitlab_token: Optional GitLab/GitHub personal access token injected as
                      GITLAB_TOKEN environment variable so private repos are
                      accessible via HTTPS credential helpers.
    """
    script = _script_path()
    if not script.exists():
        raise FileNotFoundError(f"fetch_bundles.sh not found at {script}")

    bundles_dir.mkdir(parents=True, exist_ok=True)
    mirrors_dir.mkdir(parents=True, exist_ok=True)
    ok_file.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if gitlab_token:
        env["GITLAB_TOKEN"] = gitlab_token

    cmd = [
        "bash",
        str(script),
        str(repos_file),
        str(mirrors_dir),
        str(bundles_dir),
        str(ok_file),
    ]
    logger.info("Running fetch script: %s", " ".join(cmd))

    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"fetch_bundles.sh exited with code {result.returncode}. "
            "Check the output above for details."
        )

    logger.info("Bundle fetch complete. Bundles written to %s", bundles_dir)
