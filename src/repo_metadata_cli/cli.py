"""Command-line interface for the repo metadata utility."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional

import typer

from .allowed_files import AllowedFiles
from .config import AllowedFilesConfig, TreeSitterConfig
from .fetcher import fetch_bundles
from .pipeline import run_metadata_pipeline
from .settings import load_app_settings, update_extensions_config
from .tree_sitter_support import TreeSitterManager
from .utils import configure_logging

logger = logging.getLogger(__name__)

app = typer.Typer(help="Utilities for computing repository metadata for datasets.")


def _build_allowed_files(config_file: Path) -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=config_file))


def _build_ts_manager(
    ts_config: TreeSitterConfig, skip_tree_sitter: bool
) -> Optional[TreeSitterManager]:
    if skip_tree_sitter:
        return None
    return TreeSitterManager(ts_config)


@app.callback()
def main(
    log_level: str = typer.Option(
        "INFO",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
        case_sensitive=False,
    ),
) -> None:
    configure_logging(log_level)
    logger.debug("Log level set to %s", log_level.upper())


@app.command()
def metadata(
    dataset_path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=True,
        readable=True,
        help="Directory with *.bundle files, or a .txt file with repository URLs (one per line).",
    ),
    output_csv: Path = typer.Option(Path("repo_metadata.csv"), "--output-csv", help="Where to store metadata CSV."),
    config_file: Path = typer.Option(Path("repo_metadata.toml"), help="TOML config file path."),
    skip_tree_sitter: bool = typer.Option(False, help="Skip Tree-sitter metrics (docstring ratio, avg function length)."),
    bundles_dir: Path = typer.Option(
        Path("./tmp/bundles"),
        help="Where to store fetched *.bundle files (only used when dataset_path is a .txt file).",
    ),
    mirrors_dir: Path = typer.Option(
        Path("./tmp/mirrors"),
        help="Where to store bare-mirror clones (only used when dataset_path is a .txt file).",
    ),
    ok_file: Path = typer.Option(
        Path("./tmp/fetched_repos.txt"),
        help="File that records successfully fetched repo URLs (only used when dataset_path is a .txt file).",
    ),
    gitlab_token: Optional[str] = typer.Option(
        None,
        "--gitlab-token",
        envvar="GITLAB_TOKEN",
        help="GitLab personal access token — used both for fetching private repos and for PR enrichment.",
        show_default=False,
    ),
    github_token: Optional[str] = typer.Option(
        None,
        "--github-token",
        envvar="GITHUB_TOKEN",
        help="GitHub personal access token for PR enrichment (needs repo read scope).",
        show_default=False,
    ),
    pr_cache: Optional[Path] = typer.Option(
        None,
        "--pr-cache",
        help="Path to a JSON PR cache file.  When a GitLab or GitHub token is also provided the "
             "cache is refreshed automatically before the pipeline runs, so a separate "
             "enrich-prs step is not required.",
        show_default=False,
    ),
    gitlab_base_url: str = typer.Option(
        "https://gitlab.com/api/v4",
        "--gitlab-base-url",
        help="GitLab API base URL (override for self-hosted instances, e.g. https://git.example.com/api/v4).",
    ),
) -> None:
    """Fetch bundles, enrich PR counts, and compute metadata — all in one step.

    Pass a .txt file of repository URLs to fetch bundles and auto-enrich PR counts,
    or pass a directory of already-fetched *.bundle files to run the pipeline directly.

    When --pr-cache and a token are provided, PR counts are fetched from the API before
    the pipeline starts.  For GitLab mirror repos, the original project path is resolved
    automatically from each bundle's git history, so the mirror URL in repos.txt does
    not cause zero counts.

    Example (all-in-one):

        repo-metadata metadata repos.txt \\
            --pr-cache pr_cache.json \\
            --output-csv repo_metadata.csv \\
            --gitlab-token $GITLAB_TOKEN
    """
    repos_file: Optional[Path] = None

    if dataset_path.is_file():
        if dataset_path.suffix.lower() != ".txt":
            logger.error("When passing a file, it must be a .txt file with repository URLs.")
            raise typer.Exit(code=1)
        try:
            fetch_bundles(
                repos_file=dataset_path,
                bundles_dir=bundles_dir,
                mirrors_dir=mirrors_dir,
                ok_file=ok_file,
                gitlab_token=gitlab_token,
                github_token=github_token,
            )
        except (FileNotFoundError, RuntimeError) as exc:
            logger.error("Fetch step failed: %s", exc)
            raise typer.Exit(code=1) from exc
        repos_file = dataset_path
        dataset_dir = bundles_dir
    else:
        dataset_dir = dataset_path

    # Auto-enrich PR counts when a cache path + at least one token is given.
    # bundles_dir is passed so mirror GitLab URLs are resolved to original project paths.
    if pr_cache is not None and (gitlab_token or github_token):
        from .pr_enricher import enrich_pr_cache

        effective_repos_file = repos_file  # may be None when dataset_path was a directory
        effective_bundles_dir = bundles_dir if bundles_dir.exists() else None

        if effective_repos_file is not None:
            logger.info("Enriching PR counts (this may take a few minutes for large datasets)…")
            try:
                enrich_pr_cache(
                    repos_file=effective_repos_file,
                    cache_file=pr_cache,
                    bundles_dir=effective_bundles_dir,
                    github_token=github_token,
                    gitlab_token=gitlab_token,
                    gitlab_base_url=gitlab_base_url,
                )
            except Exception as exc:
                logger.warning("PR enrichment failed: %s — continuing with existing cache", exc)
        else:
            logger.info(
                "Skipping PR enrichment: no repos file available "
                "(pass a .txt file as dataset_path to enable auto-enrichment)."
            )

    settings = load_app_settings(config_file)

    if pr_cache is not None:
        if pr_cache.exists():
            try:
                settings.pr_cache = json.loads(pr_cache.read_text(encoding="utf-8"))
                logger.info("Loaded PR cache from %s (%d entries)", pr_cache, len(settings.pr_cache))
            except Exception as exc:
                logger.warning("Could not load PR cache %s: %s — continuing without it", pr_cache, exc)
        else:
            logger.warning("PR cache file not found: %s", pr_cache)

    ts_config = TreeSitterConfig(
        extension_language_map=settings.tree_sitter.extension_language_map,
        lang_func_node_types=settings.tree_sitter.lang_func_node_types,
        language_packages=settings.tree_sitter.language_packages,
    )
    allowed_files = _build_allowed_files(config_file)
    ts_manager = _build_ts_manager(ts_config, skip_tree_sitter)

    run_metadata_pipeline(
        dataset_dir=dataset_dir,
        csv_path=output_csv,
        settings=settings,
        allowed_files=allowed_files,
        ts_manager=ts_manager,
    )


@app.command("fetch-grammars")
def fetch_grammars(
    config_file: Path = typer.Option(
        Path("repo_metadata.toml"),
        help="TOML config file path.",
    ),
) -> None:
    """Install Tree-sitter language packages listed in the TOML config."""
    settings = load_app_settings(config_file)
    packages = settings.tree_sitter.language_packages
    if not packages:
        logger.error("No language_packages specified in config.")
        raise typer.Exit(code=1)
    try:
        for pkg in packages:
            logger.info("Installing %s via uv pip ...", pkg)
            result = subprocess.run(
                ["uv", "pip", "install", pkg],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to install %s: %s",
                    pkg,
                    result.stderr.decode("utf-8", errors="ignore"),
                )
    except FileNotFoundError:
        logger.error("uv is not available on PATH; please install uv to fetch grammars.")
        raise typer.Exit(code=1)


@app.command("refresh-allowed")
def refresh_allowed_files(
    config_file: Path = typer.Option(Path("repo_metadata.toml"), help="TOML config file path."),
) -> None:
    """Update allowed_extensions in the TOML config based on extension_language_map."""
    settings = load_app_settings(config_file)
    ext_map: Dict[str, str] = dict(settings.tree_sitter.extension_language_map or {})
    if not ext_map:
        logger.error("extension_language_map is empty; please populate it in TOML.")
        raise typer.Exit(code=1)
    allowed_exts = sorted(ext_map.keys())
    update_extensions_config(config_file, allowed_exts, ext_map)
    logger.info("Updated allowed_extensions in %s", config_file)


@app.command("enrich-prs")
def enrich_prs(
    repos_file: Path = typer.Argument(
        ...,
        exists=True,
        help="Text file with one repository URL per line (same file used for fetch-bundles).",
    ),
    cache_file: Path = typer.Option(
        Path("pr_cache.json"),
        "--cache-file",
        help="Output JSON cache file.  Existing entries are preserved (resume support).",
    ),
    bundles_dir: Optional[Path] = typer.Option(
        None,
        "--bundles-dir",
        help="Directory containing *.bundle files.  When provided, GitLab mirror repos are "
             "automatically resolved to their original project path by scanning each bundle's "
             "merge-commit history.  Cache entries with total_pr=0 are retried on re-runs.",
        show_default=False,
    ),
    github_token: Optional[str] = typer.Option(
        None,
        "--github-token",
        envvar="GITHUB_TOKEN",
        help="GitHub personal access token (needs repo read scope).  "
             "Without a token, GitHub repos are skipped.",
        show_default=False,
    ),
    gitlab_token: Optional[str] = typer.Option(
        None,
        "--gitlab-token",
        envvar="GITLAB_TOKEN",
        help="GitLab personal access token.  Without a token, GitLab repos are skipped.",
        show_default=False,
    ),
    gitlab_base_url: str = typer.Option(
        "https://gitlab.com/api/v4",
        "--gitlab-base-url",
        help="GitLab API base URL (override for self-hosted instances).",
    ),
) -> None:
    """Pre-fetch reviewed PR counts from GitHub/GitLab and save to a JSON cache.

    Run this command BEFORE the metadata pipeline.  It batches GraphQL requests
    (20 repos per query for GitHub) so 10 000 repos take roughly 500 queries —
    about 6 minutes at the standard 5 000 requests/hour rate limit.

    Existing cache entries are preserved, so the command is safe to re-run and
    will only fetch repos not yet in the cache.  Use --bundles-dir to automatically
    resolve GitLab mirror repos to their original project paths (fixes zero MR counts
    that occur when repos.txt contains mirror URLs rather than the original project URLs).

    Example:

        repo-metadata enrich-prs repos.txt --bundles-dir ./tmp/bundles --cache-file pr_cache.json
        repo-metadata metadata ./bundles --pr-cache pr_cache.json
    """
    from .pr_enricher import enrich_pr_cache

    enrich_pr_cache(
        repos_file=repos_file,
        cache_file=cache_file,
        bundles_dir=bundles_dir,
        github_token=github_token,
        gitlab_token=gitlab_token,
        gitlab_base_url=gitlab_base_url,
    )


if __name__ == "__main__":
    app()
