"""Browser-wide tab lock: attach-only peers (MCP server, flow_fire) share ONE Flow tab."""
import pytest

from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Ledger
from flow_mcp.runlock import LOCK_NAME, TAB_LOCK_NAME, TabLock
from flow_mcp.service import Service
from tests.test_service import FakePage, proj


def _svc(tab_dir, **kw):
    L = Ledger(extractors=[])
    return Service.for_test(FakePage(L, **kw), L, commit_timeout_s=0.2, tab_dir=tab_dir)


def test_tab_lock_file_lives_in_profile_dir(tmp_path):
    assert TabLock(tmp_path).path == tmp_path / TAB_LOCK_NAME


def test_held_tab_lock_refuses_before_the_run_lock(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path)
    with TabLock(tmp_path):  # another driver of the shared tab
        for call in (lambda: s.fire_still(d, "nj", "L01", "p1", d / "ref.png"),
                     lambda: s.sweep(d), lambda: s.download(d, ["m1"]),
                     lambda: s.open_project("proj", d)):
            with pytest.raises(FlowError) as e:
                call()
            assert e.value.code == "RUN_LOCKED" and "shared Flow tab" in str(e.value)
            assert not (d / LOCK_NAME).exists()  # order tab -> run: the run lock was never taken
    assert s.page.calls == []  # nothing touched the page


def test_upload_takes_the_tab_lock(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); f = d / "ref.png"; f.write_bytes(b"x")
    with TabLock(tmp_path):
        with pytest.raises(FlowError) as e:
            s.upload(f, d)
    assert e.value.code == "RUN_LOCKED"


def test_tab_then_run_lock_held_through_the_fire(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); seen = []
    real = s.page.fire

    def fire():
        seen.append(((tmp_path / TAB_LOCK_NAME).exists(), (d / LOCK_NAME).exists())); real()
    s.page.fire = fire
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    assert r["status"] == "committed" and seen == [(True, True)]
    assert not (tmp_path / TAB_LOCK_NAME).exists() and not (d / LOCK_NAME).exists()  # both released


def test_upload_with_project_dir_moves_tab_to_bound_project(tmp_path):
    d = proj(tmp_path); s = _svc(tmp_path); f = d / "ref.png"; f.write_bytes(b"x")
    s.upload(f, d)
    assert s.page.calls[0][0] == "goto_project" and s.page.calls[-1][0] == "upload"
