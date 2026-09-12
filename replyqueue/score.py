"""Intent scoring 0-100. LLM when keyed (BYOK), transparent heuristic otherwise."""
from __future__ import annotations

import json
import re

from .db import utcnow

PROMPT_VERSION = "intent-v1"

SYSTEM = """You qualify buyer intent in public forum posts for a specific business.
Read the post and the ICP (ideal customer profile). Judge: is the author a
potential buyer or a high-value conversation for this business RIGHT NOW?

Return STRICT JSON only, no prose:
{"score": <integer 0-100>, "rationale": "<=20 words", "commercial": <true|false>}

Score guide:
  80-100 actively asking for a solution this business sells, ready to engage
  60-79  clear problem this business solves, open to suggestions
  40-59  adjacent pain, worth watching
  0-39   not a buyer conversation
commercial = true when replying could naturally mention the business's product."""


def parse_score(text: str) -> tuple[int, str, bool]:
    """Robustly extract {score, rationale, commercial} from an LLM reply."""
    match = re.search(r"\{[^{}]*\}", text or "", re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            score = int(float(data.get("score", 0)))
            rationale = str(data.get("rationale", ""))[:300]
            commercial = bool(data.get("commercial", False))
            return max(0, min(100, score)), rationale, commercial
        except (ValueError, TypeError):
            pass
    m = re.search(r"score[\"'\s:]+(\d{1,3})", text or "", re.IGNORECASE)
    if m:
        return max(0, min(100, int(m.group(1)))), "", False
    raise ValueError(f"could not parse score from LLM output: {(text or '')[:120]!r}")


def score_items(conn, cfg, llm, item_ids: list[int]) -> dict:
    """Score the given items. Returns run stats. Skips already-scored items."""
    from .llm.offline import OfflineLLM

    stats = {"scored": 0, "skipped": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
    for item_id in item_ids:
        if conn.execute("SELECT 1 FROM scores WHERE item_id = ?", (item_id,)).fetchone():
            stats["skipped"] += 1
            continue
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        hits = json.loads(item["keyword_hits"] or "[]")
        if isinstance(llm, OfflineLLM):
            score, rationale, commercial = llm.heuristic_score(
                item["title"], item["body"], hits, item["created_utc"], item["points"]
            )
            model, pv, tin, tout, cost = "offline-heuristic", "heuristic-v1", 0, 0, 0.0
        else:
            user = (
                f"ICP:\n{cfg.icp}\n\n"
                f"POST (r/{item['channel']}, by u/{item['author'] or '?'}):\n"
                f"Title: {item['title']}\n\n{item['body'][:2500]}"
            )
            try:
                res = llm.complete(SYSTEM, user, cfg.models["score"], max_tokens=200, temperature=0.2)
                score, rationale, commercial = parse_score(res.text)
            except Exception as exc:
                stats["errors"] = stats.get("errors", 0) + 1
                stats.setdefault("error_samples", []).append(f"{item['source_id']}: {exc}")
                continue
            model, pv = res.model, PROMPT_VERSION
            tin, tout, cost = res.tokens_in, res.tokens_out, res.cost_usd
        conn.execute(
            "INSERT OR REPLACE INTO scores(item_id, intent_score, rationale, commercial,"
            " model, prompt_version, tokens_in, tokens_out, cost_usd, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, score, rationale, int(commercial), model, pv, tin, tout, cost, utcnow()),
        )
        stats["scored"] += 1
        stats["cost_usd"] += cost
        stats["tokens_in"] += tin
        stats["tokens_out"] += tout
    conn.commit()
    return stats
