"""Token counting utilities (lazy-loading transformers tokenizer)."""

from __future__ import annotations

import logging
from typing import List, Optional

try:
    from transformers import AutoTokenizer  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AutoTokenizer = None  # type: ignore

logger = logging.getLogger(__name__)


class TokenizerProvider:
    """Lazily loads a HuggingFace tokenizer for counting tokens."""

    def __init__(self, tokenizer_id: str | None, trust_remote_code: bool = True) -> None:
        self.tokenizer_id = tokenizer_id
        self.trust_remote_code = trust_remote_code
        self._tokenizer = None

    def _ensure_tokenizer(self):
        if self._tokenizer is not None:
            return self._tokenizer
        if self.tokenizer_id is None:
            logger.info("Tokenizer id is not provided; token counting disabled.")
            return None
        if AutoTokenizer is None:
            logger.warning("transformers is not installed; token counting disabled.")
            return None
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_id, trust_remote_code=self.trust_remote_code
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load tokenizer %s: %s", self.tokenizer_id, exc)
            self._tokenizer = None
        return self._tokenizer

    def count_tokens_batch(self, texts: List[str]) -> int:
        tokenizer = self._ensure_tokenizer()
        if tokenizer is None:
            return 0
        if not texts:
            return 0
        enc = tokenizer(
            texts,
            add_special_tokens=False,
            padding=False,
            truncation=False,
        )
        return sum(len(ids) for ids in enc["input_ids"])
