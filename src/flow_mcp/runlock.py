"""One writer per project: <project>/_flow_run.lock = {pid, created, nonce}.

The MCP server and flow_fire.py are separate processes that both mutate the run state, so every
fire and sweep holds this lock. A lock held by anyone else - another process, or another RunLock
object in THIS process - is RUN_LOCKED. It is reclaimable only when stale: older than the TTL or
its pid is no longer alive; reclaim renames it to a tombstone, never overwrites it. Release deletes the lock only when it is still ours (pid + nonce)."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

from .guards import FlowError

LOCK_NAME = "_flow_run.lock"
TAB_LOCK_NAME = "_tab.lock"  # <profile_dir>/_tab.lock: one driver of the shared Flow tab
TTL_S = 7200
LINK_TRIES = 3  # bounded create retries (lock vanished / stale lock reclaimed)


def pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # ACCESS_DENIED: exists, not ours to query
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True  # cannot tell: fail closed (treat as alive)
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RunLock:
    def __init__(self, project_dir: Path, ttl_s: int = TTL_S, name: str = LOCK_NAME,
                 what: str = "another run holds this project"):
        self.name, self.what = name, what
        self.path = Path(project_dir) / name
        self.ttl_s = ttl_s
        self.nonce = uuid.uuid4().hex
        self._depth = 0

    def _read(self) -> dict | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {}  # unreadable: judged by file age only

    def _stale(self, held: dict) -> bool:
        created = held.get("created")
        if not isinstance(created, (int, float)):
            try:
                created = self.path.stat().st_mtime
            except OSError:
                return False
        if time.time() - created > self.ttl_s:
            return True
        return "pid" in held and not pid_alive(held["pid"])

    def _body(self) -> str:
        return json.dumps({"pid": os.getpid(), "created": time.time(), "nonce": self.nonce})

    def _write_tmp(self) -> Path:
        tmp = self.path.with_name(f"{self.name}.{os.getpid()}.{self.nonce[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._body())
        return tmp

    def _mine(self) -> bool:
        held = self._read()
        return bool(held) and held.get("pid") == os.getpid() and held.get("nonce") == self.nonce

    def _locked(self, held: dict, why: str | None = None) -> FlowError:
        why = why or self.what
        return FlowError("RUN_LOCKED", f"{held.get('pid')} since {held.get('created')} ({self.path}) - {why}")

    def _reclaim(self, held: dict) -> None:
        """Move a lock judged stale out of the way WITHOUT ever overwriting the lock path: rename it
        to a unique tombstone (only one renamer can win), then check the tombstone is the lock we
        judged. If someone replaced it in between, put theirs back and refuse."""
        tomb = self.path.with_name(f"{self.name}.{self.nonce}.reclaim")
        try:
            os.rename(self.path, tomb)
        except FileNotFoundError:
            return  # another reclaimer (or the holder's release) got there first: retry the create
        try:
            got = json.loads(tomb.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        if not isinstance(got, dict):
            got = {}
        if (got.get("pid"), got.get("created")) != (held.get("pid"), held.get("created")):
            try:
                os.link(tomb, self.path)  # restore the lock we must not take
            except FileExistsError:
                pass  # a third party already holds the path; theirs stands
            finally:
                tomb.unlink(missing_ok=True)
            raise self._locked(got, "lock changed while reclaiming")
        tomb.unlink(missing_ok=True)

    def acquire(self) -> None:
        if self._depth:  # same object re-entering: already ours
            self._depth += 1
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._write_tmp()
        try:
            for _ in range(LINK_TRIES):
                try:
                    os.link(tmp, self.path)  # atomic create-if-absent with the full body in it
                    break
                except FileExistsError:
                    held = self._read()
                    if held is None:
                        continue  # released between link and read: just try the create again
                    if not self._stale(held):
                        raise self._locked(held) from None
                    self._reclaim(held)
            else:
                raise self._locked(self._read() or {}, f"could not create the lock in {LINK_TRIES} tries")
        finally:
            tmp.unlink(missing_ok=True)
        self._depth = 1

    def assert_held(self) -> None:
        """Re-read the lock file: RUN_LOCKED unless it is still ours (pid + nonce). Called right before
        a paid click / run-state write, so a lock taken over by another holder stops the call."""
        if not self._depth or not self._mine():
            raise self._locked(self._read() or {}, "lock is no longer ours - not proceeding")

    def release(self) -> None:
        if not self._depth:
            return
        self._depth -= 1
        if self._depth:
            return
        if self._mine():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def TabLock(profile_dir: Path, ttl_s: int = TTL_S) -> RunLock:
    """Browser-wide lock on the ONE shared Flow tab (attach-only peers: MCP server, flow_fire).
    Same pid/nonce/TTL/tombstone mechanics as RunLock. Lock order is always tab -> run."""
    return RunLock(profile_dir, ttl_s, name=TAB_LOCK_NAME,
                   what="another process is driving the shared Flow tab")
