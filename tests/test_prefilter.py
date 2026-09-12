from replyqueue.prefilter import compile_keywords, passes, content_hash


def test_keyword_hit():
    kw = compile_keywords(["looking for", "recommend"])
    ok, hits = passes("Looking for a CRM", "any suggestions?", kw, None)
    assert ok and "looking for" in hits


def test_negative_keyword_blocks():
    kw = compile_keywords(["recommend"])
    neg = compile_keywords(["hiring"])
    ok, _ = passes("Recommend a designer", "we are hiring", kw, neg)
    assert not ok


def test_no_keywords_passes_everything():
    ok, _ = passes("anything", "at all", None, None)
    assert ok


def test_content_hash_stable_and_distinct():
    assert content_hash("Hello", "world") == content_hash("hello ", " world")
    assert content_hash("a", "b") != content_hash("a", "c")
