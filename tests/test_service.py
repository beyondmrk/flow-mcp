import dataclasses
import pathlib
import time

import pytest
from pathlib import Path
from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Ledger, Media
from flow_mcp.service import Service
from flow_mcp.runstate import RunState
from .conftest import TEST_ROOT


class FakePage:
    def __init__(self, ledger, commit=True, model="abra_i2v_8s", buttonless=False, dims=None,
                 commit_on_wait=None, land_at=None, fire_raises=None, banner=None, model_at_wait=None,
                 has_url=True, url_at_wait=None, land_on_reset=None, project_id=None):
        self.L, self.commit_, self.model, self.buttonless, self.calls = ledger, commit, model, buttonless, []
        self.dims, self.commit_on_wait, self.waits = dims, commit_on_wait, 0
        self.land_at, self.fire_raises, self.banner, self.model_at_wait = land_at, fire_raises, banner, model_at_wait
        self.has_url, self.url_at_wait = has_url, url_at_wait
        self.land_on_reset, self.project_id = land_on_reset, project_id

    def state(self): return {"signed_in": True, "captcha": False, "out_of_credits": self.buttonless,
                             "balance": 100, "url": "x", "banner": self.banner}
    def reset(self):
        self.calls.append(("reset",))
        for mid in self.land_on_reset or []:
            self._land(mid)  # a project reload re-feeds the project media into the ledger

    def current_project_id(self): return self.project_id
    def goto_project(self, pid):
        self.calls.append(("goto_project", pid)); self.project_id = pid; self.reset()
    def set_settings(self, **kw): self.calls.append(("settings", kw))
    def attach(self, name): self.calls.append(("attach", name))
    def attach_many(self, names): self.calls.append(("attach_many", list(names)))
    def upload(self, p): self.calls.append(("upload", str(p))); return Path(p).stem
    def type_prompt(self, t): self._t = t

    def fire_ready(self):
        if self.buttonless: raise FlowError("OUT_OF_CREDITS", "no fire button")
        self.calls.append(("fire_ready",))

    def _land(self, mid="m1", model=None):
        model = self.model if model is None else model
        self.L._by_id[mid] = Media(mid, self._t, model, "video", 9e18, None, None, self.dims, self.has_url)

    def fire(self):
        if self.fire_raises: raise self.fire_raises
        self.calls.append(("fire",))
        if self.commit_ and self.commit_on_wait is None and self.land_at is None:
            self._land()

    def wait(self, ms):
        # sync Playwright only dispatches responses while a page call runs: the commit lands here
        self.waits += 1
        if self.commit_on_wait is not None and self.waits == self.commit_on_wait:
            self._land()
        for mid in (self.land_at or {}).get(self.waits, []):
            self._land(mid)
        if self.model_at_wait is not None and self.waits == self.model_at_wait:
            self.L._by_id["m1"] = dataclasses.replace(self.L._by_id["m1"], model="abra_i2v_8s")
        if self.url_at_wait is not None and self.waits == self.url_at_wait:
            self.L._by_id["m1"] = dataclasses.replace(self.L._by_id["m1"], has_url=True)

    def prompt_cleared(self): return self.commit_
    def pending_tiles(self): return []
    def prepare_in_place(self): self.calls.append(("prepare_in_place",))
    def open_detail(self, wf): self.calls.append(("open_detail", wf))
    def fetch_bytes(self, m): return b"x" * 20000


TEST_PID = "0000aaaa-0000-0000-0000-000000000001"


def svc(tmp_path, **kw):
    L = Ledger(extractors=[]); s = Service.for_test(FakePage(L, **kw), L, commit_timeout_s=0.2); return s


def proj(tmp_path, stamped=True):
    """A test project. stamped: a manifest + PASS lint stamp for L01/L02 bound to prompt "hello"."""
    d = TEST_ROOT / tmp_path.name; d.mkdir(parents=True, exist_ok=True)
    RunState(d).set_project(TEST_PID, "proj")  # every fire/sweep/download needs a bound project
    if stamped:
        from flow_mcp import stamp
        (d / "Creatives").mkdir(exist_ok=True)
        m = d / "Creatives" / "nj.json"
        m.write_text('{"shots": [{"tag": "L01"}, {"tag": "L02"}]}', encoding="utf-8")
        stamp.write(d, m, {k: {"status": "PASS", "findings": [], "refs": [],
                               "prompt_sha256": stamp.prompt_sha("hello")} for k in ("L01", "L02")})
    return d


def test_clip_commit_writes_pending_then_committed(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "committed" and r["media_ids"] == ["m1"] and r["version"] == 1
    assert RunState(d).pending() == []


def test_clip_unconfirmed_never_refires(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "UNCONFIRMED"
    assert [c for c in s.page.calls if c == ("fire",)] == [("fire",)]
    assert len(RunState(d).pending()) == 1


def test_wrong_model_flagged(tmp_path):
    s = svc(tmp_path, model="abra_edit"); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "WRONG_MODEL"


def test_out_of_credits_stops(tmp_path):
    s = svc(tmp_path, buttonless=True); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "OUT_OF_CREDITS"


def test_pending_blocks_new_fire_until_swept(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "UNCONFIRMED"


def test_attach_happens_before_fire_every_time(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    names = [c[0] for c in s.page.calls]
    assert names.index("attach") < names.index("fire")


def test_reset_precedes_settings_attach_and_prompt(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    names = [c[0] for c in s.page.calls]
    assert names[:4] == ["goto_project", "reset", "settings", "attach"]  # goto = navigate to bound project
    assert names.index("fire_ready") < names.index("fire")


def test_path_outside_refused(tmp_path):
    s = svc(tmp_path)
    with pytest.raises(FlowError):
        s.fire_still(Path(r"C:\Volumes\ads\x"), "nj", "L01", "p", Path(r"C:\Volumes\ads\r.png"))


def test_download_names_and_skips(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    out = s.download(d, r["media_ids"])
    f = d / "Elements" / "Stills" / "claude_nj_line01.png"
    assert f.stat().st_size == 20000 and out["written"] == [str(f)]
    assert s.download(d, r["media_ids"])["skipped"] == [str(f)]


# --- Task 6 corrections -------------------------------------------------------------------

def test_settings_always_request_9x16(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s.fire_clip(d, "NJ_line02", "L02", "hello", d / "a.png", 8)
    kws = [c[1] for c in s.page.calls if c[0] == "settings"]
    assert [k["aspect"] for k in kws] == ["9:16", "9:16"]
    assert kws[0]["mode"] == "image" and kws[1]["mode"] == "video"


def test_commit_wait_pumps_page_never_sleeps(tmp_path, monkeypatch):
    def boom(*a):
        raise AssertionError("time.sleep in the commit wait starves Playwright's event dispatch")
    monkeypatch.setattr(time, "sleep", boom)
    s = svc(tmp_path, commit_on_wait=3); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "committed" and s.page.waits >= 3


def test_still_landscape_is_wrong_aspect(tmp_path):
    s = svc(tmp_path, dims=(1376, 768)); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "WRONG_ASPECT" and r["media_ids"] == ["m1"] and r["dims"] == [1376, 768]
    assert RunState(d).pending() == []


def test_still_portrait_committed(tmp_path):
    s = svc(tmp_path, dims=(768, 1376)); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "committed" and r["aspect_checked"] is True


def test_still_without_dims_committed_but_aspect_unchecked(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "committed" and r["aspect_checked"] is False


def test_wrong_aspect_is_a_code():
    assert FlowError("WRONG_ASPECT", "x").code == "WRONG_ASPECT"


# --- fix round 1 ------------------------------------------------------------------------------

def test_old_same_prompt_media_never_confirms_new_fire(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.L._by_id["old"] = Media("old", "hello", "abra_i2v_8s", "video", 9e18)  # same prompt, future seen_at
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "UNCONFIRMED" and r["media_ids"] == []


def test_sweep_ignores_media_committed_to_another_attempt(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)  # m1 committed to L01
    s.page.commit_ = False
    s.fire_clip(d, "NJ_line02", "L02", "hello", d / "a.png", 8)  # same prompt, nothing new lands
    r = s.sweep(d)
    assert r["resolved"] == [] and len(r["still_pending"]) == 1


def test_outputs_2_waits_for_both(tmp_path):
    s = svc(tmp_path, land_at={2: ["m1"], 5: ["m2"]}); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png", outputs=2)
    assert r["status"] == "committed" and sorted(r["media_ids"]) == ["m1", "m2"]
    assert r["partial"] is False


def test_outputs_2_only_one_arrives_is_partial(tmp_path):
    s = svc(tmp_path, land_at={2: ["m1"]}); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png", outputs=2)
    assert r["status"] == "committed" and r["media_ids"] == ["m1"]
    assert r["partial"] is True and r["expected"] == 2 and r["got"] == 1


def test_fire_button_missing_leaves_no_pending(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.page.buttonless = True
    s.page.state = lambda: {"signed_in": True, "captcha": False, "out_of_credits": False, "url": "x"}
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "OUT_OF_CREDITS"
    assert RunState(d).pending() == [] and "attempts" not in RunState(d)._load()


def test_fire_raising_before_click_marks_not_fired(tmp_path):
    s = svc(tmp_path, fire_raises=FlowError("UI_CHANGED", "Start generation vanished")); d = proj(tmp_path)
    with pytest.raises(FlowError):
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    at = list(RunState(d)._load()["attempts"].values())
    assert [a["status"] for a in at] == ["not_fired"] and RunState(d).pending() == []
    s.page.fire_raises = None  # the line is not blocked: a fresh fire is allowed
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"


def test_fire_raising_unknown_error_stays_pending(tmp_path):
    s = svc(tmp_path, fire_raises=RuntimeError("click timeout")); d = proj(tmp_path)
    with pytest.raises(RuntimeError):
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert len(RunState(d).pending()) == 1  # may have clicked: never unblock a possible spend


def test_sweep_records_still_so_download_works(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s.page._land()  # the result shows up later (e.g. after a reload)
    r = s.sweep(d)
    assert len(r["resolved"]) == 1
    out = s.download(d, ["m1"])
    assert out["written"] == [str(d / "Elements" / "Stills" / "claude_nj_line01.png")]


def test_sweep_records_clip_with_its_name(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s.page._land()
    s.sweep(d)
    out = s.download(d, ["m1"])
    assert out["written"] == [str(d / "Elements" / "Clips" / "NJ_line01_v01.mp4")]


def test_download_unknown_id_is_clear_value_error(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(ValueError, match="unknown mediaId nope"):
        s.download(d, ["nope"])


def test_banner_alone_does_not_block(tmp_path):
    s = svc(tmp_path, banner="You are out of Google Flow credits"); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"
    assert s.status()["banner"] == "You are out of Google Flow credits"


def test_clip_model_none_is_model_unknown(tmp_path):
    s = svc(tmp_path, model=""); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "MODEL_UNKNOWN" and r["media_ids"] == ["m1"]
    assert RunState(d).pending() == []


def test_clip_model_arriving_late_is_committed(tmp_path):
    s = svc(tmp_path, model="", model_at_wait=2); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"


def test_model_unknown_is_a_code():
    assert FlowError("MODEL_UNKNOWN", "x").code == "MODEL_UNKNOWN"


# --- fix round 2 ------------------------------------------------------------------------------

def test_sweep_after_restart_ignores_pre_fire_snapshot(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.L._by_id["old"] = Media("old", "hello", "abra_i2v_8s", "video", 9e18)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "UNCONFIRMED"
    # MCP restart: fresh ledger, the old media is re-fed (project reload) with seen_at=now
    L2 = Ledger(extractors=[]); s2 = Service.for_test(FakePage(L2), L2, commit_timeout_s=0.2)
    L2._by_id["old"] = Media("old", "hello", "abra_i2v_8s", "video", time.time())
    r = s2.sweep(d)
    assert r["resolved"] == [] and len(r["still_pending"]) == 1


def test_sweep_clip_model_none_is_model_unknown_not_a_take(tmp_path):
    s = svc(tmp_path, commit=False, model=""); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s.page._land()
    r = s.sweep(d)
    (res,) = r["results"].values()
    assert res["status"] == "MODEL_UNKNOWN" and RunState(d).pending() == []
    assert RunState(d)._load()["lines"].get("L01", {}).get("clip", {}).get("versions", []) == []
    assert "m1" in RunState(d).claimed_media()


def test_sweep_clip_wrong_model_not_a_take(tmp_path):
    s = svc(tmp_path, commit=False, model="abra_edit"); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s.page._land()
    (res,) = s.sweep(d)["results"].values()
    assert res["status"] == "WRONG_MODEL"
    assert RunState(d)._load()["lines"].get("L01", {}).get("clip", {}).get("versions", []) == []


def test_sweep_still_landscape_is_wrong_aspect(tmp_path):
    s = svc(tmp_path, commit=False, dims=(1376, 768)); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s.page._land()
    (res,) = s.sweep(d)["results"].values()
    assert res["status"] == "WRONG_ASPECT" and res["aspect_checked"] is True


def test_sweep_tops_up_partial_commit(tmp_path):
    s = svc(tmp_path, land_at={2: ["m1"]}); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png", outputs=2)
    assert r["partial"] is True
    s.page._land("m2")  # the second output lands after the commit window
    sw = s.sweep(d)
    assert sw["topped_up"] == {r["attempt"]: ["m2"]}
    at = RunState(d)._load()["attempts"][r["attempt"]]
    assert at["media_ids"] == ["m1", "m2"] and at["partial"] is False
    out = s.download(d, ["m2"])
    assert out["written"] == [str(d / "Elements" / "Stills" / "claude_nj_line01_alt2.png")]
    assert s.sweep(d)["topped_up"] == {}  # nothing left to add


# --- fix round 1: clip model enum + count cap, PNG container on download ----------------------

def test_clip_veo_lite_refused_until_label_verified(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, model="veo_lite")
    assert "Veo Lite label not verified yet" in str(e.value)
    assert ("fire",) not in s.page.calls and RunState(d).pending() == []


def test_clip_unknown_model_and_count_over_cap_refused(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    for kw in ({"model": "omni_flash_lite"}, {"model": "veo_3"}, {"count": 3}, {"count": 0}):
        with pytest.raises(FlowError) as e:
            s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, **kw)
        assert e.value.code == "SETTINGS_MISMATCH"
    assert ("fire",) not in s.page.calls


def test_clip_credits_come_from_the_enum(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["credits"] == 12


def _img_bytes(fmt):
    import io
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (768, 1376), (200, 30, 30)).save(b, format=fmt); return b.getvalue()


def test_download_jpeg_to_png_name_is_real_png(tmp_path):
    from PIL import Image
    s = svc(tmp_path); d = proj(tmp_path)
    s.page.fetch_bytes = lambda m: _img_bytes("JPEG")
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    f = pathlib.Path(s.download(d, r["media_ids"])["written"][0])
    assert f.suffix == ".png" and f.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(f) as im:
        assert im.format == "PNG" and im.size == (768, 1376)


def test_as_png_if_named_leaves_clips_and_non_images_alone():
    from flow_mcp.service import as_png_if_named
    jpg, webp = _img_bytes("JPEG"), _img_bytes("WEBP")
    assert as_png_if_named(pathlib.Path("c.mp4"), jpg) == jpg
    assert as_png_if_named(pathlib.Path("s.png"), b"x" * 50) == b"x" * 50
    assert as_png_if_named(pathlib.Path("s.png"), webp)[:4] == b"\x89PNG"
    png = as_png_if_named(pathlib.Path("s.png"), jpg)
    assert as_png_if_named(pathlib.Path("s.png"), png) == png  # already PNG: untouched


# --- Task 2: lint stamp, run lock, multi-ref stills ----------------------------------------------

def _fired(s): return [c for c in s.page.calls if c[0] in ("fire", "fire_ready", "reset")]


def test_clip_without_stamp_is_lint_required_before_anything(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path, stamped=False)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "LINT_REQUIRED"
    assert _fired(s) == [] and RunState(d).pending() == [] and RunState(d).attempts() == {}


def test_clip_with_pass_stamp_fires(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"
    assert not (d / "_flow_run.lock").exists()  # released after fire + settle


def test_clip_fail_stamp_and_stale_manifest_refused(tmp_path):
    from flow_mcp import stamp
    s = svc(tmp_path); d = proj(tmp_path); m = d / "Creatives" / "nj.json"
    stamp.write(d, m, {"L01": {"status": "FAIL"}})
    with pytest.raises(FlowError) as e: s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "LINT_REQUIRED"
    stamp.write(d, m, {"L01": {"status": "PASS", "prompt_sha256": stamp.prompt_sha("hello")}})
    m.write_text('{"shots": []}', encoding="utf-8")
    with pytest.raises(FlowError) as e: s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "LINT_REQUIRED" and _fired(s) == [] and RunState(d).attempts() == {}


def test_still_ignores_the_stamp(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path, stamped=False)
    assert s.fire_still(d, "nj", "L01", "p1", d / "ref.png")["status"] == "committed"


def test_still_refs_attach_many_before_fire(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", refs=[d / "a.png", d / "b.png"])
    names = [c[0] for c in s.page.calls]
    assert ("attach_many", ["a", "b"]) in s.page.calls and "attach" not in names
    assert names.index("attach_many") < names.index("fire_ready") < names.index("fire")


def test_still_single_ref_still_uses_attach(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", ref=d / "a.png")
    assert ("attach", "a") in s.page.calls


@pytest.mark.parametrize("refs", [[], ["a", "b", "c", "d"], ["a", "a"]])
def test_still_bad_refs_count_refused_before_pending(tmp_path, refs):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_still(d, "nj", "L01", "p1", refs=[d / f"{r}.png" for r in refs])
    assert e.value.code == "REF_NOT_ATTACHED"
    assert s.page.calls == [] and RunState(d).attempts() == {}


def test_still_ref_and_refs_together_or_neither_refused(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    for kw in ({"ref": d / "a.png", "refs": [d / "b.png"]}, {}):
        with pytest.raises(FlowError) as e:
            s.fire_still(d, "nj", "L01", "p1", **kw)
        assert e.value.code == "SETTINGS_MISMATCH"
    assert s.page.calls == []


def test_held_lock_is_run_locked_before_pending(tmp_path):
    import json, os
    s = svc(tmp_path); d = proj(tmp_path)
    (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
    for call in (lambda: s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8),
                 lambda: s.fire_still(d, "nj", "L01", "p1", d / "ref.png"),
                 lambda: s.sweep(d)):
        with pytest.raises(FlowError) as e:
            call()
        assert e.value.code == "RUN_LOCKED"
    assert s.page.calls == [] and RunState(d).attempts() == {}
    assert (d / "_flow_run.lock").exists()  # the other holder's lock is untouched


def test_lock_released_when_fire_raises(tmp_path):
    s = svc(tmp_path, buttonless=True); d = proj(tmp_path)
    with pytest.raises(FlowError):
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert not (d / "_flow_run.lock").exists()


# --- Fix round 1 ----------------------------------------------------------------------------------

def test_clip_prompt_must_match_linted_prompt(tmp_path):
    from flow_mcp import stamp
    s = svc(tmp_path); d = proj(tmp_path); m = d / "Creatives" / "nj.json"
    stamp.write(d, m, {"L01": {"status": "PASS", "prompt_sha256": stamp.prompt_sha("hello")}})
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello!", d / "a.png", 8)
    assert e.value.code == "LINT_REQUIRED" and s.page.calls == [] and RunState(d).attempts() == {}
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"


def test_open_project_and_download_take_the_lock(tmp_path):
    import json, os
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s.page.calls.clear()
    (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
    for call in (lambda: s.open_project("proj", d), lambda: s.download(d, r["media_ids"])):
        with pytest.raises(FlowError) as e:
            call()
        assert e.value.code == "RUN_LOCKED"
    assert s.page.calls == [] and not (d / "Elements").exists()
    (d / "_flow_run.lock").unlink()
    assert s.download(d, r["media_ids"])["written"] and not (d / "_flow_run.lock").exists()


# --- Fix round 2 ----------------------------------------------------------------------------------

def test_clip_stamp_without_prompt_binding_refused(tmp_path):
    from flow_mcp import stamp
    s = svc(tmp_path); d = proj(tmp_path)
    stamp.write(d, d / "Creatives" / "nj.json", {"L01": {"status": "PASS"}})
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "LINT_REQUIRED" and "predates prompt binding" in e.value.detail
    assert s.page.calls == [] and RunState(d).attempts() == {}


def _swap_lock_on(page, method, d):
    """Another holder takes the lock file while this call is mid-operation."""
    import json, os
    real = getattr(page, method)
    def swapped(*a, **k):
        (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
        return real(*a, **k)
    setattr(page, method, swapped)


@pytest.mark.parametrize("which", ["clip", "still"])
def test_lock_swapped_before_fire_is_run_locked_not_fired(tmp_path, which):
    s = svc(tmp_path); d = proj(tmp_path)
    _swap_lock_on(s.page, "fire_ready", d)
    with pytest.raises(FlowError) as e:
        if which == "clip":
            s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
        else:
            s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert e.value.code == "RUN_LOCKED"
    assert ("fire",) not in s.page.calls and RunState(d).pending() == []
    assert [a["status"] for a in RunState(d).attempts().values()] == ["not_fired"]
    assert (d / "_flow_run.lock").exists()  # the other holder's lock is left alone


def test_lock_swapped_before_sweep_writes_is_run_locked(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "UNCONFIRMED"
    s.page._land("m1")
    _swap_lock_on(s.page, "reset", d)
    with pytest.raises(FlowError) as e:
        s.sweep(d)
    assert e.value.code == "RUN_LOCKED" and len(RunState(d).pending()) == 1  # nothing settled
