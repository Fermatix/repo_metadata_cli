"""Columns K, L, M — Source file count, primary language, language distribution.

Also hosts AL/AM (extensions, stack) ported from v1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import get_lang_code_no_autogen, get_scc_file_stats


def _lang_distribution(ctx: RepoContext) -> dict[str, float]:
    """Language → fraction of hand-written Code lines ≥ 1%.

    Counts scc Code lines per language excluding dependency dirs
    (node_modules/vendor) AND auto-generated files (committed bundles, minified
    assets, generated stubs). This keeps primary_language anchored to the code a
    human actually wrote: a TypeScript app shipping a large committed
    ``app.bundle.js`` is reported as TypeScript, not JavaScript.

    Cached on the context so PrimaryLanguageMetric (L) and LangDistributionMetric (M)
    stay consistent — primary_language is always the max key of this distribution.
    """
    def _compute() -> dict[str, float]:
        per_lang = get_lang_code_no_autogen(
            ctx.repo_path,
            autogen_dirs=set(ctx.settings.metrics.autogen_dirs),
            exclude_dirs=list(ctx.settings.metrics.scc_exclude_dirs),
        )
        total_code = sum(per_lang.values())
        if total_code == 0:
            return {}
        return {
            name: round(code / total_code, 6)
            for name, code in per_lang.items()
            if code / total_code >= 0.01
        }

    return ctx._cached("lang_distribution", _compute)


class SourceFilesMetric(BaseMetric):
    """K: Total source file count (scc Files column)."""

    column = "K"
    field_name = "source_files"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats["total"]["files"]


class PrimaryLanguageMetric(BaseMetric):
    """L: Language with the highest share in lang_distribution (excl node_modules/vendor)."""

    column = "L"
    field_name = "primary_language"

    def compute(self, ctx: RepoContext) -> Any:
        distribution = _lang_distribution(ctx)
        if not distribution:
            return ""
        return max(distribution, key=distribution.get)


class LangDistributionMetric(BaseMetric):
    """M: JSON dict of language → fraction of Code lines ≥ 1% (excl node_modules/vendor)."""

    column = "M"
    field_name = "lang_distribution"

    def compute(self, ctx: RepoContext) -> Any:
        return json.dumps(_lang_distribution(ctx), ensure_ascii=False)


class ExtensionsMetric(BaseMetric):
    """AL: JSON dict of file extension → fraction of Code lines (excl dependency dirs)."""

    column = "AL"
    field_name = "extensions"

    def compute(self, ctx: RepoContext) -> Any:
        exclude_dirs = list(ctx.settings.metrics.scc_exclude_dirs)
        file_stats = ctx._cached(
            "scc_file_stats_no_deps",
            lambda: get_scc_file_stats(ctx.repo_path, exclude_dirs=exclude_dirs),
        )
        ext_code: dict[str, int] = {}
        for entry in file_stats:
            ext = Path(entry["path"]).suffix.lower()
            code = int(entry["code"])
            if not ext or code <= 0:
                continue
            ext_code[ext] = ext_code.get(ext, 0) + code

        total = sum(ext_code.values())
        if total <= 0:
            return json.dumps({})
        distribution = {ext: round(c / total, 6) for ext, c in ext_code.items()}
        return json.dumps(distribution, ensure_ascii=False)


class StackMetric(BaseMetric):
    """AM: Human-readable top-3 languages with percentages, e.g. "Python (62%), Go (30%)"."""

    column = "AM"
    field_name = "stack"

    def compute(self, ctx: RepoContext) -> Any:
        langs = ctx.scc_stats_no_deps.get("languages", [])
        total_code = ctx.scc_stats_no_deps["total"]["code"]
        if total_code == 0 or not langs:
            return ""
        ranked = sorted(
            ((l["name"], l["code"]) for l in langs if l["code"] > 0),
            key=lambda x: -x[1],
        )[:3]
        return ", ".join(f"{name} ({code / total_code:.0%})" for name, code in ranked)
