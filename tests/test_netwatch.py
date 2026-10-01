import json
import time
from flow_mcp.netwatch import decode_batchexecute, walk_strings, Ledger, Media, norm, generic_extract


def _wire(rpcid, payload):
    inner = json.dumps([["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]])
    return ")]}'\n\n" + str(len(inner)) + "\n" + inner + "\n"


def test_decode_one_entry():
    body = _wire("NfrxTb", [["media/abc123", "a cat on a mat", "abra_r2v_8s"]])
    out = decode_batchexecute(body)
    assert out == [("NfrxTb", [["media/abc123", "a cat on a mat", "abra_r2v_8s"]])]


def test_decode_ignores_non_wrb_and_garbage():
    body = ")]}'\n\n20\n[[\"di\",42],[\"af.httprm\",1]]\n"
    assert decode_batchexecute(body) == []
    assert decode_batchexecute("<html>") == []


def test_walk_strings_nested():
    assert sorted(walk_strings({"a": ["x", {"b": "y"}], "c": 3})) == ["x", "y"]


def test_norm():
    assert norm("  A  Cat\n on ") == "a cat on"


def test_ledger_matches_prompt_and_dedupes():
    L = Ledger(extractors=[lambda rpc, p: [Media("m1", "A cat on a mat", "abra_r2v_8s", "video", 0.0)]])
    L.record("NfrxTb", None)
    L.record("NfrxTb", None)  # Flow lists every item twice
    assert [m.media_id for m in L.all()] == ["m1"]
    assert [m.media_id for m in L.find("a cat  on a MAT", since=-1)] == ["m1"]
    assert L.find("a dog", since=-1) == []


def test_generic_extract_finds_nothing_without_known_shape():
    assert generic_extract("X", [["q", "w"]]) == []


def test_since_filter():
    """since filter: item with seen_at < since is excluded, >= included"""
    now = time.time()
    L = Ledger(extractors=[
        lambda rpc, p: [Media("m1", "cat", "model1", "video", now - 10)],  # 10s ago: excluded
        lambda rpc, p: [Media("m2", "dog", "model2", "video", now)],       # now: included
    ])
    L.record("X", None)

    # EXCLUSION: since=now (after m1's seen_at) -> not found
    found = L.find("cat", since=now)
    assert found == []

    # INCLUSION: since=now-10 (exact seen_at) -> found (>= means inclusive)
    found = L.find("cat", since=now - 10)
    assert [m.media_id for m in found] == ["m1"]

    # INCLUSION: since=now-15 (before seen_at) -> found
    found = L.find("cat", since=now - 15)
    assert [m.media_id for m in found] == ["m1"]


def test_feed_end_to_end():
    """feed() end-to-end using _wire helper with a test extractor"""
    def test_extractor(rpc, p):
        if p and isinstance(p, list) and len(p) > 0:
            media_id = p[0][0] if isinstance(p[0], list) and len(p[0]) > 0 else None
            prompt = p[0][1] if isinstance(p[0], list) and len(p[0]) > 1 else None
            model = p[0][2] if isinstance(p[0], list) and len(p[0]) > 2 else None
            if media_id:
                return [Media(media_id, prompt, model, "video", 0.0)]
        return []

    L = Ledger(extractors=[test_extractor])
    body = _wire("NfrxTb", [["media/abc123", "a cat on a mat", "abra_r2v_8s"]])
    L.feed(body)

    assert len(L.all()) == 1
    m = L.all()[0]
    assert m.media_id == "media/abc123"
    assert m.prompt == "a cat on a mat"
    assert m.model == "abra_r2v_8s"


def test_none_to_value_upgrade_keeps_seen_at():
    """None->value upgrade of prompt/model keeps seen_at"""
    old_time = time.time() - 100
    L = Ledger(extractors=[
        lambda rpc, p: [Media("m1", None, None, "video", old_time)],  # First: no prompt/model
        lambda rpc, p: [Media("m1", "cat", "model1", "video", time.time())],  # Second: add prompt/model
    ])

    L.record("X", None)
    L.record("X", None)  # Upgrade pass

    all_media = L.all()
    assert len(all_media) == 1
    m = all_media[0]
    assert m.media_id == "m1"
    assert m.prompt == "cat"
    assert m.model == "model1"
    assert m.seen_at == old_time  # Should keep original seen_at


def test_raising_extractor_alongside_good_one():
    """A raising extractor alongside a good one still records the good one's Media"""
    def bad_extractor(rpc, p):
        raise ValueError("Boom!")

    def good_extractor(rpc, p):
        return [Media("m1", "cat", "model1", "video", 0.0)]

    L = Ledger(extractors=[bad_extractor, good_extractor])
    L.record("X", None)

    # Despite bad_extractor raising, good_extractor should have recorded m1
    all_media = L.all()
    assert len(all_media) == 1
    assert all_media[0].media_id == "m1"


def test_extractor_returning_none_and_good_media():
    """Extractor returning [None, good_media] skips None, records good_media"""
    def mixed_extractor(rpc, p):
        return [None, Media("m1", "cat", "model1", "video", 0.0)]

    L = Ledger(extractors=[mixed_extractor])
    L.record("X", None)

    # None should be skipped (isinstance filter), m1 should be recorded
    all_media = L.all()
    assert len(all_media) == 1
    assert all_media[0].media_id == "m1"


def test_any_url_marks_has_url_but_only_signed_is_fetch_url():
    from flow_mcp.netwatch import _record
    mid = "12345678-1234-1234-1234-123456789abc"
    fife = [mid, None, None, None, None, None, [[None] * 13 + ["https://lh3.googleusercontent.com/fife/x"]]]
    none = [mid, None, None, None, None, None, [[None] * 13]]
    m = _record(fife)
    assert m.has_url is True and m.url is None
    assert _record(none).has_url is False


def test_ledger_keeps_has_url_on_merge():
    L = Ledger(extractors=[lambda rpc, p: p])
    L.record("x", [Media("m1", "cat", None, "video", 0.0, None, None, None, True)])
    L.record("x", [Media("m1", "cat", "abra_i2v_8s", "video", 0.0)])
    (m,) = L.all()
    assert m.has_url is True and m.model == "abra_i2v_8s"
    L2 = Ledger(extractors=[lambda rpc, p: p])
    L2.record("x", [Media("m1", "cat", "abra_i2v_8s", "video", 0.0)])
    L2.record("x", [Media("m1", "cat", "abra_i2v_8s", "video", 0.0, None, None, None, True)])
    assert L2.all()[0].has_url is True
