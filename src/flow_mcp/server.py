"""MCP wiring for Google Flow. No logic here: each tool runs the matching Service method and FastMCP
returns any exception message to Claude as a tool error.

Playwright's sync API is thread-affine, so every Service call (and the lazy Service.build) runs on
ONE dedicated worker thread - a single-worker executor, never anyio's thread pool.

Costs: stills (Nano Banana 2, Image mode) are 0 credits. Clips fire on Omni 1.1 Flash
(omni_flash) only, 12 credits per output (x count). Veo Lite is refused until its UI label and cost
are verified live. Paid fires are NEVER retried automatically: an UNCONFIRMED fire stays pending
until flow_sweep settles it."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .service import Service

mcp = FastMCP("flow")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-playwright")
_service: Service | None = None


def _svc() -> Service:
    """Only ever called on the worker thread, so no lock is needed: calls are serialized."""
    global _service
    if _service is None:
        _service = Service.build()
    return _service


async def _run(method: str, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: getattr(_svc(), method)(**kwargs))


@mcp.tool()
async def flow_status() -> dict[str, Any]:
    """Read the dedicated Flow window: url, captcha, signed_in, out_of_credits, banner, credit
    balance (None until Flow's ~30 s credit poll arrives). Free, read-only."""
    return await _run("status")


@mcp.tool()
async def flow_open_project(name: str, project_dir: str) -> dict[str, Any]:
    """Open the Flow project called `name` (creates it when none exists; refuses if two share the
    name) and bind it to the local project_dir's run state. Free."""
    return await _run("open_project", name=name, project_dir=project_dir)


@mcp.tool()
async def flow_upload(path: str, project_dir: str | None = None) -> dict[str, Any]:
    """Upload a local image into a Flow project's assets; returns the asset name (file stem) that
    flow_fire_still / flow_fire_clip attach by. With project_dir, the shared Flow tab is first moved
    to that dir's bound project (pass it: a peer may have left the tab elsewhere). Free."""
    if project_dir is None:
        return await _run("upload", path=path)
    return await _run("upload", path=path, project_dir=project_dir)


@mcp.tool()
async def flow_fire_still(project_dir: str, slug: str, line: str, prompt: str,
                          refs: list[str] | None = None, ref: str | None = None,
                          outputs: int = 1, queue: bool = False) -> dict[str, Any]:
    """Fire ONE Image-mode still: Nano Banana 2, 9:16, x`outputs`. 0 credits. Pass exactly one of
    `ref` (one uploaded asset's local path) or `refs` (1-3 paths, attached in order by stem); the
    prompt bar must then hold exactly that many thumbnails or it refuses REF_NOT_ATTACHED. Stills do
    not need a lint stamp. Refuses RUN_LOCKED while another run holds the project. Waits for Flow's
    result and commits it to run state; returns status committed | WRONG_ASPECT | NO_MEDIA_URL |
    UNCONFIRMED. Never auto-retried: UNCONFIRMED stays pending - run flow_sweep, do not re-fire.
    queue=True (waves): returns QUEUED once the fire is ACCEPTED by Flow, not landed - call
    flow_settle to collect it."""
    return await _run("fire_still", project_dir=project_dir, slug=slug, line=line, prompt=prompt,
                      ref=ref, refs=refs, outputs=outputs, queue=queue)


@mcp.tool()
async def flow_fire_clip(project_dir: str, clip_name: str, line: str, prompt: str, start_frame: str,
                         duration: int, count: int = 1, mode: str = "frames",
                         model: str = "omni_flash", reroll: bool = False,
                         queue: bool = False) -> dict[str, Any]:
    """SPENDS FLOW CREDITS. Fire a 9:16 Video-mode clip off `start_frame` (an uploaded still's local
    path). Cost: 12 credits per output on Omni 1.1 Flash - total = 12 x count. model must be
    "omni_flash" (veo_lite is refused until its label and cost are verified); count 1-2.
    Only fire on the user's explicit go. Refuses LINT_REQUIRED unless Creatives/_flow_lint.json matches
    the current manifest's sha256 and this line's lint status is PASS or WARN; refuses RUN_LOCKED
    while another run holds the project (both before anything is written). Fires ONCE and is NEVER auto-retried: an UNCONFIRMED result
    stays pending (and blocks another fire of that line) until flow_sweep settles it. Returns
    committed | WRONG_MODEL | MODEL_UNKNOWN | NO_MEDIA_URL (credits "unknown", not a take) |
    UNCONFIRMED. Refuses ALREADY_COMMITTED when the line already holds a committed clip, unless
    reroll=True: a re-roll costs credits again (12 x count) - pass it only on the user's explicit go.
    queue=True (waves): queued fires return QUEUED once the fire is ACCEPTED by Flow, not landed
    (the credits are spent at that point); the line stays pending - call flow_settle to collect it."""
    return await _run("fire_clip", project_dir=project_dir, clip_name=clip_name, line=line,
                      prompt=prompt, start_frame=start_frame, duration=duration, count=count,
                      mode=mode, model=model, reroll=reroll, queue=queue)


@mcp.tool()
async def flow_settle(project_dir: str) -> dict[str, Any]:
    """Collect QUEUED fires (queue=True). While fires are in flight it never navigates (it pumps on
    the current page); once idle (~90 s after the last queued fire) it reloads for a fresh listing.
    Commits every queued attempt whose media have landed, with the same verdicts as a live fire
    (committed | WRONG_MODEL | MODEL_UNKNOWN | NO_MEDIA_URL | WRONG_ASPECT | partial); a queued
    attempt Flow never listed after 15 min comes back LOST (spend unknown). Attempts still rendering
    stay pending in still_rendering with their age. Never fires anything. Free."""
    return await _run("settle", project_dir=project_dir)


@mcp.tool()
async def flow_sweep(project_dir: str) -> dict[str, Any]:
    """Settle pending/partial fires: reloads the bound project (refuses if the window is on another
    project) and pumps ~10 s so Flow re-reports its media, then settles (after a timeout or an MCP
    restart). Never fires anything. Refuses RUN_LOCKED while another run holds the project, and
    IN_FLIGHT while queued fires are in flight (a reload would kill them) - use flow_settle. Free."""
    return await _run("sweep", project_dir=project_dir)


@mcp.tool()
async def flow_download(project_dir: str, media_ids: list[str]) -> dict[str, Any]:
    """Download committed media (original size only, never an upscale) into project_dir's
    Elements/Stills or Elements/Clips under their run-state names; skips files already on disk.
    Bytes only via the signed URL (never the Download button). An item needing its detail view for a
    URL comes back in `deferred` while queued fires are in flight, in `failed` (NO_MEDIA_URL) when no
    URL can be had. Free (no credits)."""
    return await _run("download", project_dir=project_dir, media_ids=media_ids)


@mcp.tool()
async def flow_screenshot() -> Image:
    """PNG screenshot of the dedicated Flow window, for debugging a UI_CHANGED error. Free."""
    return Image(data=await _run("screenshot"), format="png")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
