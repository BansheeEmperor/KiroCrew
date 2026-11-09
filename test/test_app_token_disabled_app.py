"""An app token is accepted only while its app is installed and enabled.

The token is HMAC-signed with the gateway key, so its signature alone stays
valid after the owner disables or uninstalls the app. These tests run a real
app token through the real ``token_auth_middleware`` against a real
``installed.json`` record and pin that:

* a disabled or uninstalled app's token is refused the way an invalid token is;
* a token minted before a disable stays refused after the app is enabled again,
  while a fresh exchange works;
* an enabled app's token and a dashboard token are unaffected.

One injected clock drives both the token mint and the record write, so the
order of "minted" and "disabled" is set by the test, never by timer resolution.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import math
import os
import threading
import time
import types

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.apps import manager_records
from kiro_crew.apps.manager_records import InstalledApp, _write_installed, app_dir
from kiro_crew.dashboard import revocation_gen, token_auth

_APP = "token-app"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def module(self) -> types.ModuleType:
        fake = types.ModuleType("fake_time")
        fake.__dict__.update(time.__dict__)
        fake.time = lambda: self.now  # type: ignore[attr-defined]
        fake.monotonic = lambda: self.now  # type: ignore[attr-defined]
        return fake

    def tick(self) -> None:
        self.now += 1.0


@pytest.fixture
def clock(tmp_path, monkeypatch) -> _Clock:
    monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("kiro_crew.apps.manager_records.config_dir", lambda: tmp_path)
    monkeypatch.setattr(token_auth, "_get_secret", lambda: b"app-token-disabled-key")
    monkeypatch.setattr(token_auth, "_state", token_auth.TokenStateManager())
    monkeypatch.setattr(token_auth, "_app_perms_cache", {})
    monkeypatch.setattr(token_auth, "_revoked_store_singleton", None)
    monkeypatch.setattr(revocation_gen, "_gen", 0)
    monkeypatch.setattr(manager_records, "_token_cutoff_cache", {})
    c = _Clock()
    fake = c.module()
    # Through the facade: ``time`` is bound by several token_auth owners.
    monkeypatch.setattr("kiro_crew.dashboard.token_auth.time", fake)
    monkeypatch.setattr("kiro_crew.apps.manager_records.time", fake)
    return c


def _set_enabled(clock: _Clock, enabled: bool) -> None:
    clock.tick()
    _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=enabled))
    clock.tick()


def _mint(clock: _Clock) -> str:
    clock.tick()
    return token_auth.generate_token(_APP, app=_APP)


def _valid(token: str) -> bool:
    return token_auth.validate_token_with_app(token)[0]


def test_a_disabled_apps_token_is_refused_like_an_invalid_one(clock) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    assert _valid(token)

    _set_enabled(clock, False)
    assert token_auth.validate_token_with_app(token) == (False, "", "invalid signature", "")


def test_an_uninstalled_apps_token_is_refused(clock) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    (app_dir(_APP) / "installed.json").unlink()

    assert not _valid(token)


def test_a_token_from_before_a_disable_stays_dead_after_reenable(clock) -> None:
    _set_enabled(clock, True)
    old = _mint(clock)
    _set_enabled(clock, False)
    _set_enabled(clock, True)

    assert not _valid(old)
    assert _valid(_mint(clock))


def test_an_enabled_app_and_a_dashboard_token_are_unaffected(clock) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    _set_enabled(clock, True)

    assert _valid(token)
    assert token_auth.validate_token_with_app(token_auth.generate_token("owner"))[:2] == (
        True,
        "owner",
    )


def test_a_token_minted_in_the_disables_own_tick_stays_dead(clock) -> None:
    _set_enabled(clock, True)
    clock.tick()
    # A coarse clock gives the mint and the disable the same timestamp.
    same_tick = token_auth.generate_token(_APP, app=_APP)
    _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=False))
    _set_enabled(clock, True)

    assert not _valid(same_tick)


def test_a_token_minted_one_tick_after_the_cutoff_is_accepted(clock) -> None:
    _set_enabled(clock, True)
    clock.tick()
    _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=False))
    _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=True))
    clock.tick()

    assert _valid(token_auth.generate_token(_APP, app=_APP))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a POSIX FIFO")
def test_a_fifo_in_place_of_the_record_refuses_the_token_without_blocking(clock) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    record = app_dir(_APP) / "installed.json"
    record.unlink()
    os.mkfifo(record)

    # A blocking open would never return; the bound turns that into a failure.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        verdict = pool.submit(token_auth.validate_token_with_app, token).result(timeout=30)
    finally:
        # Never wait on a worker stuck in open(): the timeout above is the verdict.
        pool.shutdown(wait=False, cancel_futures=True)
    assert verdict[0] is False


def test_an_integer_cutoff_too_large_for_a_float_refuses_tokens_and_still_disables(
    clock,
) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    record = app_dir(_APP) / "installed.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["appTokensNotBefore"] = 10**400
    record.write_text(json.dumps(data), encoding="utf-8")

    assert not _valid(token)
    assert InstalledApp.from_dict(data).appTokensNotBefore == math.inf
    _set_enabled(clock, False)
    assert json.loads(record.read_text(encoding="utf-8"))["enabled"] is False


def _count_reads(monkeypatch) -> list[int]:
    reads: list[int] = []
    real = manager_records.app_token_cutoff

    def _counting(name: str):
        reads.append(threading.get_ident())
        return real(name)

    monkeypatch.setattr(manager_records, "app_token_cutoff", _counting)
    return reads


def test_the_record_is_read_once_per_ttl(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    reads = _count_reads(monkeypatch)

    assert _valid(token) and _valid(token)
    assert len(reads) == 1

    # Another process (the CLI) disables the app: seen once the entry expires.
    record = app_dir(_APP) / "installed.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["enabled"] = False
    record.write_text(json.dumps(data), encoding="utf-8")
    clock.now += manager_records._TOKEN_CUTOFF_TTL
    assert not _valid(token)
    assert len(reads) == 2


def test_a_record_write_clears_the_cached_cutoff(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    assert _valid(token)
    reads = _count_reads(monkeypatch)

    _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=False))

    assert not _valid(token)
    assert len(reads) == 1


def test_a_failed_read_is_cached_as_a_refusal(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)

    def _boom(_name: str):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(manager_records, "app_token_cutoff", _boom)
    assert not _valid(token)
    assert manager_records._token_cutoff_cache[_APP][1] is None


async def _ok(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@contextlib.asynccontextmanager
async def _client():
    app = web.Application(middlewares=[token_auth.token_auth_middleware()])
    app.router.add_get("/api/apps/{name}/manifest", _ok)
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.mark.asyncio
async def test_middleware_refuses_a_disabled_apps_token_on_its_own_route(clock) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    async with _client() as client:

        async def status(tok: str) -> int:
            resp = await client.get(f"/api/apps/{_APP}/manifest", params={"token": tok})
            return resp.status

        assert await status(token) == 200
        _set_enabled(clock, False)
        assert await status(token) == 403
        _set_enabled(clock, True)
        assert await status(token) == 403
        assert await status(_mint(clock)) == 200


@pytest.mark.asyncio
async def test_middleware_reads_the_record_off_the_event_loop(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    reads = _count_reads(monkeypatch)
    loop_thread = threading.get_ident()
    async with _client() as client:
        resp = await client.get(f"/api/apps/{_APP}/manifest", params={"token": token})
        assert resp.status == 200

    assert reads and loop_thread not in reads


def _forged(app: str) -> str:
    payload = token_auth._b64url_encode(json.dumps({"app": app}).encode())
    return f"{payload}.not-a-signature"


@pytest.mark.asyncio
async def test_a_forged_tokens_app_claim_primes_nothing(clock, monkeypatch) -> None:
    reads = _count_reads(monkeypatch)

    await token_auth.prime_app_token_cutoffs(_forged(_APP), _forged("../../outside"))

    assert reads == []
    assert manager_records._token_cutoff_cache == {}


@pytest.mark.asyncio
async def test_a_signed_token_with_an_invalid_app_name_primes_nothing(clock, monkeypatch) -> None:
    reads = _count_reads(monkeypatch)
    token = token_auth.generate_token("x", app="../../outside")

    await token_auth.prime_app_token_cutoffs(token)

    assert reads == []
    assert not _valid(token)
    assert manager_records._token_cutoff_cache == {}


def test_the_cache_never_holds_more_than_its_cap(clock, monkeypatch) -> None:
    monkeypatch.setattr(manager_records, "_TOKEN_CUTOFF_MAX_ENTRIES", 3)
    for i in range(5):
        manager_records.cached_app_token_cutoff(f"app-{i}")

    assert list(manager_records._token_cutoff_cache) == ["app-2", "app-3", "app-4"]


def test_a_read_overtaken_by_a_disable_is_not_cached(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    real = manager_records.app_token_cutoff
    calls: list[float | None] = []

    def _read_then_disable(name: str):
        cutoff = real(name)
        if not calls:
            # The disable lands while this read is in flight.
            _write_installed(_APP, InstalledApp(name=_APP, version="1.0.0", enabled=False))
        calls.append(cutoff)
        return cutoff

    monkeypatch.setattr(manager_records, "app_token_cutoff", _read_then_disable)

    assert not _valid(token)
    assert calls[0] is not None and calls[1] is None
    assert manager_records._token_cutoff_cache[_APP][1] is None


def test_a_slow_primed_read_is_still_fresh_for_the_check_after_it(clock, monkeypatch) -> None:
    _set_enabled(clock, True)
    token = _mint(clock)
    real = manager_records.app_token_cutoff
    reads: list[str] = []

    def _slow(name: str):
        reads.append(name)
        clock.now += manager_records._TOKEN_CUTOFF_TTL  # the read outlasts the TTL
        return real(name)

    monkeypatch.setattr(manager_records, "app_token_cutoff", _slow)
    manager_records.cached_app_token_cutoff(_APP)  # the prime, off the loop

    assert _valid(token)  # the check on the loop reuses it
    assert reads == [_APP]
