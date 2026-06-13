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
    language_packages: List[str] = field(default_factory=list)
    extension_language_map: Dict[str, str] = field(default_factory=dict)
    lang_func_node_types: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class MetricsSettings:
    dep_dirs: List[str] = field(
        default_factory=lambda: ["vendor", "node_modules", "bower_components"]
    )
    scc_exclude_dirs: List[str] = field(
        default_factory=lambda: ["node_modules", "vendor", "dist", "build", "bower_components"]
    )
    autogen_dirs: List[str] = field(
        default_factory=lambda: [
            "generated", "migrations", "__generated__",
            ".next", ".nuxt", "out",
        ]
    )


@dataclass
class AppSettings:
    files: FilesSettings = field(default_factory=FilesSettings)
    tree_sitter: TreeSitterSettings = field(default_factory=TreeSitterSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    # Optional pre-computed PR cache: {bundle_stem -> {total_pr, reviewed_pr, url}}
    pr_cache: Dict[str, dict] = field(default_factory=dict)
    # Optional {bundle_stem -> partner_name} map, built from a repos.txt URL list.
    partner_map: Dict[str, str] = field(default_factory=dict)
    # Optional {bundle_stem -> repo_org} map (full namespace path), same source.
    org_map: Dict[str, str] = field(default_factory=dict)
    # Optional {bundle_stem -> repo_leaf} map: short repo name, since bundles are
    # named by the full path. Lets repo_name stay the leaf. Same source.
    name_map: Dict[str, str] = field(default_factory=dict)


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


def _validate_metrics_config(metrics: MetricsSettings) -> None:
    # Dirs in both autogen_dirs and scc_exclude_dirs are valid: they are listed
    # as autogen patterns per spec but contribute 0 to autogen_loc because the
    # scoping filter restricts the autogen scan to the logical_loc file set.
    missing = set(metrics.dep_dirs) - set(metrics.scc_exclude_dirs)
    if missing:
        logger.warning(
            "Config error: dep_dirs entries %s are absent from scc_exclude_dirs — "
            "dependency code will be double-counted in logical_loc.",
            sorted(missing),
        )


def load_app_settings(config_file: Optional[Path]) -> AppSettings:
    """Load application settings from a TOML file. Missing file yields defaults."""
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
        files_settings.allowed_filenames = {"Makefile", "Dockerfile", "docker-compose.yml", "CMakeLists.txt"}

    # Tree-sitter
    language_packages = _parse_list(ts_data.get("language_packages"))
    if language_packages:
        tree_sitter_settings.language_packages = language_packages

    ext_map = _parse_str_dict(ts_data.get("extension_language_map"))
    if ext_map:
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

    metrics_data = data.get("metrics", {}) if isinstance(data, dict) else {}
    metrics_settings = MetricsSettings()
    if isinstance(metrics_data.get("dep_dirs"), list):
        metrics_settings.dep_dirs = [str(x) for x in metrics_data["dep_dirs"] if str(x).strip()]
    if isinstance(metrics_data.get("scc_exclude_dirs"), list):
        metrics_settings.scc_exclude_dirs = [str(x) for x in metrics_data["scc_exclude_dirs"] if str(x).strip()]
    if isinstance(metrics_data.get("autogen_dirs"), list):
        metrics_settings.autogen_dirs = [str(x) for x in metrics_data["autogen_dirs"] if str(x).strip()]

    _validate_metrics_config(metrics_settings)

    return AppSettings(
        files=files_settings,
        tree_sitter=tree_sitter_settings,
        metrics=metrics_settings,
    )


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
) -> None:
    """Persist allowed_extensions and extension_language_map into the TOML config."""
    cfg_path = resolve_config_path(config_file)
    data = load_config_data(cfg_path)

    if "files" not in data or not isinstance(data.get("files"), dict):
        data["files"] = {}
    data["files"]["allowed_extensions"] = allowed_extensions

    if "tree_sitter" not in data or not isinstance(data.get("tree_sitter"), dict):
        data["tree_sitter"] = {}
    data["tree_sitter"]["extension_language_map"] = extension_language_map

    save_config_data(cfg_path, data)
