"""Column D — LLM-generated repository description via OpenRouter API."""

from __future__ import annotations

import logging
import os
from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..utils import run_cmd

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL = "google/gemini-3-flash-preview"
_MAX_TOKENS = 120

_SYSTEM_PROMPT = (
    "You are a senior software engineer writing a one-line internal wiki entry for a codebase. "
    "Write exactly 1 sentence. Cover: (1) what the system does, (2) its type (mobile app / backend API / web app / CLI / library / etc.), "
    "(3) the main tech stack. "
    "No bullet points, no markdown, no headers, no hyphens as list separators. Plain prose only. "
    "Sound like a human engineer, not a doc generator. "
    "CRITICAL — Privacy and anonymity rules you must follow without exception: "
    "Do NOT mention any project names, product names, company names, brand names, or organisation names found in the code or files. "
    "Do NOT include any personally identifiable information (PII): no person names, emails, usernames, phone numbers, URLs, or domain names. "
    "Do NOT reproduce any string that looks like a secret, token, key, or credential. "
    "Describe only the technical purpose and architecture in generic terms (e.g. 'a REST API', 'a React frontend', 'a data-processing pipeline'). "
    "If you are unsure whether a term is a proper noun or PII, omit it and use a generic description instead."
)

_USER_TEMPLATE = """\
Languages: {lang_distribution}
File tree (sample):
{file_tree}
Key file excerpts:
{excerpts}
---
Write a single plain-text sentence describing this codebase.\
"""


def _build_context(ctx: RepoContext) -> str:
    scc = ctx.scc_stats
    langs = scc.get("languages", [])
    total_code = scc["total"]["code"] or 1
    lang_parts = [
        f"{l['name']} {round(l['code'] / total_code * 100)}%"
        for l in sorted(langs, key=lambda x: -x["code"])
        if l["code"] > 0
    ][:5]
    lang_distribution = ", ".join(lang_parts) or "unknown"

    file_tree = "\n".join(ctx.file_tree[:30])

    excerpts_parts: list[str] = []
    entry_points = [
        "README.md", "README.rst", "README",
        "main.py", "index.ts", "index.js", "main.go",
        "App.java", "src/main.rs", "app.py",
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    ]
    for name in entry_points:
        p = ctx.repo_path / name
        if p.exists() and p.is_file():
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[:15]
                excerpt = "\n".join(lines)
                excerpts_parts.append(f"# {name}\n{excerpt}")
                if len(excerpts_parts) >= 3:
                    break
            except OSError:
                pass

    excerpts = "\n\n".join(excerpts_parts) or "(no key files found)"
    return _USER_TEMPLATE.format(
        lang_distribution=lang_distribution,
        file_tree=file_tree,
        excerpts=excerpts,
    )


def _call_openrouter(api_key: str, user_content: str) -> str:
    import requests  # transitive dependency, always present

    payload: dict[str, Any] = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("OpenRouter call failed: %s", exc)
        return ""


class DescriptionMetric(BaseMetric):
    column = "D"
    field_name = "description"

    def compute(self, ctx: RepoContext) -> Any:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.debug("OPENROUTER_API_KEY not set; skipping description generation.")
            return ""
        user_content = _build_context(ctx)
        return _call_openrouter(api_key, user_content)
