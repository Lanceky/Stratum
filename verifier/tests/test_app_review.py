"""
HTTP surface tests for the reviewer queue and rulings (implementation.md Step 9).

The reviewer console exists because check 3 has a measured overlap band that no
threshold resolves, so some gates must be settled by a person. What matters
here is not that the endpoints return 200, but that they cannot mislead the
person using them:

  * the queue explains *why* each gate was referred, in the words recorded at
    the time rather than words recomputed now;
  * a check that never ran is presented differently from one that ran and
    objected, because conflating them is how an unexamined gate gets waved
    through;
  * no raw biometric material reaches the screen, whatever the reviewer needs
    to decide;
  * a ruling that the state machine refuses leaves no trace in the reviews
    table claiming it succeeded.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
from gate import Actor, GateState  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


PASSING = {"ran": True, "passed": True, "score": 0.95, "verdict": "PASS"}

OVERLAP = {
    "ran": True, "passed": False, "score": 0.55, "verdict": "REVIEW",
    "reason": "distance 7.30 falls inside the overlap band",
    "limitations": ["thresholds fitted on a synthetic cohort"],
}

ABSENT = {
    "ran": False, "passed": False, "score": 0.0, "verdict": "REVIEW",
    "reason": "no enrolled reference capture exists for this subject",
}


def new_gate(client, name="wf-review", ttl_s=300):
    r = client.post("/gates", json={"workflow_id": name,
                                    "mode": "authorise_action", "ttl_s": ttl_s})
    assert r.status_code == 201
    body = r.json()
    return body.get("gate_id") or body["id"]


def drive_to_scored(client, gate_id):
    for to, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                      ("SCORED", "system")):
        r = client.post(f"/gates/{gate_id}/transition",
                        json={"to": to, "actor": actor})
        assert r.status_code == 200, r.text


def referred(client, *, binding=None, authenticity=None, presence=None,
             ttl_s=300):
    """A gate sitting in REVIEW, referred for the reason the caller chose."""
    gate_id = new_gate(client, ttl_s=ttl_s)
    drive_to_scored(client, gate_id)
    r = client.post("/decide", json={
        "gate_id": gate_id,
        "presence": presence or PASSING,
        "authenticity": authenticity or PASSING,
        "binding": binding or OVERLAP,
    })
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "REVIEW"
    return gate_id


def packet(client, gate_id):
    r = client.get(f"/gates/{gate_id}/review")
    assert r.status_code == 200, r.text
    return r.json()


# ── the queue ─────────────────────────────────────────────────────────────

def test_queue_lists_only_gates_awaiting_a_person(client):
    gate_id = referred(client)

    passed = new_gate(client)
    drive_to_scored(client, passed)
    client.post("/decide", json={"gate_id": passed, "presence": PASSING,
                                 "authenticity": PASSING, "binding": PASSING})

    ids = [g["gate_id"] for g in client.get("/gates?state=REVIEW").json()["gates"]]
    assert gate_id in ids
    assert passed not in ids


def test_queue_is_oldest_first(client):
    first = referred(client)
    second = referred(client)
    ids = [g["gate_id"] for g in client.get("/gates?state=REVIEW").json()["gates"]]
    assert ids.index(first) < ids.index(second), (
        "a queue sorted newest-first starves the oldest gate, and an expired "
        "gate is a person silently refused")


def test_queue_state_filter_is_honoured(client):
    referred(client)
    body = client.get("/gates?state=SEALED").json()
    assert body["state"] == "SEALED"
    assert body["gates"] == []


def test_queue_carries_the_reason_not_just_a_score(client):
    gate_id = referred(client)
    entry = next(g for g in client.get("/gates?state=REVIEW").json()["gates"]
                 if g["gate_id"] == gate_id)
    assert "overlap band" in entry["triggering_signal"], (
        "a reviewer triaging a queue must be told what objected, not shown a "
        "number and asked to agree with it")


# ── why the gate is here ──────────────────────────────────────────────────

def test_reasons_are_read_back_not_recomputed(client):
    gate_id = referred(client)
    reasons = packet(client, gate_id)["reasons"]
    chain = client.get(f"/gates/{gate_id}/audit").json()["events"]
    recorded = [json.loads(e["payload"]) for e in chain
                if e["type"] == "transition"]
    written = next(p["reasons"] for p in recorded if p.get("to") == "REVIEW")
    assert reasons == written


def test_specific_reason_survives_fusion(client):
    """
    Regression: fusion read `reason` only from a nested `detail` key, so the
    specific finding was dropped for the exact payload shape this system's own
    check endpoints emit, and the reviewer got generic boilerplate instead.
    """
    gate_id = referred(client)
    assert any("7.30" in r for r in packet(client, gate_id)["reasons"])


def test_a_check_that_did_not_run_is_not_a_check_that_failed(client):
    gate_id = referred(client, binding=PASSING, authenticity=ABSENT)
    p = packet(client, gate_id)
    auth = next(c for c in p["checks"] if c["name"] == "authenticity")
    assert auth["ran"] is False
    assert auth["passed"] is False
    assert "did not run" in p["triggering_signal"] or "could not run" in p["triggering_signal"]


def test_the_three_routes_into_review_are_distinguishable(client):
    """REVIEW is reachable three ways and the reviewer must be told which."""
    by_binding = packet(client, referred(client, binding=OVERLAP))
    by_absence = packet(client, referred(client, binding=PASSING,
                                         authenticity=ABSENT))
    by_presence = packet(client, referred(
        client, binding=PASSING,
        presence={"ran": True, "passed": False, "score": 0.4,
                  "verdict": "REVIEW",
                  "reason": "only 2 of 4 challenge frames returned a response"}))

    signals = {by_binding["triggering_signal"],
               by_absence["triggering_signal"],
               by_presence["triggering_signal"]}
    assert len(signals) == 3


def test_limitations_reach_the_reviewer(client):
    gate_id = referred(client)
    binding = next(c for c in packet(client, gate_id)["checks"]
                   if c["name"] == "binding")
    assert binding["limitations"], (
        "a reviewer weighing a machine's finding needs to know what that "
        "finding was never able to establish")


# ── privacy ───────────────────────────────────────────────────────────────

FORBIDDEN = ("constellation", "landmark", "image", "frame_", "photo",
             "capture_blob", "pixels", "descriptor", "embedding")

# Terms that are unambiguous field names. Scanned inside string values as well
# as keys, because seeing one in prose would itself be suspicious.
FORBIDDEN_IN_PROSE = ("constellation", "capture_blob", "descriptor", "embedding")


def _keys(node) -> list[str]:
    if isinstance(node, dict):
        return [k.lower() for k in node] + [x for v in node.values() for x in _keys(v)]
    if isinstance(node, list):
        return [x for v in node for x in _keys(v)]
    return []


def _strings(node) -> list[str]:
    if isinstance(node, dict):
        return [x for v in node.values() for x in _strings(v)]
    if isinstance(node, list):
        return [x for v in node for x in _strings(v)]
    return [node.lower()] if isinstance(node, str) else []


def _coordinate_arrays(node) -> list:
    """Nested numeric pairs — the shape a constellation would arrive in."""
    found = []
    if isinstance(node, dict):
        for v in node.values():
            found += _coordinate_arrays(v)
    elif isinstance(node, list):
        if (len(node) >= 2 and all(isinstance(p, list) and len(p) == 2
                                   and all(isinstance(n, (int, float)) for n in p)
                                   for p in node)):
            found.append(node)
        for v in node:
            found += _coordinate_arrays(v)
    return found


def assert_no_biometric_material(payload) -> None:
    """
    No biometric material on the reviewer screen (context.md §11.8).

    Checked against field names and structure rather than by searching the
    serialised blob for substrings. The blob scan flagged the word
    "photograph" inside a check's own explanation of what it cannot establish
    — prose that is the entire point of the console — while a constellation
    smuggled through under an innocuous key would have passed it unnoticed.
    """
    for key in _keys(payload):
        assert not any(t in key for t in FORBIDDEN), \
            f"{key!r} is a field name that carries biometric material"
    for s in _strings(payload):
        assert not any(t in s for t in FORBIDDEN_IN_PROSE), \
            f"biometric material appeared in a string value: {s[:80]!r}"
    assert not _coordinate_arrays(payload), \
        "a point set reached the reviewer screen"


def test_review_packet_carries_no_biometric_material(client):
    gate_id = referred(client)
    assert_no_biometric_material(packet(client, gate_id))


def test_queue_carries_no_biometric_material(client):
    referred(client)
    assert_no_biometric_material(client.get("/gates?state=REVIEW").json())


def test_the_privacy_guard_catches_a_smuggled_point_set(client):
    """The guard has to fail on the thing it exists to catch."""
    gate_id = referred(client)
    p = packet(client, gate_id)
    p["checks"][0]["notes"] = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    with pytest.raises(AssertionError, match="point set"):
        assert_no_biometric_material(p)


def test_the_privacy_guard_catches_a_biometric_field_name(client):
    gate_id = referred(client)
    p = packet(client, gate_id)
    p["checks"][0]["constellations"] = {}
    with pytest.raises(AssertionError, match="biometric material"):
        assert_no_biometric_material(p)


def test_the_privacy_guard_allows_a_check_to_explain_itself(client):
    """
    "a printed photograph responds to coloured light" is what a reviewer needs
    to read. A guard that forbids it trains people to delete the explanation.
    """
    gate_id = referred(client)
    p = packet(client, gate_id)
    p["checks"][0]["limitations"] = ["a printed photograph can be turned on cue"]
    assert_no_biometric_material(p)


# ── integrity ─────────────────────────────────────────────────────────────

def test_packet_reports_the_chain_state(client):
    gate_id = referred(client)
    p = packet(client, gate_id)
    assert p["chain"]["ok"] is True
    assert p["chain"]["length"] > 0


def test_timeline_carries_each_events_hash(client):
    """
    The console draws the trail as linked blocks, one per event, labelled with
    its own digest. That picture is only worth showing if it is the real
    structure, so the hashes must travel with the timeline rather than being
    invented client-side.
    """
    gate_id = referred(client)
    timeline = packet(client, gate_id)["timeline"]
    assert all(len(e["hash"]) == 64 for e in timeline)
    # Each event names its predecessor; that link is what the drawing shows.
    for prev, nxt in zip(timeline, timeline[1:]):
        assert nxt["prev_hash"] == prev["hash"]


def test_evidence_is_written_in_check_order(client):
    """
    Fusion sorts its outcomes by name for its own reasons (authenticity,
    binding, presence). That ordering means nothing to someone reading the
    history, where 1, 2, 3 is the only sequence that reads naturally.
    """
    gate_id = referred(client)
    events = client.get(f"/gates/{gate_id}/audit").json()["events"]
    order = [json.loads(e["payload"])["check_no"]
             for e in events if e["type"] == "evidence"]
    assert order == [1, 2, 3]


def test_packet_shows_the_history_including_refusals(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/transition",
                json={"to": "SIGNED", "actor": "agent"})
    types = [e["type"] for e in packet(client, gate_id)["timeline"]]
    assert "transition.refused" in types, (
        "an agent reaching for a signature is exactly what a reviewer should "
        "see before ruling")


# ── rulings ───────────────────────────────────────────────────────────────

def test_approve_signs_the_gate(client):
    gate_id = referred(client)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "evans.k", "decision": "approve",
                          "notes": "confirmed by callback"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == str(GateState.SIGNED)


def test_reject_fails_the_gate(client):
    gate_id = referred(client)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "evans.k", "decision": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == str(GateState.FAIL)


def test_the_ruling_lands_in_the_hash_chain(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "approve"})
    assert client.get(f"/gates/{gate_id}/verify_chain").json()["ok"] is True
    types = [e["type"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]]
    assert "review" in types


def test_the_reviewer_is_named_in_the_record(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "approve",
                      "notes": "spoke to the customer"})
    reviews = packet(client, gate_id)["reviews"]
    assert [r["reviewer_id"] for r in reviews] == ["evans.k"]
    assert reviews[0]["notes"] == "spoke to the customer"


def test_an_anonymous_review_is_refused(client):
    gate_id = referred(client)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "", "decision": "approve"})
    assert r.status_code == 422, (
        "an unattributed ruling is not a review; the whole point of the band "
        "is that a person put their name to it")


def test_the_recorded_signal_is_the_one_ruled_on(client):
    """
    A rejection moves REVIEW -> FAIL, which is itself a verdict transition.
    Reading the signal after the move would describe the reviewer's own
    decision rather than the finding they were asked to settle.
    """
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "reject"})
    signal = packet(client, gate_id)["reviews"][0]["triggering_signal"]
    assert "overlap band" in signal


def test_reasons_survive_a_rejection(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "reject"})
    assert any("overlap band" in r for r in packet(client, gate_id)["reasons"]), (
        "the human's FAIL transition must not shadow fusion's referral")


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_refused_ruling_leaves_no_claim_of_approval(client):
    """
    Regression: the review row was written before the transition, so a gate
    that refused the move still carried a row reading "approve" — an approval
    that never took effect, indistinguishable from one that authorised money
    moving. The refusal itself survives in the audit chain.
    """
    gate_id = referred(client, ttl_s=10)
    appmod.store.db.execute(
        "UPDATE gates SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (gate_id,))
    appmod.store.db.commit()

    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "evans.k", "decision": "approve"})
    assert r.status_code == 409

    p = packet(client, gate_id)
    assert p["reviews"] == []
    assert p["state"] == str(GateState.REVIEW)
    assert "transition.refused" in [e["type"] for e in p["timeline"]]


def test_the_refusal_names_who_tried(client):
    gate_id = referred(client)
    appmod.store.db.execute(
        "UPDATE gates SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (gate_id,))
    appmod.store.db.commit()
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "approve"})
    refusals = [e for e in packet(client, gate_id)["timeline"]
                if e["type"] == "transition.refused"]
    assert refusals[-1]["payload"]["reviewer_id"] == "evans.k"


def test_a_settled_gate_cannot_be_ruled_on_twice(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "approve"})
    again = client.post(f"/gates/{gate_id}/review",
                        json={"reviewer_id": "someone.else",
                              "decision": "approve"})
    assert again.status_code == 409


def test_a_gate_not_in_review_is_not_decidable(client):
    gate_id = new_gate(client)
    assert packet(client, gate_id)["decidable"] is False


def test_unknown_gate_is_404(client):
    assert client.get("/gates/nope/review").status_code == 404
    assert client.post("/gates/nope/review",
                       json={"reviewer_id": "a",
                             "decision": "approve"}).status_code == 404


def test_only_approve_or_reject_is_accepted(client):
    gate_id = referred(client)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "evans.k", "decision": "maybe"})
    assert r.status_code == 422


def test_a_reviewer_cannot_reach_a_state_the_machine_forbids(client):
    """
    The ruling goes through gate_transition like everything else, so the
    console is not a side door into the state machine.
    """
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "reject"})
    assert client.get(f"/gates/{gate_id}").json()["state"] == str(GateState.FAIL)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "evans.k", "decision": "approve"})
    assert r.status_code == 409, "FAIL is terminal; no reviewer may leave it"


def test_the_transition_is_attributed_to_a_human(client):
    gate_id = referred(client)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "evans.k", "decision": "approve"})
    signed = [json.loads(e["payload"])
              for e in client.get(f"/gates/{gate_id}/audit").json()["events"]
              if e["type"] == "transition"]
    last = signed[-1]
    assert last["to"] == str(GateState.SIGNED)
    assert last["actor"] == str(Actor.HUMAN)


def test_the_packet_records_which_evidence_came_from_a_stand_in(client):
    """
    Read back out of the chain, not from the live reply. The console opens
    gates long after the capture, and provenance that only existed in the
    original response would be gone exactly when a reviewer needs it.
    """
    gate_id = referred(client, presence={**PASSING, "synthetic": True})
    presence = next(c for c in packet(client, gate_id)["checks"]
                    if c["name"] == "presence")
    assert presence["synthetic"] is True


def test_measured_evidence_is_not_flagged_as_a_stand_in(client):
    gate_id = referred(client)
    assert all(c["synthetic"] is False for c in packet(client, gate_id)["checks"])
