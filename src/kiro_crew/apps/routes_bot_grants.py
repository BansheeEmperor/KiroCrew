"""``GET/PUT /api/apps/{name}/bot-grants``: the Bots the owner ticked for an app.

Owner only, app tokens included: the path sits in the app's own namespace
(``token_auth_app_scope._app_owns_path``), so without the owner gate an app could
tick Bots for itself. ``apps.bot_grants`` says what the list grants.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from kiro_crew.apps.bot_grants import app_bot_scopes, app_granted_bots, set_app_granted_bots
from kiro_crew.apps.manager_records import MAX_BOT_GRANTS
from kiro_crew.sel import sel


async def _owner_denial(request: web.Request, operation: str) -> web.Response | None:
    from kiro_crew.dashboard.handlers._shared import require_owner_dashboard_request

    return await require_owner_dashboard_request(request, operation)


def _view(name: str) -> dict[str, object]:
    from kiro_crew.apps.manager import get_app_manifest

    manifest = get_app_manifest(name)
    return {
        "bots": list(app_granted_bots(name)),
        "declaredScopes": list(manifest.permissions.bots) if manifest else [],
        "scopes": sorted(app_bot_scopes(name)),
    }


async def handle_bot_grants(request: web.Request) -> web.Response:
    """GET reads the ticked list and the scopes; PUT ``{"bots": [slug, ...]}`` replaces the list."""
    name = request.match_info["name"]
    write = request.method == "PUT"
    denied = await _owner_denial(request, "apps.bot_grants." + ("write" if write else "read"))
    if denied is not None:
        return denied
    if not write:
        from kiro_crew.apps.manager import get_app

        if not await asyncio.to_thread(get_app, name):
            return web.json_response({"error": f"app {name!r} not installed"}, status=404)
        return web.json_response(await asyncio.to_thread(_view, name))
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    bots = body.get("bots") if isinstance(body, dict) else None
    if (
        not isinstance(bots, list)
        or len(bots) > MAX_BOT_GRANTS
        or not all(isinstance(b, str) for b in bots)
    ):
        return web.json_response(
            {"error": f"bots must be a list of at most {MAX_BOT_GRANTS} Bot ids"}, status=400
        )
    from kiro_crew.apps.manager_install import app_lifecycle_lock

    async with app_lifecycle_lock(name):
        stored = await asyncio.to_thread(set_app_granted_bots, name, bots)
    if stored is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    sel().log_api_access(
        caller=str(request.get("user", "") or "dashboard"),
        operation="apps.bot_grants.write",
        outcome="success",
        resources=f"name={name!r} bots={len(stored)}",
    )
    return web.json_response(await asyncio.to_thread(_view, name))


__all__ = ["handle_bot_grants"]
