"""Externally defined comparison metrics (columns BJ-BN)."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..base_metric import BaseMetric, RepoContext

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT_SECONDS = 720
_META_SCC_EXCLUDE_DIRS = "vendor,node_modules,dist,build,generated,migrations"


def _run_stdout(command: Sequence[str], repo_path: Path, metric_name: str) -> str:
    """Run one external recipe in ``repo_path`` and degrade to empty output."""
    try:
        result = subprocess.run(
            list(command),
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.warning(
            "Cannot compute %s: %s is not installed; using 0.",
            metric_name,
            command[0],
        )
        return ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "Cannot compute %s: %s timed out after %ds; using 0.",
            metric_name,
            command[0],
            _COMMAND_TIMEOUT_SECONDS,
        )
        return ""
    except OSError as exc:
        logger.warning(
            "Cannot compute %s: %s failed (%s); using 0.",
            metric_name,
            command[0],
            exc,
        )
        return ""

    if result.returncode != 0:
        logger.warning(
            "Cannot compute %s: %s exited with code %d; using 0.",
            metric_name,
            command[0],
            result.returncode,
        )
        return ""
    return result.stdout


def _parse_json_list(output: str) -> list[dict[str, Any]]:
    """Return the top-level object list from an scc JSON response."""
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def parse_meta_logical_loc(output: str) -> int:
    """Sum the scc ``Code`` total across all language objects."""
    total = 0
    for language in _parse_json_list(output):
        try:
            total += int(language.get("Code") or 0)
        except (TypeError, ValueError):
            continue
    return total


def parse_meta_non_authored_loc(output: str) -> int:
    """Sum ``Files[].Code`` where scc marks ``Generated`` as JSON true."""
    total = 0
    for language in _parse_json_list(output):
        files = language.get("Files")
        if not isinstance(files, list):
            continue
        for file_data in files:
            if (
                not isinstance(file_data, dict)
                or file_data.get("Generated") is not True
            ):
                continue
            try:
                total += int(file_data.get("Code") or 0)
            except (TypeError, ValueError):
                continue
    return total


def parse_meta_loc_with_generated(output: str) -> int:
    """Sum ``Files[].Code`` for every file in the fixed scc report."""
    total = 0
    for language in _parse_json_list(output):
        files = language.get("Files")
        if not isinstance(files, list):
            continue
        for file_data in files:
            if not isinstance(file_data, dict):
                continue
            try:
                total += int(file_data.get("Code") or 0)
            except (TypeError, ValueError):
                continue
    return total


def parse_meta_duplication_ratio(output: str) -> float:
    """Read ``statistics.total.percentage / 100`` from a jscpd JSON report."""
    try:
        report = json.loads(output)
        percentage = report["statistics"]["total"]["percentage"]
        return float(percentage) / 100.0
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0.0


def parse_meta_non_merge_commit_count(output: str) -> int:
    """Count git-log lines after case-insensitive removal of revert lines."""
    return sum("revert" not in line.lower() for line in output.splitlines())


def get_meta_logical_loc(repo_path: Path) -> int:
    output = _run_stdout(
        ["scc", ".", "--format", "json"],
        repo_path,
        "meta_logical_loc",
    )
    return parse_meta_logical_loc(output)


def get_meta_scc_with_generated_report(repo_path: Path) -> str:
    """Run the shared scc recipe for both generated-code comparison metrics."""
    return _run_stdout(
        [
            "scc",
            ".",
            "--gen",
            "--by-file",
            "--exclude-dir",
            _META_SCC_EXCLUDE_DIRS,
            "--format",
            "json",
        ],
        repo_path,
        "meta_non_authored_loc/meta_loc_with_generated",
    )


def get_meta_duplication_ratio(repo_path: Path) -> float:
    with tempfile.TemporaryDirectory(prefix="repo-metadata-jscpd-") as tmpdir:
        report_dir = Path(tmpdir)
        _run_stdout(
            [
                "jscpd",
                ".",
                "--min-tokens",
                "50",
                "--min-lines",
                "5",
                "--reporters",
                "json",
                "--output",
                str(report_dir),
            ],
            repo_path,
            "meta_duplication_ratio",
        )
        report_file = report_dir / "jscpd-report.json"
        try:
            output = report_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0.0
        except OSError as exc:
            logger.warning(
                "Cannot compute meta_duplication_ratio: jscpd report could not be read "
                "(%s); using 0.",
                exc,
            )
            return 0.0
        return parse_meta_duplication_ratio(output)


def get_meta_non_merge_commit_count(repo_path: Path) -> int:
    output = _run_stdout(
        ["git", "log", "--oneline", "--no-merges"],
        repo_path,
        "meta_non_merge_commit_count",
    )
    return parse_meta_non_merge_commit_count(output)


class MetaLogicalLocMetric(BaseMetric):
    """BJ: scc Code total from the unmodified external recipe."""

    column = "BJ"
    field_name = "meta_logical_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached(
            "meta_logical_loc", lambda: get_meta_logical_loc(ctx.repo_path)
        )


class MetaNonAuthoredLocMetric(BaseMetric):
    """BK: scc-generated Code total from the fixed external recipe."""

    column = "BK"
    field_name = "meta_non_authored_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached(
            "meta_non_authored_loc",
            lambda: parse_meta_non_authored_loc(
                ctx._cached(
                    "meta_scc_with_generated_report",
                    lambda: get_meta_scc_with_generated_report(ctx.repo_path),
                )
            ),
        )


class MetaDuplicationRatioMetric(BaseMetric):
    """BL: jscpd total duplication percentage expressed as a ratio."""

    column = "BL"
    field_name = "meta_duplication_ratio"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached(
            "meta_duplication_ratio",
            lambda: get_meta_duplication_ratio(ctx.repo_path),
        )


class MetaNonMergeCommitCountMetric(BaseMetric):
    """BM: non-merge Git commits whose oneline output does not contain revert."""

    column = "BM"
    field_name = "meta_non_merge_commit_count"

    def compute(self, ctx: RepoContext) -> Any:
        if ctx.vcs.name != "git":
            return 0
        return ctx._cached(
            "meta_non_merge_commit_count",
            lambda: get_meta_non_merge_commit_count(ctx.repo_path),
        )


class MetaLocWithGeneratedMetric(BaseMetric):
    """BN: all Code from the fixed scc recipe, including generated files."""

    column = "BN"
    field_name = "meta_loc_with_generated"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached(
            "meta_loc_with_generated",
            lambda: parse_meta_loc_with_generated(
                ctx._cached(
                    "meta_scc_with_generated_report",
                    lambda: get_meta_scc_with_generated_report(ctx.repo_path),
                )
            ),
        )
