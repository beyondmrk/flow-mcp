import json
import socket
import threading

import pytest
import flow_mcp.browser as br
from flow_mcp.browser import edge_args, Browser, cdp_ping, flow_pages
from flow_mcp.config import Config
from flow_mcp.guards import FlowError


def test_edge_args_flags():
    a = edge_args(Config())
    for f in ["--remote-debugging-port=9333", "--disable-background-timer-throttling",
              "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
              "--no-first-run"]:
        assert f in a
    assert any(x.startswith("--user-data-dir=") and x.endswith(".edge-profile") for x in a)


@pytest.mark.live
def test_attach_live():
    b = Browser(Config())
    assert "flow.google.com" in b.page().url


# --- raw CDP ping ---------------------------------------------------------------------------------

def _ws_server(answer: bool):
    """A one-shot WebSocket endpoint: handshake, then (optionally) reply to message id 1."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        c.recv(4096)  # the masked Runtime.evaluate frame
        if answer:
            body = json.dumps({"id": 1, "result": {"result": {"type": "number", "value": 1}}}).encode()
            c.sendall(bytes([0x81, len(body)]) + body)
        threading.Event().wait(1.5)
        c.close(); srv.close()
    threading.Thread(target=run, daemon=True).start()
    return f"ws://127.0.0.1:{port}/devtools/page/T1"


def test_cdp_ping_true_when_page_answers():
    assert cdp_ping(_ws_server(True), timeout=2) is True


def test_cdp_ping_false_when_renderer_hangs_bounded():
    import time
    t = time.time()
    assert cdp_ping(_ws_server(False), timeout=0.4) is False
    assert time.time() - t < 1.2


def test_cdp_ping_false_on_refused_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    assert cdp_ping(f"ws://127.0.0.1:{port}/devtools/page/X", timeout=0.5) is False


# --- target choice (HTTP /json/list only) ----------------------------------------------------------

def _t(tid, url, typ="page"):
    return {"id": tid, "type": typ, "url": url, "webSocketDebuggerUrl": f"ws://127.0.0.1:9333/devtools/page/{tid}"}


def test_flow_pages_exact_hostname_only():
    ts = [_t("A", "https://evil.test/?next=flow.google.com"), _t("B", "https://flow.google.com/project/x"),
          _t("C", "https://flow.google.com/", typ="iframe")]
    assert [t["id"] for t in flow_pages(ts)] == ["B"]


def test_choose_prefers_remembered_target(monkeypatch):
    ts = [_t("A", "https://flow.google.com/project/a"), _t("B", "https://flow.google.com/project/b")]
    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: ts)
    b = Browser(Config())
    assert b._choose()["id"] == "A"
    b._target_id = "B"
    assert b._choose()["id"] == "B"


def test_attach_without_flow_tab_is_browser_down_and_creates_nothing(monkeypatch):
    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: [_t("E", "edge://downloads-hub/")])
    b = Browser(Config())
    with pytest.raises(FlowError) as e:
        b._choose()
    assert e.value.code == "BROWSER_DOWN" and "open Flow in the dedicated Edge" in str(e.value)


# --- page(): ping before connect, no tab creation ---------------------------------------------------

class _Pg:
    def __init__(self, url): self.url = url
    def is_closed(self): return False
    def on(self, *a): pass


class _Ctx:
    def __init__(self, pages): self.pages = pages
    def new_page(self): raise AssertionError("attach path must never create a tab")
    def new_cdp_session(self, page): raise AssertionError("no per-page CDP sessions")


class _B:
    def __init__(self, pages): self.contexts = [_Ctx(pages)]
    def is_connected(self): return True
    def new_browser_cdp_session(self): raise AssertionError("no Target.createTarget")


class _PW:
    def __init__(self, b, calls): self.b, self.calls = b, calls
    @property
    def chromium(self): return self
    def connect_over_cdp(self, url, timeout=None):
        self.calls.append(timeout); return self.b
    def stop(self): pass


def _attached(monkeypatch, targets, pages, ping=True):
    calls = []
    monkeypatch.setattr(br, "_listening", lambda port: True)
    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: targets)
    monkeypatch.setattr(br, "cdp_ping", lambda url, timeout=5: ping)
    b = Browser(Config()); b._pw = _PW(_B(pages), calls)
    return b, calls


def test_attach_reuses_the_shared_flow_tab(monkeypatch):
    shared = _Pg("https://flow.google.com/project/p1")
    b, calls = _attached(monkeypatch, [_t("E", "edge://downloads-hub/"), _t("F", shared.url)],
                         [_Pg("edge://downloads-hub/"), shared])
    assert b.page() is shared and b._target_id == "F"
    assert calls == [br.CONNECT_TIMEOUT_MS]  # bounded connect, never the 180 s default
    assert b.page() is shared and len(calls) == 1  # cached, no re-ping / re-connect


def test_hung_tab_is_browser_down_before_connect(monkeypatch):
    b, calls = _attached(monkeypatch, [_t("HUNG1", "https://flow.google.com/project/p1")],
                         [_Pg("https://flow.google.com/project/p1")], ping=False)
    with pytest.raises(FlowError) as e:
        b.page()
    assert e.value.code == "BROWSER_DOWN" and "HUNG1" in str(e.value) and calls == []


def test_ambiguous_url_is_browser_down(monkeypatch):
    url = "https://flow.google.com/project/p1"
    b, _ = _attached(monkeypatch, [_t("F", url)], [_Pg(url), _Pg(url)])
    with pytest.raises(FlowError) as e:
        b.page()
    assert e.value.code == "BROWSER_DOWN"


def test_connect_over_cdp_retries_then_browser_down_naming_hung_pages(monkeypatch):
    calls, pings = [], iter([True, True, True, False])  # healthy at the preflight, one hangs later

    class _Chromium:
        def connect_over_cdp(self, url, timeout=None):
            calls.append(timeout); raise RuntimeError("Timeout exceeded")

    class _PW2:
        chromium = _Chromium()
        def stop(self): pass

    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: [_t("OK", "https://flow.google.com/"),
                                                                      _t("BAD", "https://flow.google.com/x")])
    monkeypatch.setattr(br, "cdp_ping", lambda url, timeout=5: next(pings))
    b = Browser(Config()); b._pw = _PW2()
    with pytest.raises(FlowError) as e:
        b._connect()
    assert e.value.code == "BROWSER_DOWN" and len(calls) == br.CONNECT_TRIES
    assert "BAD https://flow.google.com/x" in str(e.value)
    assert all(t == br.CONNECT_TIMEOUT_MS for t in calls)


def test_hung_non_flow_target_fails_fast_naming_its_url(monkeypatch):
    calls = []

    class _Chromium:
        def connect_over_cdp(self, url, timeout=None):
            calls.append(timeout); raise AssertionError("must not attach past a hung target")

    class _PW2:
        chromium = _Chromium()
        def stop(self): pass

    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: [_t("F", "https://flow.google.com/"),
                                                                      _t("D", "edge://downloads-hub/")])
    monkeypatch.setattr(br, "cdp_ping", lambda url, timeout=5: not url.endswith("/D"))
    b = Browser(Config()); b._pw = _PW2()
    with pytest.raises(FlowError) as e:
        b._connect()
    assert e.value.code == "BROWSER_DOWN" and calls == []
    assert "edge://downloads-hub/" in str(e.value) and "downloads panel" in str(e.value)


def test_launch_waits_for_the_launch_url_tab(monkeypatch):
    seq = [[], [_t("L", "https://flow.google.com/")]]
    monkeypatch.setattr(br, "list_targets", lambda port, timeout=5: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    b = Browser(Config()); b._launched = True
    assert b._choose()["id"] == "L"


def test_popen_stdio_devnull(monkeypatch):
    import subprocess
    seen = {}

    def fake_popen(args, **kw):
        seen.update(kw); raise OSError("stop here")

    monkeypatch.setattr(br, "_listening", lambda port: False)
    monkeypatch.setattr(br.subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        Browser(Config())._launch_if_needed()
    assert all(seen.get(k) is subprocess.DEVNULL for k in ("stdin", "stdout", "stderr"))
