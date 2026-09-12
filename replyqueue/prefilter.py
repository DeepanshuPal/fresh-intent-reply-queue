"""Regex keyword prefilter + dedupe. Cheap, deterministic, runs before any LLM call."""
from __future__ import annotations

import hashlib
import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256((normalize(title) + "\n" + normalize(body)[:4000]).encode()).hexdigest()


def compile_keywords(keywords: list[str]) -> re.Pattern | None:
    if not keywords:
        return None
    parts = [re.escape(k.strip()) for k in keywords if k.strip()]
    if not parts:
        return None
    return re.compile(r"(?i)(" + "|".join(parts) + ")")


def keyword_hits(pattern: re.Pattern | None, title: str, body: str) -> list[str]:
    if pattern is None:
        return []
    hay = f"{title}\n{body}"
    return sorted({m.group(0).lower() for m in pattern.finditer(hay)})


def passes(title: str, body: str, kw: re.Pattern | None, neg: re.Pattern | None) -> tuple[bool, list[str]]:
    if neg is not None and neg.search(f"{title}\n{body}"):
        return False, []
    hits = keyword_hits(kw, title, body)
    if kw is None:
        return True, hits
    return (len(hits) > 0), hits
