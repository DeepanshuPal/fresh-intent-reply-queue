#!/usr/bin/env python3
"""Day-7 precision gate: does the intent scorer agree with the human?

After about a week of live queue data there are enough human decisions
(approve / edit / reject) in the SQLite file to grade the scorer that put
those drafts in front of you. This script reads that evidence and answers
one question: at the score threshold where drafts are created, what fraction
of them did you actually want?

Labels come from your decisions, not from the model:
  positive  drafts.status = approved | edited   (an edit is still a save)
  negative  drafts.status = rejected
  pending drafts are unlabeled and excluded.

Outputs:
  - precision@threshold table across the 50-90 band
  - ranking quality (Mann-Whitney AUC of intent_score vs your label)
  - per-channel breakdown (which subs / HN drag precision down)
  - removal overlay (approved-then-mod-removed: the costliest false positive)
  - a gate verdict: PASS / FAIL / INSUFFICIENT DATA, with a concrete
    threshold recommendation for `min_intent_score_to_draft`

Usage:
  python evals/precision_gate.py --db replyqueue.db [--config replyqueue.yaml]
  python evals/precision_gate.py --demo          # synthetic data, for CI

Exit codes: 0 = gate passes, 1 = gate fails, 2 = insufficient data.
Stdlib only; reads the database directly, never writes to it.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile

POSITIVE = ("approved", "edited")
THRESHOLD_BAND = (50, 60, 70, 80, 90)


def load_decisions(conn):
    """Decided drafts joined to their intent score, channel and post outcome."""
    rows = conn.execute(
        """
        SELECT d.id AS draft_id, d.status, d.decided_at,
               s.intent_score, i.channel,
               (SELECT p.status FROM posted p WHERE p.draft_id = d.id
                ORDER BY p.id DESC LIMIT 1) AS posted_status
        FROM drafts d
        JOIN items i ON i.id = d.item_id
        LEFT JOIN scores s ON s.item_id = d.item_id
        WHERE d.status != 'pending'
        ORDER BY d.decided_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


def precision_at(rows, threshold):
    considered = [r for r in rows if r["intent_score"] is not None
                  and r["intent_score"] >= threshold]
    if not considered:
        return 0, 0.0
    pos = sum(1 for r in considered if r["status"] in POSITIVE)
    return len(considered), pos / len(considered)


def ranking_auc(rows):
    """Mann-Whitney AUC: P(a random positive outranks a random negative)."""
    pairs = [(r["intent_score"], 1 if r["status"] in POSITIVE else 0)
             for r in rows if r["intent_score"] is not None]
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def per_channel(rows, threshold):
    out = {}
    for r in rows:
        if r["intent_score"] is None or r["intent_score"] < threshold:
            continue
        n, p = out.get(r["channel"], (0, 0))
        out[r["channel"]] = (n + 1, p + (1 if r["status"] in POSITIVE else 0))
    return out


def evaluate(rows, op_threshold, min_samples, min_precision):
    scored = [r for r in rows if r["intent_score"] is not None]
    n_op, p_op = precision_at(rows, op_threshold)
    verdict, reason = "PASS", ""
    if len(scored) < min_samples:
        verdict = "INSUFFICIENT DATA"
        reason = (f"only {len(scored)} decided drafts with scores "
                  f"(need {min_samples}); keep collecting, rerun in a few days")
    elif n_op == 0:
        verdict = "INSUFFICIENT DATA"
        reason = f"no decided drafts at or above the operating threshold {op_threshold}"
    elif p_op < min_precision:
        verdict = "FAIL"
        reason = (f"precision@{op_threshold} = {p_op:.0%} is below the "
                  f"{min_precision:.0%} gate")
    return {
        "verdict": verdict, "reason": reason,
        "n_scored": len(scored), "n_op": n_op, "precision_op": p_op,
    }


def recommend_threshold(rows, min_precision):
    """Highest band threshold that still clears the gate, else 'collect more'."""
    best = None
    for t in THRESHOLD_BAND:
        n, p = precision_at(rows, t)
        if n >= 10 and p >= min_precision:
            best = t
    return best


def report(rows, result, op_threshold, min_samples, min_precision, db_label):
    w = sys.stdout.write
    w(f"day-7 precision gate - {db_label}\n")
    if rows:
        w(f"decided drafts: {len(rows)} "
          f"({rows[0]['decided_at']} .. {rows[-1]['decided_at']})\n")
    else:
        w("decided drafts: 0\n")
    unscored = sum(1 for r in rows if r["intent_score"] is None)
    if unscored:
        w(f"note: {unscored} decided drafts have no score row and are excluded\n")

    w("\nprecision@threshold (of decided drafts scored >= t, fraction you kept)\n")
    w(f"  {'t':>4} {'n':>5} {'precision':>10}\n")
    for t in THRESHOLD_BAND:
        n, p = precision_at(rows, t)
        mark = "  <- operating" if t == op_threshold else ""
        w(f"  {t:>4} {n:>5} {p:>9.0%}{mark}\n")

    auc = ranking_auc(rows)
    if auc is not None:
        w(f"\nranking quality (AUC of intent_score vs your decision): {auc:.2f} "
          f"({'orders well' if auc >= 0.7 else 'weak ordering - look at the scorer prompt'})\n")

    channels = per_channel(rows, op_threshold)
    if channels:
        w(f"\nper-channel precision at t={op_threshold}\n")
        for ch, (n, pos) in sorted(channels.items(), key=lambda kv: kv[1][0], reverse=True):
            w(f"  {ch:<24} n={n:<4} {pos / n:.0%}\n")

    removed = [r for r in rows if r["status"] in POSITIVE and r["posted_status"] == "removed"]
    survived = [r for r in rows if r["status"] in POSITIVE and r["posted_status"] == "survived"]
    if removed or survived:
        w(f"\nposted outcomes: {len(survived)} survived, {len(removed)} removed "
          f"(removals among approved drafts are the costliest false positives)\n")

    rec = recommend_threshold(rows, min_precision)
    w(f"\nverdict: {result['verdict']}")
    if result["reason"]:
        w(f" - {result['reason']}")
    w("\n")
    if result["verdict"] != "INSUFFICIENT DATA":
        if rec is None:
            w("recommendation: no band threshold clears the gate yet; keep the "
              "queue human-first and check the scorer prompt before raising volume\n")
        elif rec != op_threshold:
            w(f"recommendation: set min_intent_score_to_draft = {rec} "
              f"(currently {op_threshold})\n")
        else:
            w(f"recommendation: keep min_intent_score_to_draft = {op_threshold}\n")
    return 0 if result["verdict"] == "PASS" else (2 if result["verdict"] == "INSUFFICIENT DATA" else 1)


def build_demo_db(path):
    """Synthetic week of queue data with known precision, for CI and demos.

    Constructed so the gate passes at t=60: high scores are mostly kept,
    the 60-70 band carries the honest misses, nothing below 60 was drafted.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from replyqueue.db import SCHEMA
    conn.executescript(SCHEMA)
    now = "2026-09-17T00:00:00Z"
    demo = []
    # (intent_score, status, channel) - the shape a healthy week should have
    for i, score in enumerate([62, 64, 66, 68, 65, 61]):
        demo.append((score, "rejected" if i % 3 == 0 else "approved", "startups"))
    for score in [72, 75, 78, 71, 74, 79, 70, 76]:
        demo.append((score, "approved", "smallbusiness"))
    for score in [82, 85, 88, 91, 84, 87, 92, 95, 81, 89, 86, 90]:
        demo.append((score, "approved", "hn"))
    for score in [73, 77]:
        demo.append((score, "edited", "startups"))
    for score in [55, 58, 66]:
        demo.append((score, "rejected", "Entrepreneur"))
    for i, (score, status, channel) in enumerate(demo):
        cur = conn.execute(
            "INSERT INTO items (source, source_id, channel, title, body, url,"
            " ingested_at, content_hash, prefilter_pass) VALUES (?,?,?,?,?,?,?,?,1)",
            ("demo", f"demo_{i}", channel, f"demo item {i}", "body",
             "https://example.com", now, f"hash_{i}"))
        item_id = cur.lastrowid
        conn.execute(
            "INSERT INTO scores (item_id, intent_score, rationale, model,"
            " prompt_version, created_at) VALUES (?,?,?,?,?,?)",
            (item_id, score, "demo", "demo-model", "demo-v1", now))
        cur = conn.execute(
            "INSERT INTO drafts (item_id, text, model, prompt_version, status,"
            " created_at, decided_at) VALUES (?,?,?,?,?,?,?)",
            (item_id, "draft text", "demo-model", "demo-v1", status, now, now))
        if status == "approved" and channel == "hn" and score >= 90:
            conn.execute(
                "INSERT INTO posted (draft_id, permalink, posted_at, deadline_utc,"
                " status) VALUES (?,?,?,?,?)",
                (cur.lastrowid, "https://example.com/thread", now, 0, "survived"))
    conn.commit()
    return conn


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to replyqueue.db")
    ap.add_argument("--config", help="replyqueue.yaml to read the operating threshold from")
    ap.add_argument("--threshold", type=int, default=None,
                    help="operating threshold (default: config min_intent_score_to_draft, else 60)")
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--min-precision", type=float, default=0.5)
    ap.add_argument("--demo", action="store_true", help="run on synthetic data (CI)")
    args = ap.parse_args(argv)

    op_threshold = args.threshold
    if op_threshold is None:
        op_threshold = 60
        cfg_path = args.config or ("replyqueue.yaml" if os.path.exists("replyqueue.yaml") else None)
        if cfg_path and os.path.exists(cfg_path):
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
                from replyqueue.config import load_config
                op_threshold = load_config(cfg_path).min_intent_score_to_draft
            except Exception:
                pass

    if args.demo:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = build_demo_db(tmp.name)
        db_label = f"DEMO ({tmp.name})"
    else:
        if not args.db:
            ap.error("--db is required unless --demo")
        if not os.path.exists(args.db):
            ap.error(f"database not found: {args.db}")
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        db_label = args.db

    rows = load_decisions(conn)
    result = evaluate(rows, op_threshold, args.min_samples, args.min_precision)
    return report(rows, result, op_threshold, args.min_samples, args.min_precision, db_label)


if __name__ == "__main__":
    raise SystemExit(main())
