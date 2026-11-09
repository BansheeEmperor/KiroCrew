"""Frames the hub sends a socket that waits for its connect snapshot follow it.

``_serve_ws`` registers a socket before it takes the slot list of its connect
snapshot, which can wait for a shared pass (``slot_list_pass``). ``ws_snapshot_hold``
keeps every frame the hub sends the socket meanwhile and sends them, in order, once
the snapshot is out.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from kiro_crew.dashboard.state import DashboardState
from kiro_crew.dashboard.ws_snapshot_hold import HELD_FRAMES_KEY, held, hold_frames, release_held
from kiro_crew.testing.wait import await_until


@pytest.fixture
def state(_floor_monkeypatch, tmp_path) -> DashboardState:
    _floor_monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
    return DashboardState(
        sessions=MagicMock(count=0),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=0.0,
    )


class _Socket:
    """A dashboard socket double that keeps every frame sent to it, text or binary."""

    closed = False

    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self._flags: dict = {}

    def __setitem__(self, key: str, value) -> None:
        self._flags[key] = value

    def get(self, key: str, default=None):
        return self._flags.get(key, default)

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent.append(payload)


class TestSnapshotHold:
    @pytest.mark.asyncio
    async def test_hub_frames_wait_for_the_snapshot_and_then_go_straight_out(self, state) -> None:
        ws = _Socket()
        hold_frames(ws)
        state.register_ws(ws, owner=True)

        state._spawn_ws_send(ws, "broadcast")
        delivered = await state.deliver_ws_owners("followup_card", {"slot": "chat-1"})
        await state.deliver_ws_owners_bytes(b"live frame")

        # Nothing reached the socket ahead of its snapshot, and the owner send counts.
        assert (ws.sent, delivered) == ([], 1)

        snapshot = asyncio.ensure_future(ws.send_str("snapshot"))
        released = release_held(ws)
        before = set(state._background_tasks)
        state._spawn_ws_send(ws, "later")
        later = state._background_tasks - before
        await await_until(asyncio.gather(snapshot, *released, *later), what="the sends")

        assert ws.sent == [
            "snapshot",
            "broadcast",
            json.dumps({"type": "followup_card", "data": {"slot": "chat-1"}}),
            b"live frame",
            "later",
        ]

    @pytest.mark.asyncio
    async def test_held_frames_go_out_while_the_snapshot_still_drains(self, state) -> None:
        """The hold ends once the snapshot is queued, not once the tab has read it, so a
        tab that stops reading still gets every frame into the buffer the hub's stall
        check reads."""
        drained = asyncio.Event()

        class _DrainingSocket(_Socket):
            async def send_str(self, payload: str) -> None:
                # Written at once, then waiting for the tab to read, as aiohttp does.
                self.sent.append(payload)
                if payload == "snapshot":
                    await drained.wait()

        ws = _DrainingSocket()
        hold_frames(ws)
        state._spawn_ws_send(ws, "broadcast")

        snapshot = asyncio.ensure_future(ws.send_str("snapshot"))
        released = release_held(ws)
        await await_until(asyncio.gather(*released), what="the held send")

        assert (ws.sent, snapshot.done()) == (["snapshot", "broadcast"], False)
        drained.set()
        await await_until(snapshot, what="the snapshot send")

    @pytest.mark.asyncio
    async def test_a_failed_send_is_logged_and_the_hold_still_ends(self) -> None:
        class _GoneSocket(_Socket):
            async def send_str(self, payload: str) -> None:
                raise ConnectionResetError

        ws = _GoneSocket()
        hold_frames(ws)
        assert held(ws, "first") and held(ws, "second")

        released = release_held(ws)
        results = await await_until(
            asyncio.gather(*released, return_exceptions=True), what="the held sends"
        )

        assert [type(r) for r in results] == [ConnectionResetError, ConnectionResetError]
        assert held(ws, "after") is False
        assert ws.get(HELD_FRAMES_KEY) is None
