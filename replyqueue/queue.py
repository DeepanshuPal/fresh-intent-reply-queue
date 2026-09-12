"""Approval queue: the human is the publish step.

list / show / approve / edit / reject. Edits store the unified diff, not just
the verdict - those diffs are the few-shot training signal for future drafts.
"""
from __future__ import annotations

import difflib
import os
import subprocess
import tempfile

from .db import utcnow
from .guardrails import check_approve, log_approval, GuardrailBlock

DECIDED = ("approved", "edited", "rejected")


def pending(conn) -> list:
    return conn.execute(
        "SELECT d.*, i.title, i.channel, i.permalink, s.intent_score FROM drafts d"
        " JOIN items i ON i.id = d.item_id LEFT JOIN scores s ON s.item_id = d.item_id"
        " WHERE d.status = 'pending' ORDER BY s.intent_score DESC, d.id"
    ).fetchall()


def get_draft(conn, draft_id: int):
    row = conn.execute(
        "SELECT d.*, i.title, i.body AS item_body, i.channel, i.permalink, i.author,"
        " s.intent_score, s.rationale FROM drafts d"
        " JOIN items i ON i.id = d.item_id LEFT JOIN scores s ON s.item_id = d.item_id"
        " WHERE d.id = ?",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no draft #{draft_id}")
    return row


def approve(conn, cfg, draft_id: int) -> str:
    row = get_draft(conn, draft_id)
    if row["status"] != "pending":
        raise GuardrailBlock(f"draft #{draft_id} is already {row['status']}")
    item = conn.execute("SELECT * FROM items WHERE id = ?", (row["item_id"],)).fetchone()
    check_approve(conn, cfg, row, item)
    conn.execute(
        "UPDATE drafts SET status = 'approved', decided_at = ? WHERE id = ?",
        (utcnow(), draft_id),
    )
    log_approval(conn, draft_id, row["promotional"])
    conn.commit()
    return "approved"


def reject(conn, draft_id: int, reason: str = "") -> str:
    row = get_draft(conn, draft_id)
    if row["status"] in DECIDED:
        raise GuardrailBlock(f"draft #{draft_id} is already {row['status']}")
    conn.execute(
        "UPDATE drafts SET status = 'rejected', decided_at = ?, reject_reason = ? WHERE id = ?",
        (utcnow(), reason, draft_id),
    )
    conn.commit()
    return "rejected"


def edit(conn, cfg, draft_id: int, new_text: str | None = None) -> str:
    """Edit a pending draft (in $EDITOR when no text given). Stores the diff."""
    row = get_draft(conn, draft_id)
    if row["status"] != "pending":
        raise GuardrailBlock(f"draft #{draft_id} is already {row['status']}")
    before = row["text"]
    if new_text is None:
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix=f"draft-{draft_id}-"
        ) as fh:
            fh.write(before)
            tmp = fh.name
        try:
            subprocess.call([editor, tmp])
            with open(tmp, "r", encoding="utf-8") as fh:
                new_text = fh.read().strip()
        finally:
            os.unlink(tmp)
    new_text = (new_text or "").strip()
    if not new_text or new_text == before:
        raise GuardrailBlock("no changes made; draft left pending")
    diff = "\n".join(
        difflib.unified_diff(
            before.splitlines(), new_text.splitlines(),
            fromfile=f"draft-{draft_id}-before", tofile=f"draft-{draft_id}-after",
            lineterm="",
        )
    )
    # an edit IS an approval of the edited text - guardrails still apply
    item = conn.execute("SELECT * FROM items WHERE id = ?", (row["item_id"],)).fetchone()
    check_approve(conn, cfg, row, item)
    conn.execute(
        "INSERT INTO draft_edits(draft_id, before_text, after_text, diff, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (draft_id, before, new_text, diff, utcnow()),
    )
    conn.execute(
        "UPDATE drafts SET status = 'edited', text = ?, decided_at = ? WHERE id = ?",
        (new_text, utcnow(), draft_id),
    )
    log_approval(conn, draft_id, row["promotional"])
    conn.commit()
    return "edited"
