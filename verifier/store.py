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

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ledger
import schema
from gate import Actor, GateMode, GateState, IllegalTransition, check

DEFAULT_TTL_S = 300


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        for stmt in schema.create_sql():
            self.db.execute(stmt)
        self.db.commit()

    # ── helpers ───────────────────────────────────────────────────────────
    def _insert(self, table: str, row: dict) -> dict:
        row = {"id": row.get("id") or _uuid(), "created_at": _now(), **row}
        cols = ",".join(row)
        marks = ",".join("?" * len(row))
        self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                        list(row.values()))
        self.db.commit()
        return row

    def get(self, table: str, id_: str) -> dict | None:
        r = self.db.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
        return dict(r) if r else None

    # ── ledger ────────────────────────────────────────────────────────────
    def _head(self, gate_id: str) -> str:
        # rowid, not ts: two events can share a timestamp, and rowid is the
        # same ordering chain() reads back in.
        r = self.db.execute(
            "SELECT hash FROM audit_events WHERE gate_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (gate_id,)).fetchone()
        return r["hash"] if r else ledger.GENESIS

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

    def chain(self, gate_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM audit_events WHERE gate_id = ? ORDER BY rowid",
            (gate_id,)).fetchall()
        return [dict(r) for r in rows]

    def verify_chain(self, gate_id: str) -> ledger.ChainResult:
        return ledger.verify_chain(self.chain(gate_id))

    # ── gates ─────────────────────────────────────────────────────────────
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

    def expire_if_due(self, gate_id: str, at: datetime | None = None) -> dict:
        gate = self.get("gates", gate_id)
        if gate and self.is_expired(gate, at) and GateState(gate["state"]) not in (
                GateState.FAIL, GateState.SEALED, GateState.SIGNED):
            return self.gate_transition(gate_id, GateState.FAIL, Actor.SYSTEM,
                                        {"reason": "expired"}, at=at)
        return gate

    # ── child records ─────────────────────────────────────────────────────
    def add_capture(self, gate_id: str, frame_index: int, derived: dict,
                    pc_task_id: str | None = None) -> dict:
        return self._insert("captures", {
            "gate_id": gate_id, "frame_index": frame_index,
            "pc_task_id": pc_task_id, "derived": ledger.canonical_json(derived),
        })

    def add_evidence(self, gate_id: str, check_no: int, score: float,
                     detail: dict | None = None) -> dict:
        row = self._insert("evidence", {
            "gate_id": gate_id, "check_no": check_no, "score": str(score),
            "detail": ledger.canonical_json(detail or {}),
        })
        self.audit(gate_id, "evidence", {"check_no": check_no, "score": score})
        return row

    def add_review(self, gate_id: str, reviewer_id: str, decision: str,
                   triggering_signal: str = "", notes: str = "") -> dict:
        row = self._insert("reviews", {
            "gate_id": gate_id, "reviewer_id": reviewer_id, "decision": decision,
            "triggering_signal": triggering_signal, "notes": notes,
        })
        self.audit(gate_id, "review", {"reviewer_id": reviewer_id, "decision": decision})
        return row

    def add_attestation(self, gate_id: str, sha256: str,
                        dns_record_id: str | None = None,
                        sealed_pdf_url: str | None = None) -> dict:
        row = self._insert("attestations", {
            "gate_id": gate_id, "sha256": sha256,
            "dns_record_id": dns_record_id, "sealed_pdf_url": sealed_pdf_url,
        })
        self.audit(gate_id, "attestation", {"sha256": sha256})
        return row

    def create_tenant(self, domain: str, api_key_hash: str,
                      policy_defaults: dict | None = None) -> dict:
        return self._insert("tenants", {
            "domain": domain, "api_key_hash": api_key_hash,
            "policy_defaults": ledger.canonical_json(policy_defaults or {}),
        })

    def create_workflow(self, tenant_id: str, kind: str, payload: dict | None = None,
                        agent_session_id: str | None = None) -> dict:
        return self._insert("workflows", {
            "tenant_id": tenant_id, "kind": kind,
            "payload": ledger.canonical_json(payload or {}),
            "agent_session_id": agent_session_id,
        })
