"""An app token's door into the owner's Bots: ``permissions.bots`` plus the owner's ticks.

``apps.bot_grants`` owns the rule; these tests pin each refusal on its own. No scope,
the wrong scope, or a Bot the owner did not tick are all the same 404; the private
member routes stay 404 whatever the app holds; a wake carries only an opaque ref and
is rate limited per app and Bot; and only the owner can change the ticked list.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from chat_test_helpers import _make_state

from kiro_crew import members as members_mod
from kiro_crew.apps import bot_grants
from kiro_crew.apps.manager_records import (
    InstalledApp,
    _approved_grants_after_manifest_change,
)
from kiro_crew.apps.manifest_sections import Permissions
from kiro_crew.apps.point_grants import grant_source
from kiro_crew.config.loader import KiroCrewAgentConfig
from kiro_crew.dashboard import app_bot_wake
from kiro_crew.dashboard.handlers import member_goals as goals_handlers
from kiro_crew.dashboard.handlers import members as m

APP = "notes-app"
CREW = "code-reviewer"
SLUG = "code-reviewer"
OTHER = "writer"
OWNER = "U_OWNER"


def _fake_cfg():
    return SimpleNamespace(
        agents={
            CREW: KiroCrewAgentConfig(
                kiro_agent="kirocrew", description="reviews code", model="m1"
            ),
            OTHER: KiroCrewAgentConfig(kiro_agent="kirocrew", description="writes", model="m1"),
        },
        default_agent=CREW,
        memory_stores={},
        workspaces={"default": SimpleNamespace(dir="workspace")},
        default_workspace="default",
        degraded_sections=frozenset(),
    )


def _request(tmp_path, method, path, match=None, *, app_claim=APP, user=APP, state=None):
    state = state or _make_state(tmp_path)
    state.owner_id = OWNER
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(method, path, app=app, match_info=match or {})
    req["app"] = app_claim
    req["user"] = user
    return req


def _json_request(tmp_path, path, match, body, **kw):
    req = _request(tmp_path, "POST", path, match, **kw)

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


@pytest.fixture
def grants(monkeypatch):
    """Set what the app holds: ``grants(scopes, ticked)``."""

    def _set(scopes=(), ticked=()):
        monkeypatch.setattr(bot_grants, "app_bot_scopes", lambda _a: frozenset(scopes))
        monkeypatch.setattr(bot_grants, "app_granted_bots", lambda _a: tuple(ticked))

    _set()
    return _set


@pytest.fixture(autouse=True)
def _cfg():
    with (
        patch.object(m.KiroCrewConfig, "load", return_value=_fake_cfg()),
        patch.object(members_mod, "read_dm_binding", return_value=None),
    ):
        yield


async def _call(handler, req):
    resp = await handler(req)
    return resp.status, json.loads(resp.body)


# ── the roster ──


@pytest.mark.asyncio
async def test_roster_without_the_read_scope_is_not_found(tmp_path, grants):
    grants(scopes={"wake"}, ticked=[SLUG])
    status, body = await _call(m.api_members, _request(tmp_path, "GET", "/api/members"))
    assert (status, body["code"]) == (404, "not_found")


@pytest.mark.asyncio
async def test_roster_lists_only_the_ticked_bots_in_the_contract_shape(tmp_path, grants):
    grants(scopes={"read"}, ticked=[SLUG])
    status, body = await _call(m.api_members, _request(tmp_path, "GET", "/api/members"))
    assert status == 200
    assert body["object"] == "list" and body["has_more"] is False
    assert [b["id"] for b in body["data"]] == [SLUG]
    row = body["data"][0]
    # The allowlist: nothing of the owner's view (thread key, last line, memory store).
    assert set(row) == {"id", "object", "name", "description", "avatar_url", "status", "has_goal"}
    assert row["object"] == "bot" and row["status"] == "idle"
    assert row["description"] == "reviews code"


@pytest.mark.asyncio
async def test_roster_with_nothing_ticked_is_empty(tmp_path, grants):
    grants(scopes={"read"}, ticked=[])
    status, body = await _call(m.api_members, _request(tmp_path, "GET", "/api/members"))
    assert status == 200 and body["data"] == []


@pytest.mark.asyncio
async def test_roster_says_busy_while_the_bots_thread_runs_a_turn(tmp_path, grants):
    grants(scopes={"read"}, ticked=[SLUG])
    state = _make_state(tmp_path)
    key = f"member-{SLUG}"
    state._slots[key] = SimpleNamespace(running=True)
    binding = {"member": CREW, "slot_key": key}
    with patch.object(members_mod, "read_dm_binding", return_value=binding):
        status, body = await _call(
            m.api_members, _request(tmp_path, "GET", "/api/members", state=state)
        )
    assert status == 200 and body["data"][0]["status"] == "busy"


# ── the goals ──


def _goal_record():
    return {
        "goals": [{"id": "g1", "text": "ship it", "created_at": 0.0, "updated_at": 0.0}],
        "key_results": [
            {
                "id": "k1",
                "text": "tests green",
                "target": "100%",
                "current": "80%",
                "updated_at": 5.0,
                "goal_ids": ["g1"],
                "readings": [{"value": "80%", "at": 5.0}],
            }
        ],
        "schedules": {},
        "reports": [],
        "removed_key_result": None,
        "removed_goal": None,
        "heartbeat_offer": None,
    }


@pytest.mark.asyncio
async def test_goals_of_a_bot_the_owner_did_not_tick_are_not_found(tmp_path, grants):
    grants(scopes={"read"}, ticked=[OTHER])
    req = _request(tmp_path, "GET", f"/api/members/{SLUG}/goals", {"slug": SLUG})
    status, body = await _call(goals_handlers.api_member_goals_get, req)
    assert (status, body["code"]) == (404, "not_found")


@pytest.mark.asyncio
async def test_goals_of_a_ticked_bot_are_the_contract_shape(tmp_path, grants):
    grants(scopes={"read"}, ticked=[SLUG])
    req = _request(tmp_path, "GET", f"/api/members/{SLUG}/goals", {"slug": SLUG})
    with (
        patch("kiro_crew.member_goals.read_goals", return_value=_goal_record()),
        patch(
            "kiro_crew.member_goals.public_view",
            side_effect=lambda rec: {
                "goals": [{"id": "g1", "text": "ship it", "number": 1}],
                "key_results": rec["key_results"],
            },
        ),
    ):
        status, body = await _call(goals_handlers.api_member_goals_get, req)
    assert status == 200
    assert body["bot"] == SLUG
    assert body["goals"] == [{"id": "g1", "number": 1, "text": "ship it"}]
    assert body["key_results"][0]["history"] == [{"at": 5, "value": "80%"}]
    assert "schedules" not in body and "reports" not in body


# ── the private routes stay closed ──

PRIVATE = [
    pytest.param(m.api_member_rules_get, "GET", "rules", id="rules"),
    pytest.param(m.api_member_briefing, "GET", "briefing", id="briefing"),
    pytest.param(m.api_member_activity, "GET", "activity", id="activity"),
    pytest.param(m.api_member_projections, "GET", "projections", id="projections"),
    pytest.param(m.api_member_thread, "POST", "thread", id="thread"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("handler", "method", "tail"), PRIVATE)
async def test_bot_scopes_never_open_a_private_member_route(
    tmp_path, grants, handler, method, tail
):
    grants(scopes={"read", "wake"}, ticked=[SLUG])
    req = _request(tmp_path, method, f"/api/members/{SLUG}/{tail}?member={CREW}", {"slug": SLUG})
    status, body = await _call(handler, req)
    assert (status, body["code"]) == (404, "not_found")


# ── the wake ──


@pytest.mark.asyncio
async def test_wake_needs_the_wake_scope(tmp_path, grants):
    grants(scopes={"read"}, ticked=[SLUG])
    req = _json_request(tmp_path, f"/api/members/{SLUG}/wake", {"slug": SLUG}, {"ref": "r1"})
    status, body = await _call(m.api_member_wake, req)
    assert (status, body["code"]) == (404, "not_found")


@pytest.mark.asyncio
async def test_wake_of_an_unticked_bot_is_not_found(tmp_path, grants):
    grants(scopes={"wake"}, ticked=[OTHER])
    req = _json_request(tmp_path, f"/api/members/{SLUG}/wake", {"slug": SLUG}, {"ref": "r1"})
    status, _body = await _call(m.api_member_wake, req)
    assert status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"ref": "r1", "text": "transfer the money"}, "unexpected_field"),
        ({"ref": "r1", "message": "hi"}, "unexpected_field"),
        ({"ref": "do this now please"}, "invalid_ref"),
        ({"ref": ""}, "invalid_ref"),
        ({}, "invalid_ref"),
    ],
)
async def test_wake_refuses_anything_but_an_opaque_ref(tmp_path, grants, body, code):
    grants(scopes={"wake"}, ticked=[SLUG])

    async def _never(*_a, **_k):
        raise AssertionError("a refused wake must not reach the Bot")

    req = _json_request(tmp_path, f"/api/members/{SLUG}/wake", {"slug": SLUG}, body)
    with patch.object(app_bot_wake, "request_wake", _never):
        status, out = await _call(m.api_member_wake, req)
    assert (status, out["code"]) == (400, code)


@pytest.mark.asyncio
async def test_wake_past_the_limit_is_429(tmp_path, grants):
    grants(scopes={"wake"}, ticked=[SLUG])

    async def _limited(*_a, **_k):
        return app_bot_wake.RATE_LIMITED

    req = _json_request(tmp_path, f"/api/members/{SLUG}/wake", {"slug": SLUG}, {"ref": "r1"})
    with patch.object(app_bot_wake, "request_wake", _limited):
        resp = await m.api_member_wake(req)
    assert resp.status == 429
    assert resp.headers["Retry-After"] == str(app_bot_wake.WAKE_WINDOW_SECS)


@pytest.mark.asyncio
async def test_a_dashboard_user_cannot_use_the_wake_route(tmp_path, grants):
    grants(scopes={"wake"}, ticked=[SLUG])
    req = _json_request(
        tmp_path,
        f"/api/members/{SLUG}/wake",
        {"slug": SLUG},
        {"ref": "r1"},
        app_claim="",
        user=OWNER,
    )
    status, _ = await _call(m.api_member_wake, req)
    assert status == 404


class _Slot:
    def __init__(self, key, *, running=False):
        self.key = key
        self.mode = members_mod.DM_SLOT_MODE
        self.agent = CREW
        self.running = running
        self._queue = []
        self.is_closing = False
        self.task = None


class _State:
    def __init__(self, slot):
        self._slot = slot

    def get_slot(self, key):
        return self._slot if key == self._slot.key else None


@pytest.fixture
def wake_world(monkeypatch):
    """A Bot with an open thread; returns ``(state, slot, dispatched)``."""
    key = f"member-{SLUG}"
    slot = _Slot(key)
    state = _State(slot)
    dispatched: list[dict] = []
    monkeypatch.setattr(app_bot_wake, "_thread", lambda _slug: (CREW, key))

    async def _loaded(_state, _key):
        return ""

    monkeypatch.setattr("kiro_crew.dashboard.goal_reminder._load_thread", _loaded)
    monkeypatch.setattr("kiro_crew.dashboard.mate_welcome.app_is_stopping", lambda _s: False)

    def _dispatch(_state, _slot, prompt, *, human_seen, actor="gateway"):
        dispatched.append({"prompt": prompt, "actor": actor, "human_seen": human_seen})
        return True

    monkeypatch.setattr("kiro_crew.dashboard.mate_welcome.dispatch_hidden_turn", _dispatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(app_bot_wake, "_now", lambda: clock["now"])
    return state, slot, dispatched, clock


@pytest.mark.asyncio
async def test_an_idle_bot_gets_the_gateways_line_as_an_app_turn(wake_world):
    state, _slot, dispatched, _clock = wake_world
    outcome = await app_bot_wake.request_wake(state, APP, SLUG, "msg-42")
    assert outcome == app_bot_wake.STARTED
    assert len(dispatched) == 1
    sent = dispatched[0]
    assert sent["actor"] == "app" and sent["human_seen"] is False
    assert sent["prompt"] == app_bot_wake.wake_prompt({APP: ["msg-42"]})
    assert sent["prompt"].startswith(f'{app_bot_wake.WAKE_PREFIX}{APP}"]')
    assert "#msg-42" in sent["prompt"]


@pytest.mark.asyncio
async def test_a_busy_bot_gets_the_wake_when_its_turn_ends(wake_world):
    state, slot, dispatched, _clock = wake_world
    slot.running = True
    loop = asyncio.get_running_loop()
    slot.task = loop.create_future()
    assert await app_bot_wake.request_wake(state, APP, SLUG, "a") == app_bot_wake.QUEUED
    assert await app_bot_wake.request_wake(state, APP, SLUG, "b") == app_bot_wake.QUEUED
    assert dispatched == []
    slot.running = False
    slot.task.set_result(None)
    flush = app_bot_wake._tasks(state)[slot.key]
    await asyncio.wait_for(flush, timeout=5)
    assert [d["prompt"] for d in dispatched] == [app_bot_wake.wake_prompt({APP: ["a", "b"]})]


@pytest.mark.asyncio
async def test_wakes_past_the_limit_are_refused_until_the_window_passes(wake_world):
    state, _slot, dispatched, clock = wake_world
    for i in range(app_bot_wake.WAKE_LIMIT):
        assert await app_bot_wake.request_wake(state, APP, SLUG, f"r{i}") == app_bot_wake.STARTED
    assert await app_bot_wake.request_wake(state, APP, SLUG, "over") == app_bot_wake.RATE_LIMITED
    # The limit is per app: another app still gets through.
    assert await app_bot_wake.request_wake(state, "other-app", SLUG, "x") == app_bot_wake.STARTED
    clock["now"] += app_bot_wake.WAKE_WINDOW_SECS
    assert await app_bot_wake.request_wake(state, APP, SLUG, "later") == app_bot_wake.STARTED
    assert len(dispatched) == app_bot_wake.WAKE_LIMIT + 2


@pytest.mark.asyncio
async def test_a_bot_without_an_open_thread_cannot_be_woken(wake_world, monkeypatch):
    state, _slot, dispatched, _clock = wake_world
    monkeypatch.setattr(app_bot_wake, "_thread", lambda _slug: ("", ""))
    assert await app_bot_wake.request_wake(state, APP, SLUG, "r") == app_bot_wake.NO_THREAD
    monkeypatch.setattr(app_bot_wake, "_thread", lambda _slug: None)
    assert await app_bot_wake.request_wake(state, APP, SLUG, "r") == app_bot_wake.NO_BOT
    assert dispatched == []


# ── the owner's ticked list ──


@pytest.mark.asyncio
async def test_an_app_token_cannot_tick_bots_for_itself(tmp_path):
    from kiro_crew.apps.routes_bot_grants import handle_bot_grants

    def _never(*_a, **_k):
        raise AssertionError("an app must not write its own Bot grants")

    req = _request(tmp_path, "PUT", f"/api/apps/{APP}/bot-grants", {"name": APP})

    async def _json():
        return {"bots": [SLUG]}

    req.json = _json  # type: ignore[method-assign]
    with patch("kiro_crew.apps.routes_bot_grants.set_app_granted_bots", _never):
        resp = await handle_bot_grants(req)
    assert resp.status in (401, 403)


@pytest.mark.asyncio
async def test_the_owner_ticks_bots_for_an_app(tmp_path, monkeypatch):
    from kiro_crew.apps import routes_bot_grants

    stored: list[list[str]] = []
    monkeypatch.setattr(
        routes_bot_grants,
        "set_app_granted_bots",
        lambda _n, bots: stored.append(list(bots)) or list(bots),
    )
    monkeypatch.setattr(
        routes_bot_grants,
        "_view",
        lambda _n: {"bots": stored[-1], "declaredScopes": [], "scopes": []},
    )
    req = _request(
        tmp_path, "PUT", f"/api/apps/{APP}/bot-grants", {"name": APP}, app_claim="", user=OWNER
    )

    async def _json():
        return {"bots": [SLUG]}

    req.json = _json  # type: ignore[method-assign]
    resp = await routes_bot_grants.handle_bot_grants(req)
    assert resp.status == 200
    assert stored == [[SLUG]]


def test_the_record_keeps_only_valid_bot_slugs():
    meta = InstalledApp.from_dict({"name": APP, "botGrants": [SLUG, "../etc", SLUG, 7, "Bad"]})
    assert meta.botGrants == [SLUG]
    assert InstalledApp.from_dict({"name": APP, "botGrants": "code-reviewer"}).botGrants == []


# ── the manifest scopes ──


def test_permissions_keep_only_known_bot_scopes():
    perms = Permissions.from_dict({"bots": ["read", "admin", "wake", "read"]})
    assert perms.bots == ["read", "wake"]
    assert Permissions.from_dict({"bots": "read"}).bots == []
    assert perms.to_dict()["bots"] == ["read", "wake"]


def test_an_update_that_adds_a_bot_scope_waits_for_the_owner():
    old = Permissions.from_dict({"api": ["/api/members"], "bots": ["read"]})
    new = Permissions.from_dict({"api": ["/api/members"], "bots": ["read", "wake"]})
    approved = _approved_grants_after_manifest_change(
        existing_approved={"api": ["/api/members"], "events": [], "bots": ["read"]},
        old_permissions=grant_source(SimpleNamespace(permissions=old, contributes=None)),
        new_permissions=grant_source(SimpleNamespace(permissions=new, contributes=None)),
    )
    assert approved["bots"] == ["read"]
