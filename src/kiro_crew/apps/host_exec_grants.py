"""Gateway-held, sandbox-sealed enablement grants for apps that run code OUTSIDE the sandbox.

An app that runs code outside the agent sandbox (Dev Fleet's "pod" execs worktree
code on the host) must not be enable-able from inside the jail. The
``apps/<name>/installed.json`` enablement record cannot carry that authorization: it
lives under the agent-writable, renamable ``apps/<name>`` directory, so a sandboxed
shell can rename the directory aside (the read-only bind moves with the renamed
directory on the namespace launcher, see ``sandbox_argv`` "a renamed parent carries
the read-only mount with it"), recreate the path, and write ``enabled: true`` into a
fresh, unmasked file. A gateway reading that record would start a pod outside the
sandbox -- the exact containment boundary the gate exists to hold.

This module keeps the authorization as a marker under a TOP-LEVEL crew-home directory
(``host-exec-grants/``) the sandbox seals read-only and pre-creates, which -- as a
direct crew-home child -- is the pattern the sandbox does not let an agent rename
around (``aws-control-staging``, ``quarantined-clones``), unlike a leaf nested under
``apps/``.

The marker is written ONLY from the owner-authenticated enable path (``enable_app``,
which requires an owner dashboard request and refuses app tokens), for whichever app
the owner enabled. It is NEVER derived from the enablement record, so re-writing a
forged ``installed.json`` (for example on the boot re-registration of a builtin)
cannot mint it. It is revoked on every write that disables the app and on uninstall,
so a disabled app can never keep one.

These functions are generic -- they act on whatever app name they are handed and know
no specific app id, so core (`sandbox_leaves`, `manager_records`, `manager_uninstall`)
stays app-agnostic. The pod gate inside ``apps/builtins/dev_fleet/`` and the app token
exchange (``dashboard.token_auth_tokens``) read whether a marker exists.

**Seals beside the marker.** The same sealed directory holds two more files per app,
each written only by the gateway from a value it holds in memory, never read back from
``apps/<name>/``:

- ``<name>.grants``: the ``permissions.api`` and ``permissions.events`` entries the
  gateway approved, written with every ``approved-grants.json`` write (install, update,
  registration, an owner's consent) and, for a builtin, from its shipped manifest at
  boot. The app-token checks grant only entries in it, so rewriting ``app.json`` or
  ``approved-grants.json`` cannot widen a token.
- ``<name>.secret``: the SHA-256 of the ``.app_secret`` the gateway wrote. The token
  exchange compares against it, so a replacement ``.app_secret`` planted in a recreated
  ``apps/<name>/`` mints nothing.

App names never contain a dot, so neither file can be read as a marker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Mapping, Sequence

from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.paths import config_dir

logger = logging.getLogger(__name__)

#: Top-level crew-home directory holding one empty marker file per app the owner has
#: enabled through the authenticated enable path, and each app's seals (below). Sealed read-only in the sandbox and
#: pre-created so it exists (and is a mount point) before any spawn. Mirrored by name
#: in ``sandbox_leaves._CREW_READONLY_LEAVES`` / ``_CREW_CHILD_READABLE_LEAVES`` and
#: ``sandbox_precreate._CREW_PRECREATE_READONLY_DIR_LEAVES``.
GRANTS_LEAF = "host-exec-grants"


def _grant_path(name: str):
    return config_dir() / GRANTS_LEAF / name


def grant_host_exec(name: str) -> bool:
    """Record, from the unsandboxed gateway, that *name* is owner-enabled to run
    outside the sandbox. Call ONLY from the owner-authenticated enable path -- never
    derived from the enablement record. A write failure is logged, never raised, and
    returns False so the caller can report it; every reader fails CLOSED on a
    missing grant.
    """
    try:
        path = _grant_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError:
        logger.warning("could not record host-exec grant for %r", name, exc_info=True)
        return False
    return True


def revoke_host_exec(name: str) -> None:
    """Remove *name*'s grant. Safe to call for any app on any write that disables it
    and on uninstall: a no-op when no grant exists."""
    try:
        _grant_path(name).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not revoke host-exec grant for %r", name, exc_info=True)


def host_exec_granted(name: str) -> bool:
    """Whether the gateway recorded an owner-authenticated grant for *name*.

    The pod gate's authority. Unforgeable from the sandbox: the grant directory is
    sealed read-only and cannot be renamed around, and the marker is written only on an
    owner-authenticated enable, never from the enablement record. A forged
    ``installed.json`` never produces one.
    """
    try:
        return _grant_path(name).is_file()
    except OSError:
        return False


#: The grant families an app token is checked against, the ones ``<name>.grants`` seals.
SEALED_GRANT_FAMILIES = ("api", "events")
_GRANTS_SUFFIX = ".grants"
_SECRET_SUFFIX = ".secret"


def _seal_path(name: str, suffix: str):
    return config_dir() / GRANTS_LEAF / f"{name}{suffix}"


def _read_seal(name: str, suffix: str) -> str | None:
    try:
        return _seal_path(name, suffix).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def seal_token_grants(
    name: str, grants: Mapping[str, Sequence[str]] | None, *, widen: bool = True
) -> None:
    """Seal *grants*' api/events entries for *name*; ``None`` removes the seal.

    Called with the value the gateway is about to store, never one read back from the
    app's directory. *widen* False keeps only entries the standing seal already holds
    (an update or re-registration, which may only narrow what the owner approved); with
    no standing seal it seals *grants* as given. Raises ``OSError`` like the
    approved-set write it accompanies.
    """
    path = _seal_path(name, _GRANTS_SUFFIX)
    if grants is None:
        path.unlink(missing_ok=True)
        return
    body: dict[str, list[str]] = {}
    for family in SEALED_GRANT_FAMILIES:
        entries = [e for e in grants.get(family, ()) if isinstance(e, str) and e]
        prior = None if widen else _sealed_set(name, family)
        body[family] = entries if prior is None else [e for e in entries if e in prior]
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(body, sort_keys=True) + "\n")


def _sealed_set(name: str, family: str) -> frozenset[str] | None:
    text = _read_seal(name, _GRANTS_SUFFIX)
    if text is None:
        return None
    try:
        raw: Any = json.loads(text)
    except ValueError:
        return frozenset()
    entries = raw.get(family) if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(e for e in entries if isinstance(e, str) and e)


def has_sealed_grants(name: str) -> bool:
    """Whether a grant seal exists for *name* (an install from before it has none)."""
    return _read_seal(name, _GRANTS_SUFFIX) is not None


def sealed_grants(name: str, family: str) -> frozenset[str] | None:
    """The *family* entries an app token for *name* may be granted, or ``None``.

    ``None`` without the owner-enable marker or without a grant seal, so the caller
    grants nothing declared. A seal that cannot be parsed seals nothing (empty set).
    """
    if not host_exec_granted(name):
        return None
    return _sealed_set(name, family)


def seal_app_secret(name: str, secret: str) -> bool:
    """Record the digest of the ``.app_secret`` the gateway just wrote for *name*.

    Returns False (logged) when it cannot be written; the token exchange then fails
    closed for the app until the secret is written again.
    """
    try:
        path = _seal_path(name, _SECRET_SUFFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, hashlib.sha256(secret.encode("utf-8")).hexdigest() + "\n")
    except OSError:
        logger.warning("could not seal the app secret of %r", name, exc_info=True)
        return False
    return True


def has_sealed_secret(name: str) -> bool:
    """Whether the gateway recorded the digest of *name*'s secret."""
    return _read_seal(name, _SECRET_SUFFIX) is not None


def sealed_secret_matches(name: str, provided: str) -> bool:
    """Whether *provided* is the secret the gateway last wrote for *name* (constant time)."""
    stored = (_read_seal(name, _SECRET_SUFFIX) or "").strip()
    if not stored or not provided:
        return False
    digest = hashlib.sha256(provided.encode("utf-8", errors="surrogatepass")).hexdigest()
    return hmac.compare_digest(stored, digest)


def forget_app_seals(name: str) -> None:
    """Remove *name*'s grant and secret seals, on uninstall. Best-effort."""
    for suffix in (_GRANTS_SUFFIX, _SECRET_SUFFIX):
        try:
            _seal_path(name, suffix).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove a seal of %r", name, exc_info=True)
