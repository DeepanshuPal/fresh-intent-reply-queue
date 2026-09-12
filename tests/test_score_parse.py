import pytest

from replyqueue.score import parse_score


def test_clean_json():
    assert parse_score('{"score": 72, "rationale": "asks for crm", "commercial": true}') == (
        72, "asks for crm", True)


def test_json_in_prose():
    s, _, _ = parse_score('Sure! Here is the assessment: {"score": "41", "rationale": "ok", "commercial": false} thanks')
    assert s == 41


def test_fallback_regex():
    s, _, _ = parse_score('score: 88 because reasons')
    assert s == 88


def test_clamped():
    s, _, _ = parse_score('{"score": 240}')
    assert s == 100


def test_garbage_raises():
    with pytest.raises(ValueError):
        parse_score("no numbers here")
