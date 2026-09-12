"""SQLite schema. Run records, edits and guardrail events are append-only evidence."""
from __future__ import annotations

import sqlite3
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,            -- reddit_rss | hn_algolia
  source_id TEXT NOT NULL UNIQUE,  -- t3_x / hn objectID
  channel TEXT NOT NULL,           -- subreddit name or 'hn'
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  url TEXT,
  permalink TEXT,
  author TEXT,
  points INTEGER DEFAULT 0,
  created_utc INTEGER,
  ingested_at TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  raw_json TEXT,
  prefilter_pass INTEGER NOT NULL DEFAULT 0,
  keyword_hits TEXT
);
CREATE TABLE IF NOT EXISTS matches (
  item_id INTEGER PRIMARY KEY REFERENCES items(id),
  backend TEXT NOT NULL,
  score REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
  item_id INTEGER PRIMARY KEY REFERENCES items(id),
  intent_score INTEGER NOT NULL,
  rationale TEXT,
  commercial INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  prompt_version TEXT,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id),
  text TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  fewshot_from TEXT,               -- json array of draft ids used as examples
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|edited|rejected
  promotional INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  reject_reason TEXT
);
CREATE TABLE IF NOT EXISTS draft_edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  before_text TEXT NOT NULL,
  after_text TEXT NOT NULL,
  diff TEXT NOT NULL,              -- unified diff, kept verbatim
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posted (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES drafts(id),
  permalink TEXT NOT NULL,
  posted_at TEXT NOT NULL,
  deadline_utc INTEGER NOT NULL,   -- posted_at + 72h
  status TEXT NOT NULL DEFAULT 'watching',  -- watching|survived|removed
  last_checked_at TEXT,
  checks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS guardrail_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- weekly_ceiling|subreddit_cooldown|promo_ratio|approved
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,             -- run_<12 hex>
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  stats_json TEXT,
  config_json TEXT,
  prompt_versions_json TEXT,
  models_json TEXT,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  side_effects TEXT
);
"""


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn
