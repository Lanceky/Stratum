"""
The one-human-one-claim surface, over HTTP.

The unit tests for check 4 prove the sweep decides correctly. These prove the
routes around it hold the line the sweep cannot hold on its own: that a roster
only grows through enrolment, that a signature is refused until a gate has
actually settled, and that a second claim by the same person is stopped by the
database rather than by a lookup that concurrency could step around.

The failure that matters here is not a wrong verdict — it is a right verdict
that gets written anyway, or a signature issued over a decision nobody made.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import claim as claim_mod  # noqa: E402
from gate import GateMode  # noqa: E402
from store import Store  # noqa: E402
from synth_cohort import POSES, Identity, capture, sibling  # noqa: E402

CONTEXT = "airdrop-q1"
WALLET = "0x" + "a1" * 20
OTHER_WALLET = "0x" + "b2" * 20


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "store", Store())
    monkeypatch.setattr(appmod, "_demo_workflow", None, raising=False)
    return TestClient(appmod.app)


@pytest.fixture(scope="module")
def people():
    return [Identity(seed=7100 + i) for i in range(4)]


def shot(person, pose=0, seed=0):
    return capture(person, seed, **POSES[pose])


def enrol(client, person, ref, pose=0, seed=0, context=CONTEXT):
    return client.post("/claims/enrol", json={
        "context": context, "subject_ref": ref,
        "capture": shot(person, pose, seed)})


def verify(client, person, pose=1, seed=500, address=WALLET, gate_id=None,
           context=CONTEXT):
    body = {"context": context, "address": address,
            "capture": shot(person, pose, seed)}
    if gate_id:
        body["gate_id"] = gate_id
    return client.post("/claims/verify", json=body)


def claim_gate(client, mode=GateMode.ONE_HUMAN_ONE_CLAIM):
    s = appmod.store
    t = s.create_tenant("airdrop.example", "hash")
    w = s.create_workflow(t["id"], "claim", agent_session_id="agent-1")
    r = client.post("/gates", json={"workflow_id": w["id"], "mode": str(mode)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── enrolment ─────────────────────────────────────────────────────────────
def test_enrolment_grows_the_roster(client, people):
    assert client.get(f"/claims/roster/{CONTEXT}").json()["roster_size"] == 0
    for i, p in enumerate(people):
        assert enrol(client, p, f"p{i}", seed=i).status_code == 201
    assert client.get(f"/claims/roster/{CONTEXT}").json()["roster_size"] == len(people)


def test_a_roster_is_scoped_to_its_campaign(client, people):
    """
    A person enrolled for one airdrop is not thereby on the roster for
    another. If contexts leaked into each other, claiming in a new campaign
    would be reported as a duplicate of a claim in an unrelated one.
    """
    enrol(client, people[0], "p0", context="airdrop-q1")
    assert client.get("/claims/roster/airdrop-q2").json()["roster_size"] == 0


def test_a_capture_with_no_signal_is_refused_at_enrolment(client):
    r = client.post("/claims/enrol", json={
        "context": CONTEXT, "subject_ref": "ghost",
        "capture": {"source": "none", "scores": {}, "face_attributes": {},
                    "constellations": {}}})
    assert r.status_code == 422
    assert "without ever being comparable" in r.text


def test_a_malformed_context_is_refused(client, people):
    r = client.post("/claims/enrol", json={
        "context": "not a campaign id!", "subject_ref": "p0",
        "capture": shot(people[0])})
    assert r.status_code == 422


def test_the_roster_route_states_what_its_size_costs(client, people):
    """
    The false-match probability is a function of roster size, so a caller who
    is shown the verdict without the roster cannot interpret it.
    """
    for i, p in enumerate(people):
        enrol(client, p, f"p{i}", seed=i)
    body = client.get(f"/claims/roster/{CONTEXT}").json()
    assert body["false_match_across_a_full_sweep"] > 0
    assert body["per_comparison_false_match_bound"] > 0
    assert body["claims_recorded"] == 0


# ── the sweep, over the wire ──────────────────────────────────────────────
def test_a_newcomer_may_claim(client, people):
    for i, p in enumerate(people[:3]):
        enrol(client, p, f"p{i}", seed=i)
    r = verify(client, people[3])
    assert r.status_code == 200
    assert r.json()["uniqueness"]["verdict"] == "UNIQUE"


def test_claiming_twice_with_the_same_face_is_caught(client, people):
    for i, p in enumerate(people):
        enrol(client, p, f"p{i}", seed=i)
    body = verify(client, people[1], seed=901).json()["uniqueness"]
    assert body["verdict"] in ("DUPLICATE", "REVIEW")
    assert body["nearest"]["enrolment_id"]
    assert not body["passed"]


def test_a_sibling_is_not_treated_as_the_same_person(client, people):
    """
    The error that costs an honest newcomer their allocation. A near-relative
    is the hardest legitimate claimant, and turning them away is the failure
    this check must not make quietly.
    """
    for i, p in enumerate(people):
        enrol(client, p, f"p{i}", seed=i)
    r = client.post("/claims/verify", json={
        "context": CONTEXT, "address": WALLET,
        "capture": sibling(people[0], 31, **POSES[1])}).json()["uniqueness"]
    assert r["verdict"] != "DUPLICATE", r["nearest"]


def test_an_empty_roster_leaves_the_first_claimant_unique(client, people):
    r = verify(client, people[0]).json()["uniqueness"]
    assert r["verdict"] == "UNIQUE"
    assert r["roster_size"] == 0
    assert r["comparisons_run"] == 0


def test_a_sweep_alone_is_not_a_decision(client, people):
    """
    A clean sweep that never established the claimant was a live person is not
    a claim. Fusion applies the mode's full requirement, so the decision must
    refuse to pass on checks that were never submitted.
    """
    enrol(client, people[0], "p0")
    d = verify(client, people[1]).json()["decision"]
    assert d["verdict"] != "PASS"
    assert d["requires_human"]
    said = " ".join(d["reasons"])
    assert "presence" in said and "authenticity" in said


def test_a_bad_address_is_refused_before_any_sweep(client, people):
    enrol(client, people[0], "p0")
    r = verify(client, people[1], address="0xnope")
    assert r.status_code == 422


# ── gates ─────────────────────────────────────────────────────────────────
def test_a_sweep_against_a_gate_writes_evidence_as_check_four(client, people):
    enrol(client, people[0], "p0")
    gid = claim_gate(client)
    assert verify(client, people[1], gate_id=gid).status_code == 200
    rows = [e for e in appmod.store.evidence_for(gid) if int(e["check_no"]) == 4]
    assert len(rows) == 1


def test_a_sweep_cannot_be_attached_to_a_gate_of_another_mode(client, people):
    """
    Mode is read from the gate, never from the request. A uniqueness result
    filed against a wire-transfer gate would put a Sybil finding into a record
    that never asked the question.
    """
    enrol(client, people[0], "p0")
    gid = claim_gate(client, GateMode.AUTHORISE_ACTION)
    r = verify(client, people[1], gate_id=gid)
    assert r.status_code == 409
    assert "one_human_one_claim" in r.text


def test_an_unknown_gate_is_a_404(client, people):
    enrol(client, people[0], "p0")
    assert verify(client, people[1], gate_id="gate-nope").status_code == 404


# ── signing ───────────────────────────────────────────────────────────────
@pytest.fixture
def signing(monkeypatch):
    monkeypatch.setenv(claim_mod.KEY_ENV, "0x" + "11" * 32)
    monkeypatch.setenv(claim_mod.SECRET_ENV, "test-secret")


def settle(client, gid):
    """Walk a gate to a settled verdict the honest way, one legal step at a time."""
    s = appmod.store
    for to, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                      ("SCORED", "system"), ("PASS", "system")):
        s.gate_transition(gid, to, actor)
    return s.get("gates", gid)


def sign(client, gid, address=WALLET, enrolment="enr-1", context=CONTEXT):
    return client.post(f"/claims/{gid}/signature", json={
        "context": context, "address": address, "enrolment_id": enrolment})


def test_a_gate_still_in_flight_will_not_be_signed(client, people, signing):
    gid = claim_gate(client)
    r = sign(client, gid)
    assert r.status_code == 409
    assert "not authorised anything" in r.text


def test_a_settled_gate_signs(client, people, signing):
    gid = claim_gate(client)
    settle(client, gid)
    r = sign(client, gid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scheme"] == "EIP-191 personal_sign"
    assert body["claim"]["address"] == WALLET
    assert body["claim"]["nullifier"]


def test_the_signature_recovers_to_the_issuer(client, people, signing):
    """The operation a contract performs. If this fails, `ecrecover` fails."""
    gid = claim_gate(client)
    settle(client, gid)
    body = sign(client, gid).json()
    assert claim_mod.recover(body["message"], body["signature"]) == body["issuer"]


def test_an_unsigned_pass_is_reported_as_a_machine_decision(client, people,
                                                            signing):
    """
    A gate that reached PASS on its own was authorised by nobody. Reporting it
    as human-approved would manufacture an authorisation.
    """
    gid = claim_gate(client)
    settle(client, gid)
    assert sign(client, gid).json()["claim"]["decided_by"] == "machine"


def test_a_human_signature_is_carried_onto_the_claim(client, people, signing):
    """
    A contract that sees only "approved" cannot tell an automatic pass from
    one a person settled. That distinction is the product, and it was being
    lost: the gate walked to SIGNED by a human and the claim still said
    machine, because only a recorded review counted.
    """
    gid = claim_gate(client)
    settle(client, gid)
    appmod.store.gate_transition(gid, "SIGNED", "human")
    assert sign(client, gid).json()["claim"]["decided_by"] == "human"


def test_a_named_reviewer_outranks_a_bare_signature(client, people, signing):
    """
    Both are human, but only one was shown the evidence and ruled on it. The
    named person is the more accountable record, so it is the one reported.
    """
    gid = claim_gate(client)
    settle(client, gid)
    appmod.store.add_review(gid, "reviewer-9", "APPROVE", "looks like a person")
    appmod.store.gate_transition(gid, "SIGNED", "human")
    assert sign(client, gid).json()["claim"]["decided_by"] == "reviewer:reviewer-9"


def test_signing_without_a_key_is_not_reported_as_a_bug(client, people,
                                                        monkeypatch):
    monkeypatch.delenv(claim_mod.KEY_ENV, raising=False)
    gid = claim_gate(client)
    settle(client, gid)
    r = sign(client, gid)
    assert r.status_code == 501
    assert "report and not an authorisation" in r.text


def test_signing_records_the_claim(client, people, signing):
    gid = claim_gate(client)
    settle(client, gid)
    body = sign(client, gid).json()
    row = appmod.store.claim_for(CONTEXT, body["claim"]["nullifier"])
    assert row is not None
    assert row["address"] == WALLET
    assert row["signature"] == body["signature"]


def test_the_same_person_cannot_claim_twice_in_one_context(client, people,
                                                           signing):
    """
    The Sybil guard, end to end. Two wallets, one person: the second is refused
    by the unique index on (context, nullifier), not by a lookup.
    """
    a, b = claim_gate(client), claim_gate(client)
    settle(client, a)
    settle(client, b)
    assert sign(client, a).status_code == 200
    second = sign(client, b, address=OTHER_WALLET)
    assert second.status_code == 409
    assert "One human, one claim" in second.text


def test_the_same_person_may_claim_in_a_different_campaign(client, people,
                                                           signing):
    """
    A second campaign is a legitimate claim. The index is on (context,
    nullifier), not nullifier alone, precisely so this is allowed.
    """
    a, b = claim_gate(client), claim_gate(client)
    settle(client, a)
    settle(client, b)
    assert sign(client, a, context="airdrop-q1").status_code == 200
    assert sign(client, b, context="airdrop-q2").status_code == 200


def test_a_refused_double_claim_issues_no_signature(client, people, signing):
    """
    The write happens before the signature is returned. A signature issued and
    then not recorded would be a valid, unrecorded authorisation loose in the
    world.
    """
    a, b = claim_gate(client), claim_gate(client)
    settle(client, a)
    settle(client, b)
    sign(client, a)
    assert "signature" not in sign(client, b, address=OTHER_WALLET).json()
    assert appmod.store.db.execute(
        "SELECT COUNT(*) AS n FROM claims").fetchone()["n"] == 1


def test_a_bad_address_is_not_signed(client, people, signing):
    gid = claim_gate(client)
    settle(client, gid)
    r = sign(client, gid, address="0x00")
    assert r.status_code == 422
    assert "nobody controls" in r.text


def test_signing_an_unknown_gate_is_a_404(client, signing):
    assert sign(client, "gate-nope").status_code == 404


def test_the_claim_is_pinned_to_the_chain_head(client, people, signing):
    """
    Without the head, a signed claim is a standalone assertion. With it, the
    claim names the exact audit state it was issued against.

    The head read back afterwards is a later one, because recording the claim
    appends its own block. Pinning to the head at issue time is the point: the
    claim cannot name a state that did not yet exist when it was signed.
    """
    gid = claim_gate(client)
    settle(client, gid)
    before = appmod.store.chain(gid)[-1]["hash"]
    body = sign(client, gid).json()
    assert body["claim"]["chain_head"] == before
    assert appmod.store.chain(gid)[-1]["hash"] != before


# ── the demo affordance ───────────────────────────────────────────────────
def test_a_demo_gate_can_be_opened_in_claim_mode(client):
    """The browser has no credentials, so this is its only way to a claim gate."""
    r = client.post("/demo/gate?mode=one_human_one_claim")
    assert r.status_code == 201
    assert r.json()["mode"] == "one_human_one_claim"


def test_a_demo_gate_defaults_to_the_original_mode(client):
    assert client.post("/demo/gate").json()["mode"] == "authorise_action"


def test_an_invented_mode_is_refused(client):
    """
    A gate in a mode that does not exist would carry a requirement set nothing
    can satisfy, and the caller would read the refusal as a bug.
    """
    r = client.post("/demo/gate?mode=definitely_a_human")
    assert r.status_code == 422
    assert "one_human_one_claim" in r.text


def test_enrolling_does_not_open_a_phantom_gate(client, people):
    """
    Reaching the demo tenant used to mean calling `demo_gate` for its side
    effect, which left an unrequested gate in the census — a landing page
    counting authorisations nobody asked for.
    """
    enrol(client, people[0], "p0")
    assert client.get("/health").json()["gates"]["total"] == 0
