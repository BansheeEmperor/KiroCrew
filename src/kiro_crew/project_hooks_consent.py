"""Whether a checkout's own agent-spec hooks run in a KAS session, per folder.

A project-only agent (one ``<folder>/.kiro/agents`` declares and the user level
does not) carries ``hooks``: commands from a cloned folder. They run only when
:func:`hooks_enabled` says so for that folder, decided in this order:

1. The owner's own choice in Crew, ``on`` or ``off``. A Crew ``off`` beats
   kiro-cli's trust.
2. Otherwise kiro-cli's own "Trust this workspace?" record
   (:func:`kiro_crew.kiro_cli_workspace_trust.kiro_cli_trusts_folder`), read
   only. When kiro-cli later drops it, the hooks stop again.
3. Otherwise off, which is every folder today.

The record is ``project_hooks_consent.json`` on the keystone floor
(``config.loader_locations.project_hooks_consent_path``): neither the agent's file
tools nor its sandboxed shell can write it. The writers are the owner-gated
``/api/project-hooks/consent`` handler (:func:`set_choice`) and the gateway's
once-per-folder notice mark (:func:`mark_kiro_cli_notice_shown`). Every read
fails soft to "no choice".
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from kiro_crew.config.loader_locations import project_hooks_consent_path

logger = logging.getLogger(__name__)

ON = "on"
OFF = "off"
SOURCE_CREW = "crew"
SOURCE_KIRO_CLI = "kiro_cli"

#: Bound on the remembered folders, so the record cannot grow without end. A full
#: record refuses a new folder rather than drop an old choice.
_MAX_FOLDERS = 1000

_LOCK = threading.Lock()


class StoreUnreadable(OSError):
    """The record exists but cannot be read, so no writer may rewrite it."""


class StoreFull(Exception):
    """A new folder's choice was refused: the record is at :data:`_MAX_FOLDERS`."""


def folder_key(folder: str | os.PathLike[str] | None) -> str | None:
    """The key a folder is stored under: its resolved path, case kept.

    Never case-folded: on Windows a directory can be case-sensitive, so ``Repo`` and
    ``repo`` may be two folders, and a folded key would let one's choice cover the
    other. ``realpath`` already returns the on-disk spelling, so one folder spelled
    two ways still lands on one key.
    """
    if not folder:
        return None
    try:
        resolved = os.path.realpath(os.fspath(folder), strict=True)
    except (OSError, ValueError):
        return None
    if not os.path.isdir(resolved):
        return None
    return resolved


def _load() -> dict[str, Any]:
    """The whole record, through the ONE reader every caller uses.

    Only a missing file reads as empty (no Crew choice anywhere). Anything else
    that is not exactly the shape this module writes -- an unreadable file, bad
    UTF-8 or JSON, a non-object, a ``folders`` that is not an object of ``on`` /
    ``off``, a ``kiro_cli_notified`` that is not a list of strings -- raises
    :class:`StoreUnreadable`. Readers answer that with hooks off and never fall
    back to kiro-cli's trust; writers abandon the write, so a damaged record is
    never replaced by a guess.
    """
    path = project_hooks_consent_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise StoreUnreadable(f"project-hooks consent store cannot be read: {exc}") from exc
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise StoreUnreadable("project-hooks consent store is not valid JSON") from exc
    if not isinstance(doc, dict):
        raise StoreUnreadable("project-hooks consent store is not an object")
    folders = doc.get("folders", {})
    if not isinstance(folders, dict) or not all(
        isinstance(k, str) and v in (ON, OFF) for k, v in folders.items()
    ):
        raise StoreUnreadable("project-hooks consent store has an unknown folder entry")
    notified = doc.get("kiro_cli_notified", [])
    if not isinstance(notified, list) or not all(isinstance(k, str) for k in notified):
        raise StoreUnreadable("project-hooks consent store has an unknown notice entry")
    return doc


#: :func:`crew_choice` for a record that exists but cannot be trusted: hooks off.
UNREADABLE = "unreadable"


def crew_choice(folder: str | os.PathLike[str] | None) -> str | None:
    """The owner's own choice for *folder*: ``"on"``, ``"off"``, ``None`` (no choice),
    or :data:`UNREADABLE` when the record exists but cannot be read."""
    key = folder_key(folder)
    if key is None:
        return None
    try:
        doc = _load()
    except StoreUnreadable:
        logger.warning("project-hooks consent store is unreadable; project hooks stay off")
        return UNREADABLE
    return doc.get("folders", {}).get(key)


def hooks_enabled(folder: str | os.PathLike[str] | None) -> tuple[bool, str]:
    """Whether *folder*'s project-only spec hooks run, and who decided it.

    ``(True, "crew")``, ``(True, "kiro_cli")``, ``(False, "crew")`` for an owner
    turn-off or a Crew record that cannot be read (kiro-cli is not consulted then),
    or ``(False, "")`` when nobody turned them on. Read-only.
    """
    choice = crew_choice(folder)
    if choice == ON:
        return True, SOURCE_CREW
    if choice is not None:  # OFF, or UNREADABLE: never fall back to kiro-cli
        return False, SOURCE_CREW
    from kiro_crew.kiro_cli_workspace_trust import kiro_cli_trusts_folder

    if folder and kiro_cli_trusts_folder(folder):
        return True, SOURCE_KIRO_CLI
    return False, ""


def _write(doc: dict[str, Any]) -> None:
    from kiro_crew.atomic_write import atomic_write

    atomic_write(
        project_hooks_consent_path(),
        json.dumps(doc, indent=2, sort_keys=True),
        restrict_to_owner=True,
    )


def set_choice(folder: str | os.PathLike[str], enabled: bool) -> str:
    """Record the owner's ``on``/``off`` for *folder*; returns its key.

    Raises ``ValueError`` for a folder that is not a directory, :class:`StoreFull`,
    and ``OSError`` (:class:`StoreUnreadable` included) when the record cannot be read.
    """
    key = folder_key(folder)
    if key is None:
        raise ValueError("folder is not an existing directory")
    with _LOCK:
        doc = _load()
        folders = dict(doc.get("folders", {}))
        if key not in folders and len(folders) >= _MAX_FOLDERS:
            # Never evict: a dropped Crew turn-off would hand the folder back to
            # kiro-cli's trust and start its hooks again.
            raise StoreFull("the project-hooks record holds the most folders it can")
        folders[key] = ON if enabled else OFF
        doc["folders"] = folders
        _write(doc)
    return key


def mark_kiro_cli_notice_shown(folder: str | os.PathLike[str]) -> bool:
    """Mark the "running because of kiro-cli" notice shown for *folder*.

    ``True`` only the first time, so the caller shows the notice once per folder.
    A failed write answers ``False`` (no notice) rather than raising.
    """
    key = folder_key(folder)
    if key is None:
        return False
    try:
        with _LOCK:
            doc = _load()
            shown = list(doc.get("kiro_cli_notified", []))
            if key in shown:
                return False
            doc["kiro_cli_notified"] = (shown + [key])[-_MAX_FOLDERS:]
            _write(doc)
    except Exception:  # noqa: BLE001 - a notice mark must never fail a turn
        logger.debug("could not record the project-hooks notice mark", exc_info=True)
        return False
    return True
