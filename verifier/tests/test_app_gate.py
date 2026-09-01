"""
HTTP surface tests for the gate (implementation.md Step 3).

Covers the contract Xano and the frontend both depend on: 409 on an illegal
transition, and a verifiable chain over the wire.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "store", Store())
    return TestClient(appmod.app)


@pytest.fixture
def gate_id(client):
    s = appmod.store
    t = s.create_tenant("acme.example", "hash")
    w = s.create_workflow(t["id"], "wire_transfer", agent_session_id="agent-7")
    r = client.post("/gates", json={"workflow_id": w["id"], "mode": "authorise_action"})
    assert r.status_code == 201
    return r.json()["id"]


def _move(client, gate_id, to, actor):
    return client.post(f"/gates/{gate_id}/transition", json={"to": to, "actor": actor})


def test_health_still_reports_replay_mode(client):
    assert client.get("/health").json()["api_mode"] == "replay"


def test_health_counts_gates_by_state(client, gate_id):
    """
    The landing page reads these numbers. It previously derived "gates opened"
    from a listing endpoint that defaults to the REVIEW queue, so the total and
    the review count were the same number under two names.
    """
    gates = client.get("/health").json()["gates"]
    assert gates["total"] >= 1
    assert gates["by_state"]["REQUESTED"] >= 1
    assert gates["awaiting_review"] == gates["by_state"].get("REVIEW", 0)


def test_health_total_follows_a_transition(client, gate_id):
    """A move changes the shape of the census without changing its size."""
    before = client.get("/health").json()["gates"]
    _move(client, gate_id, "CHALLENGED", "agent")
    after = client.get("/health").json()["gates"]

    assert after["total"] == before["total"]
    assert after["by_state"]["CHALLENGED"] == before["by_state"].get("CHALLENGED", 0) + 1
    assert after["by_state"].get("REQUESTED", 0) == before["by_state"]["REQUESTED"] - 1


def test_gate_starts_in_requested(client, gate_id):
    assert client.get(f"/gates/{gate_id}").json()["state"] == "REQUESTED"


def test_unknown_gate_is_404(client):
    assert client.get("/gates/nope").status_code == 404
    assert _move(client, "nope", "CHALLENGED", "agent").status_code == 404


def test_illegal_transition_returns_409(client, gate_id):
    r = _move(client, gate_id, "SIGNED", "human")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "illegal_transition"


def test_agent_signing_returns_409_and_is_audited(client, gate_id):
    for to, actor in (("CHALLENGED", "agent"), ("CAPTURED", "human"),
                      ("SCORED", "system"), ("PASS", "system")):
        assert _move(client, gate_id, to, actor).status_code == 200

    r = _move(client, gate_id, "SIGNED", "agent")
    assert r.status_code == 409
    assert "only a human may authorise" in r.json()["detail"]["reason"]

    events = client.get(f"/gates/{gate_id}/audit").json()["events"]
    assert any(e["type"] == "transition.refused" for e in events)
    assert client.get(f"/gates/{gate_id}/verify_chain").json()["ok"] is True

    assert _move(client, gate_id, "SIGNED", "human").status_code == 200


def test_full_route_chain_verifies_over_http(client, gate_id):
    for to, actor in (("CHALLENGED", "agent"), ("CAPTURED", "human"),
                      ("SCORED", "system"), ("PASS", "system"),
                      ("SIGNED", "human"), ("SEALED", "system")):
        assert _move(client, gate_id, to, actor).status_code == 200

    chain = client.get(f"/gates/{gate_id}/verify_chain").json()
    assert chain["ok"] and chain["length"] == 7 and chain["broken_at"] is None


def test_bad_state_name_is_rejected_before_reaching_the_store(client, gate_id):
    assert _move(client, gate_id, "PROBABLY_FINE", "human").status_code == 422


def test_schema_endpoint_lists_all_tables(client):
    names = {t["name"] for t in client.get("/schema").json()["tables"]}
    assert len(names) == 9 and "audit_events" in names
