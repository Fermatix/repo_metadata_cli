"""Columns F, G, H — Raw LOC, Logical LOC, Auto-Generated LOC (all via scc)."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import (
    count_chars_in_files,
    get_auto_gen_loc,
    get_clean_logical_loc,
    get_dependency_dir_loc,
    get_scc_file_stats,
)


class RawLocMetric(BaseMetric):
    """F: Total lines including blank lines and comments (scc Lines column)."""

    column = "F"
    field_name = "raw_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats["total"]["lines"]


class LogicalLocMetric(BaseMetric):
    """G: scc Code column, excluding dependency and build-artifact directories
    (node_modules/, vendor/, .venv/, site-packages/, Pods/, dist/, build/, …
    the full set is ``metrics.scc_exclude_dirs``)."""

    column = "G"
    field_name = "logical_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats_no_deps["total"]["code"]


class AutoGenLocMetric(BaseMetric):
    """H: scc Code lines in auto-generated files, capped at Logical LOC."""

    column = "H"
    field_name = "autogen_loc"

    def compute(self, ctx: RepoContext) -> Any:
        autogen_dirs = set(ctx.settings.metrics.autogen_dirs)
        exclude_dirs = set(ctx.settings.metrics.scc_exclude_dirs)
        return ctx._cached(
            "autogen_loc",
            lambda: get_auto_gen_loc(ctx.repo_path, autogen_dirs, exclude_dirs),
        )


class CleanLogicalLocMetric(BaseMetric):
    """BG: scc Code lines of code languages only, with the extended exclusion
    list (``metrics.clean_scc_exclude_dirs``).

    The honest counterpart of logical_loc (G), which counts every language on
    the smaller exclusion list — data formats, dumps and CMS cores included.
    Hand-written markup (HTML/CSS/preprocessors/templates/XAML) counts as code;
    XML counts only on Android res/ paths; SQL counts unless the file is a
    database dump.  Like G, generated code stays inside — autogen_loc (H)
    measures that share separately.  G is left untouched for comparability
    with previously collected datasets."""

    column = "BG"
    field_name = "clean_logical_loc"

    def compute(self, ctx: RepoContext) -> Any:
        return get_clean_logical_loc(
            ctx.repo_path,
            exclude_dirs=list(ctx.settings.metrics.clean_scc_exclude_dirs),
            non_code_languages=set(ctx.settings.metrics.clean_non_code_languages),
        )


class DepDirLocMetric(BaseMetric):
    """AE: scc Code lines in dependency directories (``metrics.dep_dirs`` —
    vendor/, node_modules/, .venv/, site-packages/, Pods/, …)."""

    column = "AE"
    field_name = "dependency_dir_loc"

    def compute(self, ctx: RepoContext) -> Any:
        dep_dir_names = set(ctx.settings.metrics.dep_dirs)
        return ctx._cached("dependency_dir_loc", lambda: get_dependency_dir_loc(ctx.repo_path, dep_dir_names))


class CommentRatioMetric(BaseMetric):
    """AO: Comment-to-code ratio (scc Comment / Code, excl dependency dirs).

    Ported from v1, where this was named docstring_ratio.  Distinct from v2's
    docstring_ratio (column X), which measures the fraction of documented functions.
    """

    column = "AO"
    field_name = "comment_ratio"

    def compute(self, ctx: RepoContext) -> Any:
        total = ctx.scc_stats_no_deps["total"]
        code = total["code"]
        if code <= 0:
            return 0.0
        return round(total["comment"] / code, 6)


class SymbolsCountMetric(BaseMetric):
    """AP: Total Unicode character count across the logical_loc file set.

    Reads the same files scc counts for logical_loc (same scc_exclude_dirs) and
    sums len(text).  Shares the cached `scc --by-file` call with ExtensionsMetric.
    """

    column = "AP"
    field_name = "symbols_count"

    def compute(self, ctx: RepoContext) -> Any:
        exclude_dirs = list(ctx.settings.metrics.scc_exclude_dirs)
        file_stats = ctx._cached(
            "scc_file_stats_no_deps",
            lambda: get_scc_file_stats(ctx.repo_path, exclude_dirs=exclude_dirs),
        )
        return count_chars_in_files(file_stats)
