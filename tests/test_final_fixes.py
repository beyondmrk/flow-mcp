"""Final whole-branch review fixes: sweep reload+pump, path escapes, NO_MEDIA_URL."""
import pytest

from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Ledger
from flow_mcp.runstate import RunState
from flow_mcp.service import Service
from tests.test_service import FakePage, proj, svc


def _restarted(prompt, **kw):
    """A fresh MCP process: empty ledger, the page still showing the project."""
    L2 = Ledger(extractors=[]); page = FakePage(L2, **kw); page._t = prompt
    return Service.for_test(page, L2, commit_timeout_s=0.2)


# --- 1. sweep sees media after an MCP restart ------------------------------------------------

def test_sweep_after_restart_reloads_and_finds_landed_media(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "UNCONFIRMED"
    s2 = _restarted("hello", land_on_reset=["m1"])
    r = s2.sweep(d)
    assert ("reset",) in s2.page.calls
    assert len(r["resolved"]) == 1 and r["results"][r["resolved"][0]]["status"] == "committed"


def test_sweep_pumps_before_reading_the_ledger(tmp_path):
    s = svc(tmp_path, commit=False); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s2 = _restarted("hello", commit_on_wait=3)  # lands only while the page is pumped
    assert len(s2.sweep(d)["resolved"]) == 1


def test_sweep_waits_for_a_late_model_string(tmp_path):
    s = svc(tmp_path, commit=False, model=""); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s.page._land()
    s.page.model_at_wait = s.page.waits + 3
    (res,) = s.sweep(d)["results"].values()
    assert res["status"] == "committed"


def test_sweep_moves_the_shared_tab_to_the_bound_project(tmp_path):
    # attach-only shared tab: a peer may have left it on another project - go to ours, never refuse
    s = svc(tmp_path, commit=False, project_id="bbbb"); d = proj(tmp_path)
    RunState(d).set_project("aaaa", "proj")
    s.sweep(d)
    assert ("goto_project", "aaaa") in s.page.calls and s.page.project_id == "aaaa"


def test_unbound_project_dir_is_ui_changed(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    RunState(d).set_project(None, None)
    with pytest.raises(FlowError) as e:
        s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert e.value.code == "UI_CHANGED" and "flow_open_project" in str(e.value)


def test_download_unknown_to_ledger_reloads_once_then_writes(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s2 = _restarted("p1", land_on_reset=["m1"])
    out = s2.download(d, r["media_ids"])
    assert len(out["written"]) == 1 and s2.page.calls.count(("reset",)) == 1


def test_download_still_unknown_after_reload_raises(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    s2 = _restarted("p1")
    with pytest.raises(ValueError, match="unknown mediaId m1"):
        s2.download(d, r["media_ids"])
    assert s2.page.calls.count(("reset",)) == 1


# --- 2. downloads cannot escape the project --------------------------------------------------

@pytest.mark.parametrize("bad", ["..", "..\\evil", "a/b", "a\\b", "C:evil", "D:x", ""])
def test_clip_name_with_path_parts_refused(tmp_path, bad):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, bad, "L01", "hello", d / "a.png", 8)
    assert e.value.code == "PATH_REFUSED" and ("fire",) not in s.page.calls
    assert "attempts" not in RunState(d)._load()


@pytest.mark.parametrize("bad", ["..", "../x", "x\\y", "C:x"])
def test_slug_with_path_parts_refused(tmp_path, bad):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_still(d, bad, "L01", "p1", d / "ref.png")
    assert e.value.code == "PATH_REFUSED" and ("fire",) not in s.page.calls


def test_line_with_path_parts_refused(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "..\\L01", "hello", d / "a.png", 8)
    assert e.value.code == "PATH_REFUSED"


@pytest.mark.parametrize("evil", ["..\\..\\..\\..\\Users\\evil.mp4", "..\\..\\..\\escaped.mp4"])
def test_download_target_escaping_project_refused(tmp_path, evil):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    rs = RunState(d); raw = rs._load()  # a hand-edited / legacy run state with an escaping name
    raw["lines"]["L01"]["clip"]["versions"][0]["file"] = evil
    rs._save(raw)
    with pytest.raises(FlowError) as e:
        s.download(d, ["m1"])
    assert e.value.code == "PATH_REFUSED"
    assert not (d.parent / "escaped.mp4").exists()


# --- 3. a committed media with no URL is not a paid take -------------------------------------

def test_clip_without_media_url_is_not_a_paid_take(tmp_path):
    s = svc(tmp_path, has_url=False); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "NO_MEDIA_URL" and r["credits"] == "unknown" and r["media_ids"] == ["m1"]
    assert RunState(d)._load()["lines"].get("L01", {}).get("clip", {}).get("versions", []) == []
    assert RunState(d).pending() == [] and "m1" in RunState(d).claimed_media()


def test_clip_media_url_arriving_late_is_committed(tmp_path):
    s = svc(tmp_path, has_url=False, url_at_wait=2); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "committed" and r["credits"] == 12


def test_still_without_media_url_not_recorded(tmp_path):
    s = svc(tmp_path, has_url=False); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "NO_MEDIA_URL" and r["credits"] == "unknown"
    assert RunState(d)._load()["lines"].get("L01", {}).get("still", {}).get("candidates", []) == []


def test_sweep_media_without_url_is_no_media_url(tmp_path):
    s = svc(tmp_path, commit=False, has_url=False); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    s.page._land()
    (res,) = s.sweep(d)["results"].values()
    assert res["status"] == "NO_MEDIA_URL" and res["credits"] == "unknown"
    assert RunState(d).pending() == []
