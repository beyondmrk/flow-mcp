"""WAVE firing: queued fires return once ACCEPTED, settle() collects them as they land."""
import asyncio
import dataclasses

import pytest

from flow_mcp import server
from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Ledger, Media
from flow_mcp.runstate import RunState
from flow_mcp.service import Service

from .test_service import FakePage, proj


class WavePage(FakePage):
    """Every click lands a NEW media id (m1, m2 ...) for the typed prompt, still rendering (no URL),
    unless land=False. tile=True: the click also adds a pending tile (Flow-side acceptance evidence),
    named t1, t2 ... (ident=False: tiles without identity -> None). render(mid) is Flow finishing it
    (its pending tile goes). cleared: what prompt_cleared() reports (never acceptance on its own).
    on_click: optional callable run inside fire() (e.g. an earlier job landing in the accept window)."""

    def __init__(self, ledger, land=True, cleared=None, marks_raises=None, tile=False, ident=True,
                 on_click=None, **kw):
        super().__init__(ledger, commit=False, **kw)
        self.land, self.n, self.cleared, self.marks_raises, self.tile = land, 0, cleared, marks_raises, tile
        self.ident, self.on_click, self.pend, self.armed = ident, on_click, [], False

    def fire(self):
        self.calls.append(("fire",))
        self.n += 1
        self.armed = True
        if self.on_click:
            self.on_click(self)
        if self.tile:
            self.pend.append(("t%d" % self.n if self.ident else None, "m%d" % self.n))
        if self.land:
            self.L._by_id[f"m{self.n}"] = Media(f"m{self.n}", self._t, self.model, "video", 9e18,
                                                None, "wf" + str(self.n), self.dims, False)

    def pending_tiles(self):
        if self.marks_raises and self.armed: raise self.marks_raises
        return [t for t, _ in self.pend]

    def prompt_cleared(self):
        return bool(self.cleared)

    def render(self, mid):
        self.L._by_id[mid] = dataclasses.replace(self.L._by_id[mid], has_url=True)
        self.pend = [x for x in self.pend if x[1] != mid]


def wsvc(**kw):
    L = Ledger(extractors=[])
    return Service.for_test(WavePage(L, **kw), L, commit_timeout_s=0.2)


def fires(s):
    return s.page.calls.count(("fire",))


def test_queued_accepted_by_ledger_returns_queued_and_keeps_pending(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert r["status"] == "QUEUED" and r["media_ids"] == ["m1"] and r["credits"] == 12
    p = RunState(d).pending()
    assert len(p) == 1 and p[0]["status"] == "pending" and p[0]["queued_at"] and p[0]["queued"]
    assert p[0]["attempt_id"] == r["attempt"] and fires(s) == 1
    assert RunState(d).claimed_media() == set()  # seen at acceptance, not claimed until settled


def test_cleared_prompt_alone_is_not_acceptance(tmp_path):
    s = wsvc(land=False, cleared=True); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert r["status"] == "UNCONFIRMED"


def test_queued_accepted_by_a_new_grid_tile_alone(tmp_path):
    s = wsvc(land=False, tile=True); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert r["status"] == "QUEUED" and r["media_ids"] == []
    assert RunState(d).attempts()[r["attempt"]]["accepted_by"] == "tile"


def test_queued_not_accepted_is_unconfirmed(tmp_path):
    s = wsvc(land=False, cleared=False); d = proj(tmp_path)
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert r["status"] == "UNCONFIRMED" and fires(s) == 1
    p = RunState(d).pending()
    assert len(p) == 1 and p[0]["status"] == "unconfirmed" and "queued_at" not in p[0]


def test_second_fire_of_queued_line_refused(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert e.value.code == "UNCONFIRMED" and not getattr(e.value, "after_click", False)
    assert fires(s) == 1


def test_settle_commits_landed_and_leaves_rendering(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    b = s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)["attempt"]
    s.page.render("m1")
    r = s.settle(d)
    assert r["results"][a]["status"] == "committed" and r["results"][a]["media_ids"] == ["m1"]
    assert b in r["still_rendering"] and r["still_rendering"][b]["age_s"] >= 0
    assert r["still_rendering"][b]["line"] == "L02"
    assert [p["attempt_id"] for p in RunState(d).pending()] == [b]
    s.page.render("m2")
    r2 = s.settle(d)
    assert r2["results"][b]["status"] == "committed" and r2["still_rendering"] == {}
    assert RunState(d).pending() == [] and fires(s) == 2  # settle never fires


def test_settle_uses_the_one_verdict_path(tmp_path):
    s = wsvc(model="abra_edit"); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    s.page.render("m1")
    assert s.settle(d)["results"][a]["status"] == "WRONG_MODEL"


def test_settle_collects_a_late_unconfirmed_queued_fire(tmp_path):
    s = wsvc(land=False, cleared=False); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    s.page.L._by_id["m9"] = Media("m9", "hello", "abra_i2v_8s", "video", 9e18, None, None, None, True)
    assert s.settle(d)["results"][a]["status"] == "committed"


def test_sweep_refuses_while_queued_fires_are_in_flight(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    with pytest.raises(FlowError) as e:
        s.sweep(d)
    assert e.value.code == "IN_FLIGHT"


def test_sweep_after_idle_leaves_a_rendering_queued_fire_pending(tmp_path):
    s = wsvc(); d = proj(tmp_path); s.idle_s = 0
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    r = s.sweep(d)
    assert a in r["still_pending"] and a not in r["results"]
    assert RunState(d).attempts()[a]["status"] == "pending"


def test_stale_listed_but_urlless_queued_fire_settles_no_media_url(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    assert s.settle(d, stale_s=0)["results"][a]["status"] == "NO_MEDIA_URL"


def test_flowerror_after_click_is_marked_and_attempt_kept(tmp_path):
    s = wsvc(land=False, marks_raises=FlowError("BROWSER_DOWN", "tab died")); d = proj(tmp_path)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert e.value.code == "BROWSER_DOWN" and e.value.after_click is True
    assert [p["status"] for p in RunState(d).pending()] == ["pending"]


def test_blocking_fire_unchanged_by_default(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.page.fire = lambda: (s.page.calls.append(("fire",)), s.page._land())  # lands with URL
    r = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8)
    assert r["status"] == "committed" and "queued" not in RunState(d).attempts()[r["attempt"]]


def test_server_exposes_settle_and_queue():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert "flow_settle" in tools and "free" in tools["flow_settle"].description.lower()
    for n in ("flow_fire_clip", "flow_fire_still"):
        assert "queue" in tools[n].inputSchema["properties"]
        assert "accepted" in tools[n].description.lower() and "flow_settle" in tools[n].description


# --- fix round 1 ---------------------------------------------------------------------------------

def test_same_prompt_queued_pending_refuses_second_line(tmp_path):
    s = wsvc(); d = proj(tmp_path)  # the stamp binds L01 and L02 to the same prompt "hello"
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line02", "L02", "hello", d / "a.png", 8, queue=True)
    assert e.value.code == "SAME_PROMPT_PENDING" and not getattr(e.value, "after_click", False)
    assert fires(s) == 1 and len(RunState(d).attempts()) == 1  # nothing written, no click


def test_same_prompt_refusal_also_covers_unconfirmed_queued(tmp_path):
    s = wsvc(land=False, cleared=False); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    with pytest.raises(FlowError) as e:
        s.fire_clip(d, "NJ_line02", "L02", "hello", d / "a.png", 8, queue=True)
    assert e.value.code == "SAME_PROMPT_PENDING"


def test_queue_does_not_store_queued_media_ids(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    assert "queued_media_ids" not in RunState(d).attempts()[a]


def test_stale_timer_counts_from_first_listed_not_fired(tmp_path):
    import json
    s = wsvc(); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    raw = json.loads((d / "_flow_run.json").read_text()); raw["attempts"][a]["fired_at"] -= 3600
    (d / "_flow_run.json").write_text(json.dumps(raw))
    r = s.settle(d, stale_s=60)  # fired an hour ago, but first listed URL-less just now
    assert a in r["still_rendering"] and RunState(d).attempts()[a]["first_listed_at"] > 0
    raw = json.loads((d / "_flow_run.json").read_text()); raw["attempts"][a]["first_listed_at"] -= 120
    (d / "_flow_run.json").write_text(json.dumps(raw))
    assert s.settle(d, stale_s=60)["results"][a]["status"] == "NO_MEDIA_URL"


# --- redesign: no navigation while anything is in flight ------------------------------------------

def navs(s, since=0):
    return [c for c in s.page.calls[since:] if c[0] in ("goto_project", "reset", "open_detail")]


def test_second_queued_fire_is_prepared_in_place_without_navigation(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    k = len(s.page.calls)
    r = s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)
    assert r["status"] == "QUEUED" and navs(s, k) == []
    names = [c[0] for c in s.page.calls[k:]]
    assert names.index("prepare_in_place") < names.index("settings") < names.index("fire")


def test_first_fire_on_a_fresh_page_reloads(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert navs(s)[0][0] == "goto_project" and ("prepare_in_place",) not in s.page.calls


def test_in_place_fire_refuses_when_tab_left_the_bound_project(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.page.project_id = "0000bbbb-0000-0000-0000-000000000009"
    with pytest.raises(FlowError) as e:
        s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)
    assert e.value.code == "UI_CHANGED" and fires(s) == 1 and len(RunState(d).attempts()) == 1


def test_settle_never_navigates_while_in_flight(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    k = len(s.page.calls)
    s.settle(d)
    assert navs(s, k) == []


def test_settle_reloads_at_most_once_per_idle_period(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.idle_s = 0; k = len(s.page.calls)
    s.settle(d)
    assert navs(s, k)[0][0] == "goto_project"
    k = len(s.page.calls)
    s.settle(d)
    assert navs(s, k) == []  # the idle reload is used up until a newer fire


def test_no_idle_reload_while_a_pending_badge_shows(tmp_path):
    s = wsvc(tile=True); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.idle_s = 0; k = len(s.page.calls)
    s.settle(d)
    assert navs(s, k) == []  # a rendering job shows a badge: never navigate


def _age(d, a, secs):
    import json
    raw = json.loads((d / "_flow_run.json").read_text()); raw["attempts"][a]["fired_at"] -= secs
    (d / "_flow_run.json").write_text(json.dumps(raw))


def test_never_listed_queued_attempt_becomes_lost(tmp_path):
    s = wsvc(land=False, tile=True); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    assert a in s.settle(d)["still_rendering"]  # young: still waiting
    _age(d, a, 16 * 60)
    assert a in s.settle(d)["still_rendering"]  # its pending badge still shows: never judged lost
    s.page.pend.clear()  # Flow dropped the tile
    r = s.settle(d)
    assert r["results"][a]["status"] == "LOST" and r["results"][a]["media_ids"] == []
    assert RunState(d).attempts()[a]["status"] == "lost" and RunState(d).pending() == []


def test_lost_needs_a_listing_reload(tmp_path):
    s = wsvc(land=False, tile=True); d = proj(tmp_path); s.idle_s = 10 ** 6
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    _age(d, a, 16 * 60)
    assert a in s.settle(d)["still_rendering"]  # no reload happened: never judged lost


def test_navigating_calls_refuse_in_flight(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    (d / "a.png").write_bytes(b"x")
    for call in (lambda: s.upload(d / "a.png", d), lambda: s.open_project("proj", d)):
        with pytest.raises(FlowError) as e:
            call()
        assert e.value.code == "IN_FLIGHT"


def test_download_without_url_opens_detail_for_the_signed_url_never_a_download_click(tmp_path):
    L = Ledger(extractors=[])

    class P(FakePage):
        def fetch_bytes(self, m):
            if not m.url: raise FlowError("NO_MEDIA_URL", "no signed url")
            return b"x" * 20000

        def open_detail(self, wf):
            self.calls.append(("open_detail", wf))
            for k, m in list(self.L._by_id.items()):
                self.L._by_id[k] = dataclasses.replace(m, url="https://flow-content.google/image/" + k)
    s = Service.for_test(P(L), L, commit_timeout_s=0.2); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    L._by_id["m1"] = dataclasses.replace(L._by_id["m1"], workflow_id="wf1")
    out = s.download(d, r["media_ids"])
    assert out["written"] and ("open_detail", "wf1") in s.page.calls
    assert s.page.calls[-1][0] in ("goto_project", "reset")  # back on the project after the detail view


def test_download_needing_detail_is_deferred_while_in_flight(tmp_path):
    L = Ledger(extractors=[])

    class P(WavePage):
        def fetch_bytes(self, m):
            if not m.url: raise FlowError("NO_MEDIA_URL", "no signed url")
            return b"x" * 20000
    s = Service.for_test(P(L), L, commit_timeout_s=0.2); d = proj(tmp_path)
    a = s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)["attempt"]
    s.page.render("m1"); res = s.settle(d)["results"][a]
    s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)  # another fire in flight
    out = s.download(d, res["media_ids"])
    assert out["deferred"] == ["m1"] and out["written"] == []
    assert not any(c[0] == "open_detail" for c in s.page.calls)


def test_download_with_no_obtainable_url_is_no_media_url(tmp_path):
    L = Ledger(extractors=[])

    class P(FakePage):
        def fetch_bytes(self, m): raise FlowError("NO_MEDIA_URL", "no signed url")
    s = Service.for_test(P(L), L, commit_timeout_s=0.2); d = proj(tmp_path)
    r = s.fire_still(d, "nj", "L01", "p1", d / "ref.png")
    out = s.download(d, r["media_ids"])
    assert out["failed"] == {"m1": "NO_MEDIA_URL"} and out["written"] == []


# --- redesign fix round -------------------------------------------------------------------------

def test_earlier_job_landing_in_the_accept_window_does_not_cancel_the_new_tile(tmp_path):
    s = wsvc(tile=True); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.page.land = False
    s.page.on_click = lambda pg: pg.render("m1")  # L01's badge goes as L02's appears: count flat
    r = s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)
    assert r["status"] == "QUEUED" and r["accepted_by"] == "tile"


def test_identityless_tiles_swap_needs_a_landing_and_an_empty_editor(tmp_path):
    for cleared, landing, want in ((True, True, "QUEUED"), (True, False, "UNCONFIRMED"),
                                   (False, True, "UNCONFIRMED")):
        s = wsvc(tile=True, ident=False, cleared=cleared); d = proj(tmp_path)
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
        s.page.land = False

        def swap(pg, landing=landing):
            if landing:
                pg.render("m1")
            else:
                pg.pend = [x for x in pg.pend if x[1] != "m1"]  # badge gone, nothing landed
        s.page.on_click = swap
        r = s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)
        assert r["status"] == want, (cleared, landing, r)
        if want == "QUEUED":
            assert r["accepted_by"] == "tile_swap"
        RunState(d)._save({"lines": {}, "projectId": "0000aaaa-0000-0000-0000-000000000001"})


def test_badge_disappearing_without_a_new_one_is_unconfirmed(tmp_path):
    s = wsvc(tile=True, ident=False, cleared=True); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.page.land, s.page.tile = False, False
    s.page.on_click = lambda pg: pg.render("m1")
    assert s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)["status"] == "UNCONFIRMED"


def _lost_still(tmp_path):
    s = wsvc(land=False, tile=True); d = proj(tmp_path)
    a = s.fire_still(d, "nj", "L02", "p2", d / "ref.png", queue=True)["attempt"]
    _age(d, a, 16 * 60); s.page.pend.clear(); s.idle_s = 0
    assert s.settle(d)["results"][a]["status"] == "LOST"
    return s, d, a


def test_lost_line_refire_refused_unless_reroll(tmp_path):
    s, d, a = _lost_still(tmp_path)
    n = fires(s)
    with pytest.raises(FlowError) as e:
        s.fire_still(d, "nj", "L02", "p3", d / "ref.png", queue=True)
    assert e.value.code == "LOST_PENDING_RELEASE" and fires(s) == n
    s.fire_still(d, "nj", "L02", "p3", d / "ref.png", queue=True, reroll=True)
    assert fires(s) == n + 1


def test_lost_prompt_still_blocks_same_prompt_even_on_reroll(tmp_path):
    s, d, a = _lost_still(tmp_path)
    for line, kw in (("L03", {}), ("L02", {"reroll": True})):
        with pytest.raises(FlowError) as e:
            s.fire_still(d, "nj", line, "p2", d / "ref.png", queue=True, **kw)
        assert e.value.code == "SAME_PROMPT_PENDING"


def test_late_media_for_a_lost_attempt_is_claimed_by_it(tmp_path):
    s, d, a = _lost_still(tmp_path)
    s.page.L._by_id["late"] = Media("late", "p2", "nano_banana_2", "image", 9e18, None, None, None, True)
    r = s.settle(d)
    assert r["results"][a]["status"] == "committed" and r["results"][a]["media_ids"] == ["late"]
    assert RunState(d).attempts()[a]["status"] == "committed"


def test_tab_wide_in_flight_refuses_another_project(tmp_path):
    import pathlib
    tab = tmp_path / "tab"
    L = Ledger(extractors=[])
    s = Service.for_test(WavePage(L), L, commit_timeout_s=0.2, tab_dir=tab)
    a, b = proj(tmp_path), proj(pathlib.Path(str(tmp_path) + "_B"))
    s.fire_clip(a, "NJ_line01", "L01", "hello", a / "a.png", 8, queue=True)
    (b / "x.png").write_bytes(b"x")
    L2 = Ledger(extractors=[])  # a PEER process (e.g. the MCP server) sharing the tab
    peer = Service.for_test(WavePage(L2), L2, commit_timeout_s=0.2, tab_dir=tab)
    for call in (lambda: peer.upload(b / "x.png", b), lambda: peer.open_project("B", b),
                 lambda: peer.sweep(b)):
        with pytest.raises(FlowError) as e:
            call()
        assert e.value.code == "IN_FLIGHT"
    import json
    assert str(a) in json.loads((tab / "_tab_inflight.json").read_text())["projects"]


def test_tab_marker_prunes_a_project_with_nothing_in_flight(tmp_path):
    tab = tmp_path / "tab"
    L = Ledger(extractors=[])
    s = Service.for_test(WavePage(L), L, commit_timeout_s=0.2, tab_dir=tab)
    d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.page.render("m1"); s.settle(d)
    import json
    assert str(d) not in json.loads((tab / "_tab_inflight.json").read_text())["projects"]
    (d / "x.png").write_bytes(b"x")
    s.upload(d / "x.png", d)  # free again


def test_upload_many_reloads_once_then_uploads_all(tmp_path):
    s = wsvc(); d = proj(tmp_path)
    for n in ("a", "b", "c"):
        (d / (n + ".png")).write_bytes(b"x")
    out = s.upload_many([d / "a.png", d / "b.png", d / "c.png", d / "missing.png"], d)
    names = [c[0] for c in s.page.calls]
    assert names.count("goto_project") == 1 and names.count("upload") == 3
    assert names.index("goto_project") < names.index("upload")
    assert out["errors"] == {str(d / "missing.png"): "REF_NOT_ATTACHED"} and len(out["assets"]) == 3


# --- final fix round: shared idle window, fail-closed marker, not_fired on marker failure ----------

def _pair(tmp_path):
    tab = tmp_path / "tab"
    L1, L2 = Ledger(extractors=[]), Ledger(extractors=[])
    s = Service.for_test(WavePage(L1), L1, commit_timeout_s=0.2, tab_dir=tab)
    peer = Service.for_test(WavePage(L2), L2, commit_timeout_s=0.2, tab_dir=tab)
    return s, peer, tab


def test_idle_reload_is_shared_across_processes(tmp_path):
    import json
    s, peer, tab = _pair(tmp_path); d = proj(tmp_path)
    s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    s.idle_s = peer.idle_s = 0
    s.settle(d)  # spends the one idle reload
    assert json.loads((tab / "_tab_inflight.json").read_text())["idle_nav_for"] is not None
    peer.page.project_id = s.page.project_id
    peer.settle(d)
    assert navs(peer) == []  # the peer sees it used: no second reload in the same idle period


def test_in_flight_refusal_names_the_blocking_project(tmp_path):
    import pathlib
    s, peer, tab = _pair(tmp_path)
    a, b = proj(tmp_path), proj(pathlib.Path(str(tmp_path) + "_B"))
    s.fire_clip(a, "NJ_line01", "L01", "hello", a / "a.png", 8, queue=True)
    (b / "x.png").write_bytes(b"x")
    with pytest.raises(FlowError) as e:
        peer.upload(b / "x.png", b)
    assert e.value.code == "IN_FLIGHT" and str(a) in str(e.value)


def test_unreadable_marker_fails_closed(tmp_path):
    s, peer, tab = _pair(tmp_path); d = proj(tmp_path)
    (d / "x.png").write_bytes(b"x")
    tab.mkdir(parents=True, exist_ok=True)
    for bad, word in (("{not json", "unreadable"), ('{"projects": "x"}', "unexpected content")):
        (tab / "_tab_inflight.json").write_text(bad, encoding="utf-8")
        with pytest.raises(FlowError) as e:
            s.upload(d / "x.png", d)
        assert e.value.code == "IN_FLIGHT" and word in str(e.value)
    (tab / "_tab_inflight.json").unlink()
    assert s.upload(d / "x.png", d)["asset"] == "x"  # only a MISSING marker means free


def test_marker_write_failure_marks_the_attempt_not_fired(tmp_path, monkeypatch):
    s = wsvc(); d = proj(tmp_path)
    def boom(_d): raise FlowError("IN_FLIGHT", "cannot write tab marker")
    monkeypatch.setattr(s, "_flights_add", boom)
    with pytest.raises(FlowError):
        s.fire_clip(d, "NJ_line01", "L01", "hello", d / "a.png", 8, queue=True)
    assert fires(s) == 0 and RunState(d).pending() == []
    assert [a["status"] for a in RunState(d).attempts().values()] == ["not_fired"]


def test_marker_oserror_on_write_becomes_in_flight(tmp_path, monkeypatch):
    import flow_mcp.service as sm
    s, _, tab = _pair(tmp_path)
    def nope(a, b): raise OSError("disk full")
    monkeypatch.setattr(sm.os, "replace", nope)
    with pytest.raises(FlowError) as e:
        s._marker_save({"projects": ["x"], "idle_nav_for": None})
    assert e.value.code == "IN_FLIGHT" and "disk full" in str(e.value)
