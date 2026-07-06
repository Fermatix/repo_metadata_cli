"""Columns K, L, M — Source file count, primary language, language distribution.

Also hosts AL/AM (extensions, stack) ported from v1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import get_lang_code_no_autogen, get_scc_file_stats


def _is_code_language(name: str, ctx: RepoContext) -> bool:
    """True when *name* (scc spelling) is a real programming language.

    Markup/style/data/config/docs formats (JSON, YAML, Markdown, HTML, CSS, …)
    are listed in settings.metrics.non_code_languages — same set and semantics
    as the downstream batch scripts' NON_CODE filter.
    """
    return name not in set(ctx.settings.metrics.non_code_languages)


def _per_lang_code(ctx: RepoContext) -> dict[str, int]:
    """scc Code lines per language, excluding dependency dirs AND autogen files."""
    return ctx._cached(
        "per_lang_code",
        lambda: get_lang_code_no_autogen(
            ctx.repo_path,
            autogen_dirs=set(ctx.settings.metrics.autogen_dirs),
            exclude_dirs=list(ctx.settings.metrics.scc_exclude_dirs),
        ),
    )


def _shares(per_lang: dict[str, int]) -> dict[str, float]:
    """Language → fraction of Code lines, keeping only shares ≥ 1%."""
    total_code = sum(per_lang.values())
    if total_code == 0:
        return {}
    return {
        name: round(code / total_code, 6)
        for name, code in per_lang.items()
        if code / total_code >= 0.01
    }


def _lang_distribution(ctx: RepoContext) -> dict[str, float]:
    """Real programming language → fraction of hand-written Code lines ≥ 1%.

    Counts scc Code lines per language excluding dependency dirs
    (node_modules/vendor) AND auto-generated files (committed bundles, minified
    assets, generated stubs). This keeps primary_language anchored to the code a
    human actually wrote: a TypeScript app shipping a large committed
    ``app.bundle.js`` is reported as TypeScript, not JavaScript.

    Non-code languages (JSON/YAML/Markdown/HTML/…) are dropped BEFORE the total
    is taken, so the remaining shares renormalize over real code only. A repo
    with no real code at all yields {} (and primary_language == "").

    Cached on the context so PrimaryLanguageMetric (L) and LangDistributionMetric (M)
    stay consistent — primary_language is always the max key of this distribution.
    """
    def _compute() -> dict[str, float]:
        per_lang = {
            name: code
            for name, code in _per_lang_code(ctx).items()
            if _is_code_language(name, ctx)
        }
        return _shares(per_lang)

    return ctx._cached("lang_distribution", _compute)


def _full_lang_distribution(ctx: RepoContext) -> dict[str, float]:
    """Unfiltered variant of _lang_distribution: every scc language kept.

    Same dependency-dir/autogen exclusions and ≥1% cutoff, but non-code formats
    (JSON/YAML/Markdown/…) stay in — the raw picture of what the repo contains.
    """
    return ctx._cached("full_lang_distribution", lambda: _shares(_per_lang_code(ctx)))


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
    """M: JSON dict of real language → fraction of Code lines ≥ 1% (excl node_modules/vendor)."""

    column = "M"
    field_name = "lang_distribution"

    def compute(self, ctx: RepoContext) -> Any:
        return json.dumps(_lang_distribution(ctx), ensure_ascii=False)


class FullLangDistributionMetric(BaseMetric):
    """AV: same as M but WITHOUT the non-code language filter (JSON/YAML/… kept)."""

    column = "AV"
    field_name = "full_lang_distribution"

    def compute(self, ctx: RepoContext) -> Any:
        return json.dumps(_full_lang_distribution(ctx), ensure_ascii=False)


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
    """AM: Human-readable top-3 REAL languages with percentages, e.g. "Python (62%), Go (30%)".

    Non-code formats (JSON/YAML/Markdown/…) are dropped and percentages are
    taken over the remaining real-code lines, matching lang_distribution (M).
    """

    column = "AM"
    field_name = "stack"

    def compute(self, ctx: RepoContext) -> Any:
        langs = [
            l for l in ctx.scc_stats_no_deps.get("languages", [])
            if l["code"] > 0 and _is_code_language(l["name"], ctx)
        ]
        total_code = sum(l["code"] for l in langs)
        if total_code == 0:
            return ""
        ranked = sorted(
            ((l["name"], l["code"]) for l in langs),
            key=lambda x: -x[1],
        )[:3]
        return ", ".join(f"{name} ({code / total_code:.0%})" for name, code in ranked)
