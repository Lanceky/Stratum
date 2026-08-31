"""
An agent reaching for a human's signature.

The claim this project makes is not that the refusal happens — any state
machine refuses illegal moves. It is that the *attempt* is recorded, named,
and survives, so that "the agent tried to approve its own work" is something
you can read off the ledger months later rather than something you have to
have been watching for.

These tests cover the three parts of that: the boundary is derived rather
than listed, the refusal says which boundary was crossed, and the attempt is
readable back out of the chain.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import gate as gatemod  # noqa: E402
from gate import Actor, GateState, HUMAN_ONLY, is_escalation  # noqa: E402
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
    return r.json()["id"]


def _move(client, gate_id, to, actor):
    return client.post(f"/gates/{gate_id}/transition", json={"to": to, "actor": actor})


# ---------------------------------------------------------------- the boundary


def test_the_human_boundary_is_exactly_capture_and_signature():
    """
    Two states no machine may reach: producing a capture, and signing. Both
    require a person to have been physically present at a moment in time.
    """
    assert HUMAN_ONLY == {GateState.CAPTURED, GateState.SIGNED}


def test_the_boundary_is_derived_from_the_table_not_written_down():
    """
    The point of deriving it. If someone later adds a route into a human-only
    state that a machine may take, that state stops being human-only — without
    anyone remembering to update a list that would otherwise quietly lie.
    """
    patched = dict(gatemod.TRANSITIONS)
    patched[(GateState.REQUESTED, GateState.SIGNED)] = {Actor.SYSTEM}

    with pytest.MonkeyPatch.context() as m:
        m.setattr(gatemod, "TRANSITIONS", patched)
        assert gatemod._human_only_states() == {GateState.CAPTURED}


@pytest.mark.parametrize("to,actor,expected", [
    (GateState.SIGNED, Actor.AGENT, True),
    (GateState.CAPTURED, Actor.AGENT, True),
    (GateState.SIGNED, Actor.SYSTEM, True),
    (GateState.SIGNED, Actor.HUMAN, False),
    (GateState.SEALED, Actor.SYSTEM, False),
    (GateState.FAIL, Actor.SYSTEM, False),
    (GateState.CHALLENGED, Actor.AGENT, False),
])
def test_escalation_is_a_machine_reaching_for_a_human_only_state(to, actor, expected):
    assert is_escalation(to, actor) is expected


def test_unparseable_input_is_not_an_escalation():
    """
    Garbage in a request body is a validation problem. Calling it an attempted
    privilege escalation would put noise in the one place that must stay
    readable.
    """
    assert is_escalation("NOT_A_STATE", "agent") is False
    assert is_escalation("SIGNED", "not_an_actor") is False


# ------------------------------------------------------------- what it's told


def test_an_agent_reaching_past_the_flow_is_told_which_boundary_it_crossed(client, gate_id):
    """
    REQUESTED to SIGNED is not in the table at all, so the generic answer is
    "no such transition" — true, and useless. The reason it is not in the
    table is the thing worth saying.
    """
    r = _move(client, gate_id, "SIGNED", "agent")
    assert r.status_code == 409
    assert "only a human may authorise" in r.json()["detail"]["reason"]


def test_a_human_reaching_past_the_flow_still_gets_the_ordinary_answer(client, gate_id):
    """
    A human cannot sign a gate that has not been captured either — but that is
    sequencing, not a boundary. Saying "only a human may authorise" to a human
    would be nonsense.
    """
    r = _move(client, gate_id, "SIGNED", "human")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "no such transition"


def test_an_agent_cannot_manufacture_a_capture(client, gate_id):
    """
    The injection attack, refused at the state machine rather than at the
    sensor: frames the agent produced itself would never be scored, because it
    cannot reach the state that holds them.
    """
    _move(client, gate_id, "CHALLENGED", "agent")
    r = _move(client, gate_id, "CAPTURED", "agent")
    assert r.status_code == 409
    assert "only a human at the camera" in r.json()["detail"]["reason"]


def test_sealing_is_not_described_as_a_human_boundary(client, gate_id):
    """
    SEALED is a signing state but the system reaches it unaided. Telling an
    agent "only a human may authorise" here would cite a rule that does not
    exist — and a refusal that misstates its own reason is worse than a
    silent one, because it will be believed.
    """
    r = _move(client, gate_id, "SEALED", "agent")
    assert r.status_code == 409
    assert "only a human" not in r.json()["detail"]["reason"]


def test_the_one_thing_an_agent_may_do_it_may_still_do(client, gate_id):
    """
    The boundary is at the signature, not around the agent. If asking for a
    human were itself refused, the gate could never open.
    """
    assert _move(client, gate_id, "CHALLENGED", "agent").status_code == 200


# --------------------------------------------------------------- the record


def test_the_attempt_is_recorded_before_it_is_refused(client, gate_id):
    _move(client, gate_id, "SIGNED", "agent")

    chain = client.get(f"/gates/{gate_id}/audit").json()
    refused = [e for e in chain["events"] if e["type"] == "transition.refused"]
    assert len(refused) == 1
    payload = json.loads(refused[0]["payload"])
    assert payload["actor"] == "agent"
    assert payload["to"] == "SIGNED"


def test_a_refused_attempt_does_not_break_the_chain(client, gate_id):
    _move(client, gate_id, "SIGNED", "agent")
    assert client.get(f"/gates/{gate_id}/verify_chain").json()["ok"] is True


def test_escalations_are_read_back_out_of_the_chain(client, gate_id):
    _move(client, gate_id, "SIGNED", "agent")

    found = appmod.store.escalations(gate_id)
    assert len(found) == 1
    assert found[0]["to"] == "SIGNED"
    assert found[0]["hash"]


def test_an_ordinary_refusal_is_not_reported_as_an_escalation(client, gate_id):
    """
    The count has to mean something. If every illegal move landed in it, an
    agent reaching for a signature would sit alongside a double-submitted form
    and a reviewer would learn to ignore both.
    """
    _move(client, gate_id, "CHALLENGED", "agent")
    _move(client, gate_id, "CHALLENGED", "agent")     # ordinary: already there
    _move(client, gate_id, "SEALED", "agent")         # ordinary: not a boundary
    _move(client, gate_id, "SIGNED", "human")         # ordinary: out of sequence

    chain = client.get(f"/gates/{gate_id}/audit").json()
    assert len([e for e in chain["events"] if e["type"] == "transition.refused"]) == 3
    assert appmod.store.escalations(gate_id) == []


def test_the_reviewer_is_shown_the_attempt(client, gate_id):
    _move(client, gate_id, "SIGNED", "agent")

    packet = client.get(f"/gates/{gate_id}/review").json()
    assert len(packet["escalations"]) == 1
    assert packet["escalations"][0]["actor"] == "agent"


def test_the_queue_marks_a_gate_an_agent_tried_to_sign(client, gate_id):
    _move(client, gate_id, "SIGNED", "agent")

    row = [g for g in client.get("/gates?state=REQUESTED").json()["gates"] if g["gate_id"] == gate_id][0]
    assert row["escalations"] == 1
