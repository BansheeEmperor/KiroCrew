"""What an app may do with the owner's Bots: two declared scopes and the Bots the owner ticked.

Two things must both hold before an app token reaches a Bot:

- **A scope.** The manifest declares ``permissions.bots``: ``"read"`` (the Bot's
  name, description, busy or idle, goals and key results) or ``"wake"`` (the
  gateway's fixed wake line, ``dashboard.app_bot_wake``). ``bots`` is a staged
  grant family (``manager_records.STAGED_GRANT_FAMILIES``), so a scope an update
  adds waits for the owner like a new API path does.
- **A ticked Bot.** The owner ticks Bots for the app in its settings page
  (``PUT /api/apps/{name}/bot-grants``, owner only). The list lives on the
  install record (``InstalledApp.botGrants``) and starts empty, so a fresh
  install reaches no Bot at all. A Bot not on the list does not exist for the
  app: every route answers it 404.

A disabled app holds neither scope, whatever its token says (:func:`app_bot_scopes`).

Neither scope reaches a Bot's DM thread, briefing, rules or activity; those
routes keep refusing app tokens (``handlers.members_access._deny_app_caller``).
The app's HTTP requests still pass the path gate too, so it also declares
``/api/members`` in ``permissions.api``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from kiro_crew.apps.manager_records import hold_app_record_lock, holds_app_record_locks
from kiro_crew.apps.manifest_sections import BOT_GRANT_FAMILY, BOT_SCOPE_NAMES

logger = logging.getLogger(__name__)

#: Reads the ticked Bots' roster rows and goals.
BOT_SCOPE_READ = "read"
#: Sends a ticked Bot the fixed wake line.
BOT_SCOPE_WAKE = "wake"
BOT_SCOPES = BOT_SCOPE_NAMES


def app_bot_scopes(app_name: str) -> frozenset[str]:
    """The Bot scopes *app_name* declares AND the owner approved. Empty on any failure.

    Also empty while the app is not installed and enabled, read fresh on every
    call: an app token issued before the owner disabled the app still verifies,
    so the switch is checked here rather than trusted from the token.
    """
    if not app_name:
        return frozenset()
    try:
        from kiro_crew.apps.manager import get_app_manifest, is_app_enabled, staged_app_grants

        if not is_app_enabled(app_name):
            return frozenset()

        def _declared() -> list[str] | None:
            manifest = get_app_manifest(app_name)
            return None if manifest is None else list(manifest.permissions.bots)

        granted = staged_app_grants(app_name, BOT_GRANT_FAMILY, _declared)
    except Exception:
        logger.warning("bot scopes of %r unreadable; granting none", app_name, exc_info=True)
        return frozenset()
    return frozenset(s for s in granted if s in BOT_SCOPES)


def app_granted_bots(app_name: str) -> tuple[str, ...]:
    """The Bot slugs the owner ticked for *app_name*, in the owner's order. Empty on failure."""
    if not app_name:
        return ()
    try:
        from kiro_crew.apps.manager_records import _read_installed

        meta = _read_installed(app_name)
    except Exception:
        logger.warning("bot grants of %r unreadable; granting none", app_name, exc_info=True)
        return ()
    return tuple(meta.botGrants) if meta is not None else ()


@holds_app_record_locks
def set_app_granted_bots(app_name: str, slugs: Iterable[str]) -> list[str] | None:
    """Store the owner's ticked list for *app_name*; the stored list, or None if not installed.

    The record lock spans the read and the write, so no other record writer lands
    in between; the owner route also holds the app's lifecycle lock. Invalid slugs
    are dropped by the record's own reader, the same one every read goes through.
    """
    from dataclasses import replace

    from kiro_crew.apps.manager_records import _read_bot_grants, _read_installed, _write_installed

    hold_app_record_lock(app_name)
    meta = _read_installed(app_name)
    if meta is None:
        return None
    ticked = _read_bot_grants(list(slugs))
    _write_installed(app_name, replace(meta, botGrants=ticked))
    return ticked


__all__ = [
    "BOT_GRANT_FAMILY",
    "BOT_SCOPES",
    "BOT_SCOPE_READ",
    "BOT_SCOPE_WAKE",
    "app_bot_scopes",
    "app_granted_bots",
    "set_app_granted_bots",
]
