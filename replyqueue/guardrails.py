"""Rate and ratio caps, enforced at approve time and logged append-only.

These exist because the human posting loop only stays compliant (and welcome)
when volume stays human-scale: a weekly ceiling, a per-subreddit cooldown, and
a minimum non-promotional:promotional ratio.
"""
from __future__ import annotations

import time

from .db import utcnow


def _log(conn, kind: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO guardrail_events(kind, detail, created_at) VALUES (?, ?, ?)",
        (kind, detail, utcnow()),
    )


class GuardrailBlock(Exception):
    pass


def check_approve(conn, cfg, draft, item) -> None:
    """Raise GuardrailBlock with a human reason when an approval violates a cap."""
    g = cfg.guardrails
    now = time.time()

    decided_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE status IN ('approved', 'edited')"
        " AND decided_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]
    if decided_7d >= g.weekly_ceiling:
        _log(conn, "weekly_ceiling", f"{decided_7d} approvals in 7d >= ceiling {g.weekly_ceiling}")
        raise GuardrailBlock(
            f"weekly ceiling reached ({decided_7d}/{g.weekly_ceiling} approved in the last 7 days). "
            "Slow down - this cap is what keeps the account human-scale."
        )

    row = conn.execute(
        "SELECT MAX(d.decided_at) AS last FROM drafts d JOIN items i ON i.id = d.item_id"
        " WHERE d.status IN ('approved', 'edited') AND i.channel = ?",
        (item["channel"],),
    ).fetchone()
    if row["last"]:
        last_ts = time.mktime(time.strptime(row["last"], "%Y-%m-%dT%H:%M:%SZ"))
        elapsed_h = (now - last_ts) / 3600
        if elapsed_h < g.subreddit_cooldown_hours:
            _log(
                conn, "subreddit_cooldown",
                f"r/{item['channel']} last approved {elapsed_h:.1f}h ago < {g.subreddit_cooldown_hours}h",
            )
            raise GuardrailBlock(
                f"r/{item['channel']} cooldown: you approved a reply there {elapsed_h:.1f}h ago "
                f"(cooldown {g.subreddit_cooldown_hours}h). Repeating yourself in one sub reads as spam."
            )

    if draft["promotional"]:
        counts = conn.execute(
            "SELECT promotional, COUNT(*) AS n FROM drafts"
            " WHERE status IN ('approved', 'edited') GROUP BY promotional"
        ).fetchall()
        promo = sum(r["n"] for r in counts if r["promotional"])
        nonpromo = sum(r["n"] for r in counts if not r["promotional"])
        required = g.min_nonpromo_per_promo * (promo + 1)
        if nonpromo < required:
            _log(
                conn, "promo_ratio",
                f"nonpromo {nonpromo} < required {required} for promo #{promo + 1}",
            )
            raise GuardrailBlock(
                f"promotional ratio: approving this would make {promo + 1} promotional approvals "
                f"against {nonpromo} helpful ones (need {g.min_nonpromo_per_promo}:1). "
                "Go be useful in a few more threads first."
            )


def log_approval(conn, draft_id: int, promotional: bool) -> None:
    _log(conn, "approved", f"draft #{draft_id} approved (promotional={bool(promotional)})")
