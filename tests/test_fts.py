from replyqueue.match.fts_backend import FTSBackend, query_terms
from tests.conftest import make_item


def test_fts_ranks_relevant_above_irrelevant(conn, cfg):
    a = make_item(conn, "t3_a", "How do I find my first customers?",
                  "I am struggling to get customers for my startup, what worked for you?")
    b = make_item(conn, "t3_b", "Best mechanical keyboard?",
                  "Looking for a clicky keyboard for gaming.")
    backend = FTSBackend(cfg)
    items = [dict(r) for r in conn.execute("SELECT id, title, body FROM items").fetchall()]
    backend.index(conn, items)
    ranked = backend.rank(conn, cfg.icp, cfg.keywords)
    assert ranked[a] > ranked[b]


def test_query_terms_strip_stopwords():
    terms = query_terms("I sell to founders who want customers and growth", ["lead gen"])
    assert "founders" in terms and "customers" in terms
    assert "the" not in terms and "and" not in terms
