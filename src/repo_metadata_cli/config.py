"""Static configuration and defaults for the repo metadata CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, MutableMapping, Optional, Set

# Default to language extensions only; further filtered by grammar list when present.
DEFAULT_CONFIG_FILE = Path("repo_metadata.toml")

# Tokenizer to use for token counting; can be overridden via CLI or env.
DEFAULT_TOKENIZER_ID = os.environ.get("TOKENIZER_ID")


@dataclass
class AllowedFilesConfig:
    """
    Configuration for storing/reading allowed filenames and extensions.
    All paths are resolved relative to the current working directory unless absolute.
    """

    config_file: Path = DEFAULT_CONFIG_FILE
    default_code_filenames: Set[str] = field(default_factory=set)


@dataclass
class TreeSitterConfig:
    """
    Configuration for loading Tree-sitter grammars.
    """

    language_packages: list[str] = field(default_factory=list)
    extension_language_map: Mapping[str, str] = field(default_factory=dict)
    lang_func_node_types: MutableMapping[str, Set[str]] = field(default_factory=dict)
