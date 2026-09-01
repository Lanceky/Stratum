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
import claim  # noqa: E402
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


def test_health_reports_who_signs(client):
    """
    The landing page draws the issuer as a credential. If `can_sign` were
    reported from the presence of the variable rather than from a key that
    actually loads, the page would advertise a signature the server cannot
    produce — the one claim on it most damaging to get wrong.
    """
    iss = client.get("/health").json()["issuer"]

    assert iss["can_sign"] is (iss["address"] is not None)
    assert iss["scheme"] == claim.SCHEME
    if iss["can_sign"]:
        assert iss["address"].startswith("0x")


def test_health_ledger_grows_as_events_are_written(client, gate_id):
    """
    `blocks written` has to be the count of the audit table, not of gates. A
    number that only moved when a gate was created would sit still through the
    entire demo and read as a broken counter.
    """
    before = client.get("/health").json()["issuer"]["ledger"]
    _move(client, gate_id, "CHALLENGED", "agent")
    after = client.get("/health").json()["issuer"]["ledger"]

    assert after["events"] > before["events"]
    assert after["latest"] != before["latest"]
    assert after["at"] >= before["at"]


def test_ledger_latest_is_the_newest_block_of_some_chain(client, gate_id):
    """
    There is no global chain — each gate carries its own — so `latest` is the
    newest block across all of them and must be a real block, not a root hash
    computed over the table.
    """
    _move(client, gate_id, "CHALLENGED", "agent")
    latest = client.get("/health").json()["issuer"]["ledger"]["latest"]

    hashes = {e["hash"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]}
    assert latest in hashes


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
    """
    Compared against the schema module rather than a number typed here. A
    hardcoded count fails on every table added, which trains you to bump it
    without looking — the one case where it should fail is a table the export
    silently drops, and that is exactly what bumping the number hides.
    """
    import schema

    names = {t["name"] for t in client.get("/schema").json()["tables"]}
    assert names == set(schema.TABLES) and "audit_events" in names
