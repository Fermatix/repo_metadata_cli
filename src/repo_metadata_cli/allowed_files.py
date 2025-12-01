"""Allowed filenames/extensions configuration and filtering logic."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Set

from .config import AllowedFilesConfig
from .settings import load_app_settings

logger = logging.getLogger(__name__)


class AllowedFiles:
    """
    Lazily loads allowed extensions/filenames from disk, creating default files if missing.
    Unknown extensions are ignored; only whitelisted extensions/filenames are treated as code.
    """

    def __init__(self, config: AllowedFilesConfig | None = None) -> None:
        self.config = config or AllowedFilesConfig()
        self._settings = load_app_settings(self.config.config_file)
        self._allowed_extensions: Optional[set[str]] = None
        self._allowed_filenames: Optional[set[str]] = None
        self._default_code_extensions: Set[str] = self._compute_default_extensions()
        self._default_code_filenames: Set[str] = self._compute_default_filenames()

    def _compute_default_extensions(self) -> Set[str]:
        # TOML overrides
        if self._settings.files.allowed_extensions:
            return {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in self._settings.files.allowed_extensions
            }

        ext_map = self._settings.tree_sitter.extension_language_map
        if ext_map:
            return {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in ext_map.keys()
            }

        raise ValueError("allowed_extensions missing; set [files.allowed_extensions] or tree_sitter.extension_language_map in TOML.")

    def _compute_default_filenames(self) -> Set[str]:
        if self._settings.files.allowed_filenames:
            return set(self._settings.files.allowed_filenames)
        logger.warning("allowed_filenames missing in config; falling back to empty set.")
        return set()

    def _load_allowed_extensions(self) -> set[str]:
        # Purely in-memory; defaults are computed from TOML or grammars.
        return set(self._default_code_extensions)

    def _load_allowed_filenames(self) -> set[str]:
        return set(self._default_code_filenames)

    def _ensure_loaded(self) -> None:
        if self._allowed_extensions is None:
            self._allowed_extensions = self._load_allowed_extensions()
        if self._allowed_filenames is None:
            self._allowed_filenames = self._load_allowed_filenames()

    def is_code_path(self, path: str | Path) -> bool:
        """
        Check whether the path is considered "code" by extension or filename.
        """
        self._ensure_loaded()
        p = Path(path)

        if p.name in (self._allowed_filenames or set()):
            return True

        suffix = p.suffix.lower()
        if suffix and suffix in (self._allowed_extensions or set()):
            return True
        return False
