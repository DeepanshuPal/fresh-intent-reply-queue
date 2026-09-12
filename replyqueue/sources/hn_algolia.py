"""Hacker News discovery via the Algolia HN Search API.

Free, keyless, documented at https://hn.algolia.com/api.
Endpoint used: GET /api/v1/search_by_date?query=..&tags=(story,comment)
Response shape verified live 2026-09-12: stories carry title/url/story_text,
comments carry story_title/comment_text/story_id - both handled.
"""
from __future__ import annotations

import re

import httpx

from .base import SourceItem
from .reddit_rss import html_to_text

TAG_RE = re.compile(r"<[^>]+>")


class HNAlgoliaSource:
    name = "hn_algolia"
    API = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self, cfg):
        self.cfg = cfg
        self._client = httpx.Client(
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.request_timeout_seconds,
        )

    def fetch(self) -> list[SourceItem]:
        items: list[SourceItem] = []
        for query in self.cfg.hn_queries:
            try:
                resp = self._client.get(
                    self.API,
                    params={"query": query, "tags": "(story,comment)", "hitsPerPage": 25},
                )
                resp.raise_for_status()
                hits = resp.json().get("hits", [])
            except (httpx.HTTPError, ValueError):
                continue
            for hit in hits:
                oid = str(hit.get("objectID", ""))
                if not oid:
                    continue
                if "title" in hit and hit.get("title") is not None:
                    title = hit.get("title") or ""
                    body = html_to_text(hit.get("story_text") or "")
                    link_url = hit.get("url")
                else:  # comment hit
                    title = f"Re: {hit.get('story_title') or ''}"
                    body = html_to_text(hit.get("comment_text") or "")
                    link_url = None
                items.append(
                    SourceItem(
                        source=self.name,
                        source_id=oid,
                        channel="hn",
                        title=title.strip(),
                        body=body,
                        url=link_url,
                        permalink=f"https://news.ycombinator.com/item?id={oid}",
                        author=hit.get("author"),
                        points=int(hit.get("points") or 0),
                        created_utc=hit.get("created_at_i"),
                        raw=hit,
                    )
                )
        return items
