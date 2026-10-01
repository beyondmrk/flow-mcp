"""Sole reader/writer of <project>/_flow_run.json. Writes are atomic (tmp + replace)."""
import json
import os
import re
import time
import uuid
from pathlib import Path

from .netwatch import norm

MIN_BYTES = 10 * 1024


class RunState:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.path = self.dir / "_flow_run.json"

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"lines": {}}

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)

    def _line(self, d: dict, line: str) -> dict:
        L = d.setdefault("lines", {}).setdefault(line, {})
        L.setdefault("still", {}).setdefault("candidates", [])
        L.setdefault("clip", {}).setdefault("versions", [])
        return L

    def set_project(self, project_id: str, name: str) -> None:
        d = self._load()
        d["projectId"], d["flowProjectName"] = project_id, name
        self._save(d)

    def begin_fire(self, line: str, stage: str, prompt: str, **meta) -> str:
        """meta (e.g. slug=, clip_name=) is stored on the attempt so sweep can name results."""
        d = self._load()
        a = uuid.uuid4().hex[:12]
        d.setdefault("attempts", {})[a] = {
            "line": line,
            "stage": stage,
            "prompt": prompt,
            "prompt_norm": norm(prompt),
            "fired_at": time.time(),
            "status": "pending",
            **meta,
        }
        self._save(d)
        return a

    def commit(self, attempt_id: str, media_ids: list[str], model: str | None, **extra) -> None:
        d = self._load()
        at = d["attempts"][attempt_id]
        at.update(status="committed", media_ids=media_ids, model=model, **extra)
        self._save(d)

    def extend(self, attempt_id: str, media_ids: list[str]) -> None:
        """Top up a partial commit with outputs that landed after its commit window."""
        d = self._load()
        at = d["attempts"][attempt_id]
        at["media_ids"] = at.get("media_ids", []) + [m for m in media_ids if m not in at.get("media_ids", [])]
        at["partial"] = len(at["media_ids"]) < at.get("expected", len(at["media_ids"]))
        self._save(d)

    def queue(self, attempt_id: str, accepted_by: str) -> None:
        """A queued fire Flow ACCEPTED (accepted_by: "ledger" | "tile"): stays pending until settle()
        judges its media."""
        self._set(attempt_id, queued_at=time.time(), accepted_by=accepted_by)

    def note_listed(self, attempt_id: str, t: float | None = None) -> float:
        """First time a queued attempt's media was seen listed WITHOUT a URL (the stale clock)."""
        t = time.time() if t is None else t
        self._set(attempt_id, first_listed_at=t)
        return t

    def _set(self, attempt_id: str, **kv) -> None:
        d = self._load()
        d["attempts"][attempt_id].update(**kv)
        self._save(d)

    def attempts(self) -> dict:
        return self._load().get("attempts", {})

    MARKABLE = ("unconfirmed", "wrong_model", "model_unknown", "no_media_url", "not_fired", "lost")

    def mark(self, attempt_id: str, status: str, media_ids: list[str] | None = None) -> None:
        if status not in self.MARKABLE:
            raise ValueError(f"mark() accepts only {self.MARKABLE}, got {status!r}")
        d = self._load()
        d["attempts"][attempt_id]["status"] = status
        if media_ids is not None:
            d["attempts"][attempt_id]["media_ids"] = media_ids  # claimed: never re-matched by sweep
        self._save(d)

    def pending(self) -> list[dict]:
        return [
            dict(v, attempt_id=k)
            for k, v in self._load().get("attempts", {}).items()
            if v.get("status") in ("pending", "unconfirmed")
        ]

    def claimed_media(self) -> set[str]:
        """Every mediaId already tied to some attempt (committed, wrong_model, model_unknown)."""
        return {m for v in self._load().get("attempts", {}).values() for m in v.get("media_ids", [])}

    def next_still_name(self, line: str, slug: str) -> str:
        d = self._load()
        c = self._line(d, line)["still"]["candidates"]
        digits = re.sub(r"\D", "", line)
        if not digits:
            raise ValueError(f"next_still_name: line {line!r} contains no digits")
        nn = int(digits)
        base = f"claude_{slug}_line{nn:02d}"
        if not c:
            return f"{base}.png"
        used = set()
        # Extract alt numbers from both file and slot
        for x in c:
            # Try file field
            if (m := re.search(r"_alt(\d+)\.png$", x.get("file", ""))):
                used.add(int(m.group(1)))
            # Try slot field for alt numbers
            slot = x.get("slot", "")
            if slot != "canonical" and (m := re.search(r"alt(\d+)$", slot)):
                used.add(int(m.group(1)))
        # Once there's any candidate, skip canonical and find next alt
        n = 2
        while n in used:
            n += 1
        return f"{base}_alt{n}.png"

    def add_still(self, line: str, media_id: str, file: str) -> None:
        d = self._load()
        c = self._line(d, line)["still"]["candidates"]
        if any(x.get("mediaId") == media_id for x in c):
            return  # Skip duplicate
        slot = (
            "canonical"
            if not re.search(r"_alt\d+\.png$", file)
            else re.search(r"_(alt\d+)\.png$", file).group(1)
        )
        c.append({"slot": slot, "mediaId": media_id, "file": file, "downloaded": False, "picked": False})
        self._save(d)

    def next_clip_version(self, line: str) -> int:
        v = self._line(self._load(), line)["clip"]["versions"]
        return max((x["v"] for x in v if "v" in x), default=0) + 1

    def add_clip(self, line: str, v: int, media_id: str, file: str) -> None:
        d = self._load()
        versions = self._line(d, line)["clip"]["versions"]
        if any(x.get("mediaId") == media_id for x in versions):
            return  # Skip duplicate
        versions.append(
            {"v": v, "mediaId": media_id, "file": file, "downloaded": False, "qc": None, "picked": False}
        )
        self._save(d)

    def set_downloaded(self, media_id: str) -> None:
        d = self._load()
        for L in d.get("lines", {}).values():
            for x in L.get("still", {}).get("candidates", []) + L.get("clip", {}).get("versions", []):
                if x.get("mediaId") == media_id:
                    x["downloaded"] = True
        self._save(d)

    @staticmethod
    def should_skip(path: Path) -> bool:
        return path.exists() and path.stat().st_size > MIN_BYTES
