"""What turning the ``connected_computers`` switch off (or on) does, on either machine.

Hiding the UI is not enough, so the config watcher calls :func:`apply` on every
``dashboard.feature_previews`` change, and :func:`apply` schedules the work off
the watcher's cycle (an applier is cancelled after 10 s, and a cancel must never
leave a teardown half done):

* **off, remote gateway** -- every connector socket is told to stop and closed;
  each open channel ends as interrupted, so a running call reports its outcome
  unknown. Chat bindings, device records and every file are kept.
* **off, local machine** -- every active grant is revoked in the vault (so a
  later switch-on never silently restores access), each connector and its
  process trees stop, and each remote gateway is told when reachable.
* **on** -- the local controller starts, and finds no grant until the user
  consents again natively. Nothing is restored.

New work is refused by the gates themselves, which read the switch live.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kiro_crew.connected_computers.flag import PREVIEW_NAME, local_enabled, preview_on

logger = logging.getLogger(__name__)

_TASKS: "set[asyncio.Task[Any]]" = set()


def touched(change: Any) -> bool:
    return bool(
        getattr(change, "touched", None)
        and change.touched(
            f"dashboard.feature_previews.{PREVIEW_NAME}", "dashboard.feature_previews"
        )
    )


async def turn_off(reason: str = "connected computers was turned off") -> None:
    from kiro_crew.connected_computers.broker import get_broker
    from kiro_crew.connected_computers.controller import get_controller

    closed = await get_broker().shutdown_all(reason)
    revoked = await get_controller().off()
    logger.info(
        "connected computers off: %d connector socket(s) closed, %d local grant(s) revoked",
        closed,
        len(revoked),
    )


def apply(change: Any) -> None:
    """Schedule the teardown or start for a feature-preview change (never blocks)."""
    if not touched(change):
        return
    new_on = preview_on(getattr(change, "new", None) or object())
    old_on = preview_on(getattr(change, "old", None) or object())
    if new_on == old_on:
        return
    if new_on:
        task = asyncio.ensure_future(_start_local())
    else:
        task = asyncio.ensure_future(turn_off())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _start_local() -> None:
    from kiro_crew.connected_computers.controller import get_controller

    get_controller().start()


def start_if_enabled() -> None:
    """Post-bind start of the local controller, when this machine opted in."""
    try:
        if local_enabled():
            from kiro_crew.connected_computers.controller import get_controller

            get_controller().start()
    except Exception:  # noqa: BLE001 -- an optional feature must not stop the gateway
        logger.warning("connected computers: local controller did not start", exc_info=True)


async def shutdown() -> None:
    """Gateway shutdown: stop what runs, keep every grant, record and file."""
    from kiro_crew.connected_computers import broker as broker_mod
    from kiro_crew.connected_computers import controller as controller_mod

    try:
        if controller_mod._CONTROLLER is not None:
            await controller_mod._CONTROLLER.shutdown()
        if broker_mod._BROKER is not None:
            await broker_mod._BROKER.shutdown_all("the gateway is shutting down")
    except Exception:  # noqa: BLE001 -- shutdown must not fail on an optional feature
        logger.warning("connected computers: shutdown cleanup failed", exc_info=True)
