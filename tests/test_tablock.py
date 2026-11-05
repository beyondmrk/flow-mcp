"""Per-project tab locks: attach-only peers (the MCP servers of several Claude sessions, flow_fire)
each drive ONE project's own Flow window; two projects never wait on each other."""
import pytest

from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Ledger
from flow_mcp.runlock import LOCK_NAME, TAB_LOCK_NAME, TabLock, tab_lock_name
from flow_mcp.runstate import RunState
from flow_mcp.service import Service
from tests.test_service import TEST_PID, FakePage, proj

PID2 = "11111111-2222-3333-4444-555555555555"


def _svc(tab_dir, **kw):
    L = Ledger(extractors=[])
    return Service.for_test(FakePage(L, **kw), L, commit_timeout_s=0.2, tab_dir=tab_dir)


def test_tab_lock_file_lives_in_profile_dir(tmp_path):
    assert TabLock(tmp_path).path == tmp_path / TAB_LOCK_NAME  # legacy browser-wide lock
    assert TabLock(tmp_path, key=TEST_PID).path == tmp_path / f"_tab.{TEST_PID}.lock"
    assert tab_lock_name("home") == "_tab.home.lock"


def test_held_project_tab_lock_refuses_before_the_run_lock(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path)
    with TabLock(tmp_path, key=TEST_PID):  # another driver of THIS project's window
        for call in (lambda: s.fire_still(d, "nj", "L01", "p1", d / "ref.png"),
                     lambda: s.sweep(d), lambda: s.download(d, ["m1"]),
                     lambda: s.open_project("proj", d)):
            with pytest.raises(FlowError) as e:
                call()
            assert e.value.code == "RUN_LOCKED" and TEST_PID in str(e.value)
            assert not (d / LOCK_NAME).exists()  # order tab -> run: the run lock was never taken
    assert s.page.calls == []  # nothing touched the page


def test_another_projects_tab_lock_does_not_block(tmp_path):
    """The point of per-project tabs: a peer rendering project B never stalls a fire on project A."""
    d = proj(tmp_path); s = _svc(tmp_path)
    with TabLock(tmp_path, key=PID2), TabLock(tmp_path):  # a peer on project B + the legacy lock
        r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "committed"


def _unbound(tmp_path):
    """A fenced project dir with no Flow project bound yet."""
    d = proj(tmp_path); (d / "_flow_run.json").unlink(missing_ok=True); return d


def test_unbound_project_refuses_before_any_lock(tmp_path):
    d = _unbound(tmp_path); s = _svc(tmp_path)
    with pytest.raises(FlowError) as e:
        s.sweep(d)
    assert e.value.code == "UI_CHANGED" and "flow_open_project" in str(e.value)
    assert not (d / LOCK_NAME).exists() and s.page.calls == []


def test_upload_takes_the_project_tab_lock(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); f = d / "ref.png"; f.write_bytes(b"x")
    with TabLock(tmp_path, key=TEST_PID):
        with pytest.raises(FlowError) as e:
            s.upload(f, d)
    assert e.value.code == "RUN_LOCKED"


def test_tab_then_run_lock_held_through_the_fire(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); seen = []
    real = s.page.fire
    tab = tmp_path / tab_lock_name(TEST_PID)

    def fire():
        seen.append((tab.exists(), (d / LOCK_NAME).exists())); real()
    s.page.fire = fire
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "committed" and seen == [(True, True)]
    assert not tab.exists() and not (d / LOCK_NAME).exists()  # both released


def test_driver_points_at_the_projects_tab_for_the_call_only(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); used = []
    s.page.use = lambda pid: used.append(pid)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert used == [TEST_PID, None]  # pointed at the project's window, un-pointed on exit


def test_upload_with_project_dir_reloads_the_bound_project(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); f = d / "ref.png"; f.write_bytes(b"x")
    s.upload(f, d)
    assert s.page.calls[0][0] == "goto_project" and s.page.calls[-1][0] == "upload"


def test_inflight_marker_is_per_project(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path)
    s.fire_still(d, "nj", "L01", "p1", d / "ref.png", queue=True)
    assert (tmp_path / f"_tab.{TEST_PID}_inflight.json").exists()
    assert not (tmp_path / "_tab_inflight.json").exists()


def test_open_project_unbound_uses_a_fresh_home_tab_under_the_home_lock(tmp_path):
    d = _unbound(tmp_path); s = _svc(tmp_path); seen = []
    s.page.new_home_tab = lambda: seen.append((tmp_path / tab_lock_name("home")).exists())
    s.page.open_project = lambda name: PID2
    assert s.open_project("fresh", d) == {"projectId": PID2, "name": "fresh"}
    assert seen == [True] and RunState(d)._load()["projectId"] == PID2


def test_open_project_bound_stays_in_its_own_window(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); used = []
    s.page.use = lambda pid: used.append(pid)
    s.page.new_home_tab = lambda: pytest.fail("a bound project never opens a home tab")
    s.page.open_project = lambda name: TEST_PID
    s.open_project("proj", d)
    assert used == [TEST_PID, None]
