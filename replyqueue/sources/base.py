"""Source adapter interface. Every discovery source yields normalized SourceItems."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SourceItem:
    source: str          # reddit_rss | hn_algolia
    source_id: str       # stable id from the source (t3_x, HN objectID)
    channel: str         # subreddit name or 'hn'
    title: str
    body: str
    url: str | None
    permalink: str | None
    author: str | None
    points: int
    created_utc: int | None
    raw: dict = field(default_factory=dict)


class Source(Protocol):
    name: str

    def fetch(self) -> list[SourceItem]:
        """Pull the newest items. Must be keyless and free."""
        ...
