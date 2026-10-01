"""0-credit LIVE smoke against the dedicated Edge (CDP 9333, signed in by hand).

status -> open_project -> upload ref.png -> fire_still (Nano Banana 2, Image, 9:16, x1) -> download,
then asserts committed + file on disk + Pillow size (768, 1376).

Before the fire it applies the same Image settings the service will, and reads the settings chip
and the cost overlay: anything but Image-mode Nano Banana 2 x1 9:16 at 0 credits prints
SMOKE: FAIL BLOCKED ... and fires nothing. Never fires video.

Run:  uv run python scripts/smoke.py
Last line is always `SMOKE: PASS` or `SMOKE: FAIL <reason>`."""
import json
import re
import sys
import time
import traceback
from pathlib import Path

from PIL import Image

from flow_mcp.config import load_config
from flow_mcp.flowpage import ACCOUNT_DETAILS, ACCOUNT_PANEL_CLOSE, SETTINGS_CHIP
from flow_mcp.service import Service

PROJECT = "_bridge_smoke"
DIR = (load_config().work_root or Path.cwd()) / "_bridge_smoke"
REF = DIR / "ref.png"
PROMPT = "a single red apple on a wooden table"
COST_LINK = "a.credit-cost-link"
COST_TEXT_RE = re.compile(r"Generating will use\s+(\d+)\s+credits?", re.I)


def show(label, obj):
    print(f"{label}: {json.dumps(obj, default=str)}", flush=True)


def wait_balance(svc, budget_s=40):
    end = time.time() + budget_s
    while svc.page.balance is None and time.time() < end:
        svc.page.wait(500)
    if svc.page.balance is not None:
        return svc.page.balance
    # fallback: the Account details popover ("14 Google Flow credits"), closed by its own button
    p = svc.page.browser.page()
    p.locator(ACCOUNT_DETAILS).first.click()
    p.wait_for_timeout(1500)
    m = re.search(r"(\d+)\s+Google Flow credits", p.locator("body").inner_text())
    close = p.locator(ACCOUNT_PANEL_CLOSE)
    if close.count() and close.first.is_visible():
        close.first.click()
    return int(m.group(1)) if m else None


def precheck_cost(svc) -> str | None:
    """Apply the fire's settings, then read chip + cost. Returns a BLOCKED reason or None."""
    fp = svc.page
    fp.reset()
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None,
                    aspect="9:16")
    p = fp.browser.page()
    chip = " ".join(p.locator(SETTINGS_CHIP).first.inner_text().split())
    show("chip", chip)
    if "Video" in chip or "Nano Banana 2" not in chip or "crop_9_16" not in chip or "x1" not in chip:
        return f"chip reads {chip!r}"
    costs = []
    link = p.locator(COST_LINK)
    for i in range(link.count()):
        m = re.search(r"(\d+)", link.nth(i).inner_text())
        if m: costs.append(int(m.group(1)))
    m = COST_TEXT_RE.search(p.locator("body").inner_text())
    if m: costs.append(int(m.group(1)))
    show("cost_readings", costs)
    if any(c > 0 for c in costs):
        return f"cost reads {costs} credits"
    return None


def main() -> str:
    DIR.mkdir(parents=True, exist_ok=True)
    if not REF.exists():
        Image.new("RGB", (256, 256), (200, 30, 30)).save(REF)
    svc = Service.build()

    st = svc.status(); show("status", st)
    if st.get("captcha") or not st.get("signed_in"):
        return f"FAIL BLOCKED captcha/sign-in state {st}"
    before = wait_balance(svc); show("credits_before", before)
    if before is None:
        print("WARNING: credit balance unreadable BEFORE the fire (no nzlxg, no popover text)", flush=True)

    show("open_project", svc.open_project(PROJECT, DIR))
    show("upload", svc.upload(REF))  # idempotent: an existing exact-stem asset is returned as-is

    blocked = precheck_cost(svc)
    if blocked:
        return f"FAIL BLOCKED {blocked} - nothing fired"

    res = svc.fire_still(DIR, "bridge_smoke", "L01", PROMPT, REF); show("fire_still", res)
    if res.get("status") != "committed":
        return f"FAIL fire_still status {res.get('status')}"
    if res.get("aspect_checked") is False:
        print("note: record carried no dims; relying on the Pillow check", flush=True)

    dl = svc.download(DIR, res["media_ids"]); show("download", dl)
    files = [Path(f) for f in dl["written"] + dl["skipped"]]
    if not files or not all(f.exists() for f in files):
        return f"FAIL downloaded file missing {files}"
    size = Image.open(files[0]).size; show("pillow_size", list(size))
    if size != (768, 1376):
        return f"FAIL size {size} != (768, 1376)"

    svc.page.balance = None
    after = wait_balance(svc); show("credits_after", after)
    if after is None:
        print("WARNING: credit balance unreadable AFTER the fire - check the Flow window", flush=True)
    if before is not None and after is not None and after != before:
        return f"FAIL credits changed {before} -> {after}"
    return "PASS"


if __name__ == "__main__":
    try:
        verdict = main()
    except Exception as e:  # noqa: BLE001 - the smoke reports, never retries
        traceback.print_exc()
        verdict = f"FAIL {type(e).__name__}: {e}"
    print(f"SMOKE: {verdict}", flush=True)
    sys.exit(0 if verdict == "PASS" else 1)
