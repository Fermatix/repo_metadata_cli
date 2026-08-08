"""Columns U, BB, BE — test suite presence and static test-vs-code estimates."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..coverage_estimate import coverage_stats
from ..metric_utils import detect_test_suite


def _cached_coverage_stats(ctx: RepoContext) -> dict:
    """One shared file walk for BB/BE (and the raw tallies)."""
    return ctx._cached(
        "coverage_stats",
        lambda: coverage_stats(ctx.repo_path, ctx.tracked_files),
    )


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
        return _cached_coverage_stats(ctx)["test_coverage_pct"]


class UntestedFilesPctMetric(BaseMetric):
    """BE: Share of code files that are not test files, in percent (0-100).

    A file-count heuristic over the same tracked non-vendored code files as
    BB: files not recognized as tests by path convention, divided by all
    code files.  100 means the repo has no test files at all; 0 when the repo
    has no code files.
    """

    column = "BE"
    field_name = "untested_files_pct"

    def compute(self, ctx: RepoContext) -> Any:
        return _cached_coverage_stats(ctx)["untested_files_pct"]
