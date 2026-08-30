"""
HTTP surface tests for check 3 and the fusion layer (implementation.md Step 7).

The matcher's behaviour is covered in test_binding.py and the decision table in
test_fusion.py. What matters here is the contract a caller sees: that a probe is
judged against a named enrolment rather than an unordered pair, that a REVIEW
reaches the caller with a legible reason attached, that evidence lands in the
ledger under check 3, and that a fused decision moves the gate through the same
state machine every other transition uses rather than around it.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
from synth_cohort import POSES, Identity, capture, degraded, sibling  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


def payload(event) -> dict:
    p = event.get("payload")
    return json.loads(p) if isinstance(p, str) else (p or {})


def new_gate(client, name="wf-binding"):
    r = client.post("/gates", json={"workflow_id": name, "mode": "authorise_action"})
    assert r.status_code == 201
    body = r.json()
    return body.get("gate_id") or body["id"]


@pytest.fixture(scope="module")
def alice():
    return Identity(seed=31_337)


@pytest.fixture(scope="module")
def bob():
    return Identity(seed=31_338)


def cap(person, pose=0, seed=1):
    return capture(person, seed, **POSES[pose])


# ── the endpoint's contract ───────────────────────────────────────────────
def test_same_person_is_not_rejected(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(alice, 1, 2)})
    assert r.status_code == 200
    assert r.json()["verdict"] in ("PASS", "REVIEW")


def test_different_person_fails(client, alice, bob):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(bob, 1, 2)})
    assert r.json()["verdict"] == "FAIL"


def test_failed_binding_is_not_passed(client, alice, bob):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(bob, 1, 2)})
    assert r.json()["passed"] is False


def test_response_names_the_thresholds_it_used(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(alice, 1, 2)}).json()
    assert r["thresholds"]["lower"] < r["thresholds"]["upper"]


def test_response_carries_a_reason(client, alice, bob):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(bob, 1, 2)}).json()
    assert len(r["reason"]) > 20


def test_response_declares_its_limitations(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(alice, 1, 2)}).json()
    assert any("overlap" in x.lower() for x in r["limitations"])


def test_channels_report_what_actually_counted(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(alice, 1, 2)}).json()
    assert sum(c["effective_weight"] for c in r["channels"]) == pytest.approx(1.0, abs=1e-3)


def test_empty_capture_is_refused_not_guessed(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": {"scores": {},
                                                      "constellations": {}}})
    assert r.status_code == 422


def test_review_reaches_the_caller_with_a_reason(client):
    """A REVIEW nobody can act on is just a slower failure."""
    seen = None
    for i in range(24):
        p = Identity(seed=41_000 + i)
        body = {"enrolment": capture(p, i, **POSES[0]),
                "probe": sibling(p, i + 11, **POSES[1])}
        r = client.post("/check/binding", json=body).json()
        if r["verdict"] == "REVIEW":
            seen = r
            break
    assert seen is not None, "no REVIEW in 24 sibling comparisons"
    assert len(seen["reason"]) > 20 and seen["passed"] is False


# ── the ledger ────────────────────────────────────────────────────────────
def test_verdict_lands_in_the_ledger_under_check_three(client, alice, bob):
    gid = new_gate(client)
    client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                        "probe": cap(bob, 1, 2), "gate_id": gid})
    audit = client.get(f"/gates/{gid}/audit").json()["events"]
    # Payloads are stored as canonical JSON strings, because the hash chain is
    # computed over the exact bytes rather than over a re-serialised dict.
    assert any(e["type"] == "evidence" and payload(e).get("check_no") == 3
               for e in audit)


def test_unknown_gate_is_rejected(client, alice):
    r = client.post("/check/binding", json={"enrolment": cap(alice, 0, 1),
                                            "probe": cap(alice, 1, 2),
                                            "gate_id": "no-such-gate"})
    assert r.status_code == 404


# ── fusion over HTTP ──────────────────────────────────────────────────────
def passing_checks():
    return {"presence": {"ran": True, "passed": True, "score": 0.9},
            "authenticity": {"ran": True, "passed": True, "score": 0.8},
            "binding": {"ran": True, "passed": True, "score": 0.95,
                        "verdict": "PASS"}}


def test_all_checks_satisfied_decides_pass(client):
    r = client.post("/decide", json=passing_checks())
    assert r.status_code == 200 and r.json()["verdict"] == "PASS"


def test_check_that_could_not_run_forces_review(client):
    body = passing_checks()
    body["authenticity"] = {"ran": False, "passed": False, "score": 0.0}
    r = client.post("/decide", json=body).json()
    assert r["verdict"] == "REVIEW" and r["requires_human"] is True


def test_omitting_a_check_is_not_safer_than_declaring_it(client):
    body = passing_checks()
    del body["authenticity"]
    assert client.post("/decide", json=body).json()["verdict"] == "REVIEW"


def test_violated_check_fails_the_gate(client):
    body = passing_checks()
    body["presence"] = {"ran": True, "passed": False, "score": 0.1,
                        "detail": {"reason": "injected stream"}}
    r = client.post("/decide", json=body).json()
    assert r["verdict"] == "FAIL" and "injected stream" in " ".join(r["reasons"])


def test_no_results_at_all_is_refused(client):
    assert client.post("/decide", json={}).status_code == 422


def test_decision_moves_the_gate_through_the_state_machine(client):
    """Fusion must not get a private door into gates.state."""
    gid = new_gate(client, "wf-fusion-pass")
    for state, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                         ("SCORED", "system")):
        assert client.post(f"/gates/{gid}/transition",
                           json={"to": state, "actor": actor}).status_code == 200
    body = passing_checks() | {"gate_id": gid}
    r = client.post("/decide", json=body).json()
    assert r["state"] == "PASS"
    assert client.get(f"/gates/{gid}").json()["state"] == "PASS"


def test_review_verdict_lands_the_gate_in_review(client):
    gid = new_gate(client, "wf-fusion-review")
    for state, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                         ("SCORED", "system")):
        client.post(f"/gates/{gid}/transition", json={"to": state, "actor": actor})
    body = passing_checks() | {"gate_id": gid}
    body["authenticity"] = {"ran": False, "passed": False, "score": 0.0}
    assert client.post("/decide", json=body).json()["state"] == "REVIEW"


def test_decision_out_of_order_is_refused_not_forced(client):
    """A gate that never reached SCORED has nothing to decide on."""
    gid = new_gate(client, "wf-fusion-early")
    r = client.post("/decide", json=passing_checks() | {"gate_id": gid})
    assert r.status_code == 409


def test_decision_is_recorded_in_the_audit_chain(client):
    gid = new_gate(client, "wf-fusion-audit")
    for state, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                         ("SCORED", "system")):
        client.post(f"/gates/{gid}/transition", json={"to": state, "actor": actor})
    client.post("/decide", json=passing_checks() | {"gate_id": gid})
    audit = client.get(f"/gates/{gid}/audit").json()["events"]
    assert any(payload(e).get("to") == "PASS" for e in audit)


def test_decision_on_unknown_gate_is_rejected(client):
    r = client.post("/decide", json=passing_checks() | {"gate_id": "nope"})
    assert r.status_code == 404


# ── end to end ────────────────────────────────────────────────────────────
def test_degraded_genuine_capture_reaches_a_human_not_a_rejection(client):
    """
    The whole point of the REVIEW band, exercised through the real endpoints.

    Someone signs in bad light. The matcher cannot confidently confirm them, and
    it must not confidently reject them either — it has to reach a person.
    """
    outcomes = []
    for i in range(12):
        p = Identity(seed=52_000 + i)
        r = client.post("/check/binding",
                        json={"enrolment": capture(p, i, **POSES[0]),
                              "probe": degraded(p, i + 3, **POSES[1])}).json()
        outcomes.append(r["verdict"])
    assert "REVIEW" in outcomes
    assert "FAIL" not in outcomes
