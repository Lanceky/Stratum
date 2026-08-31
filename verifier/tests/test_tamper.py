"""
Tampering with a written event, and the chain noticing.

The append-only trigger stops a caller going through the API. These tests are
about the case where that guard is not enough: somebody underneath it, at the
database, with rights to drop the trigger. The claim is that the chain catches
the edit anyway, and says where.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as app_module
import schema
from gate import Actor, GateMode, GateState
from store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "tamper.db")


@pytest.fixture()
def gate(store):
    tenant = store.create_tenant("t.local", "k")
    wf = store.create_workflow(tenant["id"], "wire_transfer_approval")
    g = store.create_gate(wf["id"], GateMode.AUTHORISE_ACTION)
    store.gate_transition(g["id"], GateState.CHALLENGED, Actor.AGENT)
    return g


# ── the guard itself ──────────────────────────────────────────────────────

def test_a_past_event_cannot_be_updated_through_the_database(store, gate):
    event = store.chain(gate["id"])[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE audit_events SET payload = ? WHERE id = ?",
                         ("{}", event["id"]))


def test_a_past_event_cannot_be_deleted_through_the_database(store, gate):
    event = store.chain(gate["id"])[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM audit_events WHERE id = ?", (event["id"],))


# ── going around the guard ────────────────────────────────────────────────

def test_rewriting_an_event_breaks_the_chain_at_that_event(store, gate):
    assert store.verify_chain(gate["id"]).ok

    store.rewrite_past_event(gate["id"], 0, {"note": "nothing to see here"})

    result = store.verify_chain(gate["id"])
    assert not result.ok
    assert result.broken_at == 0


def test_the_break_is_reported_at_the_edited_event_not_the_first_one(store, gate):
    store.gate_transition(gate["id"], GateState.CAPTURED, Actor.HUMAN)
    assert len(store.chain(gate["id"])) > 2

    store.rewrite_past_event(gate["id"], 2, {"note": "edited"})

    assert store.verify_chain(gate["id"]).broken_at == 2


def test_the_reason_says_what_kind_of_edit_it_was(store, gate):
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})
    reason = store.verify_chain(gate["id"]).reason
    assert "payload or timestamp was altered" in reason


def test_events_before_the_edit_still_verify(store, gate):
    store.gate_transition(gate["id"], GateState.CAPTURED, Actor.HUMAN)
    chain = store.chain(gate["id"])

    store.rewrite_past_event(gate["id"], 2, {"note": "edited"})

    # Everything up to the edit is untouched, which is what makes broken_at
    # useful: it points at the earliest event that cannot be trusted.
    from ledger import verify_chain
    assert verify_chain(chain[:2]).ok


def test_the_edit_actually_lands_in_the_row(store, gate):
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})
    assert json.loads(store.chain(gate["id"])[0]["payload"]) == {"note": "edited"}


def test_the_stored_hash_is_left_alone(store, gate):
    before = store.chain(gate["id"])[0]["hash"]
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})
    assert store.chain(gate["id"])[0]["hash"] == before


# ── the guard is put back ─────────────────────────────────────────────────

def test_the_append_only_guard_is_restored_afterwards(store, gate):
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})

    event = store.chain(gate["id"])[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE audit_events SET payload = ? WHERE id = ?",
                         ("{}", event["id"]))


def test_the_guard_is_restored_even_if_the_edit_fails(store, gate, monkeypatch):
    real = store.db

    class FailsTheUpdate:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args, **kwargs):
            if sql.startswith("UPDATE audit_events"):
                raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, *args, **kwargs)

    monkeypatch.setattr(store, "db", FailsTheUpdate())
    with pytest.raises(sqlite3.OperationalError):
        store.rewrite_past_event(gate["id"], 0, {"note": "edited"})
    monkeypatch.undo()

    event = store.chain(gate["id"])[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE audit_events SET payload = ? WHERE id = ?",
                         ("{}", event["id"]))


def test_the_restored_guard_is_the_same_one_the_schema_defines(store, gate):
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})

    row = store.db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (schema.trigger_name("audit_events", "UPDATE"),)).fetchone()
    expected = schema.trigger_sql("audit_events", "UPDATE")
    assert row["sql"].strip() == expected.replace("IF NOT EXISTS ", "").strip()


def test_the_delete_guard_is_never_dropped(store, gate):
    store.rewrite_past_event(gate["id"], 0, {"note": "edited"})
    event = store.chain(gate["id"])[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM audit_events WHERE id = ?", (event["id"],))


# ── bounds ────────────────────────────────────────────────────────────────

def test_an_index_past_the_end_is_refused(store, gate):
    with pytest.raises(IndexError, match="no index"):
        store.rewrite_past_event(gate["id"], 99, {})


def test_a_negative_index_is_refused(store, gate):
    # Python would happily accept -1 as "the last one". Refused because the
    # caller almost certainly meant an index they miscounted, and silently
    # editing a different event than they named is worse than an error.
    with pytest.raises(IndexError):
        store.rewrite_past_event(gate["id"], -1, {})


def test_a_gate_with_no_events_has_nothing_to_rewrite(store):
    with pytest.raises(IndexError, match="0 events"):
        store.rewrite_past_event("no-such-gate", 0, {})


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "store", Store(tmp_path / "api.db"))
    return TestClient(app_module.app)


@pytest.fixture()
def api_gate(client):
    return client.post("/demo/gate").json()["id"]


def test_the_route_is_absent_unless_asked_for_by_name(client, api_gate,
                                                      monkeypatch):
    monkeypatch.delenv("STRATUM_DEMO_TAMPER", raising=False)
    r = client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 0})
    assert r.status_code == 404
    assert "STRATUM_DEMO_TAMPER" in r.json()["detail"]


def test_the_chain_still_verifies_when_the_route_is_off(client, api_gate,
                                                        monkeypatch):
    monkeypatch.delenv("STRATUM_DEMO_TAMPER", raising=False)
    client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 0})
    assert client.get(f"/gates/{api_gate}/verify_chain").json()["ok"]


@pytest.fixture()
def tamper_on(monkeypatch):
    monkeypatch.setenv("STRATUM_DEMO_TAMPER", "1")


def test_tampering_reports_the_broken_chain_in_the_same_response(
        client, api_gate, tamper_on):
    r = client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 0})
    assert r.status_code == 200
    assert r.json()["chain"]["ok"] is False
    assert r.json()["chain"]["broken_at"] == 0


def test_the_response_shows_what_the_payload_was_and_became(client, api_gate,
                                                            tamper_on):
    r = client.post(f"/demo/gates/{api_gate}/tamper",
                    json={"index": 0, "payload": {"note": "edited"}})
    change = r.json()["tampered"]
    assert change["before"] != change["after"]
    assert json.loads(change["after"]) == {"note": "edited"}


def test_the_response_names_the_event_that_was_edited(client, api_gate,
                                                      tamper_on):
    r = client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 0})
    assert r.json()["tampered"]["type"] == "gate.created"


def test_verify_chain_agrees_with_what_the_tamper_response_said(
        client, api_gate, tamper_on):
    r = client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 0})
    assert client.get(f"/gates/{api_gate}/verify_chain").json() == r.json()["chain"]


def test_an_unknown_gate_is_a_404_not_an_index_error(client, tamper_on):
    r = client.post("/demo/gates/nope/tamper", json={"index": 0})
    assert r.status_code == 404
    assert "no such gate" in r.json()["detail"]


def test_an_index_past_the_end_is_a_400(client, api_gate, tamper_on):
    r = client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 99})
    assert r.status_code == 400
    assert "no index" in r.json()["detail"]


def test_a_refused_index_leaves_the_chain_intact(client, api_gate, tamper_on):
    client.post(f"/demo/gates/{api_gate}/tamper", json={"index": 99})
    assert client.get(f"/gates/{api_gate}/verify_chain").json()["ok"]
