"""LLM client interface. BYOK: the user's own OpenRouter key, never ours."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class LLM(Protocol):
    name: str

    def complete(
        self, system: str, user: str, model: str, max_tokens: int = 800, temperature: float = 0.4
    ) -> LLMResult: ...


def get_llm(cfg) -> LLM:
    """OpenRouter when a key is configured; otherwise the offline heuristic engine.

    The offline engine scores (heuristically) but never drafts - drafting without
    a real model produces exactly the template slop this tool exists to avoid.
    """
    if cfg.api_key():
        from .openrouter import OpenRouterLLM

        return OpenRouterLLM(cfg)
    from .offline import OfflineLLM

    return OfflineLLM(cfg)
