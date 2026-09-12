"""Parse the REAL fixtures captured from live endpoints on 2026-09-12."""
import json
import os

from replyqueue.sources.reddit_rss import parse_feed, html_to_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_real_reddit_rss():
    with open(os.path.join(FIXTURES, "reddit_startups_new.rss")) as fh:
        entries = parse_feed(fh.read())
    assert len(entries) >= 5
    e = entries[0]
    assert e["source_id"].startswith("t3_")
    assert "reddit.com/r/" in e["permalink"]
    assert e["title"]
    assert "<" not in e["body"]  # html stripped


def test_real_algolia_shape():
    with open(os.path.join(FIXTURES, "hn_algolia_search.json")) as fh:
        data = json.load(fh)
    assert data["hits"], "fixture should contain hits"
    hit = data["hits"][0]
    assert "objectID" in hit and "author" in hit
    assert ("title" in hit) or ("comment_text" in hit)


def test_html_to_text():
    assert html_to_text("&lt;p&gt;hi &amp; bye&lt;/p&gt;") == "hi & bye"
