"""The steering files one chat has switched off: resolved paths, never bodies.

One JSON file under the data home maps a chat's session key to the sorted
resolved paths it excludes. Context assembly reads :func:`steering_off_for` once
per turn and hands the set to the two writers Crew owns (the Claude Code
``[Steering resources]`` load and the folder steering section), which skip those
documents. A chat with nothing switched off reads an empty set, so its turn text
is unchanged. The dashboard's per-chat steering route writes it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_FILE_NAME = "chat_steering_off.json"
# One gateway process writes this file; the lock keeps two toggles of one
# process from losing each other's read-modify-write, and keeps a reader from
# seeing a rewrite half done.
_WRITE_LOCK = threading.Lock()


def _store_path() -> Path:
    from kiro_crew.config.paths import data_home

    return data_home() / _FILE_NAME


class SteeringOffStoreUnreadable(OSError):
    """The store exists but cannot be read, so a write would erase it.

    *text* is what the file held, empty when it could not be read at all.
    """

    def __init__(self, message: str, text: str = "") -> None:
        super().__init__(message)
        self.text = text


# The last record this process read or wrote whole, per store path, held under
# _WRITE_LOCK. A record that later fails to parse reads as this, never as "nothing
# switched off": an off switch must fail closed.
_LAST_GOOD: dict[str, dict[str, list[str]]] = {}
# Store paths whose unreadable record was already logged, so a turn loop logs once.
_WARNED_UNREADABLE: set[str] = set()


def _read_all() -> dict[str, list[str]]:
    """The whole store: ``{}`` when it is missing.

    An existing store that cannot be read raises :class:`SteeringOffStoreUnreadable`
    (carrying its text when it had any), so a switch never overwrites every other
    chat's record with an empty one and a reader can fail closed. An empty file is
    a valid empty store (the sandbox pre-creates the leaf as ``{}``).
    """
    try:
        text = _store_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SteeringOffStoreUnreadable(str(exc)) from exc
    try:
        # The first JSON value wins and any bytes after it are ignored: a rewrite
        # writes the whole new record at offset 0 before trimming, so a gateway
        # killed between the two leaves the new record followed by old bytes.
        body = text.lstrip()
        raw = json.JSONDecoder().raw_decode(body)[0] if body else {}
    except ValueError as exc:
        raise SteeringOffStoreUnreadable("the store is not valid JSON", text) from exc
    if not isinstance(raw, dict):
        raise SteeringOffStoreUnreadable("the store is not a JSON object", text)
    return {
        key: [p for p in paths if isinstance(p, str) and p]
        for key, paths in raw.items()
        if isinstance(key, str) and isinstance(paths, list)
    }


def _every_string_in(text: str) -> frozenset[str]:
    """Every JSON string literal in *text*, a torn record's paths among them."""
    found = set()
    for literal in re.findall(r'"(?:[^"\\]|\\.)*"', text):
        try:
            found.add(json.loads(literal))
        except ValueError:
            continue
    return frozenset(s for s in found if s)


def steering_off_for(session_key: str | None) -> frozenset[str]:
    """The resolved paths *session_key* switched off; empty for none.

    A record that cannot be read fails closed: it reads as the last record this
    process held whole, and in a process that never held one, every path string
    left in the file counts as switched off for every chat. Either is logged once.
    """
    if not session_key:
        return frozenset()
    where = str(_store_path())
    # Under the write lock: an in-place rewrite briefly holds invalid JSON.
    with _WRITE_LOCK:
        try:
            store = _read_all()
        except SteeringOffStoreUnreadable as exc:
            if where not in _WARNED_UNREADABLE:
                _WARNED_UNREADABLE.add(where)
                logger.warning(
                    "steering-off store unreadable (%s); keeping switched-off files off", exc
                )
            last = _LAST_GOOD.get(where)
            if last is not None:
                return frozenset(last.get(session_key, ()))
            # shortcut: with no record held in memory (a restart onto a torn file),
            # only paths still in the file stay off; one the tear overwrote, or a
            # file that cannot be read at all, comes back on until the owner fixes
            # it, which the next switch's 409 store_unreadable makes visible.
            # Upgrade to a second committed generation if that proves too weak.
            return _every_string_in(exc.text)
        _LAST_GOOD[where] = store
        _WARNED_UNREADABLE.discard(where)
        return frozenset(store.get(session_key, ()))


def set_steering_off(session_key: str, path: str, off: bool) -> frozenset[str]:
    """Switch one resolved *path* off (or back on) for *session_key*; the new set."""
    with _WRITE_LOCK:
        target = _store_path()
        store = _read_all()
        _LAST_GOOD[str(target)] = {key: list(paths) for key, paths in store.items()}
        current = set(store.get(session_key, ()))
        if off:
            current.add(path)
        else:
            current.discard(path)
        if current:
            store[session_key] = sorted(current)
        else:
            store.pop(session_key, None)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_in_place(target, (json.dumps(store, indent=1, sort_keys=True) + "\n").encode())
        _LAST_GOOD[str(target)] = store
        return frozenset(current)


def _write_in_place(path: Path, data: bytes) -> None:
    """Rewrite *path* on its own inode, as ``shared_crew.preview.write_stored`` does.

    Why in place and not a rename: the leaf is a keystone sealed read-only in the
    sandbox (``sandbox._CREW_READONLY_LEAVES``). A running Linux sandbox bind-mounts
    this inode at the name, and a rename over a mount point in another namespace
    detaches that mount and would leave the new file writable from inside. So an
    existing record is rewritten on its inode, and an absent one is created with
    ``O_EXCL``. A symlink, reparse point or
    hard-linked record is refused, never followed. Callers hold ``_WRITE_LOCK``,
    which :func:`steering_off_for` also takes, so no reader sees the rewrite half done.

    The space a longer record needs is claimed first, as trailing spaces JSON
    ignores, so a full disk fails that step with the old record still whole. The
    new record then goes in whole at offset 0 before the trim, and the reader stops
    at the first JSON value, so a gateway killed before the trim keeps the new one.
    A kill inside the overwrite, a power loss before ``fsync``, or a copy-on-write
    filesystem refusing it can still tear the record; :func:`steering_off_for` then
    fails closed, and the next switch is refused until the file is fixed.
    """
    base = os.O_WRONLY | getattr(os, "O_BINARY", 0)
    base |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, base | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise OSError(f"refusing to rewrite {path.name}: it is a link") from None
        fd = os.open(path, base)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError(f"refusing to rewrite {path.name}: not a single-link regular file")
        named = os.lstat(path)
        if (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino):
            raise OSError(f"refusing to rewrite {path.name}: it changed while opening")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        if len(data) > st.st_size:
            _write_all(fd, b" " * (len(data) - st.st_size), st.st_size)
            os.fsync(fd)
        _write_all(fd, data, 0)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes, offset: int) -> None:
    """Write all of *data* at *offset*, the way a short ``os.write`` needs."""
    os.lseek(fd, offset, os.SEEK_SET)
    while data:
        data = data[os.write(fd, data) :]
