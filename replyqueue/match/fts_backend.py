"""Default matcher: SQLite FTS5 + BM25 against a query built from the ICP blurb."""
from __future__ import annotations

import re

STOPLIST = set(
    "the a an and or of to in for on with without at by from as is are was were be been "
    "i me my we our you your they their it its this that these those who what which how "
    "do does did am have has had can could will would should not no yes about into over "
    "under more most other some such only own same so than too very just".split()
)
WORD_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


def query_terms(icp: str, keywords: list[str], limit: int = 16) -> list[str]:
    counts: dict[str, int] = {}
    for word in WORD_RE.findall((icp or "").lower()):
        if word not in STOPLIST:
            counts[word] = counts.get(word, 0) + 1
    terms = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
    for kw in keywords or []:
        for word in WORD_RE.findall(kw.lower()):
            if word not in STOPLIST and word not in terms:
                terms.append(word)
    return terms[:limit]


class FTSBackend:
    name = "fts"

    def __init__(self, cfg):
        self.cfg = cfg

    def _ensure(self, conn) -> None:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5("
            "title, body, tokenize='porter unicode61')"
        )

    def index(self, conn, items: list[dict]) -> None:
        self._ensure(conn)
        for it in items:
            conn.execute("DELETE FROM items_fts WHERE rowid = ?", (it["id"],))
            conn.execute(
                "INSERT INTO items_fts(rowid, title, body) VALUES (?, ?, ?)",
                (it["id"], it["title"], it["body"]),
            )
        conn.commit()

    def rank(self, conn, icp: str, keywords: list[str]) -> dict[int, float]:
        self._ensure(conn)
        terms = query_terms(icp, keywords)
        if not terms:
            return {}
        query = " OR ".join(f'"{t}"' for t in terms)
        rows = conn.execute(
            "SELECT rowid AS item_id, bm25(items_fts) AS rank FROM items_fts "
            "WHERE items_fts MATCH ?",
            (query,),
        ).fetchall()
        if not rows:
            return {}
        # bm25() returns negative values; more negative = better. Normalize to 0..1.
        ranks = [r["rank"] for r in rows]
        best, worst = min(ranks), max(ranks)
        span = (worst - best) or 1.0
        return {r["item_id"]: (worst - r["rank"]) / span for r in rows}
