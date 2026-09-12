"""Reddit discovery over public Atom feeds - the .rss view of subreddit pages.

No Reddit API key anywhere. Reddit's Responsible Builder Policy gates commercial
API use, so this tool only reads the same public RSS/Atom views a feed reader
would. Verified against live feeds 2026-09-12:
  /r/<sub>/new/.rss      - newest submissions (default discovery)
  /r/<sub>/search/.rss   - keyword search, restrict_sr=1 (optional, throttled harder)

Notes from live testing:
  * Reddit rate-limits datacenter IPs and bare script UAs; a descriptive UA and
    backoff are mandatory, and even then search/.rss intermittently returns
    empty responses. /new/.rss is the reliable surface.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET

import httpx

from .base import SourceItem

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def html_to_text(fragment: str) -> str:
    text = html.unescape(fragment or "")
    text = TAG_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def parse_feed(xml_text: str) -> list[dict]:
    """Parse a Reddit Atom feed into plain dicts. Raises on malformed XML."""
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        category = entry.find(f"{ATOM}category")
        author = entry.find(f"{ATOM}author/{ATOM}name")
        published = entry.find(f"{ATOM}published") or entry.find(f"{ATOM}updated")
        content = entry.find(f"{ATOM}content")
        title_el = entry.find(f"{ATOM}title")
        id_el = entry.find(f"{ATOM}id")
        ts = 0
        if published is not None and published.text:
            try:
                ts = int(time.mktime(time.strptime(published.text[:19], "%Y-%m-%dT%H:%M:%S")))
            except ValueError:
                ts = 0
        out.append(
            {
                "source_id": (id_el.text or "").strip(),
                "permalink": link.get("href") if link is not None else None,
                "channel": category.get("term") if category is not None else "",
                "author": (author.text or "").replace("/u/", "") if author is not None else None,
                "created_utc": ts,
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "body": html_to_text(content.text if content is not None else ""),
            }
        )
    return out


class RedditRSSSource:
    name = "reddit_rss"

    def __init__(self, cfg):
        self.cfg = cfg
        self._client = httpx.Client(
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.request_timeout_seconds,
            follow_redirects=True,
        )

    def _get(self, url: str) -> str | None:
        """GET with backoff. Returns None when Reddit throttles or errors - a
        skipped feed is logged by the caller, never fatal. file:// URLs replay
        a captured feed from disk (used by tests and offline verification)."""
        if url.startswith("file://"):
            try:
                with open(url[7:], "r", encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return None
        delay = 2.0
        for attempt in range(3):
            try:
                resp = self._client.get(url)
            except httpx.HTTPError:
                time.sleep(delay)
                delay *= 3
                continue
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            time.sleep(delay)
            delay *= 3
        return None

    def _urls(self) -> list[str]:
        if self.cfg.reddit_feed_urls:
            return list(self.cfg.reddit_feed_urls)
        limit = 25
        urls = [
            f"https://www.reddit.com/r/{sub}/new/.rss?limit={limit}"
            for sub in self.cfg.subreddits
        ]
        if self.cfg.reddit_search:
            for sub in self.cfg.subreddits:
                for kw in self.cfg.keywords[:5]:
                    q = httpx.QueryParams({"q": kw, "restrict_sr": "1", "sort": "new", "limit": "10"})
                    urls.append(f"https://www.reddit.com/r/{sub}/search/.rss?{q}")
        return urls

    def fetch(self) -> list[SourceItem]:
        items: list[SourceItem] = []
        for url in self._urls():
            xml_text = self._get(url)
            if xml_text is None:
                continue
            try:
                entries = parse_feed(xml_text)
            except ET.ParseError:
                continue
            for e in entries:
                if not e["source_id"]:
                    continue
                items.append(
                    SourceItem(
                        source=self.name,
                        source_id=e["source_id"],
                        channel=e["channel"],
                        title=e["title"],
                        body=e["body"],
                        url=e["permalink"],
                        permalink=e["permalink"],
                        author=e["author"],
                        points=0,
                        created_utc=e["created_utc"] or None,
                        raw=e,
                    )
                )
            time.sleep(3.5)  # be a good feed-reader citizen; Reddit throttles bursts
        return items
