"""Reply drafting. Value-first, subreddit-aware, few-shot from the user's own edits.

The few-shot loop is the product: every approved/edited draft becomes a style
example, so draft N sounds more like the user than draft 1.
"""
from __future__ import annotations

import json

from .db import utcnow

PROMPT_VERSION = "draft-v1"

SYSTEM = """You ghostwrite forum replies for one specific user. The human approves,
edits or rejects every draft before anything is posted - your job is to make
approval the default.

Rules that are never negotiable:
- Help first. Answer the question in the post before anything else.
- Never open with praise ("great question", "love this") and never use
  marketing words ("game-changer", "supercharge", "unlock").
- About 100 words. Plain sentences. No bullet-listicle unless the post asks.
- Match the subreddit's norms: no links unless asked, disclose affiliation
  plainly if the user's own product is relevant ("full disclosure: I work on
  X"), never pretend to be a stranger to it.
- If the post is not actually a fit, say SKIP instead of writing a reply.
- End with something that moves the conversation forward (a specific
  suggestion or a real follow-up question), not a CTA."""


def fewshot_examples(conn, limit: int = 3) -> tuple[str, list[int]]:
    """Build the style-example block from the user's most recent approvals/edits."""
    rows = conn.execute(
        "SELECT id, text FROM drafts WHERE status IN ('approved', 'edited')"
        " ORDER BY decided_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "", []
    parts = []
    for row in rows:
        parts.append(f"--- example reply the user approved (draft #{row['id']}) ---\n{row['text']}")
    return "\n\n".join(parts), [row["id"] for row in rows]


def draft_for_items(conn, cfg, llm, item_ids: list[int]) -> dict:
    stats = {"drafted": 0, "skipped": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
    examples_block, example_ids = fewshot_examples(conn)
    for item_id in item_ids:
        dupe = conn.execute(
            "SELECT 1 FROM drafts WHERE item_id = ? AND status != 'rejected'", (item_id,)
        ).fetchone()
        if dupe:
            stats["skipped"] += 1
            continue
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        score = conn.execute("SELECT * FROM scores WHERE item_id = ?", (item_id,)).fetchone()
        user_parts = [
            f"THE USER'S VOICE:\n{cfg.voice.notes or '(no notes configured)'}",
        ]
        if cfg.voice.example_replies:
            user_parts.append(
                "REPLIES THE USER WROTE THEMSELVES:\n" + "\n---\n".join(cfg.voice.example_replies)
            )
        if examples_block:
            user_parts.append(f"STYLE EXAMPLES:\n{examples_block}")
        user_parts.append(
            f"CONTEXT:\nSubreddit: r/{item['channel']}\n"
            f"Intent assessment: {score['intent_score']}/100 - {score['rationale']}\n\n"
            f"THE POST TO REPLY TO:\nTitle: {item['title']}\n\n{item['body'][:3000]}"
        )
        user_parts.append("Write the reply now (about 100 words), or SKIP.")
        res = llm.complete(
            SYSTEM, "\n\n".join(user_parts), cfg.models["draft"],
            max_tokens=1500, temperature=0.6,
        )
        text = res.text.strip()
        if not text or text.upper().startswith("SKIP"):
            stats["skipped"] += 1
            continue
        conn.execute(
            "INSERT INTO drafts(item_id, text, model, prompt_version, fewshot_from, status,"
            " promotional, tokens_in, tokens_out, cost_usd, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (
                item_id, text, res.model, PROMPT_VERSION, json.dumps(example_ids),
                score["commercial"], res.tokens_in, res.tokens_out, res.cost_usd, utcnow(),
            ),
        )
        stats["drafted"] += 1
        stats["cost_usd"] += res.cost_usd
        stats["tokens_in"] += res.tokens_in
        stats["tokens_out"] += res.tokens_out
        # refresh examples so later drafts in this run learn from earlier ones
        examples_block, example_ids = fewshot_examples(conn)
    conn.commit()
    return stats
