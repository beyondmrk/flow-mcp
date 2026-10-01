from pathlib import Path

from flow_mcp.netwatch import EXTRACTORS, Ledger, decode_batchexecute, flow_extract

FX = Path(__file__).parent / "fixtures"
APPLE = "a single red apple on a wooden table"
APPLE_ID = "da04f400-bbcd-4145-89bf-f6061e9ac670"


def _payload(name):
    return decode_batchexecute((FX / name).read_text(encoding="utf-8"))[0]


def test_project_contents_yields_apple():
    L = Ledger(EXTRACTORS)
    for f in sorted(FX.glob("*.txt")):
        L.feed(f.read_text(encoding="utf-8"))
    hits = L.find(APPLE, since=-1)
    assert len(hits) == 1
    assert hits[0].kind == "image" and hits[0].media_id


def test_each_rpc_alone_attributes_the_apple():
    for name, rpc in [("Zzl0ze_project_contents.txt", "Zzl0ze"),
                      ("ogiZ0b_generate.txt", "ogiZ0b"),
                      ("as29s_media_detail.txt", "as29s")]:
        rid, pl = _payload(name)
        assert rid == rpc
        got = flow_extract(rid, pl)
        assert [m.media_id for m in got] == [APPLE_ID], name
        assert got[0].prompt == APPLE and got[0].kind == "image", name


def test_unrelated_rpcs_and_junk_yield_nothing():
    rid, pl = _payload("nzlxg_credits.txt")
    assert flow_extract(rid, pl) == []
    assert flow_extract("Zzl0ze", None) == []
    assert flow_extract("Zzl0ze", [None, None, [["not-a-uuid"], None]]) == []
    assert flow_extract("unknownRpc", [[APPLE_ID]]) == []


APPLE_WF = "209b51aa-58ab-4f44-a402-29147e9e3a27"
APPLE_URL = "https://flow-content.google/image/" + APPLE_ID + "?"


def test_ledger_apple_carries_url_and_workflow_id():
    L = Ledger(EXTRACTORS)
    for f in sorted(FX.glob("*.txt")):
        L.feed(f.read_text(encoding="utf-8"))
    (m,) = L.find(APPLE, since=-1)
    assert m.workflow_id == APPLE_WF
    assert m.url and m.url.startswith(APPLE_URL)


def test_url_only_from_signed_records_workflow_everywhere():
    for name, has_url in [("Zzl0ze_project_contents.txt", False),
                          ("ogiZ0b_generate.txt", True),
                          ("as29s_media_detail.txt", True)]:
        (m,) = flow_extract(*_payload(name))
        assert m.workflow_id == APPLE_WF, name
        assert bool(m.url) is has_url, name
        if has_url:
            assert m.url.startswith(APPLE_URL), name


def _rec(prompt6=None, prompt5=None, url=None, t14=None):
    return [APPLE_ID, None, APPLE_WF, "CAE", None,
            [None, None, None, None, None, None, [None, None, [[prompt5, None, [[[prompt5]]]]], [], 1]],
            [[None, 1, None, None, None, None, None, prompt6, 29, "", None, APPLE_WF, None, url, t14],
             None, [768, 1376]]]


def test_prompt_falls_back_to_5_6_2_0_0():
    (m,) = flow_extract("as29s", _rec(prompt6=None, prompt5="fallback prompt"))
    assert m.prompt == "fallback prompt"


def test_record_without_prompt_has_none():
    (m,) = flow_extract("as29s", _rec())
    assert m.prompt is None and m.media_id == APPLE_ID


def test_kind_unknown_without_positive_image_signal():
    assert flow_extract("as29s", _rec(prompt6="x"))[0].kind == "unknown"
    vid = "https://flow-content.google/video/" + APPLE_ID + "?Signature=REDACTED"
    assert flow_extract("as29s", _rec(prompt6="x", url=vid))[0].kind == "unknown"  # video UNVERIFIED
    assert flow_extract("as29s", _rec(prompt6="x", t14=3))[0].kind == "image"


def test_model_ignores_prompt_text():
    (m,) = flow_extract("as29s", _rec(prompt6="veo_3_1_fast style apple", prompt5="veo_3_1_fast style apple"))
    assert m.model is None
    r = _rec(prompt6="an apple")
    r.append(["veo_3_1_lite"])
    assert flow_extract("as29s", r)[0].model == "veo_3_1_lite"


def test_dims_from_6_2_on_every_rpc():
    for name in ["Zzl0ze_project_contents.txt", "ogiZ0b_generate.txt", "as29s_media_detail.txt"]:
        (m,) = flow_extract(*_payload(name))
        assert m.dims == (1376, 768), name  # the 16:9 apple


def test_dims_read_or_none():
    rec = _rec(prompt6="x")
    assert flow_extract("as29s", rec)[0].dims == (768, 1376)
    rec[6][2] = None
    assert flow_extract("as29s", rec)[0].dims is None
    rec[6][2] = ["768", 1376]
    assert flow_extract("as29s", rec)[0].dims is None


def test_video_record_from_zzl0ze_rec7_block():
    # LIVE shape 2026-09-29: an Omni clip keeps prompt/model/fife URL at rec[7][0], not rec[6][0]
    from flow_mcp.netwatch import flow_extract
    rec = ["00586fe5-2412-4b39-abda-1d954fc74aca", "a002cb7d-8dd3-4230-b2ef-30e9ac1cd247",
           "32085041-6800-45de-9804-ee6eaf1f2619", "CAE", None, [None, "{\"shot\": \"x\"}"], None,
           [[None] * 7 + ["{\"shot\": \"x\"}", "https://contribution.fife.usercontent.google.com/asb/abc",
                          None, None, None, "abra_i2v_4s", ""]]]
    [m] = flow_extract("Zzl0ze", [None, None, [rec]])
    assert (m.kind, m.model, m.prompt, m.has_url, m.url) == ("video", "abra_i2v_4s", "{\"shot\": \"x\"}", True, None)
    assert m.workflow_id == "32085041-6800-45de-9804-ee6eaf1f2619"


def test_video_record_as29s_signed_url_at_rec7_0_8():
    # LIVE shape 2026-09-30: the detail view's as29s carries the SIGNED video URL at [7][0][8]
    from flow_mcp.netwatch import flow_extract
    signed = "https://flow-content.google/video/dfb17eb9-e7df-4d0b-adc5-db0907ee05d3?Expires=1&KeyName=k&Signature=s"
    rec = ["dfb17eb9-e7df-4d0b-adc5-db0907ee05d3", None, "f017068b-9c34-44f6-a51f-5acf30d00566", "CAE", None,
           [None, "{\"shot\": \"x\"}"], None,
           [[None] * 7 + ["{\"shot\": \"x\"}", signed, None, None, None, "abra_i2v_4s", ""]]]
    [m] = flow_extract("as29s", rec)
    assert (m.kind, m.has_url, m.url) == ("video", True, signed)
