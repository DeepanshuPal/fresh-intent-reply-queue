"""Offline heuristic engine - the no-key fallback.

Scores buyer intent with transparent, tunable signals so the pipeline is
usable (and verifiable) with zero spend. It deliberately does NOT draft:
a heuristic 'draft' is the template slop this tool exists to kill. Drafting
requires an LLM key (BYOK), as documented in the README.
"""
from __future__ import annotations

import re
import time

from .base import LLMResult

INTENT_PHRASES = [
    "looking for", "recommend", "recommendation", "suggestions", "any suggestions",
    "alternative to", "alternatives", "what tool", "which tool", "best tool",
    "how do i", "how do you", "how can i", "anyone know", "anyone using",
    "help me", "struggling", "frustrated", "fed up", "need advice", "need help",
    "what do you use", "how are you", "first customers", "get customers",
    "find customers", "lead gen", "leads",
]


class OfflineLLM:
    name = "offline"

    def __init__(self, cfg):
        self.cfg = cfg

    def heuristic_score(self, title: str, body: str, keyword_hits: list[str],
                        created_utc: int | None, points: int = 0) -> tuple[int, str, bool]:
        text = f"{title}\n{body}".lower()
        score = 0
        reasons = []
        if "?" in title or "?" in body[:400]:
            score += 15
            reasons.append("asks a question")
        phrase_hits = [p for p in INTENT_PHRASES if p in text]
        if phrase_hits:
            bump = min(36, 12 * len(phrase_hits))
            score += bump
            reasons.append("intent phrases: " + ", ".join(phrase_hits[:4]))
        if keyword_hits:
            bump = min(20, 4 * len(keyword_hits))
            score += bump
            reasons.append("keyword hits: " + ", ".join(keyword_hits[:4]))
        if created_utc:
            age_h = (time.time() - created_utc) / 3600
            if age_h < 24:
                score += 10
                reasons.append("fresh (<24h)")
            elif age_h < 72:
                score += 5
        words = len(re.findall(r"\w+", body))
        if 40 <= words <= 800:
            score += 5
            reasons.append("substantive post")
        if points >= 10:
            score += 5
        score = max(0, min(100, score))
        commercial = bool(keyword_hits)
        return score, "; ".join(reasons) or "no intent signals", commercial

    def complete(self, system: str, user: str, model: str, max_tokens: int = 800,
                 temperature: float = 0.4) -> LLMResult:
        raise RuntimeError(
            "Offline engine does not draft. Set OPENROUTER_API_KEY (BYOK) to enable "
            "LLM intent scoring and drafting - see README."
        )
