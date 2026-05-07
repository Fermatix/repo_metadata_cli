"""Columns K, L, M — Source file count, primary language, language distribution."""

from __future__ import annotations

import json
from typing import Any

from ..base_metric import BaseMetric, RepoContext


class SourceFilesMetric(BaseMetric):
    """K: Total source file count (scc Files column)."""

    column = "K"
    field_name = "source_files"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.scc_stats["total"]["files"]


class PrimaryLanguageMetric(BaseMetric):
    """L: Language with the highest Code line count."""

    column = "L"
    field_name = "primary_language"

    def compute(self, ctx: RepoContext) -> Any:
        langs = ctx.scc_stats.get("languages", [])
        if not langs:
            return ""
        best = max(langs, key=lambda l: l["code"])
        return best["name"] if best["code"] > 0 else ""


class LangDistributionMetric(BaseMetric):
    """M: JSON dict of language → fraction of total Code lines (only langs ≥ 1%)."""

    column = "M"
    field_name = "lang_distribution"

    def compute(self, ctx: RepoContext) -> Any:
        langs = ctx.scc_stats.get("languages", [])
        total_code = ctx.scc_stats["total"]["code"]
        if total_code == 0 or not langs:
            return json.dumps({})
        distribution = {
            l["name"]: round(l["code"] / total_code, 6)
            for l in langs
            if l["code"] / total_code >= 0.01
        }
        return json.dumps(distribution, ensure_ascii=False)
