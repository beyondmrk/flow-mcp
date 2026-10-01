import asyncio
import threading

from flow_mcp import server


def test_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"flow_status", "flow_open_project", "flow_upload", "flow_fire_still",
                     "flow_fire_clip", "flow_sweep", "flow_settle", "flow_download", "flow_screenshot"}
    assert not any(k in n for n in names for k in ("delete", "buy", "select_all"))


def test_every_tool_states_cost():
    for t in asyncio.run(server.mcp.list_tools()):
        d = (t.description or "").lower()
        assert "free" in d or "credit" in d, t.name
    clip = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "flow_fire_clip")
    assert "12 credits" in clip.description and "5 credits" not in clip.description
    assert "refused" in clip.description  # Veo Lite: refused until verified, no unverified price
    assert "never auto-retried" in clip.description.lower()


def test_service_calls_share_one_thread(monkeypatch):
    seen = []

    class FakeService:
        def status(self):
            seen.append(threading.get_ident()); return {"ok": True}

    monkeypatch.setattr(server, "_service", FakeService())

    async def many():
        return await asyncio.gather(*(server.flow_status() for _ in range(5)))

    assert all(r == {"ok": True} for r in asyncio.run(many()))
    assert len(set(seen)) == 1 and seen[0] != threading.get_ident()


def test_fire_still_takes_ref_or_refs():
    t = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "flow_fire_still")
    props, req = t.inputSchema["properties"], set(t.inputSchema.get("required", []))
    assert "refs" in props and "ref" in props and not {"ref", "refs"} & req
    clip = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "flow_fire_clip")
    assert "LINT_REQUIRED" in clip.description and "RUN_LOCKED" in clip.description
