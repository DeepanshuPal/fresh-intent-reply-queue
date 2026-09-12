import pytest

from replyqueue.db import utcnow

from replyqueue.db import utcnow
from replyqueue.guardrails import check_approve, GuardrailBlock
from tests.conftest import make_item


def _decide(conn, item_id, promotional, decided_at="2026-09-10T00:00:00Z"):
    cur = conn.execute(
        "INSERT INTO drafts(item_id, text, status, promotional, created_at, decided_at)"
        " VALUES (?, 'x', 'approved', ?, ?, ?)",
        (item_id, int(promotional), utcnow(), decided_at),
    )
    conn.commit()
    return cur.lastrowid


def _pending(conn, item_id, promotional=0):
    cur = conn.execute(
        "INSERT INTO drafts(item_id, text, status, promotional, created_at)"
        " VALUES (?, 'x', 'pending', ?, ?)",
        (item_id, promotional, utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def test_weekly_ceiling_blocks(conn, cfg):
    cfg.guardrails.weekly_ceiling = 2
    for i in range(2):
        _decide(conn, make_item(conn, f"t3_w{i}", f"t{i}", "b"), False)
    item_id = make_item(conn, "t3_new", "hello", "world")
    draft = conn.execute("SELECT * FROM drafts WHERE id = ?", (_pending(conn, item_id),)).fetchone()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    with pytest.raises(GuardrailBlock, match="weekly ceiling"):
        check_approve(conn, cfg, draft, item)


def test_subreddit_cooldown_blocks(conn, cfg):
    item_id = make_item(conn, "t3_c1", "a", "b", channel="SaaS")
    _decide(conn, item_id, False, decided_at=utcnow())  # decided just now
    item2 = make_item(conn, "t3_c2", "c", "d", channel="SaaS")
    draft = conn.execute("SELECT * FROM drafts WHERE id = ?", (_pending(conn, item2),)).fetchone()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item2,)).fetchone()
    with pytest.raises(GuardrailBlock, match="cooldown"):
        check_approve(conn, cfg, draft, item)


def test_other_subreddit_not_blocked(conn, cfg):
    item_id = make_item(conn, "t3_d1", "a", "b", channel="SaaS")
    _decide(conn, item_id, False)
    item2 = make_item(conn, "t3_d2", "c", "d", channel="startups")
    draft = conn.execute("SELECT * FROM drafts WHERE id = ?", (_pending(conn, item2),)).fetchone()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item2,)).fetchone()
    check_approve(conn, cfg, draft, item)  # should not raise


def test_promo_ratio_blocks(conn, cfg):
    cfg.guardrails.min_nonpromo_per_promo = 3
    item_id = make_item(conn, "t3_p1", "a", "b")
    draft = conn.execute(
        "SELECT * FROM drafts WHERE id = ?", (_pending(conn, item_id, promotional=1),)
    ).fetchone()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    with pytest.raises(GuardrailBlock, match="promotional ratio"):
        check_approve(conn, cfg, draft, item)
