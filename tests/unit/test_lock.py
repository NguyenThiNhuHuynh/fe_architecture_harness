import os

import pytest

import frontforge.core.lock as lock_module
from frontforge.core.lock import RunLock, RunLockError


def test_acquire_writes_own_pid_and_release_removes_file(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock = RunLock(lock_path)

    lock.acquire()
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())

    lock.release()
    assert not lock_path.exists()


def test_acquire_blocks_when_another_live_process_holds_it(tmp_path, monkeypatch):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(lock_module, "_pid_alive", lambda pid: True)

    with pytest.raises(RunLockError):
        RunLock(lock_path).acquire()


def test_acquire_reclaims_a_stale_lock_from_a_dead_process(tmp_path, monkeypatch):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(lock_module, "_pid_alive", lambda pid: False)

    lock = RunLock(lock_path)
    lock.acquire()  # must not raise — the old holder is dead
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_is_reentrant_within_the_same_process(tmp_path):
    lock_path = tmp_path / "run.lock"
    RunLock(lock_path).acquire()

    second = RunLock(lock_path)
    second.acquire()  # same pid already owns it — must not raise
    second.release()
    assert not lock_path.exists()


def test_context_manager_releases_on_exception(tmp_path):
    lock_path = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with RunLock(lock_path):
            raise ValueError("boom")
    assert not lock_path.exists()
