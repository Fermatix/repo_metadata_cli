"""Core repository metric utilities: SCC, jscpd, infrastructure detection, tree-sitter analysis."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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

    @property
    def average(self) -> float:
        if self.function_count == 0:
            return 0.0
        return self.total_func_lines / self.function_count

    def merge(self, other: "FunctionLengthStats") -> None:
        self.total_func_lines += other.total_func_lines
        self.function_count += other.function_count


# ---------------------------------------------------------------------------
# File iteration (used by tree-sitter metrics)
# ---------------------------------------------------------------------------

def iter_code_files(repo_dir: Path, allowed_files: AllowedFiles) -> Iterable[Path]:
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        if not allowed_files.is_code_path(path):
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


# ---------------------------------------------------------------------------
# Python LOC fallback (used when scc is not installed)
# ---------------------------------------------------------------------------

# Single-line comment prefixes per tree-sitter language name
_COMMENT_PREFIXES: Dict[str, List[str]] = {
    "python": ["#"],
    "javascript": ["//"],
    "typescript": ["//"],
    "tsx": ["//"],
    "jsx": ["//"],
    "go": ["//"],
    "java": ["//"],
    "kotlin": ["//"],
    "scala": ["//"],
    "rust": ["//"],
    "c": ["//"],
    "cpp": ["//"],
    "csharp": ["//"],
    "ruby": ["#"],
    "bash": ["#"],
    "shell": ["#"],
    "php": ["//", "#"],
    "swift": ["//"],
    "haskell": ["--"],
    "ocaml": [],
    "ocaml_interface": [],
    "julia": ["#"],
    "lua": ["--"],
    "r": ["#"],
    "css": [],
    "html": [],
    "json": [],
    "toml": ["#"],
    "yaml": ["#"],
    "verilog": ["//"],
    "agda": ["--"],
    "jsdoc": [],
}

# Display names matching scc output capitalization
_LANG_DISPLAY_NAMES: Dict[str, str] = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "tsx": "TSX",
    "jsx": "JSX",
    "go": "Go",
    "java": "Java",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "rust": "Rust",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "ruby": "Ruby",
    "bash": "Bash",
    "shell": "Shell",
    "php": "PHP",
    "swift": "Swift",
    "haskell": "Haskell",
    "ocaml": "OCaml",
    "ocaml_interface": "OCaml",
    "julia": "Julia",
    "lua": "Lua",
    "r": "R",
    "css": "CSS",
    "html": "HTML",
    "json": "JSON",
    "toml": "TOML",
    "yaml": "YAML",
    "verilog": "Verilog",
    "agda": "Agda",
    "jsdoc": "JSDoc",
}


def _python_loc_fallback(repo_dir: Path, extension_language_map: Dict[str, str]) -> dict:
    """Count LOC using Python when scc is not available.

    Uses git ls-files for the file list so only tracked files are counted.
    Comment detection uses single-line prefix heuristics per language.
    Multi-line block comments (/* ... */) are counted as code lines — acceptable
    for a fallback.
    """
    try:
        out = run_cmd(["git", "ls-files"], cwd=repo_dir)
    except Exception:
        return _parse_scc_output("")

    if not out:
        return _parse_scc_output("")

    lang_stats: Dict[str, Dict[str, int]] = {}

    for rel_path_str in out.splitlines():
        rel_path_str = rel_path_str.strip()
        if not rel_path_str:
            continue
        path = repo_dir / rel_path_str
        if not path.is_file():
            continue

        suffix = path.suffix.lower()
        lang = extension_language_map.get(suffix)
        if not lang:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lines = content.splitlines()
        total = len(lines)
        if total == 0:
            continue

        blank_count = sum(1 for line in lines if not line.strip())

        comment_prefixes = _COMMENT_PREFIXES.get(lang.lower(), [])
        comment_count = 0
        if comment_prefixes:
            for line in lines:
                stripped = line.strip()
                if stripped and any(stripped.startswith(p) for p in comment_prefixes):
                    comment_count += 1

        code_count = max(0, total - blank_count - comment_count)

        display_name = _LANG_DISPLAY_NAMES.get(lang.lower(), lang.capitalize())
        if display_name not in lang_stats:
            lang_stats[display_name] = {"lines": 0, "code": 0, "comment": 0, "blank": 0, "files": 0}
        lang_stats[display_name]["lines"] += total
        lang_stats[display_name]["code"] += code_count
        lang_stats[display_name]["comment"] += comment_count
        lang_stats[display_name]["blank"] += blank_count
        lang_stats[display_name]["files"] += 1

    languages = [
        {
            "name": name,
            "lines": s["lines"],
            "code": s["code"],
            "comment": s["comment"],
            "blank": s["blank"],
            "files": s["files"],
        }
        for name, s in lang_stats.items()
    ]

    total_stats: Dict[str, int] = {
        "lines": sum(s["lines"] for s in lang_stats.values()),
        "code": sum(s["code"] for s in lang_stats.values()),
        "comment": sum(s["comment"] for s in lang_stats.values()),
        "blank": sum(s["blank"] for s in lang_stats.values()),
        "files": sum(s["files"] for s in lang_stats.values()),
    }

    return {"languages": languages, "total": total_stats}


def get_scc_stats(repo_dir: Path, extension_language_map: Optional[Dict[str, str]] = None) -> dict:
    """Run `scc --format json` and return parsed language statistics.

    Falls back to a Python-based line counter when scc is not installed.
    The fallback uses git ls-files and single-line comment heuristics.
    """
    out = run_cmd(["scc", "--format", "json", "--no-complexity", str(repo_dir)])
    if out:
        return _parse_scc_output(out)

    logger.info(
        "scc not available for %s; using Python LOC fallback. "
        "Install scc for more accurate results: brew install scc",
        repo_dir.name,
    )
    return _python_loc_fallback(repo_dir, extension_language_map or {})


# ---------------------------------------------------------------------------
# Auto-generated LOC (column H)
# ---------------------------------------------------------------------------

_AUTOGEN_DIRS: Set[str] = {
    "vendor", "node_modules", "dist", "build", "generated",
    "migrations", "__generated__", ".cache", "bower_components",
    ".next", ".nuxt", "out",
}

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
    "poetry.lock", "pipfile.lock", "composer.lock", "gemfile.lock",
    "pnpm-lock.yaml", "bun.lockb",
}

_AUTOGEN_HEADER_MARKERS: List[str] = [
    "code generated by",
    "do not edit",
    "@generated",
    "auto-generated",
    "autogenerated",
]


def _is_autogen_file(file_path: Path, repo_root: Path) -> bool:
    rel = file_path.relative_to(repo_root)
    for part in rel.parts[:-1]:
        if part.lower() in _AUTOGEN_DIRS:
            return True
    if rel.name.lower() in _AUTOGEN_EXACT_NAMES:
        return True
    for pattern in _AUTOGEN_NAME_RE:
        if pattern.match(rel.name):
            return True
    try:
        with file_path.open("rb") as fh:
            header = fh.read(512).decode("utf-8", errors="ignore")
        first_5 = "\n".join(header.splitlines()[:5]).lower()
        if any(m in first_5 for m in _AUTOGEN_HEADER_MARKERS):
            return True
    except OSError:
        pass
    return False


def get_auto_gen_loc(repo_dir: Path, extension_language_map: Optional[Dict[str, str]] = None) -> int:
    """Count non-blank, non-comment lines in auto-generated files.

    scc is not used here because it doesn't support many autogen file types
    (lock files, .min.js, etc.) and returns empty output for them, causing
    undercounting. The Python line counter handles all file types uniformly.
    """
    auto_gen: List[Path] = []
    for path in repo_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if _is_autogen_file(path, repo_dir):
            auto_gen.append(path)
    if not auto_gen:
        return 0

    ext_map = extension_language_map or {}
    total_code = 0
    for path in auto_gen:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lang = ext_map.get(path.suffix.lower(), "")
        comment_prefixes = _COMMENT_PREFIXES.get(lang.lower(), [])
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if comment_prefixes and any(stripped.startswith(p) for p in comment_prefixes):
                continue
            total_code += 1
    return total_code


# ---------------------------------------------------------------------------
# jscpd — duplication detection (column I)
# ---------------------------------------------------------------------------

def run_jscpd(repo_dir: Path) -> float:
    """Run jscpd and return duplication ratio [0, 1]. Returns 0.0 if jscpd unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        ignore_pattern = ",".join([
            "**/vendor/**", "**/node_modules/**", "**/dist/**", "**/build/**",
            "**/*.min.js", "**/*.min.css", "**/__generated__/**", "**/migrations/**",
        ])
        cmd = [
            "jscpd",
            "--min-tokens", "50",
            "--min-lines", "5",
            "--reporters", "json",
            "--output", str(report_dir),
            "--ignore", ignore_pattern,
            "--silent",
            str(repo_dir),
        ]
        run_cmd(cmd)
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


def detect_deployment_infra(repo_dir: Path) -> str:
    """Return one of: None / Basic CI / Full CI-CD / Enterprise."""
    enterprise_checks = [
        lambda: bool(list(repo_dir.rglob("*.tf"))),
        lambda: bool(list(repo_dir.rglob("Chart.yaml"))),
        lambda: bool(list(repo_dir.rglob("deployment.yaml"))),
        lambda: (repo_dir / "k8s").is_dir() or (repo_dir / "kubernetes").is_dir(),
        lambda: bool(list(repo_dir.rglob("*.k8s.yml"))),
        lambda: bool(list(repo_dir.rglob("*.k8s.yaml"))),
    ]
    if any(fn() for fn in enterprise_checks):
        return "Enterprise"

    if detect_ci_config(repo_dir):
        deploy_kws = {"deploy", "release", "publish", "ship"}
        ci_content = _read_ci_content(repo_dir)
        if any(kw in ci_content for kw in deploy_kws):
            return "Full CI-CD"
        return "Basic CI"

    return "None"


# ---------------------------------------------------------------------------
# Monitoring (column T)
# ---------------------------------------------------------------------------

# Only search in source code files — not CI configs, package.json, etc.
# This avoids false positives from CI pipelines referencing monitoring tools.
_MONITORING_SOURCE_EXTS: Set[str] = {
    ".py", ".js", ".ts", ".go", ".java", ".cs", ".rb", ".php", ".rs",
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
    test_files: List[Path] = []
    for pattern in _TEST_PATTERNS:
        for f in repo_dir.rglob(pattern):
            if ".git" not in f.parts and _is_real_test_file(f):
                test_files.append(f)

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
    container_files = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]
    if any((repo_dir / f).exists() for f in container_files):
        return "Yes"
    if list(repo_dir.rglob("*.k8s.yml")) or list(repo_dir.rglob("*.k8s.yaml")):
        return "Yes"
    for dir_name in ("deploy", "infra", "k8s", "kubernetes", "docker"):
        d = repo_dir / dir_name
        if d.is_dir() and (list(d.rglob("*.yml")) or list(d.rglob("*.yaml"))):
            return "Yes"
    if list(repo_dir.rglob("Chart.yaml")):
        return "Yes"
    if list(repo_dir.rglob("Dockerfile")):
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


# ---------------------------------------------------------------------------
# Issue tracker (column Z)
# ---------------------------------------------------------------------------

_ISSUE_PATTERN: re.Pattern = re.compile(
    r"(?:fixes?|closes?|resolves?)\s+#\d+|#\d+\b|JIRA-\w+|LINEAR-\w+|[A-Z]{2,10}-\d+",
    re.IGNORECASE,
)


def detect_issue_tracker(repo_dir: Path) -> str:
    """Return one of: None / Basic / Linked to Commits / Full+Design Docs."""
    has_design_docs = any([
        (repo_dir / "docs" / "rfcs").is_dir(),
        (repo_dir / "docs" / "adr").is_dir(),
        (repo_dir / "rfcs").is_dir(),
        (repo_dir / "adr").is_dir(),
    ])

    git_log = run_cmd(
        ["git", "log", "--all", "--no-merges", "--format=%s", "-n", "200"],
        cwd=repo_dir,
    )
    has_issue_refs = bool(_ISSUE_PATTERN.search(git_log))

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
) -> float:
    """Fraction of functions/methods with a leading docstring or doc comment."""
    if ts_manager is None:
        return 0.0

    total_funcs = 0
    funcs_with_docs = 0

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

def compute_avg_func_length(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> float:
    return compute_avg_func_length_stats(repo_dir, allowed_files, ts_manager).average


def compute_avg_func_length_stats(
    repo_dir: Path,
    allowed_files: AllowedFiles,
    ts_manager: Optional[TreeSitterManager],
) -> FunctionLengthStats:
    stats = FunctionLengthStats()
    if ts_manager is None:
        return stats

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

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in func_node_types:
                length = node.end_point[0] - node.start_point[0] + 1
                if length > 0:
                    stats.total_func_lines += length
                    stats.function_count += 1
            stack.extend(node.children)

    return stats
