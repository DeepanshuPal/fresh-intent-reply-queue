"""Single-file YAML config. Everything the tool does is driven from here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

DEFAULT_CONFIG_NAMES = ("replyqueue.yaml", "config.yaml", "config.example.yaml")


@dataclass
class MatchConfig:
    backend: str = "fts"
    threshold: float = 0.10


@dataclass
class BudgetConfig:
    max_score_calls_per_run: int = 20
    max_drafts_per_run: int = 5
    min_intent_score_to_draft: int = 60


@dataclass
class GuardrailConfig:
    weekly_ceiling: int = 10
    subreddit_cooldown_hours: int = 48
    min_nonpromo_per_promo: int = 3


@dataclass
class VoiceConfig:
    notes: str = ""
    example_replies: list[str] = field(default_factory=list)


@dataclass
class Config:
    subreddits: list[str] = field(default_factory=list)
    reddit_feed_urls: list[str] = field(default_factory=list)
    reddit_search: bool = False
    hn_queries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    icp: str = ""
    match: MatchConfig = field(default_factory=MatchConfig)
    models: dict = field(default_factory=lambda: {"score": "", "draft": ""})
    openrouter_api_key_env: str = "OPENROUTER_API_KEY"
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    user_agent: str = "fresh-intent-reply-queue/0.1"
    request_timeout_seconds: int = 30
    db_path: str = "replyqueue.db"
    path: str = ""

    def api_key(self) -> str | None:
        return os.environ.get(self.openrouter_api_key_env)


def load_config(path: str | None = None) -> Config:
    if path is None:
        for name in DEFAULT_CONFIG_NAMES:
            if os.path.exists(name):
                path = name
                break
        else:
            raise SystemExit(
                "No config found. Run `replyqueue init` to create replyqueue.yaml"
            )
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(
        subreddits=list(raw.get("subreddits", [])),
        reddit_feed_urls=list(raw.get("reddit_feed_urls", [])),
        reddit_search=bool(raw.get("reddit_search", False)),
        hn_queries=list(raw.get("hn_queries", [])),
        keywords=list(raw.get("keywords", [])),
        negative_keywords=list(raw.get("negative_keywords", [])),
        icp=str(raw.get("icp", "")),
        match=MatchConfig(**raw.get("match", {})),
        models=dict(raw.get("models", {"score": "", "draft": ""})),
        openrouter_api_key_env=raw.get("openrouter_api_key_env", "OPENROUTER_API_KEY"),
        budgets=BudgetConfig(**raw.get("budgets", {})),
        voice=VoiceConfig(**raw.get("voice", {})),
        guardrails=GuardrailConfig(**raw.get("guardrails", {})),
        user_agent=raw.get("user_agent", "fresh-intent-reply-queue/0.1"),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 30)),
        db_path=raw.get("db_path", "replyqueue.db"),
        path=path,
    )
    return cfg
