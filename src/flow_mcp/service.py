"""Orchestration. One public method = one tool. Paid fires: pending BEFORE fire, commit only on
ledger evidence of NEW media, never an automatic second fire. fire_* and sweep settle results
through ONE helper (_settle) so a swept result gets exactly the checks a live one gets.

Order inside every fire: validate args -> TabLock (one driver of the SHARED Flow tab, across the MCP
server and flow_fire) -> RunLock (one writer per project, across processes) ->
(clips only) lint stamp -> _gate / fire_ready -> begin_fire -> fire -> settle -> release. Nothing is
written to pending before the lock and the stamp pass. sweep holds the same lock.

WAVES (queue=True): the same steps up to and including the click, then pump only until Flow has
ACCEPTED the fire - Flow-side evidence only: a new media record for this prompt in the ledger, or the
project grid growing (a new tile / a new progress %). The prompt box empties ~50 ms after ANY click
and is never evidence. Bounded ACCEPT_S. Accepted -> the attempt stays pending with queued_at and the
call returns QUEUED; settle() later commits it through _settle once its media carry a URL. Not
accepted -> unconfirmed, never re-fired. A FlowError raised after the click is tagged
e.after_click = True (the click happened: callers must not treat it as free).

NO NAVIGATION WHILE ANYTHING IS IN FLIGHT - TAB-WIDE (live 2026-09-30: a goto 5-10 s after a click
lost 3 of 4 stills). Every project with a queued attempt pending is listed in <tab_dir>/_tab_inflight.json
(beside _tab.lock), so a peer working on ANOTHER project sees it too. While any is listed, the tab may
navigate only in an IDLE window: the newest queued fire is >= IDLE_S old AND the live grid shows no
pending-tile badge AND no idle navigation has happened yet for that newest fire (at most one reload per
idle period - a rendering clip may be past the badge-less moment). Otherwise: the next fire is prepared
in place, settle() pumps on the current page, a download needing the detail view is deferred, and
upload / sweep / open_project refuse IN_FLIGHT. A never-listed queued attempt older than LOST_S is marked
lost only after that idle reload AND with no pending badge on screen; a lost line refuses a new fire
(LOST_PENDING_RELEASE) unless reroll=True, its prompt keeps blocking SAME_PROMPT_PENDING, and media that
land late for it are claimed by it (settle judges lost attempts first)."""
import contextlib
import json
import os
import time
from pathlib import Path

from .config import Config, load_config
from . import stamp
from .guards import MAX_REFS, FlowError, check_path, check_clip_model, check_name
from .netwatch import Ledger, norm
from .runlock import RunLock, TabLock
from .runstate import RunState

CLIP_CREDITS = {"omni_flash": 12}  # per output; veo_lite refused until its label + cost are verified
MAX_CLIP_COUNT = 2
PORTRAIT_STILL = (768, 1376)  # [6][2] of a 9:16 Nano Banana 2 still (NOTES.md, aspect row)
PUMP_MS = 100
MODEL_WAIT_S = 10  # extra pumping for a clip's model field after its media landed
ACCEPT_S = 20  # queued fire: how long to wait for Flow to accept the click
IDLE_S = 90  # no navigation until the newest queued fire is this old (then a listing reload is safe)
LOST_S = 15 * 60  # a queued attempt never listed by an idle reload after this long -> lost
STALE_S = 15 * 60  # queued media LISTED this long without a URL (first_listed_at) -> _settle: NO_MEDIA_URL


def as_png_if_named(f: Path, data: bytes) -> bytes:
    """A .png target gets a real PNG: Flow serves stills as JPEG (sometimes WEBP). Same pixels and
    dims, lossless container. Anything else (clips, non-image bytes) passes through untouched."""
    if f.suffix.lower() != ".png":
        return data
    if not (data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        return data
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.save(out, format="PNG")
    return out.getvalue()


def rs_versions(rs: RunState, line: str) -> list:
    """The committed clip versions of a line (read-only)."""
    return ((rs._load().get("lines") or {}).get(line) or {}).get("clip", {}).get("versions") or []


class Service:
    def __init__(self, page, ledger: Ledger, commit_timeout_s: float, tab_dir: Path | None = None):
        self.page, self.L, self.timeout, self.tab_dir = page, ledger, commit_timeout_s, tab_dir
        self.wait_s = min(commit_timeout_s, MODEL_WAIT_S)  # model/url settle + post-reload pump
        self.accept_s = min(commit_timeout_s, ACCEPT_S)
        self.idle_s, self.lost_s = IDLE_S, LOST_S
        # the tab marker when there is no tab_dir (tests); with one it lives in <tab_dir>/_tab_inflight.json
        self._mem_marker = {"projects": [], "idle_nav_for": None}

    @classmethod
    def for_test(cls, page, ledger, commit_timeout_s, tab_dir=None):
        return cls(page, ledger, commit_timeout_s, tab_dir)

    @classmethod
    def build(cls, cfg: Config | None = None):
        from .browser import Browser
        from .flowpage import FlowPage
        cfg = cfg or load_config()
        L = Ledger(); b = Browser(cfg); b.on_response(lambda url, body: L.feed(body))
        return cls(FlowPage(b), L, cfg.commit_timeout_s, cfg.profile_dir)

    def _tab(self):
        """The browser-wide tab lock, ALWAYS taken before any RunLock (order: tab -> run)."""
        return TabLock(self.tab_dir) if self.tab_dir is not None else contextlib.nullcontext()

    def _gate(self) -> None:
        st = self.page.state()
        if st.get("captcha"): raise FlowError("CAPTCHA", "reCAPTCHA page - the user ticks it in the Flow window")
        if not st.get("signed_in"): raise FlowError("LOGIN_REQUIRED", "sign into Google in the Flow window")
        # state() sets out_of_credits only when the banner is up AND the fire button is gone
        if st.get("out_of_credits"): raise FlowError("OUT_OF_CREDITS", "out of Flow credits - never buy")

    def _pump_until(self, done, budget_s: float):
        # Sync Playwright only dispatches response events while a Playwright call is running, so
        # every wait goes through the page (wait_for_timeout). time.sleep would starve the ledger.
        end = time.time() + budget_s
        while not done() and time.time() < end:
            self.page.wait(PUMP_MS)

    def _new_hits(self, prompt: str, since: float, exclude: set[str]):
        return [m for m in self.L.find(prompt, since) if m.media_id not in exclude]

    def _fire(self, rs: RunState, lock: RunLock, line: str, stage: str, prompt: str, attach,
              settings: dict, expected: int, meta: dict, queue: bool = False, reroll: bool = False):
        """attach: zero-arg callable that puts the ref(s) in the prompt bar (raises FlowError).
        queue: return once the fire is ACCEPTED (QUEUED), not landed - see the module docstring."""
        blocked = [p for p in rs.pending() if p["line"] == line and p["stage"] == stage]
        if blocked:
            raise FlowError("UNCONFIRMED", f"{line} has an unconfirmed {stage} fire - run flow_sweep first")
        lost = [a for a in rs.attempts().values()
                if a.get("line") == line and a.get("stage") == stage and a.get("status") == "lost"]
        if lost and not reroll:
            raise FlowError("LOST_PENDING_RELEASE", f"{line}: a queued {stage} fire was never listed by Flow "
                            "(lost, spend unknown) - the user releases it, or reroll=True on his go")
        n = norm(prompt)  # queued attempts match media by prompt: another in flight (or lost) would cross-match
        same = [p["line"] for p in self._queued(rs, with_lost=True) if p.get("prompt_norm") == n]
        if same:
            raise FlowError("SAME_PROMPT_PENDING", f"{line}: a queued fire with the same prompt ({', '.join(same)}) "
                            "is still pending or lost - flow_settle / release it first")
        self._gate()
        self._position(rs)  # reload only when navigation is safe, else prepare in place
        self.page.set_settings(**settings)
        attach()
        self.page.type_prompt(prompt)
        self.page.fire_ready()  # button present + enabled BEFORE any pending entry is written
        exclude = {m.media_id for m in self.L.all()} | rs.claimed_media()
        since = time.time() - 1
        ctx = None
        if queue:  # acceptance baseline: pending tiles, landed media, prompts of the other jobs in flight
            ctx = {"tiles": list(self.page.pending_tiles()),
                   "landed": {m.media_id for m in self.L.all() if m.has_url},
                   "others": {p["prompt_norm"] for p in self._queued(rs)}}
            meta = dict(meta, queued=True)  # settle() collects it, pending or unconfirmed
        # the snapshot is persisted so a sweep after an MCP restart can still exclude old media
        attempt = rs.begin_fire(line, stage, prompt, expected=expected, exclude=sorted(exclude), **meta)
        try:
            if queue:
                self._flights_add(rs.dir)  # tab-wide: peers must not navigate while this may render
            lock.assert_held()  # another holder took the lock: never click, no stuck pending
            self.page.fire()
        except FlowError:
            rs.mark(attempt, "not_fired")  # FlowPage.fire only raises FlowError before its click
            raise
        try:  # the click happened: from here the attempt is never marked not_fired
            if queue:
                return attempt, self._accept(rs, attempt, prompt, since, exclude, ctx)
            self._pump_until(lambda: len(self._new_hits(prompt, since, exclude)) >= expected, self.timeout)
            hits = self._new_hits(prompt, since, exclude)
            if not hits:
                rs.mark(attempt, "unconfirmed")
                return attempt, {"status": "UNCONFIRMED", "attempt": attempt, "media_ids": []}
            return attempt, self._settle(rs, attempt, hits[:expected], self.wait_s)
        except FlowError as e:
            e.after_click = True  # spent (or may have been): flow_fire keeps it charged
            raise

    def _evidence(self, prompt: str, since: float, exclude: set, ctx: dict) -> str | None:
        """Flow-side proof the click became a job. Never the prompt box on its own.
        - "ledger": a new media record for this prompt.
        - "tile": a pending tile (flow-pending-tile with a loading-percentage badge) whose identity was
          not there before the click - a job landing in the window cannot cancel it out.
        - Tiles without a stable identity (live: unknown yet): "tile_count" = more pending tiles than
          before; or "tile_swap" = same count AND a job of ANOTHER in-flight prompt landed in the window
          AND the editor emptied (one left, ours took its place). Fewer tiles -> no evidence."""
        if self._new_hits(prompt, since, exclude):
            return "ledger"
        before, now = ctx["tiles"], list(self.page.pending_tiles())
        if all(t is not None for t in before + now):
            return "tile" if set(now) - set(before) else None
        if len(now) > len(before):
            return "tile_count"
        landed_other = any(m.has_url and m.media_id not in ctx["landed"] and m.prompt
                           and norm(m.prompt) in ctx["others"] for m in self.L.all())
        if now and len(now) == len(before) and landed_other and self.page.prompt_cleared():
            return "tile_swap"
        return None

    def _accept(self, rs: RunState, attempt: str, prompt: str, since: float, exclude: set, ctx: dict) -> dict:
        """No evidence within accept_s -> UNCONFIRMED (charged, pending, never re-fired)."""
        seen = {"by": None}

        def accepted():
            seen["by"] = self._evidence(prompt, since, exclude, ctx)
            return seen["by"] is not None
        self._pump_until(accepted, self.accept_s)
        ids = [m.media_id for m in self._new_hits(prompt, since, exclude)]
        by = "ledger" if ids else seen["by"]
        if by is None:
            rs.mark(attempt, "unconfirmed")
            return {"status": "UNCONFIRMED", "attempt": attempt, "media_ids": []}
        rs.queue(attempt, by)
        return {"status": "QUEUED", "attempt": attempt, "media_ids": ids, "accepted_by": by}

    # --- navigation guard (tab-wide) -----------------------------------------------------------------
    @staticmethod
    def _queued(rs: RunState, with_lost: bool = False) -> list[dict]:
        ok = ("pending", "unconfirmed", "lost") if with_lost else ("pending", "unconfirmed")
        return sorted((dict(v, attempt_id=k) for k, v in rs.attempts().items()
                       if v.get("queued") and v.get("status") in ok), key=lambda p: p.get("fired_at", 0))

    def _marker(self) -> Path | None:
        return Path(self.tab_dir) / "_tab_inflight.json" if self.tab_dir is not None else None

    def _marker_load(self) -> dict:
        """The tab marker {"projects": [dirs with queued attempts], "idle_nav_for": newest-fire ts whose
        one idle navigation is used}. Shared by every process on the tab (read/written under the tab
        lock). Fails CLOSED: only a missing file means an empty marker; anything unreadable raises
        IN_FLIGHT naming the cause - never read as "nothing in flight"."""
        f = self._marker()
        if f is None:
            return {"projects": list(self._mem_marker["projects"]), "idle_nav_for": self._mem_marker["idle_nav_for"]}
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"projects": [], "idle_nav_for": None}
        except (OSError, ValueError) as e:
            raise FlowError("IN_FLIGHT", f"tab marker {f} unreadable ({type(e).__name__}: {e}) - treating the "
                            "tab as busy; the user checks it (delete it only when no Flow job is rendering)") from None
        ok = (isinstance(raw, dict) and isinstance(raw.get("projects", []), list)
              and all(isinstance(x, str) for x in raw.get("projects", []))
              and (raw.get("idle_nav_for") is None or isinstance(raw.get("idle_nav_for"), (int, float))))
        if not ok:
            raise FlowError("IN_FLIGHT", f"tab marker {f} has unexpected content - treating the tab as busy; "
                            "the user checks it (delete it only when no Flow job is rendering)")
        return {"projects": list(raw.get("projects", [])), "idle_nav_for": raw.get("idle_nav_for")}

    def _marker_save(self, m: dict) -> None:
        f = self._marker()
        m = {"projects": sorted(set(m["projects"])), "idle_nav_for": m.get("idle_nav_for")}
        if f is None:
            self._mem_marker = m
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(m, indent=2), encoding="utf-8", newline="\n")
            os.replace(tmp, f)
        except OSError as e:
            raise FlowError("IN_FLIGHT", f"cannot write tab marker {f}: {e}") from None

    def _flights_add(self, d: Path) -> None:
        m = self._marker_load()
        m["projects"] = m["projects"] + [str(d)]
        self._marker_save(m)

    def _flights(self) -> dict[str, float]:
        """{project dir: newest queued fire still pending} for every project on the tab; entries with
        nothing queued pending any more are pruned (self-healing after a crash)."""
        m = self._marker_load()
        out = {}
        for d in m["projects"]:
            q = self._queued(RunState(Path(d)))
            if q:
                out[d] = max(p.get("fired_at", 0) for p in q)
        if set(out) != set(m["projects"]):
            m["projects"] = list(out)
            self._marker_save(m)
        return out

    def _idle_used(self) -> float | None:
        return self._marker_load().get("idle_nav_for")

    def _nav_state(self) -> tuple[str, float | None, list[str]]:
        """("free", None, []): nothing queued in flight on the tab. ("idle", newest, dirs): one navigation
        allowed (newest fire >= idle_s old, no pending badge on screen, the idle navigation for that
        newest fire not yet used by ANY process). ("busy", newest, dirs): no navigation."""
        fl = self._flights()
        if not fl:
            return "free", None, []
        newest = max(fl.values())
        if (time.time() - newest < self.idle_s or self._idle_used() == newest
                or self.page.pending_tiles()):
            return "busy", newest, sorted(fl)
        return "idle", newest, sorted(fl)

    def _may_navigate(self) -> tuple[bool, list[str]]:
        """(allowed, blocking project dirs). An idle-window navigation is used up by this call, recorded
        in the shared marker so no other process on the tab takes a second one."""
        st, newest, dirs = self._nav_state()
        if st == "idle":
            m = self._marker_load()
            m["idle_nav_for"] = newest
            self._marker_save(m)
        return st != "busy", dirs

    def _assert_on_bound(self, rs: RunState) -> None:
        want = rs._load().get("projectId")
        got = self.page.current_project_id()
        if not want or got != want:
            raise FlowError("UI_CHANGED", f"queued fires in flight and the tab is on {got!r}, not the bound "
                            f"project {want!r} - refusing to navigate (flow_settle once idle)")

    def _refuse_in_flight(self, what: str) -> None:
        ok, dirs = self._may_navigate()
        if not ok:
            raise FlowError("IN_FLIGHT", f"{what} would navigate the shared tab while queued fires are "
                            f"rendering in {', '.join(dirs)} - flow_settle there first")

    def _position(self, rs: RunState) -> None:
        """Before typing a fire: navigation allowed -> reload the bound project (clean prompt bar).
        Otherwise stay: assert the bound project, empty editor, clear leftover chips."""
        if self._may_navigate()[0]:
            self._reload(rs)
            return
        self._assert_on_bound(rs)
        self.page.prepare_in_place()

    # --- the one settle path (fire_* and sweep) ----------------------------------------------------
    def _current(self, hits):
        byid = {m.media_id: m for m in self.L.all()}
        return [byid.get(h.media_id, h) for h in hits]

    @staticmethod
    def _aspect(hits) -> dict:
        dims = [m.dims for m in hits]
        out = {"aspect_checked": all(x is not None for x in dims)}
        bad = next((x for x in dims if x is not None and tuple(x) != PORTRAIT_STILL), None)
        if bad:
            out.update(status="WRONG_ASPECT", dims=list(bad), expected_dims=list(PORTRAIT_STILL))
        return out

    def _verdict(self, hits, stage: str, budget_s: float):
        """(status, hits): committed | NO_MEDIA_URL | MODEL_UNKNOWN | WRONG_MODEL, after pumping for
        the media URL (every stage) and the model field (clips). A record that never carries a URL is
        a failed/refused generation: never a normal take, its spend unknown."""
        def ready(m): return m.has_url and (stage != "clip" or bool(m.model))
        self._pump_until(lambda: all(ready(m) for m in self._current(hits)), budget_s)
        hits = self._current(hits)
        if not all(m.has_url for m in hits):
            return "NO_MEDIA_URL", hits
        if stage != "clip":
            return "committed", hits
        if not all(m.model for m in hits):
            return "MODEL_UNKNOWN", hits
        try:
            for m in hits: check_clip_model(m.model)
        except FlowError:
            return "WRONG_MODEL", hits
        return "committed", hits

    @staticmethod
    def _record(rs: RunState, at: dict, ids: list[str]) -> dict:
        line = at["line"]
        if at["stage"] == "still":
            for mid in ids:
                rs.add_still(line, mid, rs.next_still_name(line, at.get("slug") or "sweep"))
            return {}
        v0 = rs.next_clip_version(line)
        for i, mid in enumerate(ids):
            rs.add_clip(line, v0 + i, mid, f"{at.get('clip_name') or line}_v{v0 + i:02d}.mp4")
        return {"version": v0}

    def _settle(self, rs: RunState, attempt: str, hits, model_budget_s: float) -> dict:
        at = rs.attempts()[attempt]
        expected = at.get("expected", len(hits))
        ids = [m.media_id for m in hits]
        status, hits = self._verdict(hits, at["stage"], model_budget_s)
        if status != "committed":
            rs.mark(attempt, status.lower(), ids)  # claimed, never a normal vNN take
            out = {"status": status, "attempt": attempt, "media_ids": ids}
            if status == "WRONG_MODEL": out["model"] = hits[0].model
            if status == "MODEL_UNKNOWN": out["detail"] = "clip landed without a model string - check it in Flow"
            if status == "NO_MEDIA_URL":
                out["credits"] = "unknown"
                out["detail"] = "Flow reported the media but never a media URL (failed/refused?) - check it in Flow"
            return out
        extra = self._record(rs, at, ids)
        partial = len(ids) < expected
        rs.commit(attempt, ids, hits[0].model, partial=partial)
        out = {"status": "committed", "attempt": attempt, "media_ids": ids, **extra,
               "partial": partial, "expected": expected, "got": len(ids)}
        if at["stage"] == "still":
            out.update(self._aspect(hits))
        return out

    # --- tools ----------------------------------------------------------------------------------
    def status(self) -> dict:
        return self.page.state()

    def open_project(self, name: str, project_dir) -> dict:
        d = check_path(project_dir)
        with self._tab(), RunLock(d):
            self._refuse_in_flight("flow_open_project")
            pid = self.page.open_project(name)
            RunState(d).set_project(pid, name)
        return {"projectId": pid, "name": name}

    def upload(self, path, project_dir=None) -> dict:
        """project_dir: upload into that dir's bound Flow project (the shared tab is moved there)."""
        p = check_path(path)
        if not p.exists(): raise FlowError("REF_NOT_ATTACHED", f"{p} not on disk")
        with self._tab():
            if project_dir is not None:
                rs = RunState(check_path(project_dir))
                self._refuse_in_flight("flow_upload")  # a new upload reloads the page too
                self._reload(rs)
            return {"asset": self.page.upload(p)}

    def upload_many(self, paths, project_dir) -> dict:
        """ONE reload of the bound project, then every upload (flow_fire, before a wave's first fire).
        Per-path refusals come back in errors {path: code}; BROWSER_DOWN / CAPTCHA / LOGIN_REQUIRED
        raise (nothing else can work)."""
        ps = [check_path(x) for x in paths]
        with self._tab():
            rs = RunState(check_path(project_dir))
            self._refuse_in_flight("flow_upload")
            self._reload(rs)
            assets, errors = {}, {}
            for p in ps:
                if not p.exists():
                    errors[str(p)] = "REF_NOT_ATTACHED"; continue
                try:
                    assets[str(p)] = self.page.upload(p)
                except FlowError as e:
                    if e.code in ("BROWSER_DOWN", "CAPTCHA", "LOGIN_REQUIRED"):
                        raise
                    errors[str(p)] = e.code
            return {"assets": assets, "errors": errors}

    def fire_still(self, project_dir, slug, line, prompt, ref=None, outputs=1, refs=None,
                   queue: bool = False, reroll: bool = False) -> dict:
        """Exactly one of ref (one path) / refs (1-3 paths). Stills never check the lint stamp.
        queue=True: return QUEUED once accepted; settle() collects it."""
        check_name(slug, "slug"); check_name(line, "line")
        if ref is not None and refs is not None:
            raise FlowError("SETTINGS_MISMATCH", "pass ref or refs, not both")
        if ref is None and refs is None:
            raise FlowError("SETTINGS_MISMATCH", "pass ref (one path) or refs (1-3 paths)")
        if refs is not None:
            refs = list(refs)
            if not 1 <= len(refs) <= MAX_REFS:
                raise FlowError("REF_NOT_ATTACHED", f"refs must hold 1-{MAX_REFS} paths, got {len(refs)}")
            stems = [check_path(r).stem for r in refs]
            if len(set(stems)) != len(stems):
                raise FlowError("REF_NOT_ATTACHED", f"refs repeat a name {stems!r} - each ref once")
            attach = lambda: self.page.attach_many(stems)
        else:
            stem = check_path(ref).stem
            attach = lambda: self.page.attach(stem)
        d = check_path(project_dir)
        with self._tab(), RunLock(d) as lk:
            rs = RunState(d)
            _, out = self._fire(rs, lk, line, "still", prompt, attach,
                                dict(mode="image", model="nano_banana_2", count=outputs,
                                     duration=None, video_mode=None, aspect="9:16"),
                                outputs, {"slug": slug}, queue=queue, reroll=reroll)
        if out["status"] in ("committed", "WRONG_ASPECT", "QUEUED"):
            out["credits"] = 0
        return out

    def fire_clip(self, project_dir, clip_name, line, prompt, start_frame, duration, count=1,
                  mode="frames", model="omni_flash", reroll: bool = False, queue: bool = False) -> dict:
        """reroll=False refuses ALREADY_COMMITTED when the line already holds a committed clip
        version (checked under the RunLock, before anything is written): a re-roll costs credits.
        queue=True: return QUEUED (charged) once Flow accepted the click; settle() collects it."""
        if model == "veo_lite":
            raise FlowError("SETTINGS_MISMATCH", "Veo Lite label not verified yet - only omni_flash fires")
        if model not in CLIP_CREDITS:
            raise FlowError("SETTINGS_MISMATCH", f"unknown clip model {model!r} (known: {sorted(CLIP_CREDITS)})")
        if not 1 <= int(count) <= MAX_CLIP_COUNT:
            raise FlowError("SETTINGS_MISMATCH", f"clip count {count} outside 1..{MAX_CLIP_COUNT}")
        check_name(clip_name, "clip_name"); check_name(line, "line")
        d, sf = check_path(project_dir), check_path(start_frame)
        with self._tab(), RunLock(d) as lk:
            if not reroll and rs_versions(RunState(d), line):
                raise FlowError("ALREADY_COMMITTED", f"{line} already has a committed clip - pass reroll=True "
                                "only on the user's go (a re-roll costs credits)")
            stamp.check(d, stamp.manifest_for(d), line, prompt)  # paid: linted manifest + prompt or no fire
            rs = RunState(d)
            _, out = self._fire(rs, lk, line, "clip", prompt, lambda: self.page.attach(sf.stem),
                                dict(mode="video", model=model, count=count, duration=duration,
                                     video_mode=mode, aspect="9:16"),
                                count, {"clip_name": clip_name}, queue=queue, reroll=reroll)
        if out["status"] in ("committed", "QUEUED"):
            out["credits"] = CLIP_CREDITS[model] * count
        return out

    def _reload(self, rs: RunState) -> None:
        """Navigate the SHARED tab to this run's bound project (under the tab lock) so Flow re-sends
        its media - the ledger only fills while a Playwright call runs, and it is empty after an
        MCP restart. A peer may have left the tab on another project: we go to ours, never fire
        into whatever is open. No bound project -> UI_CHANGED."""
        want = rs._load().get("projectId")
        if not want:
            raise FlowError("UI_CHANGED", "no Flow project bound to this project dir - run flow_open_project first")
        self.page.goto_project(want)

    def _sweep_hits(self, rs: RunState, p: dict):
        exclude = set(p.get("exclude", [])) | rs.claimed_media()
        return self._new_hits(p["prompt"], p["fired_at"] - 1, exclude)

    def sweep(self, project_dir) -> dict:
        d = check_path(project_dir)
        with self._tab(), RunLock(d) as lk:
            return self._sweep(d, lk)

    def _sweep(self, d: Path, lock: RunLock) -> dict:
        rs = RunState(d)
        self._refuse_in_flight("flow_sweep")
        self._reload(rs)
        pend = rs.pending()  # pump: with nothing pending the full budget runs, filling the ledger
        self._pump_until(lambda: bool(pend) and all(
            len(self._sweep_hits(rs, p)) >= p.get("expected", 1) for p in pend), self.wait_s)
        lock.assert_held()  # before the first run-state write
        resolved, still, results, topped = [], [], {}, {}
        now = time.time()
        for p in rs.pending():
            hits = self._sweep_hits(rs, p)
            if not hits or (p.get("queued") and not self._ripe(rs, p, hits, now, STALE_S)):
                still.append(p["attempt_id"]); continue  # a queued fire still rendering: settle() later
            res = self._settle(rs, p["attempt_id"], hits[:p.get("expected", len(hits))], self.wait_s)
            results[p["attempt_id"]] = res; resolved.append(p["attempt_id"])
        for aid, at in rs.attempts().items():  # top up partial commits with late outputs
            if at.get("status") != "committed" or not at.get("partial"):
                continue
            need = at.get("expected", 0) - len(at.get("media_ids", []))
            exclude = set(at.get("exclude", [])) | rs.claimed_media()
            hits = self._new_hits(at["prompt"], at["fired_at"] - 1, exclude)[:need]
            if hits:
                verdict, hits = self._verdict(hits, at["stage"], self.wait_s)
                if verdict != "committed":
                    results[aid] = {"status": verdict, "attempt": aid, "late_media_ids": [m.media_id for m in hits]}
                    continue  # left unclaimed and unrecorded; reported for a human look
            if not hits:
                continue
            ids = [m.media_id for m in hits]
            self._record(rs, at, ids); rs.extend(aid, ids); topped[aid] = ids
            if at["stage"] == "still":
                a = self._aspect(hits)
                if "status" in a: results[aid] = {"attempt": aid, "media_ids": ids, **a}
        return {"resolved": resolved, "still_pending": still, "results": results, "topped_up": topped,
                "media": [m.__dict__ for m in self.L.all()]}

    @staticmethod
    def _ripe(rs, p: dict, hits, now: float, stale_s: float) -> bool:
        """A queued attempt is settled once its expected media all carry a URL, or once its media have
        been LISTED URL-less for stale_s (then _settle judges it: NO_MEDIA_URL). The stale clock starts
        at first_listed_at, stamped the first time its media are seen without a URL (rs=None: read
        only, never stamps - used inside the pump)."""
        want = hits[:p.get("expected", 1)]
        if len(want) >= p.get("expected", 1) and all(m.has_url for m in want):
            return True
        if not want:
            return False
        if not p.get("first_listed_at"):
            if rs is None:
                return False
            p["first_listed_at"] = rs.note_listed(p["attempt_id"], now)
        return now - p["first_listed_at"] >= stale_s

    def settle(self, project_dir, stale_s: float = STALE_S) -> dict:
        """Collect QUEUED fires. While fires render it never navigates: it pumps on the current page.
        In an idle window (see the module docstring) it reloads the bound project ONCE for a fresh
        listing. Attempts are judged oldest first, lost ones included, so media that land late for a
        lost attempt are claimed by it. Ripe -> _settle (same verdicts as a live fire). Never listed
        after lost_s, once the idle reload happened AND no pending badge shows -> lost (LOST, spend
        unknown). The rest come back in still_rendering with their age. Never fires anything."""
        d = check_path(project_dir)
        with self._tab(), RunLock(d) as lk:
            rs = RunState(d)
            if self._may_navigate()[0]:
                self._reload(rs)
            else:
                self._assert_on_bound(rs)
            q = [p for p in self._queued(rs, with_lost=True) if p["status"] != "lost"]
            self._pump_until(lambda: bool(q) and all(
                self._ripe(None, p, self._sweep_hits(rs, p), time.time(), stale_s) for p in q), self.wait_s)
            lk.assert_held()  # before the first run-state write
            newest = max((p.get("fired_at", 0) for p in q), default=None)
            listed = newest is not None and self._idle_used() == newest and not self.page.pending_tiles()
            results, rendering, now = {}, {}, time.time()
            for p in self._queued(rs, with_lost=True):
                aid, exp = p["attempt_id"], p.get("expected", 1)
                hits = self._sweep_hits(rs, p)
                if self._ripe(rs, p, hits, now, stale_s):
                    results[aid] = self._settle(rs, aid, hits[:exp], self.wait_s)
                elif p["status"] == "lost":
                    continue  # stays lost until its media land (claimed above) or the user releases it
                elif not hits and listed and now - p.get("fired_at", now) >= self.lost_s:
                    rs.mark(aid, "lost", [])
                    results[aid] = {"status": "LOST", "attempt": aid, "media_ids": [], "line": p["line"],
                                    "stage": p["stage"], "credits": "unknown",
                                    "detail": "never listed by Flow after an idle reload with no pending "
                                              "tile - spend unknown (stills cost 0); check the Flow grid"}
                else:
                    rendering[aid] = {"line": p["line"], "stage": p["stage"], "status": p["status"],
                                      "age_s": round(now - p.get("fired_at", now)),
                                      "seen": len(hits[:exp]), "expected": exp}
            self._flights()  # prune this project from the tab marker once nothing is queued pending
            return {"results": results, "still_rendering": rendering}

    def download(self, project_dir, media_ids) -> dict:
        d = check_path(project_dir)
        with self._tab(), RunLock(d):  # it writes run state and may reload the page: never mid-fire of another run
            return self._download(d, media_ids)

    def _download(self, d: Path, media_ids) -> dict:
        rs = RunState(d); raw = rs._load()
        where = {}
        for L in raw.get("lines", {}).values():
            for x in L["still"]["candidates"]: where[x["mediaId"]] = d / "Elements" / "Stills" / x["file"]
            for x in L["clip"]["versions"]: where[x["mediaId"]] = d / "Elements" / "Clips" / x["file"]
        for mid in media_ids:
            if mid not in where:
                raise ValueError(f"unknown mediaId {mid} - run flow_sweep")
        safe = self._nav_state()[0] == "free"  # downloads never spend the one idle navigation
        known = lambda: {m.media_id for m in self.L.all()}
        deferred, failed = [], {}
        if not set(media_ids) <= known():
            if safe:
                self._reload(rs)  # ledger empty after an MCP restart: one reload + pump
                self._pump_until(lambda: set(media_ids) <= known(), self.wait_s)
        byid = {m.media_id: m for m in self.L.all()}
        for mid in media_ids:
            if mid not in byid:
                if not safe:
                    deferred.append(mid)  # a reload would kill fires in flight: later
                    continue
                raise ValueError(f"unknown mediaId {mid} - Flow did not report it after a reload")
        targets = {}
        for mid in [m for m in media_ids if m not in deferred]:  # every target checked BEFORE any write
            f = check_path(where[mid])
            try:
                f.relative_to(d)
            except ValueError:
                raise FlowError("PATH_REFUSED", f"{f} escapes the project folder {d}") from None
            targets[mid] = f
        written, skipped = [], []
        for mid, f in targets.items():
            if rs.should_skip(f): skipped.append(str(f)); continue
            data = self._bytes(rs, mid, safe)
            if data is None:
                (deferred.append(mid) if not safe else failed.__setitem__(mid, "NO_MEDIA_URL"))
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(as_png_if_named(f, data))
            rs.set_downloaded(mid); written.append(str(f))
        return {"written": written, "skipped": skipped, "deferred": deferred, "failed": failed}

    def _bytes(self, rs: RunState, mid: str, safe: bool) -> bytes | None:
        """Bytes ONLY through page.request on a signed URL - never the Download button (it opens
        Edge's downloads hub, whose target never answers CDP and blocks every later connect). No usable
        URL -> when navigation is safe, open the item's detail view once (its as29s response carries
        the signed URL), go back to the project, retry. None = no URL obtainable (or not safe now)."""
        m = {x.media_id: x for x in self.L.all()}[mid]
        try:
            return self.page.fetch_bytes(m)
        except FlowError as e:
            if e.code != "NO_MEDIA_URL" or not safe or not m.workflow_id:
                return None
        try:
            self.page.open_detail(m.workflow_id)
            self._pump_until(lambda: bool({x.media_id: x for x in self.L.all()}[mid].url), self.wait_s)
        finally:
            self._reload(rs)  # close the detail view: back on the bound project
        try:
            return self.page.fetch_bytes({x.media_id: x for x in self.L.all()}[mid])
        except FlowError as e:
            if e.code == "NO_MEDIA_URL":
                return None
            raise

    def screenshot(self) -> bytes:
        return self.page.screenshot()
