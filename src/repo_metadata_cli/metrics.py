"""Core repository metrics (without token counts)."""

from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .allowed_files import AllowedFiles
from .tree_sitter_support import TreeSitterManager
from .utils import is_utf8_file, run_cmd

logger = logging.getLogger(__name__)


def _load_json_fragment(text: str) -> Dict[str, Any]:
    """
    cloc иногда пишет лишние строки (предупреждения) в stdout рядом с JSON.
    Берём самый первый JSON-фрагмент по фигурным скобкам и парсим его.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        logger.warning("cloc output is not JSON-like: %s", text[:200])
        return {}
    fragment = text[start : end + 1]
    try:
        return json.loads(fragment)
    except JSONDecodeError as exc:
        logger.warning("Failed to parse cloc JSON: %s", exc)
        return {}


def iter_code_files(repo_dir: Path, allowed_files: AllowedFiles) -> Iterable[Path]:
    """Yield code files under repo_dir respecting allowed extensions/filenames."""
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        if not allowed_files.is_code_path(path):
            continue
        if not is_utf8_file(path):
            continue
        yield path


def detect_license(repo_dir: Path) -> str:
    """
    Naive license detector based on LICENSE*/COPYING* files in the repository root.
    Returns: MIT, APACHE-2.0, GPL, BSD, MPL-2.0, UNLICENSE, UNKNOWN.
    """
    candidates: List[Path] = []
    for p in repo_dir.iterdir():
        if not p.is_file():
            continue
        upper = p.name.upper()
        if upper.startswith("LICENSE") or upper.startswith("COPYING") or "LICENSE" in upper:
            candidates.append(p)

    if not candidates:
        return "UNKNOWN"

    candidates = sorted(candidates, key=lambda x: len(x.name))
    target = candidates[0]

    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "UNKNOWN"

    head = text[:5000]

    def has(*subs: str) -> bool:
        low = head.lower()
        return all(s.lower() in low for s in subs)

    if has("mit license", "permission is hereby granted"):
        return "MIT"
    if has("apache license", "version 2.0"):
        return "APACHE-2.0"
    if has("gnu general public license", "version 3"):
        return "GPL-3.0"
    if has("gnu general public license"):
        return "GPL"
    if has("bsd license") or "redistribution and use in source and binary forms" in head:
        return "BSD"
    if has("mozilla public license", "version 2.0"):
        return "MPL-2.0"
    if "the unlicense" in head.lower():
        return "UNLICENSE"

    name_upper = target.name.upper()
    if "MIT" in name_upper:
        return "MIT"
    if "APACHE" in name_upper:
        return "APACHE-2.0"
    if "GPL" in name_upper:
        return "GPL"
    if "BSD" in name_upper:
        return "BSD"
    if "MPL" in name_upper:
        return "MPL-2.0"

    return "UNKNOWN"


def compute_readme_stats(repo_dir: Path) -> int:
    """documentation_cnt: number of lines across README* files in repo root."""
    total_lines = 0
    for p in repo_dir.iterdir():
        if p.is_file() and p.name.lower().startswith("readme"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            total_lines += len(text.splitlines())
    return total_lines


def get_contributors_count(repo_dir: Path) -> int:
    """Count unique commit authors in repository history."""
    out = run_cmd(["git", "shortlog", "-sne", "--all"], cwd=repo_dir)
    if not out:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def compute_avg_func_length(
    repo_dir: Path, allowed_files: AllowedFiles, ts_manager: TreeSitterManager | None
) -> float:
    """
    Average function length (lines) using Tree-sitter. Only runs if grammars are available.
    """
    if ts_manager is None:
        logger.debug("Tree-sitter manager not configured; skipping avg_func_length.")
        return 0.0

    total_func_lines = 0
    total_funcs = 0

    for path in iter_code_files(repo_dir, allowed_files):
        parser_entry = ts_manager.parser_for_suffix(path.suffix)
        if parser_entry is None:
            continue
        parser, func_node_types = parser_entry

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue

        try:
            tree = parser.parse(text.encode("utf-8"))
        except Exception as exc:
            logger.debug("Tree-sitter failed to parse %s: %s", path, exc)
            continue

        root = tree.root_node
        stack = [root]

        while stack:
            node = stack.pop()
            if node.type in func_node_types:
                start_row = node.start_point[0]
                end_row = node.end_point[0]
                length = end_row - start_row + 1
                if length > 0:
                    total_func_lines += length
                    total_funcs += 1
            stack.extend(node.children)
    if total_funcs == 0:
        return 0.0

    return total_func_lines / total_funcs


def compute_duplication_ratio(repo_dir: Path, allowed_files: AllowedFiles) -> float:
    """
    Approximate duplication ratio:
      duplication_ratio = 1 - (unique lines / total lines)
    """
    total_lines = 0
    uniq: set[int] = set()

    for path in iter_code_files(repo_dir, allowed_files):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            total_lines += 1
            uniq.add(hash(stripped))

    if total_lines == 0:
        return 0.0
    return 1.0 - (len(uniq) / total_lines)


def get_cloc_stats(repo_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Run ``cloc --json`` against the current repository state.
    Returns (summary, langs) where summary = SUM, langs = {lang: stats}.
    """
    # --quiet подавляет прогресс-строки, но cloc всё равно может писать предупреждения в stdout.
    cloc_out = run_cmd(["cloc", "--json", "--quiet", str(repo_dir)])
    if not cloc_out:
        return {}, {}

    cloc_json = _load_json_fragment(cloc_out)
    if not cloc_json:
        return {}, {}

    summary = cloc_json.get("SUM", {}) or {}
    langs = {
        lang: stats
        for lang, stats in cloc_json.items()
        if lang not in ("header", "SUM")
    }
    return summary, langs
