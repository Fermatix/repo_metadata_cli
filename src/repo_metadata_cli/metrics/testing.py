"""Column U — Test suite presence."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import detect_test_suite


class TestSuiteMetric(BaseMetric):
    """U: Automated test coverage level."""

    column = "U"
    field_name = "test_suite"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_test_suite(ctx.repo_path)
