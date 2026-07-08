"""Prevents two `frontforge run` processes from racing on the same
project's .harness/state.json — each writes state without any inter-process
coordination, so running two at once corrupts/overwrites the other's
progress. This is a plain PID-file lock: good enough for "don't run this
twice by accident," not a distributed lock."""

from __future__ import annotations

import os
from pathlib import Path


class RunLockError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self._acquired = False

    def acquire(self) -> None:
        if self.path.exists():
            try:
                existing_pid = int(self.path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                existing_pid = None

            if existing_pid is not None and existing_pid != os.getpid() and _pid_alive(existing_pid):
                raise RunLockError(
                    f"another `frontforge run` is already active on this project "
                    f"(pid {existing_pid}). Stop it first, or delete {self.path} "
                    f"if you're sure it's a stale lock from a crashed process."
                )
            # Either it's our own pid (re-entrant) or the owning process is
            # dead — safe to reclaim.

        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._acquired = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
