"""Nothing Kiro Crew opens on ``memory.db`` may drop the gateway's SQLite locks on it.

On POSIX, closing ANY descriptor of a file releases every record lock the process
holds on that file. The gateway holds ``<data home>/memory.db`` open through its memory
store, so a plain open and close of it inside the gateway (the dashboard file viewer
reading a ``memory.db`` path) leaves the store writing with no lock another process can
see. That process's next close may then checkpoint and delete the ``-wal`` the store
still writes through, and the database splits. A command that only reads must not hold
a writable descriptor on the file either: only a writable descriptor can take the
exclusive lock that close needs.

Every test uses a real SQLite file under the per-test data home. Lock state is read by
a separate process, because a process never sees its own record locks as a conflict.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dashboard_owner_helpers import as_owner
from member_memory_helpers import forget_declared_stores, write_member_home

from kiro_crew import cli_commands as cc
from kiro_crew import memory_schema
from kiro_crew.config.loader import config_dir
from kiro_crew.dashboard import handlers
from kiro_crew.dashboard.handlers import files
from kiro_crew.memory_stores import resolve_store_path
from kiro_crew.testing.wait import FALLBACK_TIMEOUT_SECS
from kiro_crew.vector_memory import VectorMemoryStore, open_member_database

fcntl = pytest.importorskip("fcntl", reason="POSIX record locks")

#: SQLite's shared-lock range on the database file (PENDING_BYTE + 2, 510 bytes),
#: which a WAL connection holds while it is open.
_DB_SHARED = (0x40000002, 510)
#: The ``-shm`` byte every connection of a WAL database holds a read lock on.
_SHM_DMS = (128, 1)

#: Exits 0 when another process holds a lock in the range: an exclusive lock there
#: cannot be taken. A lock it does get is released when it exits.
_PROBE = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, int(sys.argv[3]), int(sys.argv[2]))
except OSError:
    sys.exit(0)
sys.exit(1)
"""

_LESSON = "always run the build gate before opening a PR"


def _held_by_another_process(path: Path, span: tuple[int, int]) -> bool:
    start, length = span
    probe = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, str(path), str(start), str(length)],
        cwd=path.parent,
        timeout=FALLBACK_TIMEOUT_SECS,
        check=False,
    )
    return probe.returncode == 0


def _locks_held(db: Path) -> dict[str, bool]:
    return {
        "memory.db": _held_by_another_process(db, _DB_SHARED),
        "memory.db-shm": _held_by_another_process(Path(f"{db}-shm"), _SHM_DMS),
    }


@pytest.fixture()
def gateway_store(opened) -> VectorMemoryStore:
    """The gateway's memory store, open on the data home's ``memory.db``."""
    store = opened(VectorMemoryStore())
    store.init()
    store.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    assert store.get_semantic("pref.editor") is not None
    return store


@pytest.fixture()
def mock_sel():
    with patch("kiro_crew.sel.sel") as sel:
        sel.return_value = MagicMock()
        yield sel.return_value


_ENDPOINTS = {
    "/api/file-read": (handlers.api_file_read, 400),
    "/api/file-raw": (handlers.api_file_raw, 403),
    "/api/file-download": (handlers.api_file_download, 403),
    "/api/file-stream": (handlers.api_file_stream, 403),
    "/api/file-watch": (handlers.api_file_watch, 403),
}


async def _get(route: str, path: Path) -> int:
    handler, _ = _ENDPOINTS[route]
    app = web.Application()
    app.router.add_get(route, handler)
    async with TestClient(TestServer(as_owner(app))) as client:
        async with client.get(route, params={"path": str(path)}) as resp:
            return resp.status


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(_ENDPOINTS))
async def test_the_file_api_refuses_the_live_memory_db_and_keeps_its_locks(
    gateway_store, mock_sel, route: str
) -> None:
    db = config_dir() / "memory.db"
    assert _locks_held(db) == {"memory.db": True, "memory.db-shm": True}

    status = await _get(route, db)

    assert status == _ENDPOINTS[route][1]
    assert _locks_held(db) == {"memory.db": True, "memory.db-shm": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
async def test_the_file_viewer_refuses_the_live_memory_db_sidecars(
    gateway_store, mock_sel, suffix: str
) -> None:
    db = config_dir() / "memory.db"
    assert Path(f"{db}{suffix}").is_file()

    assert await _get("/api/file-read", Path(f"{db}{suffix}")) == 400
    assert _locks_held(db) == {"memory.db": True, "memory.db-shm": True}


@pytest.mark.asyncio
async def test_the_file_viewer_still_reads_another_database_in_the_data_home(
    gateway_store, mock_sel
) -> None:
    other = config_dir() / "notes.db"
    other.write_bytes(b"SQLite format 3\x00")

    assert await _get("/api/file-read", other) == 200


@pytest.mark.asyncio
async def test_the_python_grep_engine_searches_the_data_home_and_keeps_the_locks(
    gateway_store, mock_sel, monkeypatch
) -> None:
    """The engine a host without a usable ripgrep runs, walking the data home itself."""
    db = config_dir() / "memory.db"
    (config_dir() / "notes.txt").write_text("the editor is vim\n", encoding="utf-8")
    monkeypatch.setattr(files, "_grep_rg_executable", lambda: None)
    assert _locks_held(db) == {"memory.db": True, "memory.db-shm": True}

    app = web.Application()
    app.router.add_post("/api/file-grep", handlers.api_file_grep)
    state = MagicMock()
    state.file_indexes.get.return_value = None
    state.owner_id = ""
    app["state"] = state
    async with TestClient(TestServer(as_owner(app))) as client:
        async with client.post(
            "/api/file-grep", json={"root": str(config_dir()), "q": "vim"}
        ) as resp:
            status, payload = resp.status, await resp.json()

    assert status == 200, payload
    assert payload["engine"] == "python"
    assert [Path(hit["file"]).name for hit in payload["results"]] == ["notes.txt"]
    assert _locks_held(db) == {"memory.db": True, "memory.db-shm": True}


# ── read-only CLI verbs ───────────────────────────────────────────────────────


def _writable_descriptors_on(path: Path) -> list[int]:
    """This process's descriptors on *path* opened for writing."""
    target = os.stat(path)
    found = []
    for name in os.listdir("/dev/fd"):
        fd = int(name)
        try:
            if not os.path.samestat(os.fstat(fd), target):
                continue
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except OSError:
            continue
        if flags & os.O_ACCMODE != os.O_RDONLY:
            found.append(fd)
    return found


def _seed_default_store() -> Path:
    store = VectorMemoryStore()
    store.init()
    try:
        store.write_lesson(_LESSON)
        store.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    finally:
        store.close()
    return config_dir() / "memory.db"


#: A V2 member store, the kind of store `carve` exists for. It is opened through
#: member admission, which has its own read-only open.
_MEMBER = "member-alice"
_MEMBER_CREW = "finance"


def _seed_member_store(monkeypatch) -> Path:
    write_member_home(config_dir(), "alice")
    forget_declared_stores(monkeypatch)
    db = resolve_store_path(_MEMBER)
    store = open_member_database(db, member_id="alice", store_id=_MEMBER)
    try:
        facets = memory_schema.MemoryFacets(crew=_MEMBER_CREW)
        assert store.set_semantic("pref.editor", "vim", 1.0, "user_explicit", facets=facets) is None
    finally:
        store.close()
    return db


def _run_verb(verb: str, store: str | None, tmp_path: Path) -> None:
    if verb == "learn list":
        cc._learn(argparse.Namespace(learn_action="list"))
    elif verb == "memory list":
        cc._memory_cmd(argparse.Namespace(mem_action="list", store=store))
    elif verb == "memory export":
        out = str(tmp_path / "export.json")
        cc._memory_cmd(argparse.Namespace(mem_action="export", output=out, store=store))
    else:
        fields: dict[str, object] = {facet: None for facet in memory_schema.FACET_NAMES}
        fields.update(
            mem_action="carve", store=store, kind=None, count_by="crew", limit=50, offset=0
        )
        cc._memory_cmd(argparse.Namespace(**fields))


@pytest.mark.parametrize(
    ("verb", "store"),
    [
        ("learn list", None),
        ("memory list", None),
        ("memory export", None),
        ("memory carve", None),
        ("memory export", _MEMBER),
        ("memory carve", _MEMBER),
    ],
)
def test_a_read_only_verb_opens_memory_db_without_a_writable_descriptor(
    tmp_path: Path, monkeypatch, capsys, verb: str, store: str | None
) -> None:
    db = _seed_member_store(monkeypatch) if store else _seed_default_store()
    seen: list[list[int]] = []
    close = VectorMemoryStore.close

    def _close_after_looking(self: VectorMemoryStore) -> None:
        if self._db is not None:
            seen.append(_writable_descriptors_on(db))
        close(self)

    monkeypatch.setattr(VectorMemoryStore, "close", _close_after_looking)
    try:
        _run_verb(verb, store, tmp_path)
    except SystemExit:
        # `memory carve` on the default store refuses: it has no facets.
        assert (verb, store) == ("memory carve", None)

    assert seen == [[]]
    out = capsys.readouterr().out
    if verb == "learn list":
        assert _LESSON in out
    elif verb == "memory list":
        assert "pref.editor: vim" in out
    elif verb == "memory export":
        exported = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
        assert "pref.editor" in {row["key"] for row in exported["semantic"]}
    elif store:
        assert f"{_MEMBER_CREW}: 1" in out


@pytest.mark.parametrize("sidecars", ["already there", "created by the read"])
@pytest.mark.parametrize("verb", ["learn list", "memory list", "memory export", "memory carve"])
def test_a_read_only_verb_still_restricts_an_existing_memory_db_to_its_owner(
    tmp_path: Path, verb: str, sidecars: str
) -> None:
    db = _seed_default_store()
    wal, shm = Path(f"{db}-wal"), Path(f"{db}-shm")
    if sidecars == "created by the read":
        # The seeding close emptied the `-wal` into the database, so a restored or
        # copied `memory.db` without its sidecars loses nothing here.
        wal.unlink(missing_ok=True)
        shm.unlink(missing_ok=True)
    loosened = [p for p in (db, wal, shm) if p.exists()]
    for path in loosened:
        os.chmod(path, 0o644)
    # Loosened on purpose: the read verb must restrict the data home back to 0700.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(db.parent, 0o755)

    try:
        _run_verb(verb, None, tmp_path)
    except SystemExit:
        # `memory carve` on the default store refuses: it has no facets.
        assert verb == "memory carve"

    # A WAL reader opens the `-wal` and keeps its index in the `-shm`, so the read
    # itself made both if the seeding left none, and they come out owner-only too.
    family = (db, wal, shm)
    assert [p.name for p in family if not p.exists()] == []
    assert {str(p): stat.S_IMODE(os.stat(p).st_mode) for p in family} == {
        str(p): 0o600 for p in family
    }
    assert stat.S_IMODE(os.stat(db.parent).st_mode) == 0o700


def test_a_read_of_a_file_without_the_schema_still_upgrades_it(capsys) -> None:
    db = config_dir() / "memory.db"
    db.touch()

    cc._learn(argparse.Namespace(learn_action="list"))

    assert "No lessons." in capsys.readouterr().out
    store = VectorMemoryStore()
    store.init_for_read()
    try:
        assert store.get_lessons() == []
        assert store._schema_steps([]) == []
    finally:
        store.close()
