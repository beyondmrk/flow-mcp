"""Lint stamp: Creatives/_flow_lint.json proves the manifest was linted before a PAID clip fires.

A clip may fire only when the stamp's manifest_sha256 equals the sha256 of the manifest's current
bytes AND the clip's line is in the stamp with status PASS or WARN. Anything else (no stamp, an
unreadable stamp, an edited manifest, a missing line, FAIL, an unknown status) is LINT_REQUIRED.
A line carries prompt_sha256 (prompt_sha of the exact text fire_clip sends). When the caller passes
its prompt (every clip fire does), a missing or different prompt_sha256 is LINT_REQUIRED.
Stills never check the stamp."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .guards import FlowError

STAMP_NAME = "_flow_lint.json"
FIREABLE = {"PASS", "WARN"}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prompt_sha(text: str) -> str:
    """sha256 of the exact prompt text (UTF-8) - what a stamp line's prompt_sha256 holds."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp_path(project_dir: Path) -> Path:
    return Path(project_dir) / "Creatives" / STAMP_NAME


def manifest_for(project_dir: Path) -> Path:
    """The single Creatives/*.json (not starting with '_') whose content is an object with a
    `shots` list. None or several -> LINT_REQUIRED (never guess which manifest was meant)."""
    found = []
    for f in sorted((Path(project_dir) / "Creatives").glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get("shots"), list):
            found.append(f)
    if len(found) != 1:
        names = [f.name for f in found]
        raise FlowError("LINT_REQUIRED", f"need exactly one Creatives/*.json manifest with shots, "
                        f"found {len(found)} {names} - lint the manifest first")
    return found[0]


def write(project_dir: Path, manifest_path: Path, lines: dict[str, dict]) -> Path:
    out = stamp_path(project_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {"manifest_sha256": _sha(manifest_path),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lines": lines}
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(body, fh, indent=2)
    os.replace(tmp, out)
    return out


def check(project_dir: Path, manifest_path: Path, line: str, prompt: str | None = None) -> None:
    p = stamp_path(project_dir)
    if not p.exists():
        raise FlowError("LINT_REQUIRED", f"no lint stamp {p} - lint the manifest before a paid clip")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        want, lines = raw["manifest_sha256"], raw["lines"]
        if not isinstance(lines, dict):
            raise TypeError("lines")
    except (OSError, ValueError, KeyError, TypeError):
        raise FlowError("LINT_REQUIRED", f"lint stamp {p} is unreadable - re-lint") from None
    if want != _sha(manifest_path):
        raise FlowError("LINT_REQUIRED", f"{Path(manifest_path).name} changed since it was linted - re-lint")
    entry = lines.get(line)
    if not isinstance(entry, dict):
        raise FlowError("LINT_REQUIRED", f"line {line!r} is not in the lint stamp - re-lint")
    status = entry.get("status")
    if status not in FIREABLE:
        raise FlowError("LINT_REQUIRED", f"line {line!r} lint status is {status!r} - fix it and re-lint")
    want_prompt = entry.get("prompt_sha256")
    if prompt is not None and not want_prompt:
        raise FlowError("LINT_REQUIRED", f"line {line!r}: stamp predates prompt binding - re-run flow_fire lint")
    if prompt is not None and prompt_sha(prompt) != want_prompt:
        raise FlowError("LINT_REQUIRED", f"line {line!r}: prompt differs from the linted prompt - re-lint")
