"""Columns W, X, Y, Z, AA, BC, BD — holdout, docstring ratio, README, issue
tracker, and the tree-sitter AST tallies (avg func length, function/class
counts)."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import (
    compute_docstring_ratio,
    compute_readme_stats,
    detect_issue_tracker,
    detect_license,
    detect_readme_quality,
)


class HoldoutMetric(BaseMetric):
    """W: Holdout verification status (vendor attestation)."""

    column = "W"
    field_name = "holdout"

    def compute(self, ctx: RepoContext) -> Any:
        return "Likely Private"


class DocstringRatioMetric(BaseMetric):
    """X: Fraction of functions/methods/classes with docstrings (tree-sitter)."""

    column = "X"
    field_name = "docstring_ratio"

    def compute(self, ctx: RepoContext) -> Any:
        return compute_docstring_ratio(
            ctx.repo_path,
            ctx.allowed_files,
            ctx.tree_sitter,
            exclude_dirs=list(ctx.settings.metrics.scc_exclude_dirs),
        )


class ReadmeQualityMetric(BaseMetric):
    """Y: README quality tier: None / Basic / Detailed / Comprehensive."""

    column = "Y"
    field_name = "readme_quality"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_readme_quality(ctx.repo_path)


class IssueTrackerMetric(BaseMetric):
    """Z: Issue tracker integration level from commit message analysis."""

    column = "Z"
    field_name = "issue_tracker"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_issue_tracker(ctx.repo_path, ctx.vcs)


class AvgFuncLengthMetric(BaseMetric):
    """AA: Average function length in lines (tree-sitter)."""

    column = "AA"
    field_name = "avg_func_length"

    def compute(self, ctx: RepoContext) -> Any:
        return round(ctx.func_length_stats.average, 2)


class FunctionsCountMetric(BaseMetric):
    """BC: Number of function/method definitions (tree-sitter).

    Same node-type sets, file walk and vendor exclusions as avg_func_length —
    one shared AST pass.  0 when tree-sitter is skipped.
    """

    column = "BC"
    field_name = "functions_count"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.func_length_stats.function_count


class ClassesCountMetric(BaseMetric):
    """BD: Number of class-like type declarations (tree-sitter).

    Classes, interfaces, traits, protocols, objects, records — plus structs
    in languages where structs are the class analog.  Node types per language
    come from ``[tree_sitter.lang_class_node_types]`` in the TOML config; a
    language absent from that table contributes 0.  Shares the AST pass with
    avg_func_length/functions_count.  0 when tree-sitter is skipped.
    """

    column = "BD"
    field_name = "classes_count"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.func_length_stats.class_count


class DocumentationCountMetric(BaseMetric):
    """AN: Total number of lines across README* files in the repo root (ported from v1)."""

    column = "AN"
    field_name = "documentation_cnt"

    def compute(self, ctx: RepoContext) -> Any:
        return compute_readme_stats(ctx.repo_path)


class LicenseTypeMetric(BaseMetric):
    """AG: Detected license type (MIT/APACHE-2.0/GPL/… or UNKNOWN), ported from v1."""

    column = "AG"
    field_name = "detected_license"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_license(ctx.repo_path)
