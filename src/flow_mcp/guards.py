"""Hard rules as refusals. Every refusal is a FlowError with a stable code Claude can act on."""
import os
import re
from pathlib import Path

from .config import load_config

CODES = {"PATH_REFUSED", "WRONG_MODEL", "OUT_OF_CREDITS", "CAPTCHA", "LOGIN_REQUIRED",
         "UNCONFIRMED", "UI_CHANGED", "SETTINGS_MISMATCH", "REF_NOT_ATTACHED", "BROWSER_DOWN",
         "WRONG_ASPECT", "MODEL_UNKNOWN", "NO_MEDIA_URL", "LINT_REQUIRED", "RUN_LOCKED",
         "ALREADY_COMMITTED", "SAME_PROMPT_PENDING", "IN_FLIGHT",
         "LOST_PENDING_RELEASE"}
# The path fence, read once from config.toml: every local path the bridge touches must sit under
# work_root (unset = every path is refused) and outside each of denied_roots.
_cfg = load_config()
ROOT: Path | None = _cfg.work_root
DENIED: tuple[Path, ...] = _cfg.denied_roots
MAX_REFS = 3  # multi-ref stills: 1-3 refs in the prompt bar (FlowPage + Service)
_CLIP = re.compile(r"^abra_(r2v|i2v)_")
_UNSAFE_NAME = re.compile(r"[\\/:]")  # separators or a drive letter


class FlowError(Exception):
    def __init__(self, code: str, detail: str):
        assert code in CODES, code
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _under(low: str, root: Path) -> bool:
    base = str(root).lower().rstrip("\\")
    return low == base or low.startswith(base + "\\")


def check_path(p: str | Path) -> Path:
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    r = Path(os.path.realpath(s))  # resolves junctions/symlinks, so a link cannot smuggle a path out
    if str(r).startswith("\\\\?\\"):  # realpath can hand back an extended-length path
        r = Path(str(r)[4:])
    low = str(r).lower()
    if ROOT is None:
        raise FlowError("PATH_REFUSED", "work_root is not set in config.toml")
    for d in DENIED:
        if _under(low, d):
            raise FlowError("PATH_REFUSED", f"{r} is under the denied root {d}")
    if not _under(low, ROOT):
        raise FlowError("PATH_REFUSED", f"{r} is outside {ROOT}")
    return r


def check_clip_model(name: str) -> None:
    if not _CLIP.match(name or ""):
        raise FlowError("WRONG_MODEL", f"committed clip model is {name!r}, expected abra_r2v_/abra_i2v_")


def check_name(name: str, field: str) -> str:
    """A bare file-name part (clip_name, slug, line): no separators, no drive letter, no '..'."""
    n = name if isinstance(name, str) else ""
    if not n.strip() or _UNSAFE_NAME.search(n) or ".." in n:
        raise FlowError("PATH_REFUSED", f"{field} {name!r} must be a bare name (no / \\ : or ..)")
    return n
