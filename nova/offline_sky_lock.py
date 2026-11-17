"""Portable, non-blocking, cross-process exclusive lock on a file.

Used to elect the single offline-sky job runner per instance (see
nova/offline_sky_jobs.py). Standard library only, no new dependency:

  * POSIX (Linux, macOS): ``fcntl.flock(LOCK_EX | LOCK_NB)``. flock locks belong
    to the open file description, so two handles opened separately conflict
    even inside one process, and the kernel drops the lock when the holder dies.
  * Windows: ``msvcrt.locking(LK_NBLCK)`` on the first byte. Released by the OS
    when the handle is closed or the process exits.

Unlike nova.helpers.try_acquire_file_lock this never returns an *unlocked*
handle: if no locking primitive works the caller does not get the lock, so two
runners can never both believe they own it.

This module deliberately imports nothing from ``nova`` so it can be loaded on
its own (tests run it in real subprocesses).
"""

import os

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

try:  # Windows
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


def try_lock(path: str):
    """Exclusive non-blocking lock on ``path``; the open handle, or None if held."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+b")
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        elif _msvcrt is not None:  # pragma: no cover - Windows
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - no primitive at all: refuse, never fake it
            raise OSError("no file-locking primitive available")
    except OSError:
        fh.close()
        return None
    return fh


def unlock(fh) -> None:
    """Release and close a handle from try_lock. Never raises."""
    if fh is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        elif _msvcrt is not None:  # pragma: no cover - Windows
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass
