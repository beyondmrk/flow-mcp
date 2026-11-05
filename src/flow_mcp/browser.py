"""One dedicated Edge, own profile, background throttling off. We attach over CDP and never
kill it: it outlives the MCP process so the Google sign-in and open project persist.

Tab model (LIVE 2026-11-04, multi-tab): attach-only, ONE TAB PER FLOW PROJECT. In this Edge a tab
created over CDP after a Playwright attach (Target.createTarget / context.new_page) hangs its renderer
on its first navigation, and a hung tab then blocks every connect_over_cdp. So a bridge never creates a
tab over CDP: a new tab is opened by asking EDGE ITSELF (`msedge.exe --new-window <url>` into the running
profile - verified 2026-11-04: attaches and navigates normally). A project's tab is found by URL
(/project/<id>), so nothing has to be remembered across an Edge or MCP restart. Several MCP servers
(one per Claude session) and flow_fire.py are peers; each project's tab is serialised by its own
TabLock (runlock.TabLock(key=project id)), so two projects drive two tabs at the same time."""
import base64
import json
import os
import socket
import subprocess
import time
import urllib.request
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from .config import Config
from .guards import FlowError
from .netwatch import BATCH_URL_PART

FLOW_HOST = "flow.google.com"
PING_TIMEOUT_S = 5.0
CONNECT_TIMEOUT_MS = 30000
CONNECT_TRIES = 2
LAUNCH_TAB_WAIT_S = 20.0
HUNG_TRIES = 3  # a page is "hung" only when it misses this many pings in a row
HUNG_RETRY_S = 5.0


def edge_args(cfg: Config, url: str | None = None, new_window: bool = False) -> list[str]:
    """The Edge command line. url defaults to the Flow home; new_window asks a RUNNING Edge (same
    profile dir) to open the url in a new window instead of a tab of the current one."""
    args = [str(cfg.edge_exe), f"--remote-debugging-port={cfg.cdp_port}",
            f"--user-data-dir={cfg.profile_dir}", "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows"]
    if new_window:
        args.append("--new-window")
    return args + [url or cfg.flow_url]


def flow_base(cfg: Config) -> str:
    return cfg.flow_url.split("/project/")[0].rstrip("/")


def project_url(cfg: Config, project_id: str) -> str:
    return f"{flow_base(cfg)}/project/{project_id}"


def project_pages(targets: list[dict], project_id: str) -> list[dict]:
    return [t for t in flow_pages(targets) if f"/project/{project_id}" in str(t.get("url", ""))]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def list_targets(port: int, timeout: float = PING_TIMEOUT_S) -> list[dict]:
    """GET /json/list - plain HTTP, no CDP session on any page."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as r:
            raw = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise FlowError("BROWSER_DOWN", f"GET /json/list on port {port} failed: {e}") from None
    return raw if isinstance(raw, list) else []


def flow_pages(targets: list[dict]) -> list[dict]:
    return [t for t in targets if isinstance(t, dict) and t.get("type") == "page"
            and urlparse(str(t.get("url", ""))).hostname == FLOW_HOST]


def ws_url(t: dict, port: int) -> str:
    return t.get("webSocketDebuggerUrl") or f"ws://127.0.0.1:{port}/devtools/page/{t.get('id')}"


def cdp_ping(url: str, timeout: float = PING_TIMEOUT_S) -> bool:
    """One raw-CDP Runtime.evaluate over a stdlib WebSocket, bounded by `timeout`. False when the
    page's renderer does not answer (the hang that would otherwise stall connect_over_cdp 180 s)."""
    u = urlparse(url)
    end = time.time() + timeout
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=timeout) as s:
            s.settimeout(timeout)
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                       "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                buf += chunk
            head, data = buf.split(b"\r\n\r\n", 1)
            if b" 101" not in head.split(b"\r\n", 1)[0]:
                return False
            msg = json.dumps({"id": 1, "method": "Runtime.evaluate",
                              "params": {"expression": "1", "returnByValue": True}}).encode()
            n, mask = len(msg), os.urandom(4)
            hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big"))
            s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))
            while True:
                if b'"id":1' in data.replace(b" ", b""):
                    return True
                left = end - time.time()
                if left <= 0:
                    return False
                s.settimeout(left)
                chunk = s.recv(65536)
                if not chunk:
                    return False
                data += chunk
    except OSError:
        return False


class Browser:
    """Attach-only. page() hands out the tab of the project selected with use(project_id): the Flow
    page whose URL is /project/<id>, opened as a new Edge window when there is none. With no project
    selected (use(None)) it is the remembered tab, else the first Flow tab. Tabs are created only by
    Edge itself (launch URL / --new-window), never over CDP; nothing is ever closed."""

    def __init__(self, cfg: Config):
        self.cfg, self._pw, self._b, self._page, self._cbs = cfg, None, None, None, []
        self._launched, self._target_id, self._project_id, self._wired = False, None, None, set()

    def _spawn(self, url: str | None, new_window: bool) -> None:
        subprocess.Popen(edge_args(self.cfg, url, new_window), creationflags=subprocess.DETACHED_PROCESS,
                         close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def _launch_if_needed(self, url: str | None = None) -> None:
        if _listening(self.cfg.cdp_port):
            return
        self._launched = True
        self._spawn(url, False)
        for _ in range(40):
            if _listening(self.cfg.cdp_port):
                return
            time.sleep(0.25)
        raise FlowError("BROWSER_DOWN", f"Edge did not open port {self.cfg.cdp_port}")

    def use(self, project_id: str | None) -> None:
        """Select the tab every later page() call returns: the tab on /project/<project_id>, or with
        None the remembered / first Flow tab. Cheap when unchanged."""
        if project_id != self._project_id:
            self._project_id, self._page = project_id, None

    def _open_window(self, url: str) -> None:
        """Ask the running Edge for a new window at url (never Target.createTarget - see module doc)."""
        self._spawn(url, True)

    def new_home_tab(self) -> None:
        """A fresh tab on the Flow home, for flow_open_project: it becomes the project's tab once the
        project page has loaded. Selected as the remembered tab (use(None))."""
        self._launch_if_needed()
        before = {t.get("id") for t in flow_pages(list_targets(self.cfg.cdp_port))}
        self._open_window(self.cfg.flow_url)
        end = time.time() + LAUNCH_TAB_WAIT_S
        while True:
            fresh = [t for t in flow_pages(list_targets(self.cfg.cdp_port)) if t.get("id") not in before]
            if fresh:
                self._project_id, self._target_id, self._page = None, fresh[0].get("id"), None
                return
            if time.time() >= end:
                raise FlowError("BROWSER_DOWN", f"Edge opened no new Flow tab within {LAUNCH_TAB_WAIT_S:.0f}s")
            time.sleep(0.5)

    def _choose_project(self, project_id: str) -> dict:
        """The tab on /project/<project_id>; opened as a new window when missing. Two such tabs is a
        refusal (the user closes one): the bridge never guesses which one a peer is driving."""
        opened = False
        end = time.time() + LAUNCH_TAB_WAIT_S
        while True:
            mine = project_pages(list_targets(self.cfg.cdp_port), project_id)
            if len(mine) > 1:
                raise FlowError("BROWSER_DOWN", f"{len(mine)} Flow tabs on project {project_id} - "
                                "close all but one in the dedicated Edge")
            if mine:
                return mine[0]
            if not opened:
                self._open_window(project_url(self.cfg, project_id))
                opened = True
            elif time.time() >= end:
                raise FlowError("BROWSER_DOWN", f"Edge opened no tab on project {project_id} within "
                                f"{LAUNCH_TAB_WAIT_S:.0f}s")
            time.sleep(0.5)

    def _choose(self) -> dict:
        """The shared Flow tab: the remembered target if still there, else the first Flow page.
        A launch waits for the launch-URL tab; an attach with no Flow tab never creates one."""
        end = time.time() + (LAUNCH_TAB_WAIT_S if self._launched else 0)
        while True:
            pages = flow_pages(list_targets(self.cfg.cdp_port))
            mine = [t for t in pages if t.get("id") == self._target_id]
            if mine or pages:
                return (mine or pages)[0]
            if time.time() >= end:
                raise FlowError("BROWSER_DOWN", f"no Flow tab in the Edge on port {self.cfg.cdp_port} - "
                                "open Flow in the dedicated Edge (the bridge never creates a tab)")
            time.sleep(0.5)

    def _hung_targets(self) -> list[str]:
        """Page targets (Flow or not) whose renderer does not answer a raw CDP ping, as "id url".
        A page is called hung only after HUNG_TRIES pings: a browser busy loading another project's
        window (live 2026-11-04: two windows opened at once) misses one 5 s ping and answers the next."""
        hung = {}
        for i in range(HUNG_TRIES):
            hung = {t.get("id"): f"{t.get('id')} {t.get('url')}" for t in list_targets(self.cfg.cdp_port)
                    if t.get("type") == "page" and (hung is None or i == 0 or t.get("id") in hung)
                    and not cdp_ping(ws_url(t, self.cfg.cdp_port))}
            if not hung:
                return []
            if i < HUNG_TRIES - 1:
                time.sleep(HUNG_RETRY_S)
        return list(hung.values())

    def _connect(self) -> None:
        """connect_over_cdp attaches to EVERY target of the browser; Playwright has no option to skip
        one, so a single non-answering page (e.g. edge://downloads-hub/ left by a UI download) stalls
        the attach of the Flow tab too. Unavoidable here: ping every page target first and fail fast
        with BROWSER_DOWN naming each blocking target's URL, so the user knows what to close."""
        if self._b is not None and self._b.is_connected():
            return
        hung = self._hung_targets()
        if hung:
            raise FlowError("BROWSER_DOWN", f"page target(s) not answering CDP block the attach: {hung} - "
                            "close them in the Edge window (edge://downloads-hub/ = the downloads panel)")
        self._pw = self._pw or sync_playwright().start()
        url, err = f"http://127.0.0.1:{self.cfg.cdp_port}", None
        for i in range(CONNECT_TRIES):
            try:
                self._b = self._pw.chromium.connect_over_cdp(url, timeout=CONNECT_TIMEOUT_MS)
                return
            except Exception as e:  # port open but CDP not ready yet, or a hung tab stalls the attach
                err = e
                if i < CONNECT_TRIES - 1:
                    time.sleep(1)
        hung = self._hung_targets()
        raise FlowError("BROWSER_DOWN", f"connect_over_cdp {url} failed {CONNECT_TRIES}x: {err}"
                        + (f" - page target(s) not answering CDP: {hung} (close them by hand)" if hung else ""))

    def page(self):
        if (self._page is not None and not self._page.is_closed()
                and self._b is not None and self._b.is_connected()):
            return self._page
        self._page = None
        self._launch_if_needed(project_url(self.cfg, self._project_id) if self._project_id else None)
        t = self._choose_project(self._project_id) if self._project_id else self._choose()
        if not cdp_ping(ws_url(t, self.cfg.cdp_port)):
            raise FlowError("BROWSER_DOWN", f"Flow tab {t.get('id')} ({t.get('url')}) does not answer CDP "
                            f"within {PING_TIMEOUT_S:.0f}s - its renderer is hung; close that tab by hand")
        self._connect()
        pages = [p for p in self._b.contexts[0].pages if p.url == t.get("url")]
        if len(pages) != 1:
            raise FlowError("BROWSER_DOWN", f"{len(pages)} Playwright pages at {t.get('url')} for target "
                            f"{t.get('id')} - leave exactly one Flow tab on that URL")
        self._target_id, self._page = t.get("id"), pages[0]
        if id(self._page) not in self._wired:  # one response hook per Playwright page, ever
            self._page.on("response", self._dispatch)
            self._wired.add(id(self._page))
        return self._page

    def _dispatch(self, resp) -> None:
        if BATCH_URL_PART not in resp.url:
            return
        try:
            body = resp.text()
        except Exception:
            return
        for cb in self._cbs:
            cb(resp.url, body)

    def on_response(self, cb: Callable[[str, str], None]) -> None:
        self._cbs.append(cb)

    def close_connection(self) -> None:
        if self._pw:
            self._pw.stop()
        self._pw = self._b = self._page = None
