# scripts/capture.py - records every batchexecute response (plus other flow.google.com /
# googleapis XHR/fetch responses, prefixed "other_") while free actions run in the dedicated
# Edge. Stops on Ctrl+C or when tests/fixtures/raw/STOP exists. Raw captures are gitignored.
import pathlib, re, time
from flow_mcp.browser import Browser
from flow_mcp.config import Config
from flow_mcp.netwatch import BATCH_URL_PART

out = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw"
out.mkdir(parents=True, exist_ok=True)
stop = out / "STOP"
stop.unlink(missing_ok=True)
b = Browser(Config())


def save(url, body, prefix=""):
    rpc = re.search(r"rpcids=([^&]+)", url)
    name = rpc.group(1).replace("%2C", "+").replace(",", "+") if rpc else "x"
    f = out / f"{prefix}{int(time.time()*1000)}_{name}.txt"
    f.write_text(url + "\n" + body, encoding="utf-8")
    print("saved", f.name, len(body), flush=True)


def other(resp):
    u = resp.url
    if BATCH_URL_PART in u or resp.request.resource_type not in ("xhr", "fetch"):
        return
    if "flow.google.com" not in u and "googleapis.com" not in u:
        return
    try:
        save(u, resp.text(), prefix="other_")
    except Exception:
        pass


b.on_response(save)
p = b.page()
p.on("response", other)
print("attached", p.url, flush=True)
try:
    while not stop.exists():
        p.wait_for_timeout(1000)
except KeyboardInterrupt:
    pass
finally:
    b.close_connection()
    print("stopped", flush=True)
