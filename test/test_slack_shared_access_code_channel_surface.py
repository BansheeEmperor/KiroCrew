"""slack.shared_access vs the code-channel surface (agent tools, commands, wrap-up/archive).

A shared user drives work as the owner in DMs, mentions and threads, but code channels
stay the owner's. Each entry point the code-channel surface adds must refuse them:
the channel's slash commands, the wrap-up's Archive button (at the interactions gate
and in its own handler), and a thread linked to a code channel's session from
anywhere, whose turn would reach the code-channel tools.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from slack_code_channel_surface_helpers import (  # noqa: F401 - fixtures by name
    CH,
    SLOT,
    _bind,
    _calls,
    _click,
    _slash,
    _wrap,
    env_fixture,
)

# Imported here, never under a patch: a module first imported while a name is
# patched binds the patched value for the rest of the session.
from kiro_crew.slack import events  # noqa: F401
from kiro_crew.slack.agent_sessions.session_events import _shared_user_in_code_channel
from kiro_crew.slack.code_channels import commands, wrapup
from kiro_crew.slack.code_channels.routing import shared_user_refused_for_slot
from kiro_crew.slack.handler_runtime.inbound import (
    LinkedRoute,
    route_linked_thread,
    shared_user_refused_for_session,
)
from kiro_crew.slack.shared_access import shared_action_permitted

SHARED = "U0SHARED01"


def _as_shared():
    """Treat SHARED as a confirmed, listed shared user (never the owner)."""
    return (
        patch("kiro_crew.slack.handler.is_shared_user", lambda u: u == SHARED),
        patch("kiro_crew.slack.handler.is_shared_listed", lambda u: u == SHARED),
    )


def test_a_shared_user_runs_no_code_channel_command(env: Any) -> None:
    _bind(env)
    replies: list[str] = []

    async def respond(text: str) -> None:
        replies.append(text)

    a, b = _as_shared()
    with a, b:
        for i, command in enumerate(("/summarize", "/wrap-up", "/create-pr")):
            slash = _slash(command, user=SHARED, trigger=f"T-{i}")
            assert asyncio.run(commands.handle_slash(env.orch, slash, respond))
    assert env.routed == [] and len(replies) == 3
    assert all(r["outcome"] == "denied" for r in env.sel.ops("slack.code_channel.command"))


def test_a_shared_user_cannot_archive_a_code_channel(env: Any) -> None:
    _bind(env)
    _, ts = _wrap(env)
    # Refused at the interactions gate: Archive is not a button a shared user may press.
    assert shared_action_permitted(wrapup.ARCHIVE_ACTION_ID) is False
    a, b = _as_shared()
    with a, b:
        # And by the handler itself, which admits the owner only.
        assert asyncio.run(wrapup.handle_archive_click(env.orch, _click(SHARED, ts)))
    assert _calls(env, "agents.conversations.archive") == []
    assert env.sel.ops("slack.code_channel.archive")[-1]["outcome"] == "denied"


@pytest.mark.parametrize("how", ["slot", "anchor", "revoked_anchor", "unrelated"])
def test_a_linked_thread_into_a_code_channel_session_is_refused(env: Any, how: str) -> None:
    _bind(env, anchor="100.000001")
    if how == "revoked_anchor":
        env.store.drop_binding(CH)
    slot, thread = {
        "slot": (SLOT, "555.000001"),
        "anchor": ("dashboard:chat-other", "100.000001"),
        "revoked_anchor": ("dashboard:chat-other", "100.000001"),
        "unrelated": ("dashboard:chat-other", "555.000001"),
    }[how]
    assert shared_user_refused_for_slot(slot, thread) is (how != "unrelated")


def test_route_linked_thread_refuses_a_shared_user_into_a_code_channel_session(
    env: Any,
) -> None:
    from types import SimpleNamespace

    from kiro_crew.slack import handler

    _bind(env)
    state = SimpleNamespace(get_linked_slot=lambda ts: SimpleNamespace(key=SLOT))
    a, b = _as_shared()
    with a, b, patch.object(handler, "_dashboard_state", state):
        # A plain DM thread the owner linked to the code channel's dashboard session.
        outcome = asyncio.run(
            route_linked_thread(
                "now add a regression test",
                "slack:555.000001",
                SHARED,
                "D0DM00001",
                env.slack,
                "555.000001",
            )
        )
    assert outcome is LinkedRoute.REFUSED


def test_a_closed_tab_still_refuses_a_shared_users_linked_thread(env: Any) -> None:
    # The code channel's session has no live slot (its tab is closed), but the
    # thread index still names it as the thread's owner.
    from types import SimpleNamespace

    from kiro_crew.slack import handler

    _bind(env)
    sessions = SimpleNamespace(get_session_for_thread=lambda ts: SLOT)
    state = SimpleNamespace(get_linked_slot=lambda ts: None, sessions=sessions)
    a, b = _as_shared()
    with a, b, patch.object(handler, "_dashboard_state", state):
        outcome = asyncio.run(
            route_linked_thread(
                "now add a regression test",
                "slack:555.000001",
                SHARED,
                "D0DM00001",
                env.slack,
                "555.000001",
            )
        )
    assert outcome is LinkedRoute.REFUSED


def test_a_pinned_answer_to_a_code_channel_session_is_refused(env: Any) -> None:
    _bind(env)
    a, b = _as_shared()
    with a, b:
        assert asyncio.run(shared_user_refused_for_session(SHARED, SLOT, "", "D0DM00001"))
        assert not asyncio.run(
            shared_user_refused_for_session(SHARED, "dashboard:chat-other", "", "D0DM00001")
        )
    # The owner is never refused here.
    assert not asyncio.run(shared_user_refused_for_session("UOWNER", SLOT, "", "D0DM00001"))


def test_a_shared_users_rename_or_stop_in_a_code_channel_thread_is_refused(env: Any) -> None:
    # The agent-session rename and thread Stop: the thread's session is a code
    # channel's, or the thread is its (bound or revoked) anchor, with Slack Code off.
    _bind(env, anchor="100.000001")
    env.on = False
    a, b = _as_shared()
    with a, b:
        assert asyncio.run(_shared_user_in_code_channel(SHARED, "D0DM00001", "555.0", SLOT))
        assert asyncio.run(_shared_user_in_code_channel(SHARED, "C0ORIGIN", "100.000001"))
        env.store.drop_binding(CH)
        assert asyncio.run(_shared_user_in_code_channel(SHARED, "C0ORIGIN", "100.000001"))
        assert not asyncio.run(_shared_user_in_code_channel(SHARED, "C0ORIGIN", "555.000001"))


@pytest.mark.parametrize(
    "spelling", [SLOT, f"dashboard:{SLOT}", "slack:100.000001", "dashboard:slack:100.000001"]
)
def test_every_spelling_of_a_code_channel_session_is_refused(env: Any, spelling: str) -> None:
    # The binding stores the bare slot key; the thread index and an asker carry the
    # ``dashboard:`` form, and a channel-born slot runs on ``slack:<anchor>``.
    _bind(env, anchor="100.000001")
    assert shared_user_refused_for_slot(spelling, "") is True
    assert shared_user_refused_for_slot("dashboard:chat-other", "") is False


def test_a_refused_answer_to_a_code_channel_session_leaves_the_card_answerable(
    env: Any,
) -> None:
    # A card posted by the code channel's session, mirrored into an ordinary thread:
    # the shared user's ordinary answer is refused BEFORE the card is claimed.
    from types import SimpleNamespace

    from kiro_crew.slack import interactions, interactions_options, outbound

    _bind(env)
    sessions = SimpleNamespace(get_session_for_thread=lambda ts: f"dashboard:{SLOT}")
    orch = SimpleNamespace(slack=env.slack, dashboard_state=SimpleNamespace(sessions=sessions))
    told: list[str] = []

    async def _tell(channel: str, user: str, thread: str, text: str) -> None:
        told.append(text)

    a, b = _as_shared()
    with (
        a,
        b,
        patch.object(interactions, "_orch", orch),
        patch.object(interactions, "is_shared_listed", lambda u: u == SHARED),
        patch.object(interactions, "_tell_user", _tell),
        patch.object(outbound, "_ANSWERED", {}),
    ):
        refused = asyncio.run(
            interactions_options._shared_destination_refused(
                "C0PLAIN", SHARED, "555.000001", "options", None
            )
        )
        assert refused is True
        assert outbound._ANSWERED == {}  # nothing claimed: the owner can still answer
    assert told == ["⛔ Only the bot owner can do that."]


def test_a_shared_users_stop_in_a_thread_resolving_to_a_code_channel_session_is_refused(
    env: Any,
) -> None:
    # The Stop's thread is neither the code channel nor its anchor, but the session
    # the Stop resolves to is the code channel's (a dashboard-linked thread).
    from types import SimpleNamespace

    from kiro_crew.slack.agent_sessions import session_events

    _bind(env, anchor="100.000001")
    stops: list[str] = []

    async def stop_turn(key: str, **_kw: Any) -> str:
        stops.append(key)
        return "soft"

    async def _prime(*_a: Any) -> None:
        return None

    orch = SimpleNamespace(sessions=SimpleNamespace(stop_turn=stop_turn), _session_tasks={})
    event = {
        "channel": "D0DM00001",
        "thread_ts": "555.000001",
        "user": SHARED,
        "event_ts": "555.900001",
    }
    a, b = _as_shared()
    with (
        a,
        b,
        patch.object(session_events, "prime_shared_user", _prime),
        patch.object(session_events, "_may_use_bot", lambda u: True),
        patch.object(
            session_events,
            "resolve_session",
            lambda *_a: ("555.000001", f"dashboard:{SLOT}", False),
        ),
        patch.object(session_events, "_is_ours", lambda *_a: True),
    ):
        outcome = asyncio.run(session_events.handle_stopped(orch, event))
    assert outcome == "denied" and stops == []


def test_a_shared_users_bang_stop_in_a_code_channel_sessions_thread_is_refused(
    env: Any,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from slack_events_coverage_helpers import _event, _make_orch

    _bind(env)
    orch = _make_orch()
    orch.sessions.has_session = MagicMock(return_value=True)
    orch.sessions.get_session_for_thread = MagicMock(return_value=f"dashboard:{SLOT}")
    orch.sessions.note_stop = MagicMock(return_value=True)
    orch.sessions.stop_turn = AsyncMock(return_value="soft")
    a, b = _as_shared()
    with (
        a,
        b,
        patch("kiro_crew.slack.events.sel", return_value=MagicMock()),
        patch("kiro_crew.slack.events.is_owner", return_value=False),
        patch("kiro_crew.slack.events.is_allowed_user", return_value=False),
        patch("kiro_crew.slack.events.is_shared_user", lambda u: u == SHARED),
    ):
        asyncio.run(
            events._route_message(
                orch,
                _event(text="!stop", user=SHARED, channel="D0DM00001", thread_ts="555.000001"),
                events.SeenCache(),
            )
        )
    orch.sessions.stop_turn.assert_not_awaited()
    orch.sessions.note_stop.assert_not_called()


def test_a_native_turn_relinked_to_a_code_channel_session_is_not_acquired(env: Any) -> None:
    # The owner links the thread to the code channel's session after the linked-thread
    # check ran: the turn re-resolves its owner before acquisition and must refuse.
    from slack_handler_helpers import FakeSessionManager

    from conftest import MockSlackClient
    from kiro_crew.slack import handler

    _bind(env)

    class Relinked(FakeSessionManager):
        owner_key = "dashboard:chat-other"

        def get_session_for_thread(self, _ts: str) -> str:
            return self.owner_key

    sessions = Relinked()

    async def resolve_memory(_builder: Any, key: str) -> str:
        if key == "dashboard:chat-other":
            await asyncio.sleep(0)
            sessions.owner_key = f"dashboard:{SLOT}"
        return f"memory:{key}"

    async def hydrate(*_a: Any) -> None:
        return None

    slack = MockSlackClient()
    a, b = _as_shared()
    with (
        a,
        b,
        patch.object(handler, "session_store_for_turn", resolve_memory),
        patch.object(handler, "_hydrate_thread_overrides", hydrate),
        patch.object(handler, "_hydrate_conv_flags", lambda *_a: None),
    ):
        asyncio.run(
            handler.handle_message(slack, sessions, "D0DM00001", "hi", "555.000001", "msg1", SHARED)
        )
    assert sessions.keys_seen == []
    # The refused turn takes down its Working block (with its Stop button) and status.
    working = [p["ts"] for kind, p in slack.actions if kind == "blocks"]
    deleted = {p["ts"] for kind, p in slack.actions if kind == "delete"}
    assert working and set(working) <= deleted
    statuses = [p["status"] for kind, p in slack.actions if kind == "set_thread_status"]
    assert not statuses or statuses[-1] == ""


def test_a_transport_turn_relinked_to_a_code_channel_session_is_not_acquired(
    env: Any,
) -> None:
    import importlib

    from kiro_crew.slack import transport_dispatch

    golden = importlib.import_module("test_slack_golden_transcript")
    _bind(env)
    acquired: list[str] = []

    class Relinked(golden.FakeSessions):  # type: ignore[name-defined, misc]
        owner_key = "dashboard:chat-other"

        def get_session_for_thread(self, _ts: str) -> str:
            return self.owner_key

        async def get_or_create(self, session_key: str, **_kw: Any) -> Any:
            acquired.append(session_key)
            return await super().get_or_create(session_key)

    sessions = Relinked(golden.ScriptedProvider([golden.make_event("ok")]))

    async def resolve_memory(_builder: Any, key: str) -> str:
        if key == "dashboard:chat-other":
            sessions.owner_key = f"dashboard:{SLOT}"
        return f"memory:{key}"

    async def hydrate(*_a: Any) -> None:
        return None

    slack = golden.RecordingSlackClient()
    a, b = _as_shared()
    with (
        a,
        b,
        patch.object(transport_dispatch, "session_store_for_turn", resolve_memory),
        patch.object(transport_dispatch, "_hydrate_thread_overrides", hydrate),
        patch.object(transport_dispatch, "_hydrate_conv_flags", lambda *_a: None),
        patch.object(transport_dispatch, "_get_default_agent", lambda: ""),
        patch.object(transport_dispatch, "_thread_agents", {}),
    ):
        asyncio.run(
            transport_dispatch.handle_message_transport(
                slack,
                sessions,
                "D0DM00001",
                "hi",
                "555.000001",
                "555.000002",
                SHARED,
            )
        )
    assert acquired == []
    # The renderer's teardown clears the working status the refused turn set.
    statuses = [kw.get("status") for m, kw in slack.transcript if m == "set_thread_status"]
    assert not statuses or statuses[-1] == ""
    assert not [m for m, _kw in slack.transcript if m == "post_blocks"]
