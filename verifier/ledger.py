"""
Hash-chained audit ledger (implementation.md Step 3c).

    hash = sha256(prev_hash || gate_id || type || canonical_json(payload) || ts)

Append-only. Any edit or deletion of a past event breaks every hash after it,
so tampering is detectable without trusting the database. `verify_chain`
returns the first index that fails, which is what the reviewer console shows.

The canonical encoding matters more than it looks: if two nodes serialise the
same payload differently, they compute different hashes and the chain appears
broken when nothing is wrong. Sorted keys, no whitespace, UTF-8.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

GENESIS = "0" * 64


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def event_hash(prev_hash: str, gate_id: str, type_: str, payload: Any, ts: str) -> str:
    blob = "".join((prev_hash, gate_id, type_, canonical_json(payload), ts))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    length: int
    head: str
    broken_at: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "length": self.length, "head": self.head,
                "broken_at": self.broken_at, "reason": self.reason}


def verify_chain(events: Iterable[dict]) -> ChainResult:
    """
    Recompute every hash in order and compare.

    `events` must be ordered as written. Each needs gate_id, type, payload,
    prev_hash, hash, ts.
    """
    prev = GENESIS
    n = 0

    for i, e in enumerate(events):
        n = i + 1
        if e["prev_hash"] != prev:
            return ChainResult(False, n, prev, i,
                               f"prev_hash mismatch: expected {prev[:12]}…, "
                               f"got {e['prev_hash'][:12]}…")

        payload = e["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else None

        expect = event_hash(prev, e["gate_id"], e["type"], payload, e["ts"])
        if e["hash"] != expect:
            return ChainResult(False, n, prev, i,
                               f"hash mismatch at event '{e['type']}': "
                               f"payload or timestamp was altered")
        prev = e["hash"]

    return ChainResult(True, n, prev)
