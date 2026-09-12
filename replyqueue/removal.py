"""Removal detection: poll manually-posted permalinks for 72 hours.

A mod-removed reply is the highest-signal label this tool produces - it means
the draft read as spam to the one audience that matters. Those labels feed the
stats page and (later) the drafter's negative examples.

Reddit side is deliberately NOT the API: we re-read the public thread RSS the
same way a feed reader would. If the comment id vanishes from the thread's RSS
or the thread itself stops serving, we mark it removed. This is best-effort -
RSS lag and Reddit throttling can delay detection by hours. HN uses the
official Algolia items API (dead/deleted flags).
"""
from __future__ import annotations

import time

import httpx

from .db import utcnow
from .sources.reddit_rss import parse_feed

WINDOW_SECONDS = 72 * 3600


def record_posted(conn, draft_id: int, permalink: str) -> int:
    permalink = permalink.strip()
    if "reddit.com" not in permalink and "news.ycombinator.com" not in permalink:
        raise ValueError("permalink must be a reddit.com or news.ycombinator.com URL")
    cur = conn.execute(
        "INSERT INTO posted(draft_id, permalink, posted_at, deadline_utc) VALUES (?, ?, ?, ?)",
        (draft_id, permalink, utcnow(), int(time.time()) + WINDOW_SECONDS),
    )
    conn.commit()
    return cur.lastrowid


def _check_reddit(client, permalink: str) -> str:
    """Return 'alive' | 'removed' | 'unknown' for a reddit permalink."""
    rss_url = permalink.split("?")[0].rstrip("/") + "/.rss"
    try:
        resp = client.get(rss_url)
    except httpx.HTTPError:
        return "unknown"
    if resp.status_code in (403, 404):
        return "removed"
    if resp.status_code != 200 or not resp.text.strip():
        return "unknown"
    if "[removed]" in resp.text or "[deleted]" in resp.text:
        return "removed"
    try:
        entries = parse_feed(resp.text)
    except Exception:
        return "unknown"
    if not entries:
        return "unknown"
    # comment permalinks end with .../<comment_id>/; check that id still shows up
    parts = [p for p in permalink.rstrip("/").split("/") if p]
    comment_id = parts[-1] if parts else ""
    if comment_id and len(comment_id) >= 6:
        for e in entries:
            if comment_id in (e.get("source_id") or "") or comment_id in (e.get("permalink") or ""):
                return "alive"
        # thread-level permalink (a post, not a comment)
        if "/comments/" in permalink and permalink.rstrip("/").split("/")[-2:-1] != ["comments"]:
            thread_id = permalink.split("/comments/")[1].split("/")[0]
            if comment_id == thread_id:
                return "alive"
        return "removed"
    return "alive"


def _check_hn(client, permalink: str) -> str:
    try:
        oid = permalink.split("id=")[-1]
        resp = client.get(f"https://hn.algolia.com/api/v1/items/{oid}")
        if resp.status_code == 404:
            return "removed"
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError, IndexError):
        return "unknown"
    if data.get("deleted") or data.get("dead"):
        return "removed"
    return "alive"


def check_posted(conn, cfg, force: bool = False) -> dict:
    stats = {"checked": 0, "removed": 0, "survived": 0, "still_watching": 0, "unknown": 0}
    client = httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        timeout=cfg.request_timeout_seconds,
        follow_redirects=True,
    )
    rows = conn.execute("SELECT * FROM posted WHERE status = 'watching'").fetchall()
    for row in rows:
        if not force and row["last_checked_at"]:
            last = time.mktime(time.strptime(row["last_checked_at"], "%Y-%m-%dT%H:%M:%SZ"))
            if time.time() - last < 3600:
                stats["still_watching"] += 1
                continue
        if "reddit.com" in row["permalink"]:
            verdict = _check_reddit(client, row["permalink"])
        else:
            verdict = _check_hn(client, row["permalink"])
        stats["checked"] += 1
        now_ts = time.time()
        if verdict == "removed":
            conn.execute(
                "UPDATE posted SET status='removed', last_checked_at=?, checks=checks+1 WHERE id=?",
                (utcnow(), row["id"]),
            )
            stats["removed"] += 1
        elif now_ts >= row["deadline_utc"]:
            status = "survived" if verdict == "alive" else "survived"
            conn.execute(
                "UPDATE posted SET status=?, last_checked_at=?, checks=checks+1 WHERE id=?",
                (status, utcnow(), row["id"]),
            )
            stats["survived"] += 1
        else:
            conn.execute(
                "UPDATE posted SET last_checked_at=?, checks=checks+1 WHERE id=?",
                (utcnow(), row["id"]),
            )
            stats["unknown" if verdict == "unknown" else "still_watching"] += 1
        time.sleep(1.0)
    conn.commit()
    return stats
