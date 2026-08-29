"""
Hash-chained audit ledger tests (implementation.md Step 3c).

The claim is "tamper-evident". These tests are what makes that a fact rather
than a slide.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ledger  # noqa: E402
from gate import Actor, GateMode, GateState, IllegalTransition  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def gate(store):
    t = store.create_tenant("acme.example", "hash")
    w = store.create_workflow(t["id"], "wire_transfer")
    return store.create_gate(w["id"], GateMode.AUTHORISE_ACTION)


def _walk(store, gate_id):
    for to, actor in ((GateState.CHALLENGED, Actor.AGENT),
                      (GateState.CAPTURED, Actor.HUMAN),
                      (GateState.SCORED, Actor.SYSTEM),
                      (GateState.PASS, Actor.SYSTEM),
                      (GateState.SIGNED, Actor.HUMAN),
                      (GateState.SEALED, Actor.SYSTEM)):
        store.gate_transition(gate_id, to, actor)


# ── canonical encoding ────────────────────────────────────────────────────
def test_canonical_json_ignores_key_order():
    assert ledger.canonical_json({"b": 1, "a": 2}) == ledger.canonical_json({"a": 2, "b": 1})


def test_canonical_json_has_no_incidental_whitespace():
    assert ledger.canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_hash_changes_with_every_input():
    base = ("p", "g", "t", {"x": 1}, "ts")
    h = ledger.event_hash(*base)
    for i, alt in enumerate(("p2", "g2", "t2", {"x": 2}, "ts2")):
        args = list(base)
        args[i] = alt
        assert ledger.event_hash(*args) != h


# ── chain integrity ───────────────────────────────────────────────────────
def test_chain_starts_at_genesis(store, gate):
    assert store.chain(gate["id"])[0]["prev_hash"] == ledger.GENESIS


def test_full_lifecycle_chain_verifies(store, gate):
    _walk(store, gate["id"])
    r = store.verify_chain(gate["id"])
    assert r.ok and r.broken_at is None
    assert r.length == 7  # creation + six transitions


def test_empty_chain_is_vacuously_valid(store):
    assert store.verify_chain("no-such-gate").ok


def test_altering_a_payload_breaks_the_chain(store, gate):
    _walk(store, gate["id"])
    events = store.chain(gate["id"])
    events[2]["payload"] = '{"actor":"agent","from":"CHALLENGED","to":"CAPTURED"}'
    r = ledger.verify_chain(events)
    assert not r.ok and r.broken_at == 2
    assert "altered" in r.reason


def test_deleting_an_event_breaks_the_chain(store, gate):
    _walk(store, gate["id"])
    events = store.chain(gate["id"])
    del events[3]
    r = ledger.verify_chain(events)
    assert not r.ok and r.broken_at == 3
    assert "prev_hash mismatch" in r.reason


def test_reordering_events_breaks_the_chain(store, gate):
    _walk(store, gate["id"])
    events = store.chain(gate["id"])
    events[2], events[3] = events[3], events[2]
    assert not ledger.verify_chain(events).ok


def test_head_advances_and_links(store, gate):
    _walk(store, gate["id"])
    events = store.chain(gate["id"])
    for prev, cur in zip(events, events[1:]):
        assert cur["prev_hash"] == prev["hash"]


# ── append-only enforcement ───────────────────────────────────────────────
def test_audit_events_reject_update(store, gate):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE audit_events SET type = 'forged' WHERE gate_id = ?",
                         (gate["id"],))


def test_audit_events_reject_delete(store, gate):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM audit_events WHERE gate_id = ?", (gate["id"],))


# ── refusals are evidence ─────────────────────────────────────────────────
def test_a_refused_transition_is_recorded(store, gate):
    with pytest.raises(IllegalTransition):
        store.gate_transition(gate["id"], GateState.SIGNED, Actor.AGENT)

    refusals = [e for e in store.chain(gate["id"]) if e["type"] == "transition.refused"]
    assert len(refusals) == 1
    assert "agent" in refusals[0]["payload"]
    assert store.verify_chain(gate["id"]).ok, "refusals must not break the chain"


def test_an_agent_signing_attempt_survives_in_the_ledger(store, gate):
    """
    The auditor's question is "did anything try to bypass the human?". The
    answer has to be permanent.
    """
    store.gate_transition(gate["id"], GateState.CHALLENGED, Actor.AGENT)
    for _ in range(3):
        with pytest.raises(IllegalTransition):
            store.gate_transition(gate["id"], GateState.SIGNED, Actor.AGENT)

    assert sum(e["type"] == "transition.refused" for e in store.chain(gate["id"])) == 3
    assert store.verify_chain(gate["id"]).ok
