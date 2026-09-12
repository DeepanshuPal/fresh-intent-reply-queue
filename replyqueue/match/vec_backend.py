"""sqlite-vec matcher: cosine similarity over OpenRouter embeddings (BYOK).

Optional. sqlite-vec is pre-v1, so this backend is isolated behind the adapter
in base.py and only loads with `pip install .[vec]`. Embeddings come from the
user's own OpenRouter key (config: match.embed_model). No key -> clean error,
caller falls back to fts.
"""
from __future__ import annotations

import json
import struct

import httpx
import sqlite_vec  # noqa: F401  (import error here is the adapter's guard signal)


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


class VecBackend:
    name = "vec"
    DEFAULT_MODEL = "openai/text-embedding-3-small"

    def __init__(self, cfg):
        self.cfg = cfg
        key = cfg.api_key()
        if not key:
            raise RuntimeError("vec backend needs an OpenRouter key (BYOK)")
        self._key = key
        self._model = cfg.models.get("embed", self.DEFAULT_MODEL)
        self._client = httpx.Client(
            base_url="https://openrouter.ai/api/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=cfg.request_timeout_seconds,
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(
            "/embeddings", json={"model": self._model, "input": texts}
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def _ensure(self, conn) -> None:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0("
            "embedding float[1536])"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_meta (item_id INTEGER PRIMARY KEY)"

        )

    def index(self, conn, items: list[dict]) -> None:
        self._ensure(conn)
        todo = [
            it
            for it in items
            if conn.execute(
                "SELECT 1 FROM vec_meta WHERE item_id = ?", (it["id"],)
            ).fetchone()
            is None
        ]
        for start in range(0, len(todo), 32):
            chunk = todo[start : start + 32]
            vectors = self._embed([f"{it['title']}\n{it['body'][:1500]}" for it in chunk])
            for it, vec in zip(chunk, vectors):
                conn.execute("DELETE FROM items_vec WHERE rowid = ?", (it["id"],))
                conn.execute(
                    "INSERT INTO items_vec(rowid, embedding) VALUES (?, ?)",
                    (it["id"], _pack(vec)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO vec_meta(item_id) VALUES (?)", (it["id"],)
                )
        conn.commit()

    def rank(self, conn, icp: str, keywords: list[str]) -> dict[int, float]:
        self._ensure(conn)
        query_vec = self._embed([f"{icp}\n{' '.join(keywords or [])}"])[0]
        rows = conn.execute(
            "SELECT rowid AS item_id, distance FROM items_vec "
            "WHERE embedding MATCH ? AND k = 200",
            (_pack(query_vec),),
        ).fetchall()
        if not rows:
            return {}
        dists = [r["distance"] for r in rows]
        lo, hi = min(dists), max(dists)
        span = (hi - lo) or 1.0
        return {r["item_id"]: 1.0 - (r["distance"] - lo) / span for r in rows}
