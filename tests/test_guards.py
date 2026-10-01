import os
from pathlib import Path

import pytest
from flow_mcp import guards
from flow_mcp.guards import FlowError, check_path, check_clip_model, check_name, CODES

from .conftest import DENIED_ROOT, TEST_ROOT


def test_path_under_work_root_ok():
    assert check_path(TEST_ROOT / "x" / "a.png") == TEST_ROOT / "x" / "a.png"
    assert check_path(TEST_ROOT) == TEST_ROOT


@pytest.mark.parametrize("p", [
    DENIED_ROOT / "a.png",                                   # a denied root, though under work_root
    "\\\\?\\" + str(DENIED_ROOT / "a.png"),                  # same, extended-length spelling
    TEST_ROOT.parent / "a.png",                              # above work_root
    TEST_ROOT / ".." / "other" / "a.png",                    # climbs out with ..
    TEST_ROOT.parent / (TEST_ROOT.name + "x") / "a.png",     # sibling sharing the name prefix
])
def test_path_refused(p):
    with pytest.raises(FlowError) as e:
        check_path(p)
    assert e.value.code == "PATH_REFUSED"


def test_no_work_root_refuses_every_path(monkeypatch):
    monkeypatch.setattr(guards, "ROOT", None)
    with pytest.raises(FlowError) as e:
        check_path(TEST_ROOT / "a.png")
    assert e.value.code == "PATH_REFUSED" and "work_root" in e.value.detail


@pytest.mark.parametrize("m", ["abra_r2v_8s", "abra_i2v_10s"])
def test_clip_model_ok(m):
    check_clip_model(m)


@pytest.mark.parametrize("m", ["abra_edit", "veo_3_1_lite", ""])
def test_clip_model_wrong(m):
    with pytest.raises(FlowError) as e:
        check_clip_model(m)
    assert e.value.code == "WRONG_MODEL"


def test_codes_complete():
    assert CODES == {"PATH_REFUSED", "WRONG_MODEL", "OUT_OF_CREDITS", "CAPTCHA", "LOGIN_REQUIRED",
                     "UNCONFIRMED", "UI_CHANGED", "SETTINGS_MISMATCH", "REF_NOT_ATTACHED", "BROWSER_DOWN",
                     "WRONG_ASPECT", "MODEL_UNKNOWN", "NO_MEDIA_URL", "LINT_REQUIRED", "RUN_LOCKED",
                     "ALREADY_COMMITTED", "SAME_PROMPT_PENDING", "IN_FLIGHT",
                     "LOST_PENDING_RELEASE"}


def test_path_under_work_root_still_ok_after_realpath():
    r = check_path(str(TEST_ROOT) + "/x/../x/a.png")
    assert r == TEST_ROOT / "x" / "a.png"


def test_junction_inside_work_root_pointing_outside_refused(tmp_path):
    import _winapi
    base = TEST_ROOT / tmp_path.name
    base.mkdir(parents=True, exist_ok=True)
    j = base / "escape"
    if not j.exists():
        _winapi.CreateJunction(str(tmp_path), str(j))
    try:
        with pytest.raises(FlowError) as e:
            check_path(j / "a.png")
        assert e.value.code == "PATH_REFUSED"
    finally:
        os.rmdir(j)  # removes the junction only, never its target


@pytest.mark.parametrize("n", ["NJ_line01", "nj", "L01", "abc-1.2"])
def test_safe_name_ok(n):
    assert check_name(n, "clip_name") == n


@pytest.mark.parametrize("n", ["", "..", "a/b", "a" + chr(92) + "b", "C:x", "../x"])
def test_safe_name_refused(n):
    with pytest.raises(FlowError) as e:
        check_name(n, "clip_name")
    assert e.value.code == "PATH_REFUSED"
