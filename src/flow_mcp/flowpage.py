"""The Flow DOM, and only the DOM. Every selector lives in the constants block below; a missing
one raises FlowError("UI_CHANGED", "<selector> in flowpage.py") so a Flow redesign fails closed.

Selectors marked LIVE were confirmed with locator.count() on the live project page
(tests/fixtures/NOTES.md, 2026-09-29). The Task 7 read-only probe confirmed or replaced every
selector Task 6 left UNVERIFIED (NOTES.md, "Task 7 selector pass").

Hard rules: no Ctrl+A / select-all / delete key presses anywhere; clearing the prompt = reloading
the project URL. Never click a credit-purchase or an Upscaled download."""
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .guards import MAX_REFS, FlowError
from .netwatch import Media, decode_batchexecute

# --- selectors (LIVE unless marked) ---------------------------------------------------------
AGENT_PILL = "button.agent-mode-chip"                                   # aria-pressed="false" = OFF
SETTINGS_CHIP = 'button[aria-label="Settings trigger"]'
TOGGLE = "button.mat-button-toggle-button"                               # selected = aria-checked="true"
MODEL_TRIGGER = 'button[aria-label="Select model family"]'               # LIVE (T7) text "🍌 Nano Banana 2 arrow_drop_down"
MODEL_OPTION = '[role="menu"] [role="menuitem"]'                         # LIVE (T7) text "🍌 Nano Banana 2" (also "... 2 Lite")
ATTACH_OPEN = 'button[aria-label="Add ingredients to the prompt box"]'
# LIVE 2026-09-29: Video/Frames mode dropped ATTACH_OPEN for Start/End frame chips; the Start chip
# opens the same asset picker, and an attached frame replaces the empty chip with an image
START_CHIP = "flow-base-prompt-box button.empty-chip"
START_CHIP_RE = re.compile(r"^\s*Start\s*$")
PROMPT_BOX_IMG = "flow-base-prompt-box img.chip-image"  # review r1: chip images only, not icons
# LIVE 2026-09-29: a filled frame slot is a chip button aria-label "Image ingredient" whose click
# removes it; Flow keeps it across reloads, so frames are cleared before every upload/attach
FRAME_FILLED = 'flow-base-prompt-box button[aria-label="Image ingredient"]'
PICKER_SEARCH = 'input[aria-label="Search assets"]'
# the picker is a cdk-overlay pane, NOT a role=dialog, and has no <input type=file> (T7)
PICKER_UPLOAD_RE = re.compile(r"Upload media")                          # LIVE (T7) button -> native file chooser
PICKER_ROW = '[aria-label="Asset list"] [role="option"]'                 # LIVE (T7) button.asset-item "ref.png / Image"
# LIVE (T7): "Uploading" label while bytes upload (~7 s, until rpc maseQ returns). NOT anchored: the
# row's textContent starts with an icon ligature ("image..."), so ^Uploading never matched and the
# first smoke returned mid-upload; the reload that followed lost the asset.
PICKER_UPLOADING_RE = re.compile(r"Uploading")
# LIVE (T7): the picker list is fetched once per page load, and the server listing lags a fresh
# upload by more than ~8 s, so upload() confirms the asset by reloading until a fresh list has it.
UPLOAD_LISTED_WAIT_S = 120
UPLOAD_RELOAD_GAP_MS = 5000
PICKER_ROW_ACTIVE = "asset-item-active"                                  # LIVE (T7) class of the selected row
PICKER_PREVIEW = "img.detail-preview-image"                              # LIVE (T7) alt "Preview of <title>"
ADD_TO_PROMPT_RE = re.compile(r"^\s*add to prompt\s*$", re.I)
PROMPT_BAR_THUMB = "flow-base-prompt-box img.chip-image"                # LIVE (T7) alt "Ingredient image"
FIRE = 'button[aria-label="Start generation"]'
EDITOR = 'div.ProseMirror[contenteditable="true"]'
EDITOR_PLACEHOLDER = "What do you want to create?"
PROJECT_TITLE = 'input[aria-label="Editable text"]'
NEW_PROJECT = 'button:has-text("New project")'
PROJECT_CARD = "flow-project-card"                                      # LIVE (T7) home grid card
PROJECT_CARD_TITLE = ".project-title-label"                              # LIVE (T7) text "<name> edit" (edit icon)
PROJECT_OPEN = 'a[aria-label="Open project"]'                            # LIVE (T7) the card's only safe click
GRID_WAIT_MS = 10000  # the home grid renders seconds after goto; count too early = duplicate project
# Empty-state ("no projects yet") marker for the home grid. UNVERIFIED - the test account has projects,
# so it was never seen live. None = detection off: a grid that shows no card fails closed (UI_CHANGED)
# and open_project never creates a project on a load it cannot read.
EMPTY_STATE_RE: "re.Pattern | None" = None
# Detail view marker: its presence says the item's detail view rendered. NEVER clicked - the Download
# menu hands the file to Edge's downloads hub, whose page target never answers CDP and blocks every
# later connect_over_cdp (live 2026-09-30). Bytes come only from page.request on a signed URL.
DETAIL_MARKER = 'button[aria-label="Download media"]'
# Grid acceptance probe for queued fires. LIVE 2026-09-30: a landed project-grid tile is a
# <flow-grid-tile-container aria-label="<title>"> holding flow-image-tile > img[data-media-id] (there is
# NO a[href*="/edit/"] in the grid). The grid is windowed: a new tile pushes an old one out, so the
# TILE count stays flat (7 -> 7 live). The acceptance evidence is the badge: a queued fire adds a
# <flow-pending-tile> with div.loading-percentage "NN%" (0->1->2->3->4 live, 0.05-0.08 s after each
# click). ONLY badges inside a pending tile count - a stray "NN%" elsewhere on the page never does.
# A miss only means "no tile evidence": the fire falls back to the ledger signal, else UNCONFIRMED.
GRID_TILE = "flow-grid-tile-container"
PENDING_TILE = "flow-pending-tile"
PENDING_BADGE = ".loading-percentage"
# Identity of a pending tile, first attribute present. UNVERIFIED which (if any) Flow sets; a tile with
# none reads as None and the service falls back to counting (see Service._evidence).
PENDING_TILE_ID_ATTRS = ("data-media-id", "data-id", "data-workflow-id", "id", "aria-label")
# Ingredient chip in the prompt bar (NOTES: img.chip-image inside button[aria-label="Ingredient"]).
# UNVERIFIED that a click removes it (FRAME_FILLED's does). prepare_in_place fails closed if it stays.
INGREDIENT_CHIP = 'flow-base-prompt-box button[aria-label="Ingredient"]'
ACCOUNT_WAIT_S = 5  # state(): the account control hydrates a moment after a (re)load
ACCOUNT_DETAILS = '[aria-label="Account details"]'
ACCOUNT_PANEL_CLOSE = 'button[aria-label="Close account panel"]'
OUT_OF_CREDITS_TEXT = "out of Google Flow"
CAPTCHA_URL = "google.com/sorry"
LOGIN_HOST = "accounts.google.com"

MODE_RE = {"image": r"^\s*image\s*Image\s*$", "video": r"^\s*videocam\s*Video\s*$"}  # textContent "imageImage" (T7)
VIDEO_MODE_RE = {"frames": r"crop_free\s*Frames", "ingredients": r"chrome_extension\s*Ingredients"}
ASPECT_RE = {"9:16": r"crop_9_16\s*9:16", "16:9": r"crop_16_9\s*16:9"}
COUNT_RE = r"^\s*x{n}\s*$"
DURATION_RE = r"^\s*{n}s\s*$"
MODEL_LABEL = {"nano_banana_2": "Nano Banana 2", "omni_flash": "Omni 1.1 Flash"}  # label in chip/panel
PROJECT_ID_RE = re.compile(r"/project/([0-9a-f-]{36})")
THUMB_WAIT_MS = 5000  # how long a prompt-bar thumbnail may take to appear after Add


def _is_media(headers: dict, body: bytes) -> bool:
    ct = (headers or {}).get("content-type", "").lower()
    if ct.startswith(("image/", "video/")):
        return True
    return (body[:8] == b"\x89PNG\r\n\x1a\n" or body[:3] == b"\xff\xd8\xff" or body[4:8] == b"ftyp"
            or (body[:4] == b"RIFF" and body[8:12] == b"WEBP"))


class FlowPage:
    def __init__(self, browser):
        self.browser, self.ui_timeout_ms, self.balance = browser, 5000, None
        self.mode = None  # "image" | "frames" | "video" once set_settings ran; None = unknown (upload)
        if hasattr(browser, "on_response"):
            browser.on_response(self._on_response)

    # --- plumbing --------------------------------------------------------------------------
    def _on_response(self, url: str, body: str) -> None:
        for rpcid, payload in decode_batchexecute(body):
            if rpcid == "nzlxg" and isinstance(payload, list) and len(payload) > 5 \
                    and isinstance(payload[5], int):
                self.balance = payload[5]  # remaining credits (NOTES: nzlxg[5])

    def _p(self):
        """The Flow page, visible, with the account popover out of the way."""
        p = self.browser.page()
        if p.evaluate("document.visibilityState") != "visible":
            p.bring_to_front()  # the dedicated window only - browser.page() never hands out another
            if p.evaluate("document.visibilityState") != "visible":
                raise FlowError("UI_CHANGED", "document.visibilityState != visible after bring_to_front")
        close = p.locator(ACCOUNT_PANEL_CLOSE)
        if close.count() and close.first.is_visible():
            close.first.click()  # its backdrop blocks every later click; Escape does not close it
        return p

    def _need(self, p, sel: str, timeout_ms: int | None = None):
        loc = p.locator(sel)
        try:
            loc.first.wait_for(state="attached", timeout=timeout_ms or self.ui_timeout_ms)
        except Exception:
            raise FlowError("UI_CHANGED", f"{sel} in flowpage.py") from None
        return loc

    def _toggle(self, p, pattern: str):
        return p.locator(TOGGLE).filter(has_text=re.compile(pattern))

    def _project_url(self, p) -> str:
        m = PROJECT_ID_RE.search(p.url)
        if not m:
            raise FlowError("UI_CHANGED", f"not on a /project/<id> page ({p.url})")
        return p.url[: m.end()]

    def wait(self, ms: int) -> None:
        """Pump Playwright (response events only dispatch during a Playwright call)."""
        self.browser.page().wait_for_timeout(ms)

    # --- state ----------------------------------------------------------------------------
    def state(self) -> dict:
        p = self._p()
        url = p.url
        host = urlparse(url).hostname
        banner = self._banner(p)
        return {"url": url,
                "captcha": CAPTCHA_URL in url,
                "signed_in": host != LOGIN_HOST and CAPTCHA_URL not in url and self._account(p),
                # override 7: out of credits only when the banner is up AND the fire button is gone
                "out_of_credits": banner is not None and p.locator(FIRE).count() == 0,
                "banner": banner,
                "balance": self.balance}

    def _account(self, p) -> bool:
        """The account control, waited for up to ACCOUNT_WAIT_S: right after a relaunch it renders a
        moment late, and an instant count() read that as signed out (a false LOGIN_REQUIRED)."""
        end = time.time() + ACCOUNT_WAIT_S
        while p.locator(ACCOUNT_DETAILS).count() == 0:
            if time.time() >= end:
                return False
            p.wait_for_timeout(250)
        return True

    def _banner(self, p) -> str | None:
        b = p.get_by_text(OUT_OF_CREDITS_TEXT)
        return b.first.inner_text().strip() if b.count() else None

    def open_project(self, name: str) -> str:
        p = self._p()
        m = PROJECT_ID_RE.search(p.url)
        if m and p.locator(PROJECT_TITLE).count() and p.locator(PROJECT_TITLE).first.input_value() == name:
            return m.group(1)
        p.goto(self.browser.cfg.flow_url)
        self._need(p, NEW_PROJECT, 15000)
        try:
            p.locator(PROJECT_CARD).first.wait_for(state="attached", timeout=GRID_WAIT_MS)
        except Exception:
            empty = EMPTY_STATE_RE is not None and p.get_by_text(EMPTY_STATE_RE).count() > 0
            if not empty:  # never create a project on a grid load we could not read
                raise FlowError("UI_CHANGED", f"{PROJECT_CARD} never appeared on the home grid and no "
                                "empty-state was detected - not creating a project") from None
        title_re = re.compile(rf"^\s*{re.escape(name)}\s*(edit)?\s*$")
        tiles = p.locator(PROJECT_CARD).filter(has=p.locator(PROJECT_CARD_TITLE, has_text=title_re))
        if tiles.count() > 1:
            raise FlowError("UI_CHANGED", f"{tiles.count()} projects named {name!r} - pick one by hand")
        if tiles.count() == 1:
            tiles.first.locator(PROJECT_OPEN).first.click()  # never the card's Delete button
        else:
            self._need(p, NEW_PROJECT).first.click()
            title = self._need(p, PROJECT_TITLE, 15000).first
            title.fill(name)
            title.press("Enter")
        p.wait_for_url(PROJECT_ID_RE, timeout=30000)
        return PROJECT_ID_RE.search(p.url).group(1)

    def current_project_id(self) -> str | None:
        m = PROJECT_ID_RE.search(self.browser.page().url)
        return m.group(1) if m else None

    def goto_project(self, project_id: str) -> None:
        """Navigate the shared tab to a bound project (the caller holds the tab lock)."""
        if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", project_id):
            raise FlowError("UI_CHANGED", f"bad project id {project_id!r}")
        base = self.browser.cfg.flow_url.split("/project/")[0].rstrip("/")
        p = self.browser.page()
        p.goto(f"{base}/project/{project_id}")
        self._need(p, EDITOR, 15000)
        if self.current_project_id() != project_id:  # a redirect / peer nav: never fire elsewhere
            raise FlowError("UI_CHANGED", f"tab did not land on bound project {project_id} "
                                          f"(now {self.current_project_id()!r})")

    def reset(self) -> None:
        """Clear prompt + attached refs the only allowed way: reload the project URL."""
        p = self.browser.page()
        p.goto(self._project_url(p))
        self._need(p, EDITOR, 15000)

    # --- settings ---------------------------------------------------------------------------
    def _open_panel(self, p) -> None:
        chip = self._need(p, SETTINGS_CHIP).first
        for _ in range(2):  # NOTES: the panel sometimes needs a second click
            if p.locator(TOGGLE).count() and p.locator(TOGGLE).first.is_visible():
                return
            chip.click()
            try:
                p.locator(TOGGLE).first.wait_for(state="visible", timeout=4000)
                return
            except Exception:
                continue
        raise FlowError("UI_CHANGED", f"{TOGGLE} in flowpage.py (settings panel did not open)")

    def _close_panel(self, p) -> None:
        if p.locator(TOGGLE).count() and p.locator(TOGGLE).first.is_visible():
            p.keyboard.press("Escape")  # LIVE (T7): Escape closes the settings popover
            p.wait_for_timeout(300)

    def _apply(self, p, wanted: dict[str, str]) -> dict[str, bool]:
        """Click every wanted toggle that is not selected; return {name: selected} re-read."""
        for name, pat in wanted.items():
            t = self._toggle(p, pat)
            if t.count() == 0:
                raise FlowError("UI_CHANGED", f"{TOGGLE} /{pat}/ ({name}) in flowpage.py")
            if t.first.get_attribute("aria-checked") != "true":
                t.first.click()
                p.wait_for_timeout(150)
        return {n: self._toggle(p, pat).first.get_attribute("aria-checked") == "true"
                for n, pat in wanted.items()}

    def set_settings(self, mode: str, model: str, count: int, duration: int | None = None,
                     video_mode: str | None = None, aspect: str = "9:16") -> None:
        p = self._p()
        if mode not in MODE_RE or aspect not in ASPECT_RE or not 1 <= int(count) <= 4:
            raise FlowError("SETTINGS_MISMATCH", f"unsupported mode={mode} aspect={aspect} count={count}")
        if model not in MODEL_LABEL:
            raise FlowError("SETTINGS_MISMATCH", f"no UI label known for model {model!r}")
        self.mode = "image" if mode == "image" else ("frames" if video_mode == "frames" else "video")
        self._open_panel(p)
        # mode first: switching mode re-renders the group (durations exist only in Video)
        self._apply(p, {"mode": MODE_RE[mode]})
        label = MODEL_LABEL[model]
        self._select_model(p, label)
        wanted = {"mode": MODE_RE[mode], "aspect": ASPECT_RE[aspect],
                  "count": COUNT_RE.format(n=int(count))}  # always explicit: xN carries over modes
        if mode == "video":
            if video_mode not in VIDEO_MODE_RE or duration is None:
                raise FlowError("SETTINGS_MISMATCH", f"video needs video_mode+duration, got {video_mode}/{duration}")
            wanted["video_mode"] = VIDEO_MODE_RE[video_mode]
            wanted["duration"] = DURATION_RE.format(n=int(duration))
        got = self._apply(p, wanted)
        if not all(got.values()):
            got = self._apply(p, wanted)  # the one correction attempt
        if not all(got.values()):
            raise FlowError("SETTINGS_MISMATCH", "not selected after correction: "
                            + ", ".join(f"{n}={wanted[n]}" for n, ok in got.items() if not ok)
                            + f" | aspect wanted {aspect}")
        if not self._model_selected(p, label):
            raise FlowError("SETTINGS_MISMATCH", f"model {label!r} no longer selected after toggles")
        self._close_panel(p)
        chip_text = p.locator(SETTINGS_CHIP).first.inner_text()
        if mode == "image" and label not in chip_text:  # the Image chip names the model
            raise FlowError("SETTINGS_MISMATCH", f"chip reads {chip_text!r}, wanted model {label!r}")
        if f"x{int(count)}" not in chip_text or aspect.replace(":", "_") not in chip_text:
            raise FlowError("SETTINGS_MISMATCH", f"chip reads {chip_text!r}, wanted {aspect} x{count}")
        self._agent_off(p)

    def _model_selected(self, p, label: str) -> bool:
        t = p.locator(MODEL_TRIGGER)
        if not t.count():
            return False
        text = t.first.inner_text().replace("arrow_drop_down", "")  # icon ligature text (T7)
        return re.search(rf"(^|\s){re.escape(label)}\s*$", text) is not None

    def _select_model(self, p, label: str) -> None:
        trig = self._need(p, MODEL_TRIGGER)
        for _ in range(2):  # select, then one correction
            if self._model_selected(p, label):
                return
            trig.first.click()
            # labels carry an emoji prefix; the $ anchor keeps "Nano Banana 2" off "... 2 Lite"
            opt = p.locator(MODEL_OPTION).filter(has_text=re.compile(rf"(^|\s){re.escape(label)}\s*$"))
            try:
                opt.first.wait_for(state="visible", timeout=self.ui_timeout_ms)
            except Exception:
                raise FlowError("UI_CHANGED", f"{MODEL_OPTION} /{label}/ in flowpage.py") from None
            opt.first.click()
            p.wait_for_timeout(200)
        if not self._model_selected(p, label):
            raise FlowError("SETTINGS_MISMATCH",
                            f"model {label!r} not selected (reads {trig.first.inner_text()!r})")

    def _agent_off(self, p) -> None:
        pill = p.locator(AGENT_PILL)
        if pill.count() == 0:
            # ruling: absent pill is fine only when the normal fire button is there
            if p.locator(FIRE).count() == 0:
                raise FlowError("UI_CHANGED", f"{AGENT_PILL} and {FIRE} both missing in flowpage.py")
            return
        if pill.first.get_attribute("aria-pressed") == "false":
            return
        pill.first.click()
        p.wait_for_timeout(300)
        state = pill.first.get_attribute("aria-pressed")
        if state != "false":
            raise FlowError("SETTINGS_MISMATCH", f"Agent pill aria-pressed={state!r} after one click")

    # --- refs ---------------------------------------------------------------------------------
    def _wait_prompt_box(self, p) -> None:
        """The prompt box renders seconds after a project load. Image mode waits for Add-ingredients
        ONLY (a leftover filled frame chip is not the ready signal - review r1); Frames mode accepts the
        Start chip or a filled frame; unknown mode (upload) takes any attach control."""
        if self.mode == "image":
            sel = ATTACH_OPEN
        elif self.mode == "frames":
            sel = f"{START_CHIP}, {FRAME_FILLED}"
        else:
            sel = f"{ATTACH_OPEN}, {START_CHIP}, {FRAME_FILLED}"
        try:
            p.locator(sel).first.wait_for(state="visible", timeout=20000)
        except Exception:
            raise FlowError("UI_CHANGED", f"prompt box control {sel} never rendered ({self.mode or 'any'} "
                            "mode) in flowpage.py") from None

    def _frames(self, p) -> bool:
        if self.mode in ("image", "frames"):
            return self.mode == "frames"
        return p.locator(ATTACH_OPEN).count() == 0

    def _clear_frames(self, p) -> None:
        """Frames mode: empty the Start/End slots (a leftover frame must never ride into a new clip)."""
        for _ in range(4):
            filled = p.locator(FRAME_FILLED)
            if not filled.count():
                return
            filled.first.click()
            p.wait_for_timeout(400)
        raise FlowError("UI_CHANGED", f"{FRAME_FILLED} would not clear in flowpage.py")

    def _open_picker(self, p) -> str:
        """Open the asset picker; returns "ingredient" (Image / Ingredients) or "start" (Frames)."""
        kind = "ingredient"
        self._wait_prompt_box(p)
        if self._frames(p):
            self._clear_frames(p)
        if not self._frames(p) and p.locator(ATTACH_OPEN).count():
            p.locator(ATTACH_OPEN).first.click()
        else:
            start = p.locator(START_CHIP).filter(has_text=START_CHIP_RE)
            if not start.count():
                raise FlowError("UI_CHANGED", f"neither {ATTACH_OPEN} nor the Start frame chip in flowpage.py")
            start.first.click()
            kind = "start"
        self._need(p, PICKER_SEARCH)  # the missed-slot trap: no asset search = wrong panel
        return kind

    @staticmethod
    def _row_is(text: str, stem: str) -> bool:
        """Exact stem match on a picker row ("ref.png / Image", maybe an icon line first)."""
        for line in (x.strip() for x in text.splitlines()):
            if Path(line).suffix and Path(line).stem == stem:
                return True
        return False

    @staticmethod
    def _row_named(text: str, name: str) -> bool:
        """A picker row whose title is exactly `name`, or a file whose stem is `name`."""
        for line in (x.strip() for x in text.splitlines()):
            if line == name or (Path(line).suffix and Path(line).stem == name):
                return True
        return False

    def _previews(self, p, name: str) -> bool:
        """The picker preview shows exactly `name` (alt "Preview of <title>"; "ref2.png" is not "ref")."""
        alt = self._preview_alt(p)
        title = alt[len("Preview of "):].strip() if alt.startswith("Preview of ") else ""
        return bool(title) and self._row_named(title, name)

    def _exact_rows(self, p, stem: str) -> list[str]:
        rows = p.locator(PICKER_ROW).filter(has_text=stem)
        return [t for t in (rows.nth(i).inner_text() for i in range(rows.count())) if self._row_is(t, stem)]

    def upload(self, path: Path) -> str:
        """Idempotent: an asset whose file stem matches exactly is returned, never re-uploaded."""
        p = self._p()
        path = Path(path)
        self._open_picker(p)
        p.locator(PICKER_SEARCH).first.fill(path.stem)
        p.wait_for_timeout(1500)
        existing = self._exact_rows(p, path.stem)
        if any(PICKER_UPLOADING_RE.search(t) for t in existing):
            p.keyboard.press("Escape")
            raise FlowError("REF_NOT_ATTACHED", f"{path.name} is mid-upload in this window - wait, then retry")
        if existing:
            p.keyboard.press("Escape")
            return path.stem
        p.locator(PICKER_SEARCH).first.fill("")
        up = p.get_by_role("button", name=PICKER_UPLOAD_RE)
        if up.count() == 0:
            raise FlowError("UI_CHANGED", "button /Upload media/ in flowpage.py")
        with p.expect_file_chooser(timeout=self.ui_timeout_ms * 2) as fc:
            up.first.click()
        fc.value.set_files(str(path))
        search = p.locator(PICKER_SEARCH).first
        end = time.time() + 90
        while time.time() < end:
            search.fill(path.stem)
            p.wait_for_timeout(1500)
            rows = p.locator(PICKER_ROW).filter(has_text=path.stem)
            # the row shows up at once as "Uploading / <file>"; done = it lost that prefix
            if rows.count() >= 1 and rows.filter(has_text=PICKER_UPLOADING_RE).count() == 0:
                p.keyboard.press("Escape")
                break
        else:
            raise FlowError("REF_NOT_ATTACHED", f"uploaded {path.name} never finished uploading")
        if self._listed_after_reload(path.stem):
            return path.stem
        raise FlowError("REF_NOT_ATTACHED", f"uploaded {path.name} is not in a fresh picker listing "
                        f"after {UPLOAD_LISTED_WAIT_S}s - check Flow before uploading it again")

    def _listed_after_reload(self, stem: str) -> bool:
        """True once a freshly loaded picker lists the asset (the only proof the server kept it)."""
        end = time.time() + UPLOAD_LISTED_WAIT_S
        while True:
            self.reset()
            p = self._p()
            self._open_picker(p)
            p.locator(PICKER_SEARCH).first.fill(stem)
            p.wait_for_timeout(1500)
            n = len(self._exact_rows(p, stem))  # substring hits ("ref2" for "ref") are not proof
            p.keyboard.press("Escape")
            if n >= 1:
                return True
            if time.time() >= end:
                return False
            p.wait_for_timeout(UPLOAD_RELOAD_GAP_MS)

    def _visible_rows(self, p):
        rows = p.locator(PICKER_ROW)
        return [rows.nth(i) for i in range(rows.count()) if rows.nth(i).is_visible()]

    @staticmethod
    def _preview_alt(p) -> str:
        prev = p.locator(PICKER_PREVIEW)
        return (prev.first.get_attribute("alt", timeout=1000) or "") if prev.count() else ""

    def attach(self, name: str) -> None:
        p = self._p()
        self._wait_prompt_box(p)
        frames = self._frames(p)
        if frames:
            self._clear_frames(p)
        thumb_sel = PROMPT_BOX_IMG if frames else PROMPT_BAR_THUMB
        before = p.locator(thumb_sel).count()
        if self._open_picker(p) == "start" and not frames:
            raise FlowError("UI_CHANGED", "picker kind changed between checks")
        p.locator(PICKER_SEARCH).first.fill(name)
        p.wait_for_timeout(800)
        # the search is a substring filter: LIVE 2026-09-30 "ref" also listed ref2.png - keep only
        # rows whose name IS the ref (file stem or bare title)
        rows = [r for r in self._visible_rows(p) if self._row_named(r.inner_text(), name)]
        if len(rows) != 1:
            p.keyboard.press("Escape")
            raise FlowError("REF_NOT_ATTACHED", f"{len(rows)} picker rows named {name!r} (need exactly 1)")
        # a lone search hit is auto-selected, and clicking a selected row DESELECTS it (T7): click
        # only when it is not already the active row
        if PICKER_ROW_ACTIVE not in (rows[0].get_attribute("class") or "").split():
            rows[0].click()
        # the preview <img> is attached with alt "Preview of <title>" but often never loads (0x0 box,
        # so never "visible" - T7 smoke run 2): wait for its alt to name the ref instead
        end = time.time() + self.ui_timeout_ms / 1000
        while not self._previews(p, name) and time.time() < end:
            p.wait_for_timeout(150)
        if not self._previews(p, name):
            alt = self._preview_alt(p)
            p.keyboard.press("Escape")
            raise FlowError("REF_NOT_ATTACHED", f"picker preview is {alt!r}, not {name!r}")
        add = p.get_by_role("button", name=ADD_TO_PROMPT_RE)
        if add.count() == 0:
            raise FlowError("UI_CHANGED", "button /add to prompt/i in flowpage.py")
        thumbs = p.locator(thumb_sel)
        for _ in range(2):  # Add, then one retry - only if NO thumbnail showed up in the wait
            if add.count() and add.first.is_visible():
                add.first.click()
            end = time.time() + THUMB_WAIT_MS / 1000
            while thumbs.count() == before and time.time() < end:
                p.wait_for_timeout(150)
            if thumbs.count() > before:
                break
        p.wait_for_timeout(500)  # settle: a late second thumbnail must be seen here, not after fire
        n = thumbs.count()
        if frames and p.locator(START_CHIP).filter(has_text=START_CHIP_RE).count():
            raise FlowError("REF_NOT_ATTACHED", f"{name!r}: the Start frame chip is still empty - not firing")
        if n == before + 1:
            return
        if n > before + 1:
            raise FlowError("REF_NOT_ATTACHED", f"{name!r} added {n - before} thumbnails (need exactly 1) - not firing")
        raise FlowError("REF_NOT_ATTACHED", f"{name!r}: prompt-bar thumbnails stayed at {before}")

    def attach_many(self, names: list[str]) -> None:
        """Attach 1-3 refs in order (each attach adds exactly one thumbnail), then require the
        prompt bar to hold exactly len(names) thumbnails - a multi-ref still never fires short."""
        names = list(names)
        p = self._p()
        self._wait_prompt_box(p)
        if self._frames(p):
            raise FlowError("REF_NOT_ATTACHED", f"Frames mode takes one start frame, not attach_many {names!r} "
                            "- use attach()")
        if not 1 <= len(names) <= MAX_REFS or len(set(names)) != len(names):
            raise FlowError("REF_NOT_ATTACHED", f"need 1-{MAX_REFS} distinct refs, got {names!r}")
        for n in names:
            self.attach(n)
        p = self._p()
        n = p.locator(PROMPT_BAR_THUMB).count()
        if n != len(names):
            raise FlowError("REF_NOT_ATTACHED", f"prompt bar holds {n} thumbnails after attaching "
                            f"{len(names)} refs {names!r} - not firing")

    # --- prompt + fire ---------------------------------------------------------------------------
    def _editor_text(self, p) -> str:
        t = self._need(p, EDITOR).first.inner_text().strip()
        return "" if t == EDITOR_PLACEHOLDER else t

    def type_prompt(self, text: str) -> None:
        p = self._p()
        if self._editor_text(p):
            raise FlowError("UI_CHANGED", "prompt editor not empty before typing - reset() first")
        p.locator(EDITOR).first.click()
        p.keyboard.insert_text(text)  # React-safe, no key events that could hit shortcuts
        got = p.locator(EDITOR).first.inner_text()
        if EDITOR_PLACEHOLDER in got or len(" ".join(got.split())) != len(" ".join(text.split())):
            try:
                self.reset()
            finally:
                raise FlowError("UI_CHANGED", f"prompt read-back {len(got)} chars != {len(text)}")

    def _fire_button(self, p):
        btn = p.locator(FIRE)
        if btn.count() == 0:
            if self._banner(p) is not None:
                raise FlowError("OUT_OF_CREDITS", "no Start generation button and the out-of-credits banner is up")
            raise FlowError("UI_CHANGED", f"{FIRE} in flowpage.py")
        # Flow keeps the button disabled for a moment while a fresh start frame / prompt settles
        # (2026-09-30: two false UI_CHANGED on PUMP1, enabled ~2 s later) - wait, then judge
        end = time.time() + 15
        while btn.first.is_disabled() and time.time() < end:
            p.wait_for_timeout(250)
        if btn.first.is_disabled():
            raise FlowError("UI_CHANGED", f"{FIRE} is disabled")
        return btn.first

    def fire_ready(self) -> None:
        """Called BEFORE the pending entry is written: button present and enabled."""
        self._fire_button(self._p())

    def fire(self) -> None:
        """Raises FlowError only before its click (the service marks those attempts not_fired)."""
        p = self._p()
        btn = self._fire_button(p)
        self._agent_off(p)  # re-read the Agent pill rule right before the click
        btn.click()

    def prompt_cleared(self) -> bool:
        """Diagnostics only: Flow empties the editor ~50 ms after ANY click - never acceptance."""
        return self._editor_text(self._p()) == ""

    # --- in-place fire preparation + grid probe (queued fires: no navigation) ---------------------
    def pending_tiles(self) -> list:
        """One entry per pending tile that shows a progress badge (read-only, no navigation): its
        identity (first of PENDING_TILE_ID_ATTRS present) or None when it has none."""
        return self._p().evaluate(
            """([tile, badge, attrs]) => [...document.querySelectorAll(tile)]
                 .filter(t => t.querySelector(badge))
                 .map(t => { for (const a of attrs) { const v = t.getAttribute(a); if (v) return v; }
                             return null; })""",
            [PENDING_TILE, PENDING_BADGE, list(PENDING_TILE_ID_ATTRS)])

    def prepare_in_place(self) -> None:
        """Ready the prompt bar for the next fire WITHOUT a reload (a reload kills generations in
        flight). The editor must already be empty (a real fire consumes it) - else fail closed. Leftover
        frame / ingredient chips are removed with their own chip clicks; any thumbnail left -> UI_CHANGED."""
        p = self._p()
        if self._editor_text(p):
            raise FlowError("UI_CHANGED", "prompt editor not empty before an in-place fire - refusing to "
                                          "reload while queued fires are in flight")
        for _ in range(6):
            chips = p.locator(f"{FRAME_FILLED}, {INGREDIENT_CHIP}")
            if not chips.count():
                break
            chips.first.click()
            p.wait_for_timeout(400)
        if p.locator(PROMPT_BAR_THUMB).count():
            raise FlowError("UI_CHANGED", f"{PROMPT_BAR_THUMB} still in the prompt bar after removing chips "
                                          "(in-place fire, no reload allowed)")

    # --- bytes ------------------------------------------------------------------------------------
    def fetch_bytes(self, media: Media) -> bytes:
        """Bytes ONLY via page.request on the signed URL. No URL, a non-OK answer or non-media bytes ->
        NO_MEDIA_URL (the service may open the detail view for a fresh URL). Never a UI download."""
        if not media.url:
            raise FlowError("NO_MEDIA_URL", f"{media.media_id}: no signed URL in the ledger")
        r = self._p().request.get(media.url)
        b = r.body() if r.ok else b""
        if r.ok and b and _is_media(r.headers, b):
            return b
        raise FlowError("NO_MEDIA_URL", f"{media.media_id}: signed URL answered {r.status} "
                                        f"{r.headers.get('content-type')!r} - expired or not media")

    def open_detail(self, workflow_id: str) -> None:
        """Open an item's detail view so Flow sends its as29s record (signed URL). The caller holds the
        tab lock, only navigates when nothing is in flight, and goes back to the project afterwards."""
        p = self.browser.page()
        p.goto(f"{self._project_url(p)}/edit/{workflow_id}")
        self._need(p, DETAIL_MARKER, 15000)  # presence only - NEVER clicked

    def screenshot(self) -> bytes:
        return self.browser.page().screenshot()
