"""Tree-sitter language build/load helpers."""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from tree_sitter import Parser
from tree_sitter_language_pack import get_language, get_parser
from .config import TreeSitterConfig

logger = logging.getLogger(__name__)


class TreeSitterManager:
    """
    Manages building/loading Tree-sitter grammars and provides parsers per extension.
    """

    def __init__(self, config: TreeSitterConfig | None = None) -> None:
        self.config = config or TreeSitterConfig()
        self._languages: Dict[str, object] = {}
        self._parsers: Dict[str, Parser] = {}

    def _ensure_languages(self) -> None:
        needed_langs = sorted(set(self.config.extension_language_map.values()))

        for lang in needed_langs:
            if lang in self._languages:
                continue
            try:
                self._languages[lang] = get_language(lang)
                self._parsers[lang] = get_parser(lang)
            except Exception as exc:
                logger.warning("Failed to initialize Tree-sitter language %s: %s", lang, exc)
                continue


    def parser_for_suffix(self, suffix: str) -> Optional[Tuple[Parser, set[str]]]:
        """
        Returns a (Parser, func_node_types) tuple for a file suffix, or None if unavailable.
        """
        self._ensure_languages()

        lang_name = self.config.extension_language_map.get(suffix.lower())
        if not lang_name:
            logger.debug("No language mapping configured for suffix %s", suffix)
            return None

        func_node_types = self.config.lang_func_node_types.get(lang_name)
        if not func_node_types:
            logger.debug("No function node types configured for language %s", lang_name)
            return None
        parser = self._parsers.get(lang_name)
        if parser is None:
            try:
                parser = get_parser(lang_name)
                self._parsers[lang_name] = parser
            except Exception as exc:
                logger.warning("Failed to create parser for %s: %s", lang_name, exc)
                return None

        return parser, func_node_types
