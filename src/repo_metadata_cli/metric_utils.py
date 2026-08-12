"""Core repository metric utilities: SCC, jscpd, infrastructure detection, tree-sitter analysis."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, cast

from .allowed_files import AllowedFiles
from .tree_sitter_support import TreeSitterManager
from .utils import is_utf8_file, run_cmd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared dataclasses (used by tree-sitter traversal)
# ---------------------------------------------------------------------------

@dataclass
class FunctionLengthStats:
    total_func_lines: int = 0
    function_count: int = 0
    class_count: int = 0

    @property
    def average(self) -> float:
        if self.function_count == 0:
            return 0.0
        return self.total_func_lines / self.function_count

    def merge(self, other: "FunctionLengthStats") -> None:
        self.total_func_lines += other.total_func_lines
        self.function_count += other.function_count
        self.class_count += other.class_count


# ---------------------------------------------------------------------------
# File iteration (used by tree-sitter metrics)
# ---------------------------------------------------------------------------

# VCS metadata is never hand-written code; excluded from AST walks even when
# the caller passes no exclude_dirs.
_ITER_VCS_DIRS = frozenset({".git", ".hg", ".svn"})


def compile_exclude_matcher(
    exclude_dirs: Optional[Iterable[str]] = None,
) -> "Callable[[Sequence[str]], bool]":
    """Build a predicate telling whether a path lies in an excluded directory.

    Mirrors scc's ``--exclude-dir`` semantics so every metric sees the same
    file set, whether it comes from scc or from a Python walk:

    * a bare name (``node_modules``) matches that directory at ANY depth;
    * a path with separators (``bitrix/modules``) matches those segments in
      sequence at ANY depth, so an installation nested under ``www/`` or a
      backup copy is caught as well;
    * matching is case-sensitive, like scc — ``Plugins`` (a vendor SDK
      convention) must not silently take out a first-party ``plugins``.

    The predicate takes the DIRECTORY segments of a repo-relative path.
    """
    bare: Set[str] = set()
    chains: List[Tuple[str, ...]] = []
    for raw in exclude_dirs or ():
        entry = str(raw).strip().strip("/")
        if not entry:
            continue
        parts = tuple(p for p in entry.split("/") if p)
        if len(parts) == 1:
            bare.add(parts[0])
        elif parts:
            chains.append(parts)

    def matches(dir_parts: "Sequence[str]") -> bool:
        if any(part in bare for part in dir_parts):
            return True
        for chain in chains:
            span = len(chain)
            for start in range(len(dir_parts) - span + 1):
                if tuple(dir_parts[start : start + span]) == chain:
                    return True
        return False

    return matches


def iter_code_files(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    exclude_dirs: Optional[List[str]] = None,
) -> Iterable[Path]:
    """Yield parseable code files, skipping directories listed in
    ``exclude_dirs`` (bare names and ``a/b`` paths, any depth — see
    ``compile_exclude_matcher``) — pass ``settings.metrics.scc_exclude_dirs``
    to keep tree-sitter metrics on the same vendor-free file set as
    logical_loc.

    Generated files (``*.min.js``, ``*.d.ts``, ``*_pb2.py``, ``*.g.dart`` …)
    and files over 2 MB are skipped via the same rules as the test-coverage
    estimates — one minified bundle or a codegen twin per model would
    otherwise dominate functions_count/classes_count while BB/BE/BF exclude
    it."""
    # late import: coverage_estimate is a leaf module (no metric_utils import)
    from .coverage_estimate import GENERATED_FILE_RE, MAX_FILE_BYTES

    is_excluded = compile_exclude_matcher(list(_ITER_VCS_DIRS) + list(exclude_dirs or ()))
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        if is_excluded(path.relative_to(repo_dir).parts[:-1]):
            continue
        if not allowed_files.is_code_path(path):
            continue
        if GENERATED_FILE_RE.search(path.name):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if not is_utf8_file(path):
            continue
        yield path


# ---------------------------------------------------------------------------
# SCC — Succinct Code Counter
# ---------------------------------------------------------------------------

def _parse_scc_output(out: str) -> dict:
    """Parse `scc --format json` output into a normalized dict."""
    empty: dict = {"languages": [], "total": {"lines": 0, "code": 0, "comment": 0, "blank": 0, "files": 0}}
    if not out:
        return empty
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("["), out.rfind("]")
        if start == -1 or end == -1:
            return empty
        try:
            data = json.loads(out[start : end + 1])
        except json.JSONDecodeError:
            return empty
    if not isinstance(data, list):
        return empty

    languages = []
    total: Dict[str, int] = {"lines": 0, "code": 0, "comment": 0, "blank": 0, "files": 0}
    for lang in data:
        if not isinstance(lang, dict) or not lang.get("Name"):
            continue
        entry = {
            "name": lang["Name"],
            "lines": int(lang.get("Lines", 0)),
            "code": int(lang.get("Code", 0)),
            "comment": int(lang.get("Comment", 0)),
            "blank": int(lang.get("Blank", 0)),
            "files": int(lang.get("Count", 0)),
        }
        languages.append(entry)
        for k in ("lines", "code", "comment", "blank", "files"):
            total[k] += entry[k]  # type: ignore[literal-required]

    return {"languages": languages, "total": total}


# VCS metadata dirs must always be excluded from scc.  Passing --exclude-dir at
# all REPLACES scc's built-in default (.git,.hg,.svn), so once the caller adds
# dependency dirs, scc would otherwise start counting .hg/.git internals — which
# made hg repos diverge from git on identical trees.  Always prepend them.
_SCC_VCS_DIRS: List[str] = [".git", ".hg", ".svn"]


def _scc_exclude_arg(exclude_dirs: Optional[List[str]]) -> List[str]:
    merged = list(dict.fromkeys(_SCC_VCS_DIRS + list(exclude_dirs or [])))
    return ["--exclude-dir", ",".join(merged)]


def get_scc_stats(
    repo_dir: Path,
    exclude_dirs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run `scc --format json` and return parsed language statistics.

    Pass `exclude_dirs` to skip dependency/build directories (Logical LOC mode).
    VCS metadata dirs (.git/.hg/.svn) are always excluded.  Returns empty stats
    if scc is not installed.
    """
    cmd = ["scc", "--format", "json", "--no-complexity"]
    cmd += _scc_exclude_arg(exclude_dirs)
    cmd.append(str(repo_dir))
    return _parse_scc_output(run_cmd(cmd) or "")


# ---------------------------------------------------------------------------
# Auto-generated LOC (column H)
# ---------------------------------------------------------------------------

_DEP_DIR_NAMES: Set[str] = {"vendor", "node_modules", "bower_components"}

_AUTOGEN_DIRS: Set[str] = {
    "generated", "migrations", "__generated__",
    ".next", ".nuxt", "out",
    # Generator output convention (gSOAP, protoc, Android legacy gen/)
    "gen",
    # Unity IL2CPP transpiler output (C# -> C++), often millions of committed
    # lines. Matched case-insensitively (dir names are compared via .lower()),
    # so these entries must be lowercase.
    "il2cppoutput", "il2cppoutputproject",
}

# Consecutive path-segment pairs that mark generated trees where the leaf name
# alone would be too generic to blacklist ("versions" is a normal dir name;
# "alembic/versions" is always generated migration files).
_AUTOGEN_DIR_PAIRS: List[Tuple[str, str]] = [
    ("alembic", "versions"),
]

# Marker directories/files dropped by code generators next to their output:
# their presence marks every file in that directory (and below) as generated.
_GENERATOR_FOOTPRINTS: List[str] = [
    ".openapi-generator",
    ".openapi-generator-ignore",
    ".swagger-codegen",
    ".swagger-codegen-ignore",
]

_AUTOGEN_NAME_RE: List[re.Pattern] = [
    re.compile(r".*_generated\..+$", re.IGNORECASE),
    re.compile(r".*_pb2\.py$", re.IGNORECASE),
    re.compile(r".*\.pb\.go$", re.IGNORECASE),
    re.compile(r".*\.min\.js$", re.IGNORECASE),
    re.compile(r".*\.min\.css$", re.IGNORECASE),
    re.compile(r".*\.bundle\.js$", re.IGNORECASE),
]

_AUTOGEN_EXACT_NAMES: Set[str] = {
    "package-lock.json", "yarn.lock", "cargo.lock", "go.sum",
}

# Substrings that mark a file header as generator output.  "generated by"
# deliberately subsumes the many generator phrasings ("code generated by",
# "auto generated by", "automatically generated by", …); scaffold files that
# use the same words but are meant to be hand-edited are rescued by
# _AUTOGEN_HEADER_WHITELIST below.  The dump phrases catch database dumps
# (mysqldump / pg_dump / phpMyAdmin / SQLyog / SSMS "Script Date:").
_AUTOGEN_HEADER_MARKERS: List[str] = [
    "generated by",
    "generated using",
    "auto-generated",
    "do not edit",
    "do not modify",
    "@generated",
    "mysql dump",
    "postgresql database dump",
    "phpmyadmin sql dump",
    "sqlyog",
    "script date:",
]

# Scaffold headers: the file was CREATED by a tool but is meant to be edited by
# hand afterwards (framework settings/config skeletons, translation skeletons).
# A whitelist hit overrides any marker hit.
_AUTOGEN_HEADER_WHITELIST: List[str] = [
    "django-admin startproject",
    "rails generate rspec:install",
    "rspec --init",
    "you may modify this file by translating",
]

# How much of the file head the marker scan covers.  15 lines / 2 KB: several
# generators (e.g. OpenAPI Generator TS templates) place their banner after a
# licence block, past the first few lines.
_HEADER_SCAN_BYTES = 2048
_HEADER_SCAN_LINES = 15

# Size/shape heuristics: a file whose average line exceeds this many bytes is
# minified or a data dump, not hand-written code; same for absurd line counts.
# Files below the size floor are skipped — too small to distort metrics.
_AVG_LINE_BYTES_LIMIT = 255
_LINE_COUNT_LIMIT = 50_000
_SHAPE_CHECK_MIN_BYTES = 16_384


def _read_header(file_path: Path) -> str:
    """First _HEADER_SCAN_LINES lines (bounded by _HEADER_SCAN_BYTES), lowercased."""
    try:
        with file_path.open("rb") as fh:
            header = fh.read(_HEADER_SCAN_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return ""
    return "\n".join(header.splitlines()[:_HEADER_SCAN_LINES]).lower()


def _count_lines(file_path: Path) -> int:
    """Newline count via streamed binary reads (no full-file decode)."""
    lines = 0
    try:
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                lines += chunk.count(b"\n")
    except OSError:
        return 0
    return lines


def _has_generated_shape(file_path: Path) -> bool:
    """True for minified/dump-shaped files: huge average line length or an
    absurd line count.  Only files ≥ _SHAPE_CHECK_MIN_BYTES are examined."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return False
    if size < _SHAPE_CHECK_MIN_BYTES:
        return False
    lines = _count_lines(file_path)
    if lines <= 0:
        # a file this big with no newline at all is one minified line
        return True
    return size / lines > _AVG_LINE_BYTES_LIMIT or lines > _LINE_COUNT_LIMIT


_SQL_INSERT_LINE_LIMIT = 50
_SQL_COPY_LINE_LIMIT = 3
_SQL_SCAN_BYTES = 5 << 20  # dumps show their INSERT/COPY blocks early


def _is_sql_dump(file_path: Path) -> bool:
    """True when a .sql file is a database dump rather than hand-written DDL/
    migrations: a dump-tool banner, a dump-like shape (see
    _has_generated_shape), or row-data blocks — ≥50 INSERT INTO lines or ≥3
    COPY … FROM stdin blocks (pg_dump)."""
    header = _read_header(file_path)
    if any(m in header for m in _AUTOGEN_HEADER_MARKERS):
        return True
    if _has_generated_shape(file_path):
        return True
    inserts = copies = 0
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
            read = 0
            for line in fh:
                read += len(line)
                if read > _SQL_SCAN_BYTES:
                    break
                stripped = line.lstrip().lower()
                if stripped.startswith("insert into"):
                    inserts += 1
                    if inserts >= _SQL_INSERT_LINE_LIMIT:
                        return True
                elif stripped.startswith("copy ") and "from stdin" in stripped:
                    copies += 1
                    if copies >= _SQL_COPY_LINE_LIMIT:
                        return True
    except OSError:
        pass
    return False


def _dir_has_generator_footprint(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in _GENERATOR_FOOTPRINTS)


def _is_autogen_file(file_path: Path, repo_root: Path, autogen_dirs: Set[str]) -> bool:
    rel = file_path.relative_to(repo_root)
    parts_lower = [part.lower() for part in rel.parts[:-1]]
    for part in parts_lower:
        if part in autogen_dirs:
            return True
    for first, second in _AUTOGEN_DIR_PAIRS:
        for i in range(len(parts_lower) - 1):
            if parts_lower[i] == first and parts_lower[i + 1] == second:
                return True
    if rel.name.lower() in _AUTOGEN_EXACT_NAMES:
        return True
    for pattern in _AUTOGEN_NAME_RE:
        if pattern.match(rel.name):
            return True
    # Generator footprint markers in any ancestor directory (up to repo root)
    directory = file_path.parent
    while True:
        if _dir_has_generator_footprint(directory):
            return True
        if directory == repo_root or directory.parent == directory:
            break
        directory = directory.parent
    header = _read_header(file_path)
    if header:
        if any(w in header for w in _AUTOGEN_HEADER_WHITELIST):
            return False
        if any(m in header for m in _AUTOGEN_HEADER_MARKERS):
            return True
    if file_path.suffix.lower() == ".sql" and _is_sql_dump(file_path):
        return True
    return _has_generated_shape(file_path)


def _parse_scc_by_file_output(out: str) -> List[Dict[str, Any]]:
    """Parse `scc --format json --by-file` output into [{path, code, language}]."""
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("["), out.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(out[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in cast(List[Any], data):
        if not isinstance(item, dict):
            continue
        lang_entry = cast(Dict[str, Any], item)
        language = str(lang_entry.get("Name") or "")
        files_raw = lang_entry.get("Files")
        if not isinstance(files_raw, list):
            continue
        for fitem in cast(List[Any], files_raw):
            if not isinstance(fitem, dict):
                continue
            file_entry = cast(Dict[str, Any], fitem)
            location = str(file_entry.get("Location") or "")
            code = int(file_entry.get("Code") or 0)
            if location:
                result.append({"path": Path(location), "code": code, "language": language})
    return result


def get_scc_file_stats(
    repo_dir: Path,
    exclude_dirs: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Run `scc --format json --by-file` and return [{path, code}] per file.

    Uses the same --exclude-dir list as logical_loc so the returned file set
    is identical to what scc counts for column G.  Returns [] if scc is
    unavailable.
    """
    cmd = ["scc", "--format", "json", "--by-file", "--no-complexity"]
    cmd += _scc_exclude_arg(exclude_dirs)
    cmd.append(str(repo_dir))
    out = run_cmd(cmd)
    if not out:
        return []
    return _parse_scc_by_file_output(out)


def count_chars_in_files(file_stats: List[Dict[str, Any]]) -> int:
    """Sum of Unicode character counts across the given scc file set.

    `file_stats` is the output of `get_scc_file_stats` (each entry has a `path`).
    Files are read with errors='ignore'; unreadable files are skipped.  Used by
    symbols_count, which must cover exactly the logical_loc file set.
    """
    total = 0
    for entry in file_stats:
        path = Path(entry["path"])
        try:
            total += len(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return total


def get_auto_gen_loc(
    repo_dir: Path,
    autogen_dirs: Optional[Set[str]] = None,
    exclude_dirs: Optional[Set[str]] = None,
) -> int:
    """Count scc Code lines in auto-generated files (spec column H).

    Uses `scc --by-file --exclude-dir` with the same exclusion list as
    logical_loc, so the candidate file set is identical to column G by
    construction.  Autogen detection (directory patterns, filename patterns,
    header markers) is applied to that file set, and the Code values already
    provided by scc are summed — no second scc invocation needed.
    Returns 0 if scc is not installed.
    """
    _dirs = autogen_dirs if autogen_dirs is not None else _AUTOGEN_DIRS
    _excl_list = list(exclude_dirs) if exclude_dirs else []
    file_stats = get_scc_file_stats(repo_dir, exclude_dirs=_excl_list)
    return sum(
        entry["code"]
        for entry in file_stats
        if _is_autogen_file(entry["path"], repo_dir, _dirs)
    )


def get_lang_code_no_autogen(
    repo_dir: Path,
    autogen_dirs: Optional[Set[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Per-language scc Code lines EXCLUDING auto-generated files.

    Same `scc --by-file` invocation and autogen detection as ``get_auto_gen_loc``,
    but aggregated per language over the NON-autogen files. Used so that
    ``primary_language`` / ``lang_distribution`` reflect hand-written source —
    a committed bundle, minified asset, or generated stub no longer decides the
    primary language (e.g. a TypeScript app with a large committed ``.bundle.js``
    is no longer reported as JavaScript). Returns {} if scc is unavailable.
    """
    _dirs = autogen_dirs if autogen_dirs is not None else _AUTOGEN_DIRS
    cmd = ["scc", "--format", "json", "--by-file", "--no-complexity"]
    if exclude_dirs:
        cmd += ["--exclude-dir", ",".join(exclude_dirs)]
    cmd.append(str(repo_dir))
    out = run_cmd(cmd)
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("["), out.rfind("]")
        if start == -1 or end == -1:
            return {}
        try:
            data = json.loads(out[start : end + 1])
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, list):
        return {}
    per_lang: Dict[str, int] = {}
    for item in cast(List[Any], data):
        if not isinstance(item, dict):
            continue
        lang_entry = cast(Dict[str, Any], item)
        name = lang_entry.get("Name")
        files_raw = lang_entry.get("Files")
        if not name or not isinstance(files_raw, list):
            continue
        total = 0
        for fitem in cast(List[Any], files_raw):
            if not isinstance(fitem, dict):
                continue
            file_entry = cast(Dict[str, Any], fitem)
            location = str(file_entry.get("Location") or "")
            if not location or _is_autogen_file(Path(location), repo_dir, _dirs):
                continue
            total += int(file_entry.get("Code") or 0)
        if total > 0:
            per_lang[str(name)] = per_lang.get(str(name), 0) + total
    return per_lang


# Android resource XML is hand-written UI markup: res/<kind>/… where <kind>
# starts with one of these prefixes (layout, layout-land, values-ru, …).
_ANDROID_RES_KIND_PREFIXES: Tuple[str, ...] = (
    "layout", "values", "drawable", "menu", "navigation", "anim",
    "xml", "mipmap", "color", "font", "raw", "transition", "interpolator",
)


def _is_android_res_xml(file_path: Path, repo_root: Path) -> bool:
    """True for XML under an Android res/ tree or an AndroidManifest.xml."""
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        rel = file_path
    if rel.name == "AndroidManifest.xml":
        return True
    parts = rel.parts[:-1]
    for i, part in enumerate(parts[:-1]):
        if part == "res" and parts[i + 1].lower().startswith(_ANDROID_RES_KIND_PREFIXES):
            return True
    return False


def get_clean_logical_loc(
    repo_dir: Path,
    exclude_dirs: Optional[List[str]] = None,
    non_code_languages: Optional[Set[str]] = None,
) -> int:
    """Count scc Code lines of code languages only (column BG, clean_logical_loc).

    Same construction as logical_loc but honest about what counts as code:

    * the file set excludes ``clean_scc_exclude_dirs`` (a superset of the
      logical_loc exclusions covering CMS cores, committed framework copies,
      build outputs);
    * data/config/docs languages (``clean_non_code_languages``) are dropped —
      hand-written markup (HTML/CSS/preprocessors/templates/XAML) stays in;
    * XML counts only where it is hand-written UI markup (Android res/ trees,
      AndroidManifest.xml);
    * SQL counts unless the file is a database dump (see ``_is_sql_dump``).

    Generated files are NOT subtracted here — like logical_loc, the metric
    keeps codegen inside; autogen_loc measures that share separately.
    Returns 0 if scc is unavailable.
    """
    nc_langs = non_code_languages or set()
    total = 0
    for entry in get_scc_file_stats(repo_dir, exclude_dirs=exclude_dirs):
        code = int(entry["code"])
        if code <= 0:
            continue
        language = str(entry.get("language") or "")
        if language == "XML":
            if _is_android_res_xml(Path(entry["path"]), repo_dir):
                total += code
            continue
        if language == "SQL":
            if not _is_sql_dump(Path(entry["path"])):
                total += code
            continue
        if language in nc_langs:
            continue
        total += code
    return total


def get_dependency_dir_loc(repo_dir: Path, dep_dir_names: Optional[Set[str]] = None) -> int:
    """Count scc Code lines in dependency directories (vendor/, node_modules/, bower_components/).

    Excluded from both Logical LOC and Auto-Generated LOC; reported separately as column AE.
    Returns 0 if no dependency directories are present or scc is unavailable.
    Pass `dep_dir_names` to override the default set of dependency directories.
    """
    _names = dep_dir_names if dep_dir_names is not None else _DEP_DIR_NAMES
    # Count dependency LOC the SAME way logical_loc (G) excludes it: scc's
    # --exclude-dir matches a dir name at ANY depth, so AE must too — summing
    # only root-level dep dirs lost nested vendor/node_modules and broke the
    # invariant AE == (LOC dropped from G because of dep dirs).
    file_stats = get_scc_file_stats(repo_dir)  # full tree (VCS dirs already excluded)
    total = 0
    for entry in file_stats:
        path = Path(entry["path"])
        try:
            rel_parts = path.relative_to(repo_dir).parts
        except ValueError:
            rel_parts = path.parts
        if any(part in _names for part in rel_parts[:-1]):
            total += int(entry["code"])
    return total


# ---------------------------------------------------------------------------
# jscpd — duplication detection (column I)
# ---------------------------------------------------------------------------

def run_jscpd(repo_dir: Path) -> float:
    """Run jscpd and return duplication ratio [0, 1]. Returns 0.0 if jscpd unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        ignore_pattern = ",".join([
            # VCS metadata — otherwise jscpd scans .hg revlog data (detected as
            # "D" source) and Mercurial repos diverge from git on identical code.
            "**/.git/**", "**/.hg/**", "**/.svn/**",
            # Directory-based autogen
            "**/vendor/**", "**/node_modules/**", "**/dist/**", "**/build/**",
            "**/__generated__/**", "**/migrations/**", "**/generated/**",
            # Filename pattern autogen
            "**/*_generated.*", "**/*_pb2.py", "**/*.pb.go",
            "**/*.min.js", "**/*.min.css", "**/*.bundle.js",
            # Lock files
            "**/package-lock.json", "**/yarn.lock", "**/Cargo.lock",
            "**/go.sum", "**/poetry.lock", "**/pnpm-lock.yaml",
        ])
        cmd = [
            "jscpd",
            "--min-tokens", "50",
            "--min-lines", "5",
            # jscpd parses a unit-less value as BYTES; "200" skipped every real
            # source file and made duplication_ratio always 0.  Use an explicit
            # unit with generous headroom (autogen/lock files are excluded above).
            "--max-size", "1024mb",
            "--reporters", "json",
            "--output", str(report_dir),
            "--ignore", ignore_pattern,
            "--silent",
            str(repo_dir),
        ]
        run_cmd(cmd, timeout=720)
        report_file = report_dir / "jscpd-report.json"
        if not report_file.exists():
            logger.debug("jscpd report not found for %s; returning 0.0", repo_dir)
            return 0.0
        try:
            report = json.loads(report_file.read_text())
            pct = report.get("statistics", {}).get("total", {}).get("percentage", 0)
            return min(float(pct) / 100.0, 1.0)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Fork detection (column J)
# ---------------------------------------------------------------------------

# Patterns in merge commit messages that indicate upstream pull from a fork's parent.
# e.g. "Merge branch 'main' of github.com:owner/repo into dev"
#      "Merge remote-tracking branch 'upstream/main'"
_FORK_MERGE_RE = re.compile(
    r"merge\s+(?:branch\s+'.+?'\s+of\s+(?:https?://)?(?:github\.com|gitlab\.com|bitbucket\.org)[:/]"
    r"|remote[- ]tracking\s+branch\s+'upstream/)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# PR / MR count (columns P, Q)
# ---------------------------------------------------------------------------

# GitHub standard merge: "Merge pull request #123 from ..."
_GITHUB_PR_RE = re.compile(r"merge pull request #(\d+)", re.IGNORECASE)
# GitHub squash merge: subject ends with "(#123)"
_GITHUB_SQUASH_RE = re.compile(r"\(#(\d+)\)\s*$", re.MULTILINE)
# GitLab MR footer in merge commit body: "See merge request namespace/project!123"
_GITLAB_MR_RE = re.compile(r"see merge request [^\n]*!(\d+)", re.IGNORECASE)


def count_pull_requests(repo_dir: Path) -> Tuple[int, int]:
    """Return (total_pr_count, reviewed_pr_count) detected from git history.

    Detects:
    - GitHub standard merges: "Merge pull request #NNN"
    - GitHub squash merges: subject ends with "(#NNN)"
    - GitLab MRs: merge commit body contains "See merge request ...!NNN"

    reviewed_pr_count is always 0 — requires API access to determine reviews.
    """
    # Full bodies of merge commits (covers GitLab "See merge request" footers
    # and GitHub "Merge pull request" subjects in one pass)
    merge_bodies = run_cmd(["git", "log", "--all", "--merges", "--format=%B"], cwd=repo_dir) or ""
    # All commit subjects (needed for squash-merged GitHub PRs)
    all_subjects = run_cmd(["git", "log", "--all", "--format=%s"], cwd=repo_dir) or ""

    github_prs: Set[str] = set()
    for m in _GITHUB_PR_RE.finditer(merge_bodies):
        github_prs.add(m.group(1))
    for m in _GITHUB_SQUASH_RE.finditer(all_subjects):
        github_prs.add(m.group(1))

    gitlab_mrs: Set[str] = set()
    for m in _GITLAB_MR_RE.finditer(merge_bodies):
        gitlab_mrs.add(m.group(1))

    total = len(github_prs) + len(gitlab_mrs)
    return total, 0


def detect_fork_pct(repo_dir: Path) -> float:
    """Return 1.0 if the repo appears to be a fork, 0.0 otherwise."""
    # 1. Explicit upstream remote in .git/config
    git_config = repo_dir / ".git" / "config"
    if git_config.exists():
        try:
            content = git_config.read_text(encoding="utf-8", errors="ignore").lower()
            if '[remote "upstream"]' in content:
                return 1.0
        except OSError:
            pass

    # 2. refs/remotes/upstream/* exist (upstream was fetched at some point)
    upstream_refs = run_cmd(
        ["git", "for-each-ref", "--format=%(refname)", "refs/remotes/upstream"],
        cwd=repo_dir,
    )
    if upstream_refs and upstream_refs.strip():
        return 1.0

    # 3. Merge commit messages contain external GitHub/GitLab URL (git pull from upstream)
    merge_log = run_cmd(
        ["git", "log", "--all", "--merges", "--format=%s", "--max-count=200"],
        cwd=repo_dir,
    )
    if merge_log and _FORK_MERGE_RE.search(merge_log):
        return 1.0

    return 0.0


# ---------------------------------------------------------------------------
# CI / Deployment (columns R, S)
# ---------------------------------------------------------------------------

_CI_INDICATORS: List[str] = [
    ".github/workflows",
    ".circleci",
    ".travis.yml",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    ".appveyor.yml",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".buildkite",
    "circle.yml",
]


def detect_ci_config(repo_dir: Path) -> bool:
    return any((repo_dir / p).exists() for p in _CI_INDICATORS)


def _read_ci_content(repo_dir: Path) -> str:
    parts: List[str] = []
    for ci in _CI_INDICATORS:
        p = repo_dir / ci
        if p.is_file():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore").lower())
            except OSError:
                pass
        elif p.is_dir():
            for f in p.rglob("*.yml"):
                try:
                    parts.append(f.read_text(encoding="utf-8", errors="ignore").lower())
                except OSError:
                    pass
            for f in p.rglob("*.yaml"):
                try:
                    parts.append(f.read_text(encoding="utf-8", errors="ignore").lower())
                except OSError:
                    pass
    return "\n".join(parts)


# Deploy keywords matched at a word boundary so "ship" no longer fires inside
# "ownership"/"membership"/"relationship"/"township" (\b before "ship" is absent
# in those words).  \w* lets it still catch deploy/deployment, shipped/shipping…
_DEPLOY_KW_RE: re.Pattern = re.compile(r"\b(?:deploy|release|publish|ship)\w*", re.IGNORECASE)


def _nonvendor_rglob(repo_dir: Path, pattern: str) -> List[Path]:
    """rglob results excluding vendored/third-party paths (node_modules, vendor…)."""
    return [p for p in repo_dir.rglob(pattern) if not _is_vendor_path(p, repo_dir)]


def detect_deployment_infra(repo_dir: Path) -> str:
    """Return one of: None / Basic CI / Full CI-CD / Enterprise."""
    enterprise_checks = [
        lambda: bool(_nonvendor_rglob(repo_dir, "*.tf")),
        lambda: bool(_nonvendor_rglob(repo_dir, "Chart.yaml")),
        lambda: bool(_nonvendor_rglob(repo_dir, "deployment.yaml")),
        lambda: (repo_dir / "k8s").is_dir() or (repo_dir / "kubernetes").is_dir(),
        lambda: bool(_nonvendor_rglob(repo_dir, "*.k8s.yml")),
        lambda: bool(_nonvendor_rglob(repo_dir, "*.k8s.yaml")),
    ]
    if any(fn() for fn in enterprise_checks):
        return "Enterprise"

    if detect_ci_config(repo_dir):
        ci_content = _read_ci_content(repo_dir)
        if _DEPLOY_KW_RE.search(ci_content):
            return "Full CI-CD"
        return "Basic CI"

    return "None"


# ---------------------------------------------------------------------------
# Monitoring (column T)
# ---------------------------------------------------------------------------

# Only search in source code files — not CI configs, package.json, etc.
# This avoids false positives from CI pipelines referencing monitoring tools.
_MONITORING_SOURCE_EXTS: Set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".cs", ".rb", ".php", ".rs",
}

# Word-boundary regex patterns for APM tool detection (prevents false positives from
# substrings like "rollbar" inside "scrollbar" or "sentry" inside "NightAttributesEntry").
# Each tuple: (pattern, tool_name). Patterns are matched against lowercased file content.
_MONITORING_APM_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\bsentry_sdk\b'),         "sentry"),       # Python
    (re.compile(r'\bsentry\b'),             "sentry"),       # JS/Go/Java import as standalone word
    (re.compile(r'\bdatadog\b'),            "datadog"),
    (re.compile(r'\bddtrace\b'),            "datadog"),
    (re.compile(r'\bnewrelic\b'),           "newrelic"),
    (re.compile(r'\bnew_relic\b'),          "newrelic"),
    (re.compile(r'\bprometheus_client\b'),  "prometheus_client"),
    (re.compile(r'\bpagerduty\b'),          "pagerduty"),
    (re.compile(r'\bopsgenie\b'),           "opsgenie"),
    (re.compile(r'\bhoneycomb\b'),          "honeycomb"),
    (re.compile(r'\bjaeger\b'),             "jaeger"),
    (re.compile(r'\bopentelemetry\b'),      "opentelemetry"),
    (re.compile(r'@opentelemetry/'),        "opentelemetry"),
    (re.compile(r'\belastic_apm\b'),        "elastic_apm"),
    (re.compile(r'\brollbar\b'),            "rollbar"),       # matches "rollbar.", "import rollbar"
    (re.compile(r'\bbugsnag\b'),            "bugsnag"),
    (re.compile(r'\braygun4'),              "raygun"),        # raygun4net, raygun4py, etc.
    (re.compile(r'raygun\.io'),             "raygun"),
    (re.compile(r'\binstana\b'),            "instana"),
    (re.compile(r'\bdynatrace\b'),          "dynatrace"),
]

_MONITORING_FULL_SRE: Set[str] = {
    "opentelemetry", "jaeger", "honeycomb",
}

# More specific logging patterns (require actual logger instantiation)
_BASIC_LOGGING_PATTERNS: List[str] = [
    "console.log(",            # JS/TS — spec: "console.log statements alone count as Basic"
    "console.error(",          # JS/TS
    "console.warn(",           # JS/TS
    "logging.basicconfig",     # Python
    "logging.getlogger",       # Python
    "logrus.new",              # Go
    "zap.new",                 # Go
    "zap.newproduction",       # Go
    "winston.createlogger",    # Node.js
    "bunyan.createlogger",     # Node.js
    "log4j.getlogger",         # Java
    "logger.getlogger",        # Java
    "logback",                 # Java
    "pino(",                   # Node.js
]


# Directories that contain 3rd-party code — skip when detecting project's own monitoring
_MONITORING_SKIP_DIRS: Set[str] = {
    "node_modules", "vendor", "third_party", "thirdparty", "3rdparty",
    # Unity-specific SDK/asset dirs
    "Packages", "PackageCache",
}


def _is_vendor_path(path: Path, repo_dir: Path) -> bool:
    """Return True if path is inside a known 3rd-party directory."""
    try:
        rel_parts = path.relative_to(repo_dir).parts
    except ValueError:
        return False
    return any(p in _MONITORING_SKIP_DIRS for p in rel_parts)


def detect_monitoring(repo_dir: Path) -> str:
    """Return one of: None / Basic / APM+Alerting / Full SRE."""
    found_apm: Set[str] = set()
    has_basic_logging = False

    for path in repo_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if _is_vendor_path(path, repo_dir):
            continue
        if path.suffix.lower() not in _MONITORING_SOURCE_EXTS:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        for pattern, tool_name in _MONITORING_APM_PATTERNS:
            if tool_name not in found_apm and pattern.search(content):
                found_apm.add(tool_name)
        if not has_basic_logging and any(pat in content for pat in _BASIC_LOGGING_PATTERNS):
            has_basic_logging = True

    if any(t in found_apm for t in _MONITORING_FULL_SRE):
        return "Full SRE"
    if found_apm:
        return "APM+Alerting"
    if has_basic_logging:
        return "Basic"
    return "None"


# ---------------------------------------------------------------------------
# Test suite (column U)
# ---------------------------------------------------------------------------

# Glob patterns for test file discovery (by filename convention)
_TEST_PATTERNS: List[str] = [
    "test_*.py", "*_test.py", "*_test.go", "*.spec.ts", "*.spec.js",
    "*.test.ts", "*.test.js", "*Test.java", "*Spec.java", "*_test.rb",
    "*_spec.rb", "*_test.rs", "*_test.cs", "*Test.cs",
    # React/TSX/JSX conventions
    "*.test.tsx", "*.spec.tsx", "*.test.jsx", "*.spec.jsx",
]

# Compiled-language files require test framework markers in their content.
# Maps suffix → list of lowercased strings that confirm it's a real test file.
_TEST_CONTENT_MARKERS: Dict[str, List[str]] = {
    ".cs": [
        "using nunit.framework",
        "using microsoft.visualstudio.testtools.unittesting",
        "using xunit",
        "using unityengine.testtools",
        "[testfixture]",
        "[testclass]",
        "[test]",
        "[testmethod]",
        "[fact]",       # xUnit
        "[theory]",     # xUnit
    ],
    ".java": [
        "@test",
        "import org.junit",
        "import org.testng",
        "import androidx.test",
    ],
}

_TEST_CONFIG_FILES: List[str] = [
    "pytest.ini", "jest.config.js", "jest.config.ts", "jest.config.mjs",
    ".mocharc.js", ".mocharc.yml", "karma.conf.js", "phpunit.xml",
    "phpunit.xml.dist", "rspec", ".rspec",
]


def _is_real_test_file(path: Path) -> bool:
    """For compiled languages, verify the file actually contains test framework markers."""
    markers = _TEST_CONTENT_MARKERS.get(path.suffix.lower())
    if markers is None:
        return True  # non-compiled language: filename convention is enough
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return any(m in content for m in markers)


def detect_test_suite(repo_dir: Path) -> str:
    """Return one of: None / Basic / Comprehensive."""
    # Use a set keyed on resolved path: _TEST_PATTERNS overlap (e.g. test_*.py and
    # *_test.py both match test_x_test.py), and without dedup a single file was
    # counted twice, inflating the >=10 "Comprehensive" threshold.
    seen: Set[Path] = set()
    for pattern in _TEST_PATTERNS:
        for f in repo_dir.rglob(pattern):
            if ".git" in f.parts or ".hg" in f.parts:
                continue
            key = f.resolve()
            if key not in seen and _is_real_test_file(f):
                seen.add(key)
    # Also recognise files inside a __tests__/ directory (common JS/TS convention).
    for f in repo_dir.rglob("__tests__/*"):
        if f.is_file() and ".git" not in f.parts and ".hg" not in f.parts:
            seen.add(f.resolve())

    test_files: List[Path] = list(seen)
    if not test_files:
        has_config = any((repo_dir / cfg).exists() for cfg in _TEST_CONFIG_FILES)
        return "Basic" if has_config else "None"

    test_parent_dirs = {f.parent for f in test_files}
    if len(test_files) >= 10 or len(test_parent_dirs) >= 3:
        return "Comprehensive"
    return "Basic"


# ---------------------------------------------------------------------------
# Containerized (column V)
# ---------------------------------------------------------------------------

def detect_containerized(repo_dir: Path) -> str:
    """Return Yes or No."""
    container_files = [
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore",
        "compose.yml", "compose.yaml", "Containerfile",  # Compose v2 / Podman
    ]
    if any((repo_dir / f).exists() for f in container_files):
        return "Yes"
    if _nonvendor_rglob(repo_dir, "*.k8s.yml") or _nonvendor_rglob(repo_dir, "*.k8s.yaml"):
        return "Yes"
    for dir_name in ("deploy", "infra", "k8s", "kubernetes", "docker"):
        d = repo_dir / dir_name
        if d.is_dir() and (list(d.rglob("*.yml")) or list(d.rglob("*.yaml"))):
            return "Yes"
    if _nonvendor_rglob(repo_dir, "Chart.yaml"):
        return "Yes"
    if _nonvendor_rglob(repo_dir, "Dockerfile"):
        return "Yes"
    return "No"


# ---------------------------------------------------------------------------
# README quality (column Y)
# ---------------------------------------------------------------------------

_README_NAMES: List[str] = ["README.md", "README.rst", "README.txt", "README.adoc", "README"]


def detect_readme_quality(repo_dir: Path) -> str:
    """Return one of: None / Basic / Detailed / Comprehensive."""
    readme_path: Optional[Path] = None
    for name in _README_NAMES:
        p = repo_dir / name
        if p.exists():
            readme_path = p
            break

    has_docs_dir = (repo_dir / "docs").is_dir()
    has_contributing = (repo_dir / "CONTRIBUTING.md").exists()

    if readme_path is None:
        return "None"

    try:
        content = readme_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "None"

    if len(content.strip()) < 50:
        return "None"

    lower = content.lower()
    has_setup = any(kw in lower for kw in ("install", "setup", "getting started", "requirements", "prerequisites", "quickstart"))
    has_usage = any(kw in lower for kw in ("usage", "example", "quick start", "how to use", "## use"))
    has_architecture = any(kw in lower for kw in ("architecture", "how it works", "overview", "design", "structure"))

    if (has_docs_dir or has_contributing) and has_setup and has_usage:
        return "Comprehensive"
    if has_setup and (has_usage or has_architecture):
        return "Detailed"
    if len(content.strip()) > 200:
        return "Basic"
    return "None"


def compute_readme_stats(repo_dir: Path) -> int:
    """documentation_cnt: total number of lines across README* files in the repo root."""
    total_lines = 0
    for p in repo_dir.iterdir():
        if p.is_file() and p.name.lower().startswith("readme"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            total_lines += len(text.splitlines())
    return total_lines


# ---------------------------------------------------------------------------
# License detection
# ---------------------------------------------------------------------------

def detect_license(repo_dir: Path) -> str:
    """Naive license detector based on LICENSE*/COPYING* files in the repository root.

    Returns one of: MIT, APACHE-2.0, GPL-3.0, GPL, BSD, MPL-2.0, UNLICENSE, UNKNOWN.
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
    if has("bsd license") or "redistribution and use in source and binary forms" in head.lower():
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


# ---------------------------------------------------------------------------
# Repository size (disk usage)
# ---------------------------------------------------------------------------

def _parse_du_kb(output: str) -> int:
    """Parse the leading kilobyte count from `du -sk` output."""
    try:
        return int(output.split()[0])
    except (IndexError, ValueError):
        return 0


def get_dir_size_mb(path: Path) -> float:
    """Return the size of `path` in megabytes via `du -sk`, rounded to 3 decimals."""
    kb = _parse_du_kb(run_cmd(["du", "-sk", str(path)]))
    return round(kb / 1024, 3)


# ---------------------------------------------------------------------------
# Issue tracker (column Z)
# ---------------------------------------------------------------------------

# The JIRA/Linear-key alternatives MUST be case-sensitive: a global re.IGNORECASE
# let "[A-Z]{2,10}-\d+" match lowercase tokens like "react-18", "utf-8",
# "python-3", "sha-256", flagging ordinary commits as issue-linked.  Only the
# action verbs are case-insensitive (inline (?i:...)); project keys stay uppercase.
_ISSUE_PATTERN: re.Pattern = re.compile(
    r"(?i:(?:fixes?|closes?|resolves?)\s+#\d+)"
    r"|#\d+\b"
    r"|\bJIRA-\w+"
    r"|\bLINEAR-\w+"
    r"|\b[A-Z]{2,10}-\d+\b",
)


def detect_issue_tracker(repo_dir: Path, vcs=None) -> str:
    """Return one of: None / Basic / Linked to Commits / Full+Design Docs.

    ``vcs`` supplies recent commit subjects (git or mercurial).  When omitted, a
    GitVCS backend is used so existing callers/tests keep their git behaviour.
    """
    if vcs is None:
        from .vcs.git import GitVCS  # late import: avoid cycle
        vcs = GitVCS()

    has_design_docs = any([
        (repo_dir / "docs" / "rfcs").is_dir(),
        (repo_dir / "docs" / "adr").is_dir(),
        (repo_dir / "rfcs").is_dir(),
        (repo_dir / "adr").is_dir(),
    ])

    subjects = vcs.recent_commit_subjects(repo_dir, limit=200)
    has_issue_refs = bool(_ISSUE_PATTERN.search(subjects))

    if has_issue_refs:
        return "Full+Design Docs" if has_design_docs else "Linked to Commits"

    if (
        (repo_dir / ".github" / "ISSUE_TEMPLATE").exists()
        or (repo_dir / ".github" / "ISSUE_TEMPLATE.md").exists()
    ):
        return "Basic"

    return "None"


# ---------------------------------------------------------------------------
# Docstring ratio via tree-sitter (column X)
# ---------------------------------------------------------------------------

_COMMENT_NODE_TYPES: frozenset[str] = frozenset({
    "comment", "block_comment", "line_comment", "doc_comment",
    "multiline_comment", "documentation_comment",
})

_STRING_NODE_TYPES: frozenset[str] = frozenset({
    "string", "string_literal", "raw_string_literal",
    "interpreted_string_literal", "concatenated_string",
})

_BODY_NODE_TYPES: frozenset[str] = frozenset({
    "block", "suite", "body", "statement_block",
})

# Named node types to skip when walking backwards looking for a doc comment.
# These annotate or wrap functions but are not themselves doc comments.
_DOC_SKIP_TYPES: frozenset[str] = frozenset({
    "decorator",           # Python @decorator
    "annotation",          # Java/Kotlin/Dart @Override
    "method_signature",    # Dart class method: method_signature precedes function_body
    "function_signature",  # Dart top-level: function_signature precedes function_body
})

# Wrapper nodes where the function is the main child but the doc comment sits
# at the grandparent level (outside the wrapper).
_WRAPPER_NODE_TYPES: frozenset[str] = frozenset({
    "export_statement",      # TS/JS: export function foo() {} / export class Foo {}
    "decorated_definition",  # Python: @decorator\ndef foo(): ...
})


def _check_preceding_comment(children: List[Any], up_to_idx: int) -> bool:
    """Walk backwards through children[0:up_to_idx] looking for a doc comment.

    Skips unnamed nodes (whitespace/punctuation) and _DOC_SKIP_TYPES (annotations,
    decorators, Dart method_signature) to reach the comment that documents the
    function.  Stops at the first unrelated named node.
    """
    for i in range(up_to_idx - 1, -1, -1):
        prev = children[i]
        if prev.type in _COMMENT_NODE_TYPES:
            return True
        if not prev.is_named:
            continue  # whitespace / punctuation — keep looking
        if prev.type in _DOC_SKIP_TYPES:
            continue  # decorator / annotation / Dart signature — keep looking
        break       # hit an unrelated node — stop
    return False


def _func_has_doc(func_node: Any, parent: Optional[Any], func_idx: int) -> bool:
    """Return True if the function node has a preceding doc comment or body docstring."""
    if parent is not None:
        # 1. Direct check: look backwards in parent's children (handles most languages)
        if _check_preceding_comment(parent.children, func_idx):
            return True

        # 2. Wrapper check: if the function sits inside export_statement or
        #    decorated_definition the comment lives at the grandparent level.
        if parent.type in _WRAPPER_NODE_TYPES and parent.parent is not None:
            gp = parent.parent
            parent_id = parent.id
            for i, ch in enumerate(gp.children):
                if ch.id == parent_id:
                    if _check_preceding_comment(gp.children, i):
                        return True
                    break

    # 3. Docstring as first statement of function body.
    #    Handles both newer grammars (bare `string` node) and older ones
    #    (string wrapped inside `expression_statement`).
    for child in func_node.children:
        if child.type in _BODY_NODE_TYPES:
            named = child.named_children
            if named:
                first = named[0]
                if first.type in _STRING_NODE_TYPES:
                    return True
                if first.type == "expression_statement":
                    inner = first.named_children
                    if inner and inner[0].type in _STRING_NODE_TYPES:
                        return True
            break

    return False


def compute_docstring_ratio(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    exclude_dirs: Optional[List[str]] = None,
) -> float:
    """Fraction of functions/methods with a leading docstring or doc comment."""
    if ts_manager is None:
        return 0.0

    total_funcs = 0
    funcs_with_docs = 0

    for path in iter_code_files(repo_dir, allowed_files, exclude_dirs):
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
        except Exception:
            continue

        stack: List[Tuple[Any, Optional[Any], int]] = [(tree.root_node, None, -1)]
        while stack:
            node, parent, idx = stack.pop()
            if node.type in func_node_types:
                total_funcs += 1
                if _func_has_doc(node, parent, idx):
                    funcs_with_docs += 1
            for i, child in enumerate(node.children):
                stack.append((child, node, i))

    if total_funcs == 0:
        return 0.0
    return round(funcs_with_docs / total_funcs, 6)


# ---------------------------------------------------------------------------
# Function length via tree-sitter (column AA)
# ---------------------------------------------------------------------------

def _is_class_definition_node(node: Any) -> bool:
    """Weed out grammar quirks where the class node type alone over-matches.

    * C-family ``class_specifier``/``struct_specifier`` (cpp, glsl, hlsl) are
      also emitted for forward declarations (``class Fwd;``) and elaborated
      type references (``struct Point p;``) — only a body makes a definition.
    * Go ``type_spec`` covers EVERY named type (``type MyID int``); only
      struct/interface types are class analogs.
    * Swift ``extension Foo`` shares ``class_declaration`` with
      class/struct/enum/actor but declares no new type.  Other grammars never
      put an ``extension`` keyword child in class_declaration, so the check
      is a no-op for them.
    """
    t = node.type
    if t in ("class_specifier", "struct_specifier"):
        return any(c.type == "field_declaration_list" for c in node.children)
    if t == "type_spec":
        return any(c.type in ("struct_type", "interface_type") for c in node.children)
    if t == "class_declaration":
        return all(c.type != "extension" for c in node.children)
    return True


def compute_avg_func_length(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    exclude_dirs: Optional[List[str]] = None,
) -> float:
    return compute_avg_func_length_stats(
        repo_dir, allowed_files, ts_manager, exclude_dirs
    ).average


def compute_avg_func_length_stats(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
    exclude_dirs: Optional[List[str]] = None,
) -> FunctionLengthStats:
    """One AST pass per file: function lengths/count (AA, BC) and class
    count (BD) are tallied together."""
    stats = FunctionLengthStats()
    if ts_manager is None:
        return stats

    for path in iter_code_files(repo_dir, allowed_files, exclude_dirs):
        parser_entry = ts_manager.node_types_for_suffix(path.suffix)
        if parser_entry is None:
            continue
        parser, func_node_types, class_node_types = parser_entry

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

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in func_node_types:
                length = node.end_point[0] - node.start_point[0] + 1
                if length > 0:
                    stats.total_func_lines += length
                    stats.function_count += 1
            # is_named: in some grammars (ruby, js/ts) the class KEYWORD token
            # shares the type string with the declaration node — anonymous
            # tokens must not count.
            if (
                node.type in class_node_types
                and node.is_named
                and _is_class_definition_node(node)
            ):
                stats.class_count += 1
            stack.extend(node.children)

    return stats
