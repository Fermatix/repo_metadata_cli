"""Columns F, G, H — Raw LOC, Logical LOC, Auto-Generated LOC (all via scc)."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import get_auto_gen_loc


class RawLocMetric(BaseMetric):
    """F: Total lines including blank lines and comments (scc Lines column)."""

    column = "F"
    field_name = "raw_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats["total"]["lines"]


class LogicalLocMetric(BaseMetric):
    """G: scc Code column, excluding node_modules/, vendor/, dist/, build/."""

    column = "G"
    field_name = "logical_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats_no_deps["total"]["code"]


class AutoGenLocMetric(BaseMetric):
    """H: scc Code lines in auto-generated files, capped at Logical LOC."""

    column = "H"
    field_name = "autogen_loc"

    def compute(self, ctx: RepoContext) -> Any:
        ext_map = dict(ctx.settings.tree_sitter.extension_language_map)
        autogen = ctx._cached("autogen_loc", lambda: get_auto_gen_loc(ctx.repo_path, ext_map))
        logical = ctx.scc_stats_no_deps["total"]["code"]
        return min(autogen, logical)
