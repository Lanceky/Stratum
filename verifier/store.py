"""
Local store implementing the Step 3 data model.

Xano is the system of record in production. This is a byte-for-byte equivalent
of the same schema and the same transition rules, so that:

  * Steps 4-7 can be built and tested before a Xano credential arrives, and
  * the Xano function stack has a reference implementation to be checked
    against rather than being the only copy of the logic.

`gate_transition` is the only function permitted to change gates.state. Every
call — accepted or refused — appends to the hash-chained audit ledger, so a
rejected attempt by an agent to sign is itself permanent evidence.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ledger
import schema
from gate import (Actor, GateMode, GateState, IllegalTransition, check,
                  is_escalation)

DEFAULT_TTL_S = 300


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _locked(fn):
    """
    Serialise one whole store operation.

    Not only for SQLite's sake, though one connection shared across the
    server's threadpool is reason enough. The operations that matter here are
    read-then-write pairs, and holding the lock for just the write would not
    save them:

      * `audit` reads the chain head, then writes the block that points at it.
        Interleaved, two appends read the same head and write two blocks
        claiming the same parent — a forked chain, which verify_chain then
        reports as tampering that never happened.
      * `gate_transition` reads the state, checks the move is legal, then
        writes the new state. Interleaved, two callers both read REQUESTED and
        both pass a check that was only ever true for one of them.

    Reentrant because the public methods call each other: `create_gate` calls
    `audit`, `expire_if_due` calls `gate_transition`.
    """
    @functools.wraps(fn)
    def go(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return go


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        for stmt in schema.create_sql():
            self.db.execute(stmt)
        self.db.commit()

    # ── helpers ───────────────────────────────────────────────────────────
    @_locked
    def _insert(self, table: str, row: dict) -> dict:
        row = {"id": row.get("id") or _uuid(), "created_at": _now(), **row}
        cols = ",".join(row)
        marks = ",".join("?" * len(row))
        self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                        list(row.values()))
        self.db.commit()
        return row

    @_locked
    def get(self, table: str, id_: str) -> dict | None:
        r = self.db.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
        return dict(r) if r else None

    @_locked
    def gates_in_state(self, state: GateState | str, limit: int = 50) -> list[dict]:
        """
        The review queue.

        Ordered oldest first, deliberately. A reviewer working newest-first
        leaves the oldest gate to expire, and an expired gate is a person who
        was told to wait and then silently refused.
        """
        rows = self.db.execute(
            "SELECT * FROM gates WHERE state = ? ORDER BY created_at ASC LIMIT ?",
            (str(GateState(state)), limit)).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def census(self) -> dict[str, int]:
        """
        How many gates exist, by state.

        Separate from `gates_in_state` because that one takes a limit and
        returns rows: counting by listing would silently stop at 50 and report
        a total that is really a page size.
        """
        rows = self.db.execute(
            "SELECT state, COUNT(*) AS n FROM gates GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}

    @_locked
    def evidence_for(self, gate_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM evidence WHERE gate_id = ? ORDER BY check_no ASC",
            (gate_id,)).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def reviews_for(self, gate_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM reviews WHERE gate_id = ? ORDER BY created_at ASC",
            (gate_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── ledger ────────────────────────────────────────────────────────────
    @_locked
    def _head(self, gate_id: str) -> str:
        # rowid, not ts: two events can share a timestamp, and rowid is the
        # same ordering chain() reads back in.
        r = self.db.execute(
            "SELECT hash FROM audit_events WHERE gate_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (gate_id,)).fetchone()
        return r["hash"] if r else ledger.GENESIS

    @_locked
    def audit(self, gate_id: str, type_: str, payload: Any = None) -> dict:
        """Append one event. The only writer of audit_events."""
        prev, ts = self._head(gate_id), _now()
        return self._insert("audit_events", {
            "gate_id": gate_id,
            "type": type_,
            "payload": ledger.canonical_json(payload),
            "prev_hash": prev,
            "hash": ledger.event_hash(prev, gate_id, type_, payload, ts),
            "ts": ts,
        })

    @_locked
    def chain(self, gate_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM audit_events WHERE gate_id = ? ORDER BY rowid",
            (gate_id,)).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def verify_chain(self, gate_id: str) -> ledger.ChainResult:
        return ledger.verify_chain(self.chain(gate_id))

    @_locked
    def escalations(self, gate_id: str) -> list[dict]:
        """
        Every time a non-human reached for a step only a human may take.

        Read back out of the chain rather than tracked in a column of its own,
        because the chain is the record. A separate counter could disagree with
        it, and if the two ever differed there would be no way to say which was
        lying.
        """
        out = []
        for e in self.chain(gate_id):
            if e["type"] != "transition.refused":
                continue
            payload = json.loads(e["payload"]) if e["payload"] else {}
            if not is_escalation(payload.get("to"), payload.get("actor")):
                continue
            out.append({"at": e["ts"], "hash": e["hash"], **payload})
        return out

    @_locked
    def rewrite_past_event(self, gate_id: str, index: int,
                           payload: Any) -> dict:
        """
        Edit an event that has already been written, in place.

        Nothing in the API reaches this. It is here so the demo can show what
        the chain is actually for: the threat is not a caller with a stolen
        key — that caller cannot alter a past event at all, only append — but
        somebody at the database with enough privilege to drop the guard.

        So this drops the guard, edits the row, and puts the guard back,
        which is the most favourable possible version of that attack: no
        trace left in the schema, no error raised, the row simply different
        than it was. The stored hash is left alone, because an attacker who
        recomputed it would still have to recompute every hash after it, and
        the point of the exercise is that verify_chain notices either way.
        """
        events = self.chain(gate_id)
        if not 0 <= index < len(events):
            raise IndexError(
                f"gate has {len(events)} events, no index {index}")

        event = events[index]
        before = event["payload"]
        after = ledger.canonical_json(payload)

        guard = schema.trigger_name("audit_events", "UPDATE")
        self.db.execute(f"DROP TRIGGER IF EXISTS {guard}")
        try:
            self.db.execute("UPDATE audit_events SET payload = ? WHERE id = ?",
                            (after, event["id"]))
            self.db.commit()
        finally:
            self.db.execute(schema.trigger_sql("audit_events", "UPDATE"))
            self.db.commit()

        return {"index": index, "type": event["type"], "hash": event["hash"],
                "before": before, "after": after}

    # ── gates ─────────────────────────────────────────────────────────────
    @_locked
    def create_gate(self, workflow_id: str, mode: GateMode | str,
                    challenge_spec: dict | None = None,
                    ttl_s: int = DEFAULT_TTL_S) -> dict:
        gate = self._insert("gates", {
            "workflow_id": workflow_id,
            "mode": str(GateMode(mode)),
            "state": str(GateState.REQUESTED),
            "nonce": _uuid(),
            "challenge_spec": ledger.canonical_json(challenge_spec or {}),
            "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat(),
        })
        self.audit(gate["id"], "gate.created",
                   {"mode": gate["mode"], "state": gate["state"]})
        return gate

    def is_expired(self, gate: dict, at: datetime | None = None) -> bool:
        return (at or datetime.now(UTC)) > datetime.fromisoformat(gate["expires_at"])

    @_locked
    def gate_transition(self, gate_id: str, to: GateState | str, actor: Actor | str,
                        detail: dict | None = None,
                        at: datetime | None = None) -> dict:
        """
        The single choke point. Nothing else may write gates.state.

        Refusals raise IllegalTransition (HTTP 409) *and* are recorded, because
        an agent trying to sign is exactly the event an auditor wants to see.
        """
        gate = self.get("gates", gate_id)
        if gate is None:
            raise KeyError(f"no gate {gate_id}")

        frm, to, actor = GateState(gate["state"]), GateState(to), Actor(actor)
        expired = self.is_expired(gate, at)

        try:
            check(frm, to, actor, expired=expired)
        except IllegalTransition as exc:
            self.audit(gate_id, "transition.refused", {
                "from": str(frm), "to": str(to), "actor": str(actor),
                "reason": exc.reason, **(detail or {}),
            })
            raise

        self.db.execute("UPDATE gates SET state = ? WHERE id = ?", (str(to), gate_id))
        self.db.commit()
        self.audit(gate_id, "transition", {
            "from": str(frm), "to": str(to), "actor": str(actor), **(detail or {}),
        })
        return self.get("gates", gate_id)

    @_locked
    def expire_if_due(self, gate_id: str, at: datetime | None = None) -> dict:
        gate = self.get("gates", gate_id)
        if gate and self.is_expired(gate, at) and GateState(gate["state"]) not in (
                GateState.FAIL, GateState.SEALED, GateState.SIGNED):
            return self.gate_transition(gate_id, GateState.FAIL, Actor.SYSTEM,
                                        {"reason": "expired"}, at=at)
        return gate

    # ── child records ─────────────────────────────────────────────────────
    @_locked
    def add_capture(self, gate_id: str, frame_index: int, derived: dict,
                    pc_task_id: str | None = None) -> dict:
        return self._insert("captures", {
            "gate_id": gate_id, "frame_index": frame_index,
            "pc_task_id": pc_task_id, "derived": ledger.canonical_json(derived),
        })

    @_locked
    def add_evidence(self, gate_id: str, check_no: int, score: float,
                     detail: dict | None = None) -> dict:
        row = self._insert("evidence", {
            "gate_id": gate_id, "check_no": check_no, "score": str(score),
            "detail": ledger.canonical_json(detail or {}),
        })
        self.audit(gate_id, "evidence", {"check_no": check_no, "score": score})
        return row

    @_locked
    def add_review(self, gate_id: str, reviewer_id: str, decision: str,
                   triggering_signal: str = "", notes: str = "") -> dict:
        row = self._insert("reviews", {
            "gate_id": gate_id, "reviewer_id": reviewer_id, "decision": decision,
            "triggering_signal": triggering_signal, "notes": notes,
        })
        self.audit(gate_id, "review", {"reviewer_id": reviewer_id, "decision": decision})
        return row

    @_locked
    def add_attestation(self, gate_id: str, sha256: str,
                        dns_record_id: str | None = None,
                        sealed_pdf_url: str | None = None) -> dict:
        row = self._insert("attestations", {
            "gate_id": gate_id, "sha256": sha256,
            "dns_record_id": dns_record_id, "sealed_pdf_url": sealed_pdf_url,
        })
        self.audit(gate_id, "attestation", {"sha256": sha256})
        return row

    @_locked
    def create_tenant(self, domain: str, api_key_hash: str,
                      policy_defaults: dict | None = None) -> dict:
        return self._insert("tenants", {
            "domain": domain, "api_key_hash": api_key_hash,
            "policy_defaults": ledger.canonical_json(policy_defaults or {}),
        })

    @_locked
    def create_workflow(self, tenant_id: str, kind: str, payload: dict | None = None,
                        agent_session_id: str | None = None) -> dict:
        return self._insert("workflows", {
            "tenant_id": tenant_id, "kind": kind,
            "payload": ledger.canonical_json(payload or {}),
            "agent_session_id": agent_session_id,
        })

    # ── enrolments and claims ─────────────────────────────────────────────
    @_locked
    def create_enrolment(self, tenant_id: str, subject_ref: str,
                         identity_vector: dict, context: str | None = None) -> dict:
        """
        Put a person on a roster.

        `context` rides in subject_ref rather than getting a column of its own:
        the roster is per-campaign, and a person enrolled for one airdrop is
        not thereby on the roster for another. Prefixing keeps that scoping in
        one place and keeps the Xano schema unchanged.
        """
        ref = f"{context}:{subject_ref}" if context else subject_ref
        return self._insert("enrolments", {
            "tenant_id": tenant_id, "subject_ref": ref,
            "identity_vector": ledger.canonical_json(identity_vector),
            "enrolled_at": _now(),
        })

    @_locked
    def roster(self, context: str, limit: int = 5000) -> list[dict]:
        """
        Every enrolment a claim in this context must be swept against.

        Ordered oldest first so the sweep is deterministic: two runs over the
        same roster should name the same nearest enrolment, or a reviewer
        cannot reproduce what they were shown.
        """
        rows = self.db.execute(
            "SELECT * FROM enrolments WHERE subject_ref LIKE ? "
            "ORDER BY enrolled_at ASC, rowid ASC LIMIT ?",
            (f"{context}:%", limit)).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def claim_for(self, context: str, nullifier: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM claims WHERE context = ? AND nullifier = ?",
            (context, nullifier)).fetchone()
        return dict(r) if r else None

    @_locked
    def claim_for_gate(self, gate_id: str) -> dict | None:
        """
        The claim this gate authorised, if it authorised one.

        Most gates have none, so the certificate asks rather than assumes. A
        claim block rendered from an empty row would print a wallet field with
        nothing in it, which reads as a wallet that failed rather than a gate
        that was never about one.
        """
        r = self.db.execute(
            "SELECT * FROM claims WHERE gate_id = ? ORDER BY rowid DESC LIMIT 1",
            (gate_id,)).fetchone()
        return dict(r) if r else None

    @_locked
    def add_claim(self, gate_id: str, enrolment_id: str, context: str,
                  nullifier: str, address: str, verdict: str,
                  decided_by: str, signature: str | None = None) -> dict:
        """
        Record a settled claim.

        The unique index on (context, nullifier) is what actually stops a
        second claim, not the lookup a caller may have done first — under
        concurrency that lookup is a suggestion. sqlite3.IntegrityError is
        allowed to propagate for the same reason: swallowing it here would turn
        a refused double-claim into a silent success.
        """
        row = self._insert("claims", {
            "gate_id": gate_id, "enrolment_id": enrolment_id,
            "context": context, "nullifier": nullifier, "address": address,
            "verdict": verdict, "decided_by": decided_by, "signature": signature,
        })
        self.audit(gate_id, "claim", {
            "context": context, "nullifier": nullifier, "address": address,
            "verdict": verdict, "decided_by": decided_by,
            "signed": signature is not None,
        })
        return row
