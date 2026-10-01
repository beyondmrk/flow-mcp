"""Flow-runner whole-branch review fixes: no double-bill across runs, wrong-project fire."""
import inspect
from types import SimpleNamespace

import pytest

from flow_mcp import server
from flow_mcp.flowpage import FlowPage
from flow_mcp.guards import CODES, FlowError
from flow_mcp.runstate import RunState
from tests.test_service import proj, svc


# --- 1. a committed line refuses a second paid fire unless reroll=True -------------------------

def test_already_committed_is_a_code():
    assert "ALREADY_COMMITTED" in CODES


def test_committed_line_refuses_second_clip_fire(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    assert s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)["status"] == "committed"
    attempts_before = dict(RunState(d).attempts())
    s.page.calls.clear()
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert e.value.code == "ALREADY_COMMITTED"
    assert RunState(d).attempts() == attempts_before  # no new pending written
    assert RunState(d).pending() == []
    assert ("fire",) not in s.page.calls


def test_reroll_true_fires_a_committed_line(tmp_path):
    s = svc(tmp_path); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    land = s.page._land
    s.page._land = lambda mid="m2", model=None: land(mid, model)  # the re-roll lands NEW media
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, reroll=True)
    assert r["status"] == "committed" and r["version"] == 2


def test_mcp_tool_exposes_reroll_default_false():
    sig = inspect.signature(server.flow_fire_clip)
    assert sig.parameters["reroll"].default is False
    assert "credits" in (server.flow_fire_clip.__doc__ or "").lower()
    assert "reroll" in (server.flow_fire_clip.__doc__ or "")


# --- 2. goto_project must land on the bound project -------------------------------------------

class _Loc:
    first = SimpleNamespace(wait_for=lambda **k: None)


class _Page:
    def __init__(self, land_url): self.url, self.land_url = "https://flow.test/", land_url
    def goto(self, url): self.url = self.land_url or url
    def locator(self, sel): return _Loc()


def _fp(land_url=None):
    pg = _Page(land_url)
    b = SimpleNamespace(page=lambda: pg, cfg=SimpleNamespace(flow_url="https://flow.test/"))
    return FlowPage(b)


PID = "0000aaaa-0000-0000-0000-000000000001"
OTHER = "0000bbbb-0000-0000-0000-000000000002"


def test_goto_project_lands_ok():
    fp = _fp()
    fp.goto_project(PID)
    assert fp.current_project_id() == PID


def test_goto_project_landing_elsewhere_is_ui_changed():
    fp = _fp(land_url=f"https://flow.test/project/{OTHER}")
    with pytest.raises(FlowError) as e:
        fp.goto_project(PID)
    assert e.value.code == "UI_CHANGED" and "did not land on bound project" in e.value.detail


def test_goto_project_redirect_off_project_is_ui_changed():
    fp = _fp(land_url="https://flow.test/")
    with pytest.raises(FlowError) as e:
        fp.goto_project(PID)
    assert e.value.code == "UI_CHANGED"
