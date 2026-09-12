"""Append-only run records: every batch operation leaves evidence."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict

from .db import utcnow


@dataclass
class RunRecord:
    kind: str
    id: str = field(default_factory=lambda: "run_" + uuid.uuid4().hex[:12])
    stats: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    models: set = field(default_factory=set)
    prompt_versions: set = field(default_factory=set)
    side_effects: list = field(default_factory=list)


class run:
    """Context manager: inserts the row at start, completes it once at end."""

    def __init__(self, conn, cfg, kind: str):
        self.conn = conn
        self.cfg = cfg
        self.rec = RunRecord(kind=kind)

    def __enter__(self) -> RunRecord:
        with open(self.cfg.path, "r", encoding="utf-8") as fh:
            config_snapshot = fh.read()
        self.conn.execute(
            "INSERT INTO runs(id, kind, started_at, status, config_json) VALUES (?, ?, ?, 'running', ?)",
            (self.rec.id, self.rec.kind, utcnow(), config_snapshot),
        )
        self.conn.commit()
        return self.rec

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = "failed" if exc_type else "completed"
        if exc_type:
            self.rec.side_effects.append(f"error: {exc_type.__name__}: {exc}")
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, stats_json=?, prompt_versions_json=?,"
            " models_json=?, tokens_in=?, tokens_out=?, cost_usd=?, side_effects=? WHERE id=?",
            (
                utcnow(), status, json.dumps(self.rec.stats),
                json.dumps(sorted(self.rec.prompt_versions)),
                json.dumps(sorted(self.rec.models)),
                self.rec.tokens_in, self.rec.tokens_out, round(self.rec.cost_usd, 6),
                "\n".join(self.rec.side_effects), self.rec.id,
            ),
        )
        self.conn.commit()
        return False  # never swallow exceptions
