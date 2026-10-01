import json, os, time
import pytest
from pathlib import Path
from flow_mcp import stamp, runlock
from flow_mcp.guards import FlowError

from .conftest import TEST_ROOT

def proj(tmp_path):
    d = TEST_ROOT / tmp_path.name
    (d / "Creatives").mkdir(parents=True, exist_ok=True)
    m = d / "Creatives" / "home_nj.json"; m.write_text(json.dumps({"shots": [{"tag": "L01"}]}), encoding="utf-8")
    return d, m

def test_missing_stamp_refuses(tmp_path):
    d, m = proj(tmp_path)
    with pytest.raises(FlowError) as e: stamp.check(d, m, "L01")
    assert e.value.code == "LINT_REQUIRED"

def test_pass_stamp_ok_then_sha_mismatch(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "PASS", "findings": [], "refs": []}})
    stamp.check(d, m, "L01")
    m.write_text(json.dumps({"shots": [{"tag": "L01", "x": 1}]}), encoding="utf-8")
    with pytest.raises(FlowError): stamp.check(d, m, "L01")

def test_fail_line_refuses(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "FAIL", "findings": [], "refs": []}})
    with pytest.raises(FlowError): stamp.check(d, m, "L01")

def test_warn_line_ok(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "WARN", "findings": [], "refs": []}})
    stamp.check(d, m, "L01")

def test_manifest_for_picks_shots_json(tmp_path):
    d, m = proj(tmp_path)
    (d / "Creatives" / "_preflight.json").write_text("{}")
    assert stamp.manifest_for(d) == m

def test_lock_blocks_second_holder(tmp_path):
    d, _ = proj(tmp_path)
    with runlock.RunLock(d):
        (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
        with pytest.raises(FlowError) as e:
            with runlock.RunLock(d): pass
        assert e.value.code == "RUN_LOCKED"

def test_stale_lock_reclaimed(tmp_path):
    d, _ = proj(tmp_path)
    (d / "_flow_run.lock").write_text(json.dumps({"pid": 999999, "created": time.time() - 99999}))
    with runlock.RunLock(d): assert (d / "_flow_run.lock").exists()
    assert not (d / "_flow_run.lock").exists()


# --- extras beyond the brief -------------------------------------------------------------------

def test_line_missing_from_stamp_refuses(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "PASS", "findings": [], "refs": []}})
    with pytest.raises(FlowError) as e: stamp.check(d, m, "L02")
    assert e.value.code == "LINT_REQUIRED"

def test_unknown_status_and_corrupt_stamp_refuse(tmp_path):
    d, m = proj(tmp_path)
    p = stamp.write(d, m, {"L01": {"status": "SKIPPED"}})
    assert p == d / "Creatives" / "_flow_lint.json"
    with pytest.raises(FlowError): stamp.check(d, m, "L01")
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(FlowError) as e: stamp.check(d, m, "L01")
    assert e.value.code == "LINT_REQUIRED"

def test_stamp_file_shape(tmp_path):
    d, m = proj(tmp_path)
    raw = json.loads(stamp.write(d, m, {"L01": {"status": "PASS"}}).read_text(encoding="utf-8"))
    assert set(raw) == {"manifest_sha256", "created", "lines"} and len(raw["manifest_sha256"]) == 64

def test_manifest_for_none_or_ambiguous_refuses(tmp_path):
    d, m = proj(tmp_path)
    (d / "Creatives" / "home_ny.json").write_text(json.dumps({"shots": []}), encoding="utf-8")
    with pytest.raises(FlowError) as e: stamp.manifest_for(d)
    assert e.value.code == "LINT_REQUIRED"
    m.unlink(); (d / "Creatives" / "home_ny.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    (d / "Creatives" / "notes.json").write_text("not json", encoding="utf-8")
    with pytest.raises(FlowError): stamp.manifest_for(d)

def test_lock_own_pid_other_object_is_locked(tmp_path):
    d, _ = proj(tmp_path)
    with runlock.RunLock(d):
        with pytest.raises(FlowError) as e:
            with runlock.RunLock(d): pass
        assert e.value.code == "RUN_LOCKED"
    assert not (d / "_flow_run.lock").exists()

def test_same_object_reentry_ok(tmp_path):
    d, _ = proj(tmp_path)
    lk = runlock.RunLock(d)
    with lk:
        with lk: pass
        assert (d / "_flow_run.lock").exists()
    assert not (d / "_flow_run.lock").exists()

def test_dead_pid_fresh_lock_reclaimed(tmp_path):
    d, _ = proj(tmp_path)
    (d / "_flow_run.lock").write_text(json.dumps({"pid": 999999, "created": time.time()}))
    with runlock.RunLock(d):
        assert json.loads((d / "_flow_run.lock").read_text())["pid"] == os.getpid()

def test_live_pid_expired_ttl_reclaimed(tmp_path):
    d, _ = proj(tmp_path)
    (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time() - 7300}))
    with runlock.RunLock(d): pass

def test_release_never_deletes_a_foreign_lock(tmp_path):
    d, _ = proj(tmp_path)
    with runlock.RunLock(d):
        (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
    assert (d / "_flow_run.lock").exists()

def test_pid_alive():
    assert runlock.pid_alive(os.getpid()) and runlock.pid_alive(os.getppid())
    assert not runlock.pid_alive(999999)


# --- Fix round 1 ---------------------------------------------------------------------------------

def test_prompt_sha_bound_to_line(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "PASS", "prompt_sha256": stamp.prompt_sha("héllo")}})
    stamp.check(d, m, "L01", "héllo")
    stamp.check(d, m, "L01")  # no prompt given: only the status + manifest sha are checked
    with pytest.raises(FlowError) as e: stamp.check(d, m, "L01", "hello")
    assert e.value.code == "LINT_REQUIRED" and "prompt differs" in e.value.detail

def test_prompt_sha_is_utf8_sha256():
    import hashlib
    assert stamp.prompt_sha("é") == hashlib.sha256("é".encode("utf-8")).hexdigest()

def test_stamp_without_prompt_sha_refuses_a_prompted_check(tmp_path):
    d, m = proj(tmp_path)
    stamp.write(d, m, {"L01": {"status": "PASS"}})
    with pytest.raises(FlowError) as e: stamp.check(d, m, "L01", "anything")
    assert e.value.code == "LINT_REQUIRED" and "predates prompt binding" in e.value.detail
    stamp.check(d, m, "L01")  # no prompt given (not a clip fire): still fine

def test_reclaim_never_uses_os_replace(tmp_path, monkeypatch):
    d, _ = proj(tmp_path)
    (d / "_flow_run.lock").write_text(json.dumps({"pid": 999999, "created": time.time() - 99999}))
    def boom(*a, **k): raise AssertionError("os.replace used on the lock")
    monkeypatch.setattr(runlock.os, "replace", boom)
    with runlock.RunLock(d):
        assert json.loads((d / "_flow_run.lock").read_text())["pid"] == os.getpid()
    assert list(d.glob("_flow_run.lock*")) == []  # no tombstone or tmp left behind

def test_reclaim_race_fresh_lock_is_restored_not_stolen(tmp_path, monkeypatch):
    d, _ = proj(tmp_path); lock = d / "_flow_run.lock"
    lock.write_text(json.dumps({"pid": 999999, "created": time.time() - 99999}))
    fresh = {"pid": os.getppid(), "created": time.time()}
    real = os.rename
    def racing_rename(src, dst):
        if Path(src) == lock:  # another process reclaimed + took it between our read and rename
            lock.unlink(); lock.write_text(json.dumps(fresh))
        return real(src, dst)
    monkeypatch.setattr(runlock.os, "rename", racing_rename)
    with pytest.raises(FlowError) as e:
        with runlock.RunLock(d): pass
    assert e.value.code == "RUN_LOCKED"
    assert json.loads(lock.read_text()) == fresh  # the live holder's lock is back in place
    assert list(d.glob("_flow_run.lock.*")) == []

def test_lock_vanishing_after_exists_retries_link(tmp_path, monkeypatch):
    d, _ = proj(tmp_path); calls = []
    real = os.link
    def flaky_link(src, dst):
        calls.append(dst)
        if len(calls) == 1: raise FileExistsError(dst)  # it existed, then was released
        return real(src, dst)
    monkeypatch.setattr(runlock.os, "link", flaky_link)
    with runlock.RunLock(d): assert (d / "_flow_run.lock").exists()
    assert len(calls) == 2

def test_link_retries_are_bounded(tmp_path, monkeypatch):
    d, _ = proj(tmp_path)
    def always_exists(src, dst): raise FileExistsError(dst)
    monkeypatch.setattr(runlock.os, "link", always_exists)
    with pytest.raises(FlowError) as e:
        with runlock.RunLock(d): pass
    assert e.value.code == "RUN_LOCKED"


def test_assert_held(tmp_path):
    d, _ = proj(tmp_path)
    with runlock.RunLock(d) as lk:
        lk.assert_held()
        (d / "_flow_run.lock").write_text(json.dumps({"pid": os.getppid(), "created": time.time()}))
        with pytest.raises(FlowError) as e: lk.assert_held()
        assert e.value.code == "RUN_LOCKED"
    with pytest.raises(FlowError): runlock.RunLock(d).assert_held()  # never acquired
