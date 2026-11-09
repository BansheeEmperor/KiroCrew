"""What the warm welcome reads before it posts (RFC section 3.9.1): which pull
requests to read live, and the digest that stops a re-send when nothing changed.
The gateway renders every fact (section 3.11).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from kiro_crew.dashboard import warm_task_brief_inputs as inputs


def _ask_texts(
    waiting: Sequence[Mapping[str, Any]], asks: Sequence[Mapping[str, Any]]
) -> list[str]:
    """The text of every open ask, where the live read looks for full PR URLs."""
    texts = [
        part
        for item in waiting
        for part in (str(item.get("text") or ""), *(str(o) for o in item.get("options") or []))
        if part
    ]
    for item in asks:
        values = (item.get("title"), item.get("summary"), item.get("decision"))
        texts.extend(str(v) for v in (*values, *(item.get("artifacts") or {}).values()) if v)
    return texts


def live_refs(
    work_items: Sequence[Mapping[str, Any]],
    waiting: Sequence[Mapping[str, Any]],
    terminal: Callable[[object], bool],
) -> list[inputs.PrRef]:
    """The pull requests to read before the brief may name them (M3): each
    open ask item's ledger PR, then full PR URLs in the asks' text, then
    every other open item's ledger PR. A bare ``#N`` is never guessed."""
    asks = [item for item in work_items if inputs.is_open_ask(item, terminal)]
    rest = [item for item in work_items if not terminal(item.get("state")) and item not in asks]
    ledger = [ref for item in asks if (ref := inputs.ledger_ref(item)) is not None]
    refs = inputs.pr_refs(_ask_texts(waiting, asks), ledger)
    for item in rest:
        ref = inputs.ledger_ref(item)
        if ref is not None and ref not in refs and len(refs) < inputs.MAX_LIVE_PRS:
            refs.append(ref)
    return refs


def facts_digest(ledger_digest: str, waiting: object, live: Sequence[Mapping[str, Any]]) -> str:
    """What a brief stood on: the stored ledgers, what waited and each PR
    read, minus read times. Equal digests mean nothing changed since."""
    reads = [
        {k: v for k, v in entry.items() if k not in ("read_at", "not_checked_since", "_read_epoch")}
        for entry in live
    ]
    blob = json.dumps(
        [ledger_digest, waiting, reads], ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


FACTS_DIGEST_KEY = "facts_digest"


def previous_facts_digest(
    rows: Sequence[Mapping[str, Any]], result_kind: str, miss_key: str
) -> str:
    """The facts digest of the newest delivered brief, or ""."""
    for row in reversed(rows):
        meta = row.get("meta")
        if isinstance(meta, Mapping) and meta.get("kind") == result_kind:
            digest = meta.get(FACTS_DIGEST_KEY)
            return digest if isinstance(digest, str) and not meta.get(miss_key) else ""
    return ""
