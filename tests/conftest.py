import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from replyqueue.config import Config
from replyqueue.db import connect


@pytest.fixture()
def cfg(tmp_path):
    return Config(
        subreddits=["startups"],
        hn_queries=["reddit marketing"],
        keywords=["looking for", "recommend", "what tool"],
        negative_keywords=["hiring"],
        icp="I sell to founders trying to find customers and do community-led growth.",
        db_path=str(tmp_path / "test.db"),
        path=str(tmp_path / "cfg.yaml"),
    )


@pytest.fixture()
def conn(cfg, tmp_path):
    with open(cfg.path, "w") as fh:
        fh.write("subreddits: [startups]\n")
    c = connect(cfg.db_path)
    yield c
    c.close()


def make_item(conn, source_id, title, body, channel="startups", prefilter=1,
              hits='["recommend"]'):
    from replyqueue.db import utcnow
    from replyqueue.prefilter import content_hash

    cur = conn.execute(
        "INSERT INTO items(source, source_id, channel, title, body, url, permalink,"
        " author, points, created_utc, ingested_at, content_hash, prefilter_pass,"
        " keyword_hits) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("reddit_rss", source_id, channel, title, body, "http://x",
         f"https://www.reddit.com/r/{channel}/comments/{source_id}/x/", "alice",
         3, 1780000000, utcnow(), content_hash(title, body), prefilter, hits),
    )
    conn.commit()
    return cur.lastrowid
