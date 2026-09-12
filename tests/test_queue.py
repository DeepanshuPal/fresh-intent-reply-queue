import pytest

from replyqueue.queue import approve, edit, reject, get_draft, pending
from replyqueue.guardrails import GuardrailBlock
from tests.conftest import make_item


def _mk(conn, cfg, text="Solid answer: try weekly threads first, they convert better than ads."):
    item_id = make_item(conn, "t3_q1", "How do I market?", "No budget, need ideas.")
    conn.execute(
        "INSERT INTO drafts(item_id, text, status, promotional, created_at)"
        " VALUES (?, ?, 'pending', 0, datetime('now'))",
        (item_id, text),
    )
    conn.commit()
    return conn.execute("SELECT MAX(id) AS i FROM drafts").fetchone()["i"]


def test_approve_sets_status(conn, cfg):
    did = _mk(conn, cfg)
    assert approve(conn, cfg, did) == "approved"
    assert get_draft(conn, did)["status"] == "approved"


def test_double_approve_blocked(conn, cfg):
    did = _mk(conn, cfg)
    approve(conn, cfg, did)
    with pytest.raises(GuardrailBlock):
        approve(conn, cfg, did)


def test_edit_stores_unified_diff(conn, cfg):
    did = _mk(conn, cfg)
    edit(conn, cfg, did, new_text="Shorter: weekly threads, not ads.")
    row = conn.execute("SELECT * FROM draft_edits WHERE draft_id = ?", (did,)).fetchone()
    assert row and "-Solid answer" in row["diff"] and "+Shorter" in row["diff"]
    assert get_draft(conn, did)["status"] == "edited"
    assert get_draft(conn, did)["text"].startswith("Shorter")


def test_reject_keeps_reason(conn, cfg):
    did = _mk(conn, cfg)
    reject(conn, did, "off-topic")
    assert get_draft(conn, did)["reject_reason"] == "off-topic"
    assert pending(conn) == []


def test_fewshot_uses_approvals(conn, cfg):
    from replyqueue.draft import fewshot_examples

    did = _mk(conn, cfg)
    approve(conn, cfg, did)
    block, ids = fewshot_examples(conn)
    assert did in ids and "weekly threads" in block
