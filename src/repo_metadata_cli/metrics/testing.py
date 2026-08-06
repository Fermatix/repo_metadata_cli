"""Columns U, BB — Test suite presence and static test-coverage estimate."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..coverage_estimate import coverage_stats
from ..metric_utils import detect_test_suite


class TestSuiteMetric(BaseMetric):
    """U: Automated test coverage level."""

    column = "U"
    field_name = "test_suite"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_test_suite(ctx.repo_path)


class TestCoveragePctMetric(BaseMetric):
    """BB: STATIC test-to-code LOC ratio in percent, capped at 100.

    No tests are executed — this is the share of test-file lines among all
    code lines of the tracked non-vendored tree, not runtime coverage.  Works
    identically for Git and Mercurial via the full tracked-file list, with a
    filesystem fallback in plain-directory mode.  0 when there are no code
    lines at all.
    """

    column = "BB"
    field_name = "test_coverage_pct"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached(
            "test_coverage_pct",
            lambda: coverage_stats(ctx.repo_path, ctx.tracked_files)["test_coverage_pct"],
        )
