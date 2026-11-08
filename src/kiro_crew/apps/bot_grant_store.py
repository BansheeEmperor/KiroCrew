"""Where the owner's ticked Bots for each app live: a gateway-only record.

``PUT /api/apps/{name}/bot-grants`` writes one ``<app>.json`` under
``member-records/app-bot-grants/``. ``member-records/`` is masked in every sandbox mode
and refused by the agent file tools (:mod:`kiro_crew.member_records`), so code running
in the sandbox can neither read nor write the list.

The record names the install it belongs to (the install record's ``installedAt``), so a
reinstall under the same name reaches no Bot until the owner ticks again, and uninstall
removes the record (:func:`remove_granted_bots`).

Beside it, ``<app>.scopes.json`` holds the Bot scopes (``permissions.bots``) the owner
approved, kept in the same record directory. ``manager_records._write_approved_grants``
keeps it in step with every approval write, and only a consent moment may add a scope
(:func:`write_approved_scopes`).

This module imports nothing from the ``apps.manager`` facade, so the uninstall owner can
call it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from kiro_crew import member_records, platform_compat
from kiro_crew.apps.execution import APP_NAME_RE
from kiro_crew.apps.manifest_sections import BOT_SCOPE_NAMES
from kiro_crew.atomic_write import atomic_write, fsync_dir, read_bytes_with_retry

#: The record kind under ``member-records/``; one of ``member_records.RECORD_KINDS``.
APP_BOT_GRANTS_KIND = "app-bot-grants"

#: Most Bots one app's ticked list may name. A proposal, to be confirmed.
MAX_BOT_GRANTS = 200

#: Largest record read; a ticked list of the most slugs fits well under it.
_RECORD_MAX_BYTES = 64 * 1024


def _read_bot_grants(value: Any) -> list[str]:
    """A ticked Bot list: valid member slugs, each once, capped. Anything else is none."""
    # Deferred: the member-space module is not needed to import this one.
    from kiro_crew.members import MemberSlugError, validate_slug

    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        try:
            slug = validate_slug(entry)
        except MemberSlugError:
            continue
        if slug not in out:
            out.append(slug)
    return out[:MAX_BOT_GRANTS]


def grants_path(app_name: str) -> Path:
    """``member-records/app-bot-grants/<app>.json``, containment-checked. Raises ``ValueError``."""
    if not isinstance(app_name, str) or not APP_NAME_RE.fullmatch(app_name):
        raise ValueError(f"unsafe app name: {app_name!r}")
    root = member_records.records_dir(APP_BOT_GRANTS_KIND).resolve()
    target = (root / f"{app_name}.json").resolve()
    if target.parent != root:
        raise ValueError(f"app name {app_name!r} escapes {root}")
    return target


def read_granted_bots(app_name: str, installed_at: str) -> list[str]:
    """The slugs the owner ticked for this install of *app_name*; ``[]`` when none.

    A record for another app name or another install reads as none. An unreadable or
    malformed record raises, so the caller grants nothing rather than guessing.
    """
    path = grants_path(app_name)
    try:
        raw = read_bytes_with_retry(path, max_bytes=_RECORD_MAX_BYTES)
    except FileNotFoundError:
        return []
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"bot grants record for {app_name!r} is not an object")
    if data.get("app") != app_name or data.get("installedAt") != installed_at:
        return []
    return _read_bot_grants(data.get("bots"))


def write_granted_bots(app_name: str, installed_at: str, slugs: Iterable[str]) -> list[str]:
    """Store the owner's ticked list for this install; the list as stored.

    Invalid slugs are dropped by the same reader every read uses. An empty list removes
    the record. Raises ``OSError`` when the write does not land, so the owner is told.
    """
    ticked = _read_bot_grants(list(slugs))
    if not ticked:
        # Only the ticked list: the approved scopes are the owner's separate decision.
        _unlink(grants_path(app_name))
        return []
    path = grants_path(app_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in (path.parent, path.parent.parent):
        # Defence in depth only: the sandbox mask and the file-tool floor are the fences.
        try:
            platform_compat.restrict_dir_to_owner(directory)
        except OSError:
            pass
    payload = {"app": app_name, "installedAt": installed_at, "bots": ticked}
    atomic_write(path, json.dumps(payload) + "\n", fsync=True)
    fsync_dir(path.parent)
    fsync_dir(path.parent.parent)
    return ticked


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    fsync_dir(path.parent, best_effort=True)


def remove_granted_bots(app_name: str) -> None:
    """Remove *app_name*'s ticked list and approved scopes, if any. Raises ``OSError`` when one stays."""
    _unlink(grants_path(app_name))
    _unlink(scopes_path(app_name))


def scopes_path(app_name: str) -> Path:
    """``member-records/app-bot-grants/<app>.scopes.json``: the Bot scopes the owner approved.

    No app name holds a ``.``, so this name never meets another app's ticked list.
    """
    ticks = grants_path(app_name)
    return ticks.with_name(f"{app_name}.scopes.json")


def read_approved_scopes(app_name: str) -> list[str] | None:
    """The Bot scopes the owner approved for *app_name*; ``None`` when none were recorded.

    The only source the Bot gate reads (``bot_grants.app_bot_scopes``). An unreadable
    or malformed record raises, so the caller grants nothing.
    """
    path = scopes_path(app_name)
    try:
        raw = read_bytes_with_retry(path, max_bytes=_RECORD_MAX_BYTES)
    except FileNotFoundError:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("app") != app_name:
        raise ValueError(f"bot scopes record for {app_name!r} is malformed")
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        raise ValueError(f"bot scopes record for {app_name!r} is malformed")
    return [s for s in BOT_SCOPE_NAMES if s in scopes]


def write_approved_scopes(app_name: str, scopes: Iterable[str], *, widen: bool) -> list[str]:
    """Store the Bot scopes approved for *app_name*; the scopes as stored.

    *widen* is True only at a consent moment: an install, a first registration or the
    owner's approval. Any other write (an update, a re-registration, a rollback) keeps
    only scopes already recorded and never adds one. Raises ``OSError`` when the write
    does not land.
    """
    wanted = [s for s in BOT_SCOPE_NAMES if s in set(scopes)]
    if not widen:
        current = read_approved_scopes(app_name) or []
        wanted = [s for s in wanted if s in current]
    return _store_scopes(app_name, wanted)


def snapshot_approved_scopes(app_name: str) -> list[str] | None:
    """The approved Bot scopes before a lifecycle transaction, for its rollback.

    An unreadable record snapshots as ``[]``, so a rollback restores no scope.
    """
    try:
        return read_approved_scopes(app_name)
    except (OSError, ValueError):
        return []


def restore_approved_scopes(app_name: str, snapshot: list[str] | None) -> None:
    """Put back a :func:`snapshot_approved_scopes` taken before a failed transaction.

    The snapshot came from this record, so restoring it adds nothing the owner had not
    approved. ``None`` (no record then) removes the record.
    """
    if snapshot is None:
        remove_approved_scopes(app_name)
        return
    _store_scopes(app_name, [s for s in BOT_SCOPE_NAMES if s in set(snapshot)])


def _store_scopes(app_name: str, wanted: list[str]) -> list[str]:
    path = scopes_path(app_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in (path.parent, path.parent.parent):
        try:
            platform_compat.restrict_dir_to_owner(directory)
        except OSError:
            pass
    atomic_write(path, json.dumps({"app": app_name, "scopes": wanted}) + "\n", fsync=True)
    fsync_dir(path.parent)
    return wanted


def remove_approved_scopes(app_name: str) -> None:
    """Remove *app_name*'s approved Bot scopes, if any."""
    _unlink(scopes_path(app_name))


__all__ = [
    "APP_BOT_GRANTS_KIND",
    "MAX_BOT_GRANTS",
    "grants_path",
    "read_approved_scopes",
    "read_granted_bots",
    "remove_approved_scopes",
    "remove_granted_bots",
    "restore_approved_scopes",
    "scopes_path",
    "snapshot_approved_scopes",
    "write_approved_scopes",
    "write_granted_bots",
]
