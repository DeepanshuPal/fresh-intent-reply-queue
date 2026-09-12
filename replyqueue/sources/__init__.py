from .base import SourceItem, Source
from .reddit_rss import RedditRSSSource
from .hn_algolia import HNAlgoliaSource

__all__ = ["SourceItem", "Source", "RedditRSSSource", "HNAlgoliaSource"]


def build_sources(cfg):
    sources = []
    if cfg.subreddits:
        sources.append(RedditRSSSource(cfg))
    if cfg.hn_queries:
        sources.append(HNAlgoliaSource(cfg))
    return sources
