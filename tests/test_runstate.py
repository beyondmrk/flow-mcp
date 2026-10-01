import json
from pathlib import Path
from flow_mcp.runstate import RunState


def rs(tmp_path):
    return RunState(tmp_path)


def test_pending_written_before_commit(tmp_path):
    r = rs(tmp_path)
    a = r.begin_fire("L01", "clip", "Hi I am NJ")
    raw = json.loads((tmp_path / "_flow_run.json").read_text(encoding="utf-8"))
    assert raw["attempts"][a]["status"] == "pending"
    assert [p["attempt_id"] for p in r.pending()] == [a]
    r.commit(a, ["m1"], "abra_r2v_8s")
    assert r.pending() == []
    assert json.loads((tmp_path / "_flow_run.json").read_text())["attempts"][a]["media_ids"] == ["m1"]


def test_still_names_canonical_then_alts(tmp_path):
    r = rs(tmp_path)
    n1 = r.next_still_name("L01", "new_jersey")
    r.add_still("L01", "m1", n1)
    n2 = r.next_still_name("L01", "new_jersey")
    r.add_still("L01", "m2", n2)
    n3 = r.next_still_name("L01", "new_jersey")
    assert (n1, n2, n3) == (
        "claude_new_jersey_line01.png",
        "claude_new_jersey_line01_alt2.png",
        "claude_new_jersey_line01_alt3.png",
    )


def test_clip_versions(tmp_path):
    r = rs(tmp_path)
    assert r.next_clip_version("L02") == 1
    r.add_clip("L02", 1, "m9", "NJ_line02_v01.mp4")
    assert r.next_clip_version("L02") == 2


def test_keeps_existing_shape(tmp_path):
    (tmp_path / "_flow_run.json").write_text(
        json.dumps(
            {
                "projectId": "p",
                "unknown_key": "preserved",
                "lines": {
                    "L01": {
                        "still": {
                            "candidates": [
                                {
                                    "slot": "canonical",
                                    "mediaId": "x",
                                    "file": "claude_s_line01.png",
                                    "downloaded": True,
                                    "picked": True,
                                }
                            ]
                        }
                    }
                },
            }
        )
    )
    r = rs(tmp_path)
    # Verify naming recognizes existing candidate
    assert r.next_still_name("L01", "s") == "claude_s_line01_alt2.png"
    # Perform a write (add_still)
    r.add_still("L01", "m2", "claude_s_line01_alt2.png")
    # Assert preservation through write
    raw = json.loads((tmp_path / "_flow_run.json").read_text())
    assert raw["projectId"] == "p"
    assert raw["unknown_key"] == "preserved"
    assert raw["lines"]["L01"]["still"]["candidates"][0]["picked"] is True
    # Assert new candidate added as _alt2
    assert len(raw["lines"]["L01"]["still"]["candidates"]) == 2
    assert raw["lines"]["L01"]["still"]["candidates"][1]["file"] == "claude_s_line01_alt2.png"


def test_should_skip(tmp_path):
    f = tmp_path / "a.png"
    r = rs(tmp_path)
    assert not r.should_skip(f)
    f.write_bytes(b"x" * 100)
    assert not r.should_skip(f)
    f.write_bytes(b"x" * 10241)
    assert r.should_skip(f)


def test_set_downloaded(tmp_path):
    r = rs(tmp_path)
    # Add a still and a clip with the same media_id
    r.add_still("L01", "m1", "claude_test_line01.png")
    r.add_clip("L02", 1, "m1", "clip_v01.mp4")
    # Mark the downloaded
    r.set_downloaded("m1")
    raw = json.loads((tmp_path / "_flow_run.json").read_text())
    # Verify still is marked downloaded
    assert raw["lines"]["L01"]["still"]["candidates"][0]["downloaded"] is True
    assert raw["lines"]["L01"]["still"]["candidates"][0]["picked"] is False
    # Verify clip is marked downloaded
    assert raw["lines"]["L02"]["clip"]["versions"][0]["downloaded"] is True
    assert raw["lines"]["L02"]["clip"]["versions"][0]["qc"] is None


def test_set_downloaded_tolerates_missing_media_id(tmp_path):
    r = rs(tmp_path)
    # Seed with an entry missing mediaId
    (tmp_path / "_flow_run.json").write_text(
        json.dumps(
            {
                "lines": {
                    "L01": {
                        "still": {
                            "candidates": [{"slot": "canonical", "file": "bad.png"}]
                        }
                    }
                }
            }
        )
    )
    r = rs(tmp_path)
    # Should not crash
    r.set_downloaded("m1")
    raw = json.loads((tmp_path / "_flow_run.json").read_text())
    assert raw["lines"]["L01"]["still"]["candidates"][0] == {"slot": "canonical", "file": "bad.png"}


def test_mark_unconfirmed_appears_in_pending(tmp_path):
    r = rs(tmp_path)
    a = r.begin_fire("L01", "clip", "test prompt")
    r.mark(a, "unconfirmed")
    pending = r.pending()
    assert len(pending) == 1
    assert pending[0]["attempt_id"] == a
    assert pending[0]["status"] == "unconfirmed"


def test_mark_only_accepts_valid_statuses(tmp_path):
    r = rs(tmp_path)
    a = r.begin_fire("L01", "clip", "test prompt")
    try:
        r.mark(a, "invalid")
        assert False, "mark() should raise ValueError"
    except ValueError as e:
        assert "unconfirmed" in str(e) and "wrong_model" in str(e)


def test_add_still_skips_duplicate_media_id(tmp_path):
    r = rs(tmp_path)
    r.add_still("L01", "m1", "claude_test_line01.png")
    r.add_still("L01", "m1", "claude_test_line01_alt2.png")  # Same media_id
    raw = json.loads((tmp_path / "_flow_run.json").read_text())
    assert len(raw["lines"]["L01"]["still"]["candidates"]) == 1


def test_add_clip_skips_duplicate_media_id(tmp_path):
    r = rs(tmp_path)
    r.add_clip("L01", 1, "m1", "clip_v01.mp4")
    r.add_clip("L01", 2, "m1", "clip_v02.mp4")  # Same media_id
    raw = json.loads((tmp_path / "_flow_run.json").read_text())
    assert len(raw["lines"]["L01"]["clip"]["versions"]) == 1


def test_next_still_name_raises_for_line_without_digits(tmp_path):
    r = rs(tmp_path)
    try:
        r.next_still_name("ABC", "test")
        assert False, "next_still_name() should raise ValueError"
    except ValueError as e:
        assert "ABC" in str(e) and "no digits" in str(e)


def test_next_clip_version_skips_entries_without_v(tmp_path):
    r = rs(tmp_path)
    # Seed with an entry missing 'v'
    (tmp_path / "_flow_run.json").write_text(
        json.dumps(
            {
                "lines": {
                    "L01": {
                        "clip": {
                            "versions": [
                                {"mediaId": "m1", "file": "old.mp4"}
                            ]
                        }
                    }
                }
            }
        )
    )
    r = rs(tmp_path)
    assert r.next_clip_version("L01") == 1


def test_next_still_name_counts_alt_from_slot_when_file_missing(tmp_path):
    r = rs(tmp_path)
    # Seed with an entry missing file but with slot="alt2"
    (tmp_path / "_flow_run.json").write_text(
        json.dumps(
            {
                "lines": {
                    "L01": {
                        "still": {
                            "candidates": [
                                {"slot": "alt2", "mediaId": "x"}
                            ]
                        }
                    }
                }
            }
        )
    )
    r = rs(tmp_path)
    # alt2 slot is used, so next name should be _alt3
    assert r.next_still_name("L01", "test") == "claude_test_line01_alt3.png"


def test_next_still_name_with_canonical_slot_skips_to_alt2(tmp_path):
    r = rs(tmp_path)
    # Seed with an entry missing file but with slot="canonical"
    (tmp_path / "_flow_run.json").write_text(
        json.dumps(
            {
                "lines": {
                    "L01": {
                        "still": {
                            "candidates": [
                                {"slot": "canonical", "mediaId": "x"}
                            ]
                        }
                    }
                }
            }
        )
    )
    r = rs(tmp_path)
    # Once there's any candidate, skip canonical and return _alt2
    assert r.next_still_name("L01", "test") == "claude_test_line01_alt2.png"


def test_not_fired_is_markable_and_not_pending(tmp_path):
    r = RunState(tmp_path)
    a = r.begin_fire("L01", "clip", "p", clip_name="NJ_line01")
    r.mark(a, "not_fired")
    assert r.pending() == []
    assert r._load()["attempts"][a]["clip_name"] == "NJ_line01"


def test_model_unknown_is_markable(tmp_path):
    r = RunState(tmp_path)
    a = r.begin_fire("L01", "clip", "p")
    r.mark(a, "model_unknown")
    assert r.pending() == []
