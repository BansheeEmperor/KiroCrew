"""Bind a dashboard turn's trace root: its turn id, its causes and its crew attributes.

``_run_chat`` calls :func:`bind_turn` once per turn. A top-level turn (not a nested
``/prompts get`` turn sharing its task) also takes:

- the sub-agent completions it delivers, handed over by the queue drain
  (``tracing.subagents.delivered``): one joins that run's trace, several give links;
- its crew attributes, when the slot is an Ultra Mode or Bot conductor or one of
  their workers (``tracing.crew``).

Both are read only while tracing is on. The work-item read touches disk, so it runs
on a worker thread before anything is bound.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any

from kiro_crew.tracing import crew as trace_crew
from kiro_crew.tracing import provider as trace_provider
from kiro_crew.tracing import subagents as trace_subagents
from kiro_crew.tracing import turns as trace_turns

logger = logging.getLogger(__name__)


def _stamped_row(state: Any, slot_key: str) -> dict[str, Any]:
    """*slot_key*'s row with the Crew Mode and mate stamps, by the slot list's rule. No I/O."""
    from kiro_crew import crew_mode
    from kiro_crew.dashboard.session_control_parts.crew_filing import _frame_parents

    try:
        rows = crew_mode.live_stamp_rows(
            state._slots, _frame_parents(state), settled=crew_mode.lineage_is_settled()
        )
        return dict(rows.get(slot_key) or {})
    except Exception:
        logger.debug("trace crew stamps unreadable", exc_info=True)
        return {}


async def _crew_attrs(state: Any, slot_key: str, hash_key: bytes | None) -> dict[str, Any]:
    row = _stamped_row(state, slot_key)
    root, _dispatcher, _kind = trace_crew.root_of(slot_key, row)
    if not root:
        return {}
    item = await asyncio.to_thread(trace_crew.bound_item, slot_key) if root != slot_key else None
    return trace_crew.attrs(hash_key, slot_key, row, item)


async def bind_turn(state: Any, slot: Any, turn_id: str, *, top: bool) -> contextvars.Token | None:
    """Bind *turn_id* as the root of the ACP turns this task streams next. Never raises.

    Release with ``tracing.turns.release_turn_root``. Nothing is bound until every
    read is done, so a cancelled read leaves nothing to release.
    """
    join, links, attrs = None, (), None
    trace = trace_provider.peek()
    if top and trace is not None:
        try:
            attrs = await _crew_attrs(state, str(getattr(slot, "key", "") or ""), trace.hash_key)
            join, links = trace_subagents.completion_binding(trace_subagents.take_causes())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("trace root binding failed", exc_info=True)
    return trace_turns.bind_turn_root(turn_id, join=join, links=links, attrs=attrs)
