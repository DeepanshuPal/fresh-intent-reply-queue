"""Semantic match: rank prefiltered items against the user's ICP blurb.

Adapter interface so the embedding store is swappable:
  fts  - SQLite FTS5 keyword/BM25 ranking. Default. Zero deps, verified.
  vec  - sqlite-vec cosine similarity over OpenRouter embeddings (BYOK).
         sqlite-vec is pre-v1, so it lives behind this adapter and an
         optional extra (pip install .[vec]). Falls back to fts with a
         warning when the extension is unavailable.
"""
from __future__ import annotations

from typing import Protocol


class MatchBackend(Protocol):
    name: str

    def index(self, conn, items: list[dict]) -> None: ...

    def rank(self, conn, icp: str, keywords: list[str]) -> dict[int, float]:
        """Return {item_id: normalized score 0..1} for indexed items."""
        ...


def get_backend(cfg) -> MatchBackend:
    if cfg.match.backend == "vec":
        try:
            from .vec_backend import VecBackend

            return VecBackend(cfg)
        except Exception as exc:  # sqlite_vec missing or failed to load
            print(f"[match] vec backend unavailable ({exc}); falling back to fts")
            from .fts_backend import FTSBackend

            return FTSBackend(cfg)
    from .fts_backend import FTSBackend

    return FTSBackend(cfg)
