import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    edge_exe: Path = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    cdp_port: int = 9333
    profile_dir: Path = HERE / ".edge-profile"
    flow_url: str = "https://flow.google.com/"
    commit_timeout_s: int = 90
    work_root: Path | None = None  # the path fence (guards.check_path); unset = every path refused
    denied_roots: tuple[Path, ...] = ()  # refused even when under work_root


def _coerce(key: str, value):
    if key in ("edge_exe", "profile_dir", "work_root"):
        return Path(value)
    if key == "denied_roots":
        return tuple(Path(x) for x in value)
    return value


def load_config(path: Path | None = None) -> Config:
    path = path or HERE / "config.toml"
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {f.name: f.type for f in fields(Config)}
    bad = set(raw) - set(known)
    if bad:
        raise ValueError(f"unknown config keys: {sorted(bad)}")
    return Config(**{k: _coerce(k, v) for k, v in raw.items()})
