"""Static test-vs-code estimates (columns BB ``test_coverage_pct``,
BE ``untested_files_pct``).

These are LOC/file-count heuristics, NOT runtime coverage: no tests are
executed.  ``test_coverage_pct`` is the share of test-file lines among all
code lines of the tracked (non-vendored) tree, capped at 100.
``untested_files_pct`` is the share of code files that are not test files.

Only code files count (``CODE_EXTENSIONS``; generated files excluded by
``GENERATED_FILE_RE``), so lock files, JSON/YAML fixtures, Markdown, SVG and
sourcemaps do not dilute the denominator.  Lines are counted as newline bytes,
without language parsing.  Whether a file is a test is decided from its path
(:func:`is_test_file`).  Binary files and files over :data:`MAX_FILE_BYTES`
are skipped.  Deterministic — no LLM, no network calls.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

MAX_FILE_BYTES = 2_000_000

# Vendor directories excluded from the ratio (path segment match at any depth).
VENDOR_DIRS = {
    "node_modules", "vendor", "Pods", ".venv", "venv", "site-packages",
    "bower_components", "DerivedData", "Carthage", ".dart_tool", ".gradle",
    "__pycache__", ".tox", "Godeps", "dist", "build",
}

# --- test-file detection: broad, across major languages and layouts ----------
#
# Tests live either in dedicated directories or next to the code, named by
# convention.  Both cases are matched so coverage is not underestimated.  The
# match is against the basename and against WHOLE path segments (not
# substrings — otherwise ``contest/`` and ``latest.js`` are false positives).

# Directory markers: a full path-segment name (case-insensitive).
_TEST_DIR_SEGMENTS = {
    "test", "tests", "spec", "specs", "__tests__", "__mocks__", "testing",
    "unittest", "unittests", "unit_test", "unit_tests", "unit-tests",
    "testcase", "testcases", "test_case", "test_cases",
    "e2e", "integration_test", "integration_tests", "integrationtest",
    "integrationtests", "itest", "itests", "functional_test", "functional_tests",
    "acceptance", "acceptancetest", "acceptance_tests", "smoke", "smoke_tests",
    "cypress", "features", "androidtest", "testsuite", "test_suite", "testsuites",
    # Gradle/Maven/Swift/.NET layouts: src/test, src/androidTest, XxxTests/
    "androidtestdebug", "androidtestrelease",
}
# Basename: test|spec prefixes/suffixes separated by a delimiter (test_x,
# x_test., x.spec., spec_x, x_tests.), plus conftest.py (pytest) and .feature
# (Cucumber/BDD).  Case-insensitive.  The delimiter is mandatory — "latest" and
# "greatest" must not match.
_TEST_NAME_RE = re.compile(
    r"(?:^|[._-])(?:test|spec)s?(?=[._-])"
    r"|^conftest\.py$"
    r"|\.feature$",
    re.I,
)
# CamelCase test classes: FooTest.java, BarTests.cs, BazSpec.scala,
# QuxSuite.scala, XxxTestCase.py, FooIT.java (Maven failsafe).  A capitalised
# suffix right before a code extension — case-SENSITIVE, so lowercase words do
# not match.
_TEST_CAMEL_RE = re.compile(
    r"(?:Test|Tests|TestCase|TestCases|Spec|Specs|Suite|Suites|IT|ITCase)"
    r"\.(?:java|kt|kts|scala|groovy|cs|swift|m|mm|php|py|rb|ts|tsx|js|jsx|mjs|cjs|"
    r"dart|go|rs|cc|cpp|cxx|cs|clj|cljs|fs|fsx|vb|ex|exs|erl|hs|lua|pl|pm|r|jl)$"
)


def is_test_file(path: str) -> bool:
    """Whether a file is a test — by directory marker or naming convention."""
    parts = path.split("/")
    if any(seg.lower() in _TEST_DIR_SEGMENTS for seg in parts[:-1]):
        return True
    name = parts[-1]
    return bool(_TEST_NAME_RE.search(name) or _TEST_CAMEL_RE.search(name))


# --- which files count as code for the test-to-code ratio --------------------
CODE_EXTENSIONS = {
    "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "vue", "svelte",
    "java", "kt", "kts", "scala", "groovy", "swift", "m", "mm", "h", "hpp",
    "c", "cc", "cpp", "cxx", "cs", "go", "rs", "rb", "php", "dart",
    "clj", "cljs", "fs", "fsx", "vb", "ex", "exs", "erl", "hs", "lua",
    "pl", "pm", "r", "jl", "sh", "bash", "zsh", "sql",
    "html", "css", "scss", "sass", "less",
    "feature",  # Cucumber/BDD — test code
}
# Obvious generated content: minified assets, typings, snapshots,
# protobuf/codegen output.
GENERATED_FILE_RE = re.compile(
    r"(?:\.min\.(?:js|css)|\.map|\.snap|\.lock|\.d\.ts|_pb2(?:_grpc)?\.py|"
    r"\.pb\.go|\.g\.dart|\.freezed\.dart|\.generated\.\w+)$", re.I)


def is_vendored_path(path: str) -> bool:
    """Whether a repo-relative path lies inside a known vendor directory."""
    return any(part in VENDOR_DIRS for part in path.split("/"))


def is_code_file(path: str) -> bool:
    """Whether the path is a hand-written code file by extension."""
    name = path.rsplit("/", 1)[-1]
    if GENERATED_FILE_RE.search(name):
        return False
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in CODE_EXTENSIONS


def coverage_stats(repo_path: Path, files: List[str]) -> Dict[str, int]:
    """Test-vs-code statistics over the given tracked-file list.

    Returns:

    * ``test_coverage_pct`` (int 0..100) — share of test-file lines among ALL
      code lines (tests included in the denominator), capped at 100;
    * ``untested_files_pct`` (int 0..100) — share of code files that are not
      test files;
    * ``total_code_lines``, ``test_code_lines``, ``total_code_files``,
      ``test_code_files`` — the raw tallies.

    All percentages are 0 when the repo has no code at all.
    """
    total = test = 0
    total_files = test_files = 0
    for f in files:
        if is_vendored_path(f) or not is_code_file(f):
            continue
        p = repo_path / f
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            data = p.read_bytes()
            if b"\0" in data[:4096]:  # binary
                continue
            loc = data.count(b"\n")
        except OSError:
            continue
        total += loc
        total_files += 1
        if is_test_file(f):
            test += loc
            test_files += 1
    pct = min(100, round(100 * test / total)) if total else 0
    untested_pct = round(100 * (total_files - test_files) / total_files) if total_files else 0
    return {
        "test_coverage_pct": pct,
        "untested_files_pct": untested_pct,
        "total_code_lines": total,
        "test_code_lines": test,
        "total_code_files": total_files,
        "test_code_files": test_files,
    }
