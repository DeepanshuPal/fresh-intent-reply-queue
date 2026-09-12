"""OpenRouter chat-completions client. Docs: https://openrouter.ai/docs/api-reference

Verified live 2026-09-12 against /api/v1/models (445 models listed, free-tier
':free' variants carry zero pricing) and /api/v1/chat/completions.
We pass usage.include=true so responses carry token usage; OpenRouter also
returns usage.cost (credits) for paid models - 0 on :free models.
"""
from __future__ import annotations

import time

import httpx

from .base import LLMResult


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, cfg):
        self.cfg = cfg
        self._client = httpx.Client(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {cfg.api_key()}",
                "HTTP-Referer": "https://github.com/DeepanshuPal/fresh-intent-reply-queue",
                "X-Title": "fresh-intent-reply-queue",
            },
            timeout=httpx.Timeout(cfg.request_timeout_seconds + 60),
        )

    def complete(
        self, system: str, user: str, model: str, max_tokens: int = 800, temperature: float = 0.4
    ) -> LLMResult:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "usage": {"include": True},
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._client.post("/chat/completions", json=body)
                if resp.status_code in (429, 500, 502, 503):
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                text = data["choices"][0]["message"].get("content")
                if not text:  # reasoning models occasionally return null content
                    last_err = RuntimeError(f"empty completion from {model}")
                    time.sleep(2 * (attempt + 1))
                    continue
                return LLMResult(
                    text=text,
                    model=data.get("model", model),
                    tokens_in=int(usage.get("prompt_tokens") or 0),
                    tokens_out=int(usage.get("completion_tokens") or 0),
                    cost_usd=float(usage.get("cost") or 0.0),
                )
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_err = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"OpenRouter call failed after retries: {last_err}")
