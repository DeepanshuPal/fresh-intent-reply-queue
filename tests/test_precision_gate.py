"""The day-7 precision gate: metric math and verdicts on synthetic labels."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))

import precision_gate as pg


def row(score, status, channel="startups", posted=None):
    return {"draft_id": 1, "status": status, "decided_at": "2026-09-17T00:00:00Z",
            "intent_score": score, "channel": channel, "posted_status": posted}


def test_precision_at_counts_edits_as_saves():
    rows = [row(80, "approved"), row(90, "edited"), row(85, "rejected"), row(40, "rejected")]
    n, p = pg.precision_at(rows, 70)
    assert n == 3 and abs(p - 2 / 3) < 1e-9
    n, p = pg.precision_at(rows, 50)
    assert n == 3  # the 40-score draft stays below every sane band


def test_precision_at_empty_band():
    assert pg.precision_at([row(10, "rejected")], 90) == (0, 0.0)


def test_unscored_decisions_are_excluded():
    rows = [row(None, "approved"), row(90, "approved")]
    n, p = pg.precision_at(rows, 50)
    assert n == 1 and p == 1.0


def test_auc_perfect_and_random():
    perfect = [row(90, "approved"), row(95, "edited"), row(10, "rejected"), row(20, "rejected")]
    assert pg.ranking_auc(perfect) == 1.0
    inverted = [row(10, "approved"), row(90, "rejected")]
    assert pg.ranking_auc(inverted) == 0.0
    assert pg.ranking_auc([row(50, "approved")]) is None  # no negatives


def test_evaluate_verdicts():
    good = [row(80, "approved") for _ in range(25)] + [row(30, "rejected") for _ in range(10)]
    assert pg.evaluate(good, 60, 30, 0.5)["verdict"] == "PASS"
    bad = [row(80, "rejected") for _ in range(25)] + [row(80, "approved") for _ in range(10)]
    assert pg.evaluate(bad, 60, 30, 0.5)["verdict"] == "FAIL"
    thin = [row(80, "approved") for _ in range(5)]
    assert pg.evaluate(thin, 60, 30, 0.5)["verdict"] == "INSUFFICIENT DATA"


def test_recommend_threshold_picks_highest_clearing_band():
    rows = ([row(s, "approved") for s in (75, 80, 85, 90, 72, 78, 82, 88, 92, 95)]
            + [row(62, "rejected") for _ in range(10)])
    assert pg.recommend_threshold(rows, 0.5) == 70


def test_demo_db_passes_gate(tmp_path):
    db = tmp_path / "demo.db"
    conn = pg.build_demo_db(str(db))
    rows = pg.load_decisions(conn)
    assert len(rows) >= 30
    result = pg.evaluate(rows, 60, 30, 0.5)
    assert result["verdict"] == "PASS"
