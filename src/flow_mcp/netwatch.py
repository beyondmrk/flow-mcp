"""Flow talks to POST flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute. This module
turns those responses into Media records. It never reads the DOM."""
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator

BATCH_URL_PART = "/data/batchexecute"


@dataclass(frozen=True)
class Media:
    media_id: str
    prompt: str | None
    model: str | None
    kind: str
    seen_at: float
    url: str | None = None
    workflow_id: str | None = None
    dims: tuple[int, int] | None = None  # [6][2] = [w, h]
    # ANY media URL at [6][0][13] (signed or fife) was seen: a result that really rendered. A record
    # that never carries one is treated as a failed/refused generation (service: NO_MEDIA_URL).
    has_url: bool = False


def norm(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt or "").strip().lower()


def decode_batchexecute(body: str) -> list[tuple[str, object]]:
    if not body.startswith(")]}'"):
        return []
    out: list[tuple[str, object]] = []
    for line in body[4:].splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            rows = json.loads(line)
        except json.JSONDecodeError:
            continue
        for row in rows:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and isinstance(row[2], str):
                try:
                    out.append((row[1], json.loads(row[2])))
                except json.JSONDecodeError:
                    pass
    return out


def walk_strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)


def generic_extract(rpcid: str, payload: object) -> list[Media]:
    """Placeholder-free default: knows no shape, returns nothing. Task 5 adds real extractors."""
    return []


# Field paths below are the live-verified ones in tests/fixtures/NOTES.md (2026-09-29).
# A "media record" is the list shared by Zzl0ze (project contents, payload[2][i]),
# as29s (media detail, the whole payload) and ogiZ0b (generate result, payload[0][i]):
#   rec[0] mediaId (uuid) · rec[2] workflowId · rec[6][0][7] prompt · rec[6][0][13] media URL
#   rec[6][0][14] == 3 on the observed image still · rec[6][2] [w, h]
# Kind: only the IMAGE signal is verified. Video classification is UNVERIFIED (to be confirmed
# in the first paid check), so anything without a positive image signal is "unknown".
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MODEL = re.compile(r"^(abra_|nano_banana|veo)")
_SIGNED = "https://flow-content.google/"


def _at(obj, *path):
    for i in path:
        if not isinstance(obj, list) or not isinstance(i, int) or i >= len(obj):
            return None
        obj = obj[i]
    return obj


def _url(rec) -> str | None:
    """The signed flow-content URL (ogiZ0b / as29s). Zzl0ze carries a fife URL there that
    returns 400, so it is not a fetch URL and is ignored."""
    u = _at(rec, 6, 0, 13)
    return u if isinstance(u, str) and u.startswith(_SIGNED) else None


def _kind(rec) -> str:
    u = _url(rec)
    if u and u.startswith(_SIGNED + "image/"):
        return "image"
    if _at(rec, 6, 0, 14) == 3:  # observed on the Nano Banana 2 still
        return "image"
    return "unknown"


def _prompt(rec) -> str | None:
    for path in ((6, 0, 7), (5, 6, 2, 0, 0)):
        p = _at(rec, *path)
        if isinstance(p, str) and p.strip():
            return p
    return None


def _strip(obj, drop: set[str]):
    if isinstance(obj, str):
        return None if obj in drop else obj
    if isinstance(obj, list):
        return [_strip(v, drop) for v in obj]
    return obj


def _video_record(rec, mid: str) -> Media | None:
    """LIVE 2026-09-29 (Zzl0ze, Omni 1.1 Flash): a video keeps its block at rec[7][0], not rec[6][0]:
    [7][0][7] prompt · [7][0][12] model (abra_i2v_4s ...) · [7][0][8] rendered-media (fife) URL ·
    [7][0][13] signed URL (empty in the listing) · rec[2] workflowId. The fife URL is the proof it
    rendered; bytes need a signed URL (the detail view's as29s), never this URL and never the
    Download button."""
    v = _at(rec, 7, 0)
    model = _at(v, 12)
    if not (isinstance(model, str) and model.startswith("abra_")):
        return None
    prompt = _at(v, 7) if isinstance(_at(v, 7), str) and _at(v, 7).strip() else _at(rec, 5, 1)
    wf = _at(rec, 2)
    wf = wf if isinstance(wf, str) and _UUID.match(wf) else None
    # LIVE 2026-09-30 (as29s, detail view): the SIGNED video URL arrives at [7][0][8], not [13];
    # the listing's [8] is a fife URL (other host), so the _SIGNED prefix tells them apart.
    url = next((s for s in (_at(v, 13), _at(v, 8)) if isinstance(s, str) and s.startswith(_SIGNED)), None)
    has_url = any(isinstance(x, str) and x.startswith("http") for x in (_at(v, 8), _at(v, 13)))
    return Media(mid, prompt if isinstance(prompt, str) else None, model, "video", 0.0, url, wf, None, has_url)


def _record(rec) -> Media | None:
    mid = _at(rec, 0)
    if not isinstance(mid, str) or not _UUID.match(mid):
        return None
    vid = _video_record(rec, mid)
    if vid is not None:
        return vid
    prompt = _prompt(rec)
    prompts = {s for s in (_at(rec, 6, 0, 7), _at(rec, 5, 6, 2, 0, 0)) if isinstance(s, str)}
    # model from non-prompt fields only: prompt strings (all copies) are removed before the scan
    model = next((s for s in walk_strings(_strip(rec, prompts)) if s and _MODEL.match(s)), None)
    wf = _at(rec, 2)
    wf = wf if isinstance(wf, str) and _UUID.match(wf) else None
    d = _at(rec, 6, 2)
    dims = (d[0], d[1]) if (isinstance(d, list) and len(d) == 2
                            and all(type(x) is int for x in d)) else None
    raw = _at(rec, 6, 0, 13)
    has_url = isinstance(raw, str) and raw.startswith("http")
    return Media(mid, prompt, model, _kind(rec), 0.0, _url(rec), wf, dims, has_url)


def flow_extract(rpcid: str, payload: object) -> list[Media]:
    if rpcid == "Zzl0ze":
        recs = _at(payload, 2)
    elif rpcid == "ogiZ0b":
        recs = _at(payload, 0)
    elif rpcid == "as29s":
        recs = [payload]
    else:
        return []
    if not isinstance(recs, list):
        return []
    return [m for m in (_record(r) for r in recs) if m is not None]


EXTRACTORS: list[Callable[[str, object], list[Media]]] = [generic_extract, flow_extract]


class Ledger:
    def __init__(self, extractors=None):
        self._x = extractors if extractors is not None else EXTRACTORS
        self._by_id: dict[str, Media] = {}
        self._lock = threading.Lock()

    def record(self, rpcid: str, payload: object) -> None:
        for fx in self._x:
            try:
                media_list = fx(rpcid, payload)
                for m in media_list:
                    # Skip non-Media items (e.g., None)
                    if not isinstance(m, Media):
                        continue

                    with self._lock:
                        old = self._by_id.get(m.media_id)
                        # Determine if we should upgrade
                        should_upgrade = old is None
                        if old and old.prompt is None and m.prompt:
                            should_upgrade = True
                        if old and old.model is None and m.model:
                            should_upgrade = True
                        if old and old.kind == "unknown" and m.kind != "unknown":
                            should_upgrade = True
                        if old and m.url and m.url != old.url:
                            should_upgrade = True  # fill, or refresh an expiring signed URL
                        if old and old.workflow_id is None and m.workflow_id:
                            should_upgrade = True
                        if old and old.dims is None and m.dims:
                            should_upgrade = True
                        if old and not old.has_url and m.has_url:
                            should_upgrade = True

                        if should_upgrade:
                            # On first sight, use time.time() unless extractor set positive seen_at
                            new_seen_at = m.seen_at if m.seen_at > 0 else time.time()
                            # On upgrade, always keep old.seen_at
                            if old:
                                new_seen_at = old.seen_at

                            self._by_id[m.media_id] = Media(
                                m.media_id,
                                m.prompt if m.prompt is not None else (old.prompt if old else None),
                                m.model if m.model is not None else (old.model if old else None),
                                m.kind if m.kind != "unknown" else (old.kind if old else m.kind),
                                new_seen_at,
                                m.url if m.url is not None else (old.url if old else None),
                                m.workflow_id if m.workflow_id is not None else (old.workflow_id if old else None),
                                m.dims if m.dims is not None else (old.dims if old else None),
                                m.has_url or bool(old and old.has_url),
                            )
            except Exception as e:
                fx_name = getattr(fx, "__name__", repr(fx))
                print(f"Extractor {fx_name} failed: {e}", file=sys.stderr)
                continue

    def feed(self, body: str) -> None:
        for rpcid, payload in decode_batchexecute(body):
            self.record(rpcid, payload)

    def all(self) -> list[Media]:
        with self._lock:
            return list(self._by_id.values())

    def find(self, prompt: str, since: float) -> list[Media]:
        n = norm(prompt)
        with self._lock:
            return [m for m in self._by_id.values() if m.seen_at >= since and m.prompt and norm(m.prompt) == n]
