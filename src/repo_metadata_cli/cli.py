"""Command-line interface for the repo metadata utility."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional, Set

import typer

from .allowed_files import AllowedFiles
from .config import AllowedFilesConfig, DEFAULT_TOKENIZER_ID, TreeSitterConfig
from .analyzer import RepoAnalyzer
from .token_stats import TokenizerProvider
from .tree_sitter_support import TreeSitterManager
from .settings import load_app_settings, update_extensions_config
from .utils import configure_logging

logger = logging.getLogger(__name__)

app = typer.Typer(help="Utilities for computing repository metadata for datasets.")

def _build_analyzer(
    config_file: Path,
    ts_config: TreeSitterConfig,
    skip_tree_sitter: bool,
    tokenizer_id: Optional[str],
) -> RepoAnalyzer:
    allowed_files = AllowedFiles(
        AllowedFilesConfig(
            config_file=config_file,
        )
    )
    ts_manager = None
    if not skip_tree_sitter:
        ts_manager = TreeSitterManager(
            ts_config,
        )
    tokenizer_provider = TokenizerProvider(tokenizer_id) if tokenizer_id else None
    return RepoAnalyzer(
        allowed_files=allowed_files,
        tree_sitter=ts_manager,
        tokenizer_provider=tokenizer_provider,
    )


@app.callback()
def main(
    log_level: str = typer.Option(
        "INFO",
        help="Уровень логирования: DEBUG, INFO, WARNING, ERROR.",
        case_sensitive=False,
    ),
) -> None:
    """
    Глобальная настройка CLI (сейчас — только уровень логирования).
    """
    configure_logging(log_level)
    logger.debug("Log level set to %s", log_level.upper())


@app.command()
def metadata(
    dataset_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, readable=True, help="Directory with *.bundle files."),
    output_csv: Path = typer.Option(Path("repo_metadata.csv"), help="Where to store metadata CSV."),
    config_file: Path = typer.Option(Path("repo_metadata.toml"), help="TOML config file path."),
    skip_tree_sitter: bool = typer.Option(False, help="Skip Tree-sitter metrics (avg function length)."),
) -> None:
    """Compute metadata (no token counts) for all bundles in a dataset directory."""
    settings = load_app_settings(config_file)
    ts_config = TreeSitterConfig(
        extension_language_map=settings.tree_sitter.extension_language_map,
        lang_func_node_types=settings.tree_sitter.lang_func_node_types,
        language_packages=settings.tree_sitter.language_packages,
    )
    analyzer = _build_analyzer(
        config_file=config_file,
        ts_config=ts_config,
        skip_tree_sitter=skip_tree_sitter,
        tokenizer_id=None,
    )
    analyzer.run_metadata_pipeline(dataset_dir, output_csv)


@app.command()
def tokens(
    dataset_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, readable=True, help="Directory with *.bundle files."),
    output_csv: Path = typer.Option(Path("repo_tokens.csv"), help="Where to store token stats CSV."),
    config_file: Path = typer.Option(Path("repo_metadata.toml"), help="TOML config file path."),
    tokenizer_id: Optional[str] = typer.Option(
        DEFAULT_TOKENIZER_ID,
        help="HF tokenizer id to use. Defaults to $TOKENIZER_ID or the config default.",
    ),
) -> None:
    """Compute token statistics for all bundles in a dataset directory."""
    settings = load_app_settings(config_file)
    ts_config = TreeSitterConfig(
        extension_language_map=settings.tree_sitter.extension_language_map,
        lang_func_node_types=settings.tree_sitter.lang_func_node_types,
        language_packages=settings.tree_sitter.language_packages,
    )
    analyzer = _build_analyzer(
        config_file=config_file,
        ts_config=ts_config,
        skip_tree_sitter=True,  # Tree-sitter not required for token counting.
        tokenizer_id=tokenizer_id,
    )
    analyzer.run_tokens_pipeline(dataset_dir, output_csv)


@app.command()
def merge(
    metadata_csv: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Path to metadata CSV."),
    tokens_csv: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Path to tokens CSV."),
    output_csv: Path = typer.Option(Path("repo_metadata_with_tokens.csv"), help="Where to write merged CSV."),
) -> None:
    """Merge metadata and token stats into a single CSV."""
    RepoAnalyzer.merge_metadata_and_tokens(metadata_csv, tokens_csv, output_csv)


@app.command("fetch-grammars")
def fetch_grammars(
    config_file: Path = typer.Option(
        Path("repo_metadata.toml"),
        help="TOML config file path for grammar_repos.",
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
            logger.info("Installing language package %s via uv pip ...", pkg)
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
            else:
                logger.debug("Successfully installed %s", pkg)
    except FileNotFoundError:
        logger.error("uv is not available on PATH; please install uv to fetch grammars.")
        raise typer.Exit(code=1)


@app.command("refresh-allowed")
def refresh_allowed_files(
    config_file: Path = typer.Option(
        Path("repo_metadata.toml"), help="TOML config file path."
    ),
) -> None:
    """
    Update allowed_extensions and extension maps in the TOML config.
    """
    settings = load_app_settings(config_file)
    ext_map: Dict[str, str] = dict(settings.tree_sitter.extension_language_map or {})
    if not ext_map:
        logger.error("extension_language_map is empty; please populate it in TOML.")
        raise typer.Exit(code=1)

    allowed_exts = sorted(ext_map.keys())
    update_extensions_config(config_file, allowed_exts, ext_map, {})
    logger.info("Updated allowed_extensions in %s", config_file)


if __name__ == "__main__":
    app()
