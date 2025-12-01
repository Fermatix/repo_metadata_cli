"""Structured configuration loader using TOML."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .config import DEFAULT_CONFIG_FILE

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover - fallback for Python 3.10
    import tomli as tomllib  # type: ignore
import tomli_w  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class FilesSettings:
    allowed_extensions: Optional[Set[str]] = None
    allowed_filenames: Optional[Set[str]] = None


@dataclass
class TreeSitterSettings:
    grammar_repos: Optional[List[str]] = None
    language_packages: List[str] = field(default_factory=list)
    extension_language_map: Dict[str, str] = field(default_factory=dict)
    lang_func_node_types: Dict[str, Set[str]] = field(default_factory=dict)
    language_repo_map: Dict[str, str] = field(default_factory=dict)
    vendor_dir: Path = Path("vendor")


@dataclass
class AppSettings:
    files: FilesSettings = field(default_factory=FilesSettings)
    tree_sitter: TreeSitterSettings = field(default_factory=TreeSitterSettings)


def _parse_list(raw) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return None


def _parse_str_dict(raw) -> Optional[Dict[str, str]]:
    if raw is None or not isinstance(raw, dict):
        return None
    parsed: Dict[str, str] = {}
    for k, v in raw.items():
        if k is None or v is None:
            continue
        key = str(k).strip()
        val = str(v).strip()
        if key and val:
            parsed[key] = val
    return parsed


def _parse_str_set_dict(raw) -> Optional[Dict[str, Set[str]]]:
    if raw is None or not isinstance(raw, dict):
        return None
    parsed: Dict[str, Set[str]] = {}
    for k, v in raw.items():
        if k is None or v is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        if isinstance(v, list):
            parsed[key] = {str(item).strip() for item in v if str(item).strip()}
    return parsed


def load_app_settings(config_file: Optional[Path]) -> AppSettings:
    """
    Load application settings from a TOML file. Missing file yields defaults.
    Paths are resolved relative to the config file location (or CWD).
    """
    cfg_path = resolve_config_path(config_file)
    files_settings = FilesSettings()
    tree_sitter_settings = TreeSitterSettings()

    if cfg_path.exists():
        try:
            with cfg_path.open("rb") as f:
                data = tomllib.load(f)
        except Exception as exc:
            logger.warning("Failed to load config file %s (%s); using defaults.", cfg_path, exc)
            data = {}
    else:
        data = {}
    
    files_data = data.get("files", {}) if isinstance(data, dict) else {}
    ts_data = data.get("tree_sitter", {}) if isinstance(data, dict) else {}

    # Files
    allowed_ext = files_data.get("allowed_extensions")
    if isinstance(allowed_ext, list):
        files_settings.allowed_extensions = {
            ext if ext.startswith(".") else "." + ext
            for ext in allowed_ext
            if str(ext).strip()
        }

    allowed_names = files_data.get("allowed_filenames")
    if isinstance(allowed_names, list):
        files_settings.allowed_filenames = {str(name).strip() for name in allowed_names if str(name).strip()}
    else:
        # Fallback to common build filenames when not specified.
        files_settings.allowed_filenames = {"Makefile", "Dockerfile", "docker-compose.yml", "CMakeLists.txt"}

    # Tree-sitter
    grammar_repos = _parse_list(ts_data.get("grammar_repos"))
    if grammar_repos:
        tree_sitter_settings.grammar_repos = grammar_repos

    vendor_dir = ts_data.get("vendor_dir")
    if vendor_dir:
        p = Path(str(vendor_dir))
        tree_sitter_settings.vendor_dir = p if p.is_absolute() else (cfg_path.parent / p)

    language_packages = _parse_list(ts_data.get("language_packages"))
    if language_packages:
        tree_sitter_settings.language_packages = language_packages

    ext_map = _parse_str_dict(ts_data.get("extension_language_map"))
    if ext_map:
        # Normalize extensions to start with dot
        tree_sitter_settings.extension_language_map = {
            (k.lower() if k.startswith(".") else f".{k.lower()}"): v
            for k, v in ext_map.items()
        }
    else:
        raise ValueError(
            f"extension_language_map must be specified in [tree_sitter] section of TOML ({cfg_path})."
        )

    func_nodes = _parse_str_set_dict(ts_data.get("lang_func_node_types"))
    if func_nodes:
        tree_sitter_settings.lang_func_node_types = func_nodes
    else:
        raise ValueError(
            f"lang_func_node_types must be specified in [tree_sitter] section of TOML ({cfg_path})."
        )

    language_repo_map = _parse_str_dict(ts_data.get("language_repo_map"))
    if language_repo_map:
        tree_sitter_settings.language_repo_map = language_repo_map

    return AppSettings(files=files_settings, tree_sitter=tree_sitter_settings)


def resolve_config_path(config_file: Optional[Path]) -> Path:
    cfg_path = config_file or DEFAULT_CONFIG_FILE
    return cfg_path if cfg_path.is_absolute() else Path.cwd() / cfg_path


def load_config_data(config_path: Path) -> Dict:
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}
    return {}


def save_config_data(config_path: Path, data: Dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("wb") as f:
        tomli_w.dump(data, f)


def update_extensions_config(
    config_file: Optional[Path],
    allowed_extensions: List[str],
    extension_language_map: Dict[str, str],
    language_repo_map: Dict[str, str],
) -> None:
    """
    Persist allowed_extensions and extension/language mappings into the TOML config, preserving other fields.
    """
    cfg_path = resolve_config_path(config_file)
    data = load_config_data(cfg_path)

    if "files" not in data or not isinstance(data.get("files"), dict):
        data["files"] = {}

    data["files"]["allowed_extensions"] = allowed_extensions
    if "tree_sitter" not in data or not isinstance(data.get("tree_sitter"), dict):
        data["tree_sitter"] = {}

    data["tree_sitter"]["extension_language_map"] = extension_language_map
    data["tree_sitter"]["language_repo_map"] = language_repo_map
    save_config_data(cfg_path, data)
