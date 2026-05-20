"""Columns F, G, H — Raw LOC, Logical LOC, Auto-Generated LOC (all via scc)."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import get_auto_gen_loc, get_dep_dir_loc


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
        autogen_dirs = set(ctx.settings.metrics.autogen_dirs)
        exclude_dirs = set(ctx.settings.metrics.scc_exclude_dirs)
        autogen = ctx._cached(
            "autogen_loc",
            lambda: get_auto_gen_loc(ctx.repo_path, ext_map, autogen_dirs, exclude_dirs),
        )
        logical = ctx.scc_stats_no_deps["total"]["code"]
        return min(autogen, logical)


class DepDirLocMetric(BaseMetric):
    """AE: scc Code lines in dependency directories (vendor/, node_modules/, bower_components/)."""

    column = "AE"
    field_name = "dep_dir_loc"

    def compute(self, ctx: RepoContext) -> Any:
        dep_dir_names = set(ctx.settings.metrics.dep_dirs)
        return ctx._cached("dep_dir_loc", lambda: get_dep_dir_loc(ctx.repo_path, dep_dir_names))
