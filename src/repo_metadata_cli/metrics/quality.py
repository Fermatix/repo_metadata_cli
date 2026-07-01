"""Columns I, J — Duplication Ratio (jscpd) and Fork %."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import run_jscpd


class DuplicationMetric(BaseMetric):
    """I: Fraction of duplicated code blocks detected by jscpd [0, 1]."""

    column = "I"
    field_name = "duplication_ratio"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx._cached("duplication_ratio", lambda: run_jscpd(ctx.repo_path))


class ForkPctMetric(BaseMetric):
    """J: 0.0 if original repo, 1.0 if fork of another repository."""

    column = "J"
    field_name = "fork_pct"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.vcs.detect_fork(ctx.repo_path)
