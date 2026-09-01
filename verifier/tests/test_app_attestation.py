"""
HTTP surface tests for issuing a sealed certificate.

Everything else STRATUM produces is only meaningful while the verifier is
running and its database is intact. A relying party years later has neither, so
this route is the one that has to survive being wrong: a PDF cannot be recalled,
corrected or re-explained once it has been filed.

What matters here is therefore not that the route returns bytes. It is that it
refuses to produce a document in the three situations where a document would
mislead — an unfinished gate, a regime nobody claimed, and a rendering that
never actually happened — and that when it does produce one, the audit chain
commits to that exact file.

Nutrient is stubbed throughout. These tests are about what the route decides,
not about whether the renderer works; `test_nutrient.py` covers the wire.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import certificate  # noqa: E402
import nutrient  # noqa: E402
from fixtures import FixtureMissing  # noqa: E402

PASSING = {"ran": True, "passed": True, "score": 0.95, "verdict": "PASS"}
OVERLAP = {"ran": True, "passed": False, "score": 0.55, "verdict": "REVIEW",
           "reason": "distance 7.30 falls inside the overlap band"}

SEALED_PDF = b"%PDF-1.7 sealed"


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


@pytest.fixture
def sealer(monkeypatch):
    """A stand-in seal that records what it was handed."""
    seen = {}

    def fake_seal(att):
        seen["att"] = att
        return SEALED_PDF

    monkeypatch.setattr(appmod.certificate, "seal", fake_seal)
    return seen


def new_gate(client, ttl_s=300):
    r = client.post("/gates", json={"workflow_id": "wf-cert",
                                    "mode": "authorise_action", "ttl_s": ttl_s})
    assert r.status_code == 201
    body = r.json()
    return body.get("gate_id") or body["id"]


def drive_to_scored(client, gate_id):
    for to, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                      ("SCORED", "system")):
        assert client.post(f"/gates/{gate_id}/transition",
                           json={"to": to, "actor": actor}).status_code == 200


def decided(client, *, binding=None):
    gate_id = new_gate(client)
    drive_to_scored(client, gate_id)
    r = client.post("/decide", json={
        "gate_id": gate_id, "presence": PASSING, "authenticity": PASSING,
        "binding": binding or PASSING})
    assert r.status_code == 200, r.text
    return gate_id


def cert(client, gate_id, **params):
    return client.get(f"/gates/{gate_id}/attestation.pdf", params=params)


# ── what may be issued ────────────────────────────────────────────────────
def test_a_decided_gate_yields_a_pdf(client, sealer):
    r = cert(client, decided(client))
    assert r.status_code == 200
    assert r.content == SEALED_PDF
    assert r.headers["content-type"] == "application/pdf"


def test_an_unknown_gate_is_not_invented(client, sealer):
    assert cert(client, "no-such-gate").status_code == 404


def test_a_gate_still_in_flight_is_refused(client, sealer):
    """
    A certificate for an undecided gate would circulate as a finished record of
    an open question, and nothing downstream would know the difference.
    """
    gate_id = new_gate(client)
    r = cert(client, gate_id)
    assert r.status_code == 409
    assert "not a verdict" in r.json()["detail"]


def test_an_unfinished_gate_never_reaches_the_renderer(client, monkeypatch):
    monkeypatch.setattr(appmod.certificate, "seal", lambda a: pytest.fail(
        "an undecided gate was sent for rendering"))
    assert cert(client, new_gate(client)).status_code == 409


# ── the regime must be given, never guessed ───────────────────────────────
def test_an_unknown_jurisdiction_is_refused_rather_than_defaulted(client, sealer):
    """
    An unlabelled certificate is recoverable; a mislabelled one is not. The
    route must not quietly fall back to UNSPECIFIED when handed nonsense.
    """
    r = cert(client, decided(client), jurisdiction="NARNIA")
    assert r.status_code == 422
    assert "must not claim a regime" in r.json()["detail"]


def test_an_unknown_risk_tier_is_refused(client, sealer):
    assert cert(client, decided(client), risk_tier="EXTREME").status_code == 422


def test_the_requested_regime_reaches_the_document(client, sealer):
    cert(client, decided(client), jurisdiction="EU_AMLR", risk_tier="ENHANCED")
    assert str(sealer["att"].jurisdiction) == "EU_AMLR"
    assert str(sealer["att"].risk_tier) == "ENHANCED"


# ── the verdict comes from the chain, not the current state ───────────────
def test_a_review_a_human_approved_is_not_certified_as_a_clean_pass(client, sealer):
    """
    After approval the gate reads SIGNED, which is not a verdict. Reading the
    state directly would erase the difference between a gate that satisfied
    every check and one a person chose to let through — the single most
    important distinction on the page.
    """
    gate_id = decided(client, binding=OVERLAP)
    r = client.post(f"/gates/{gate_id}/review",
                    json={"reviewer_id": "r1", "decision": "approve"})
    assert r.status_code == 200
    assert client.get(f"/gates/{gate_id}").json()["state"] == "SIGNED"

    cert(client, gate_id)
    assert sealer["att"].outcome == "REVIEW"


def test_the_ruling_that_stands_is_the_one_reported(client, sealer):
    gate_id = decided(client, binding=OVERLAP)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "reviewer-04", "decision": "approve",
                      "notes": "spoke to them"})
    cert(client, gate_id)
    assert sealer["att"].reviewer["reviewer_id"] == "reviewer-04"


def test_a_gate_nobody_reviewed_carries_no_reviewer(client, sealer):
    cert(client, decided(client))
    assert sealer["att"].reviewer is None


def test_the_certificate_reports_the_ruling_in_force(client, sealer):
    """
    The state machine does not currently let a gate back into REVIEW, so the
    API cannot produce two rulings today. `add_review` is a plain insert
    though, and the row the certificate reads is a choice — a superseded
    decision printed as the standing one would be a false record, so the
    behaviour is pinned rather than left to depend on the transition table.
    """
    gate_id = decided(client, binding=OVERLAP)
    client.post(f"/gates/{gate_id}/review",
                json={"reviewer_id": "first", "decision": "approve"})
    appmod.store.add_review(gate_id, "second", "reject", "check 3", "overruled")

    cert(client, gate_id)
    assert sealer["att"].reviewer["reviewer_id"] == "second"


# ── the chain commits to the document ─────────────────────────────────────
def test_issuing_is_itself_recorded(client, sealer):
    gate_id = decided(client)
    cert(client, gate_id)
    kinds = [e["type"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]]
    assert "attestation" in kinds


def test_the_record_commits_to_the_exact_bytes_issued(client, sealer):
    """
    Two certificates for one gate can then be told apart, and a PDF produced
    later can be matched against the record rather than taken on trust.
    """
    gate_id = decided(client)
    r = cert(client, gate_id)
    events = client.get(f"/gates/{gate_id}/audit").json()["events"]
    payload = json.loads([e for e in events if e["type"] == "attestation"][-1]["payload"])
    assert payload["document_sha256"] == hashlib.sha256(r.content).hexdigest()


def test_the_digest_is_published_on_the_response(client, sealer):
    """So a caller can verify the download without opening it."""
    r = cert(client, decided(client))
    assert r.headers["x-stratum-document-sha256"] == hashlib.sha256(r.content).hexdigest()


def test_a_failed_issue_leaves_no_trail_claiming_success(client, monkeypatch):
    """
    An audit record of an attestation implies a document is in circulation.
    Writing one for an attempt that produced nothing would send an auditor
    looking for a file that never existed.
    """
    gate_id = decided(client)
    monkeypatch.setattr(appmod.certificate, "seal",
                        lambda a: (_ for _ in ()).throw(
                            nutrient.NutrientError("upstream is down")))
    assert cert(client, gate_id).status_code == 502

    kinds = [e["type"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]]
    assert "attestation" not in kinds


def test_issuing_does_not_break_the_chain(client, sealer):
    gate_id = decided(client)
    cert(client, gate_id)
    assert client.get(f"/gates/{gate_id}/verify_chain").json()["ok"] is True


# ── a tampered gate is still certified, and says so ───────────────────────
def test_a_broken_chain_still_issues_and_is_marked(client, sealer, monkeypatch):
    """
    Withholding the document would leave the tamper visible only from inside
    the system that was tampered with. It must be issued, and it must carry the
    failure rather than reading like an ordinary certificate.
    """
    import ledger
    gate_id = decided(client)
    monkeypatch.setattr(appmod.store, "verify_chain", lambda g: ledger.ChainResult(
        ok=False, length=4, head="deadbeef", broken_at=1,
        reason="hash mismatch at event 'transition'"))

    r = cert(client, gate_id)
    assert r.status_code == 200
    assert sealer["att"].chain_intact is False
    assert any("audit chain" in u for u in sealer["att"].unestablished())


def test_the_record_says_the_chain_was_broken_when_it_was(client, sealer,
                                                          monkeypatch):
    import ledger
    gate_id = decided(client)
    monkeypatch.setattr(appmod.store, "verify_chain", lambda g: ledger.ChainResult(
        ok=False, length=4, head="deadbeef", broken_at=1, reason="mismatch"))
    cert(client, gate_id)

    events = client.get(f"/gates/{gate_id}/audit").json()["events"]
    payload = json.loads([e for e in events if e["type"] == "attestation"][-1]["payload"])
    assert payload["chain_intact"] is False


# ── upstream failures are distinguished from our own ──────────────────────
def test_a_missing_credential_reports_as_unavailable_not_as_a_bad_request(client,
                                                                         monkeypatch):
    gate_id = decided(client)
    monkeypatch.setattr(appmod.certificate, "seal",
                        lambda a: (_ for _ in ()).throw(
                            nutrient.NotAuthorised("NUTRIENT_PROCESSOR_API is not set")))
    r = cert(client, gate_id)
    assert r.status_code == 503
    assert "NUTRIENT_PROCESSOR_API" in r.json()["detail"]


def test_replay_mode_explains_why_a_certificate_cannot_be_recorded(client,
                                                                   monkeypatch):
    """
    A certificate embeds its issue time, so no two renders are the same bytes
    and the key derived from them never repeats. Replay does not return a stale
    document here — it raises every time, and the message has to say so or the
    operator will go looking for a fixture that could never exist.
    """
    gate_id = decided(client)
    monkeypatch.setattr(appmod.certificate, "seal",
                        lambda a: (_ for _ in ()).throw(
                            FixtureMissing("nutrient-build", "abc123")))
    r = cert(client, gate_id)
    assert r.status_code == 503
    assert "NUTRIENT_API_MODE=live" in r.json()["detail"]


def test_an_upstream_error_is_reported_as_upstream(client, monkeypatch):
    gate_id = decided(client)
    monkeypatch.setattr(appmod.certificate, "seal",
                        lambda a: (_ for _ in ()).throw(
                            nutrient.NutrientError("build: HTTP 500")))
    assert cert(client, gate_id).status_code == 502


# ── the download is usable ────────────────────────────────────────────────
def test_the_filename_identifies_the_gate(client, sealer):
    gate_id = decided(client)
    disposition = cert(client, gate_id).headers["content-disposition"]
    assert gate_id[:8] in disposition
    assert disposition.endswith('.pdf"')
