"""
HTTP surface tests for check 2 (implementation.md Step 6).

The statistics are covered in test_authenticity.py. What matters here is the
contract a caller sees: that an SD capture is refused rather than passed, that
the baseline announces itself as provisional, and that a verdict against a gate
lands in the ledger under check 2 with the signal that objected.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import synth_zones as sz  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


@pytest.fixture(scope="module")
def gate(client):
    r = client.post("/gates", json={"workflow_id": "wf-authenticity",
                                    "mode": "authorise_action"})
    assert r.status_code == 201
    body = r.json()
    return body.get("gate_id") or body["id"]


@pytest.fixture(scope="module")
def genuine() -> dict:
    """A held-out genuine face, from a seed the module baseline never saw."""
    return sz.genuine(10_000_101)[0]


def test_a_genuine_capture_passes(client, genuine):
    r = client.post("/check/authenticity", json={"scores": genuine})
    assert r.status_code == 200
    body = r.json()
    assert body["ran"] is True
    assert body["passed"] is True
    assert body["check"] == 2


def test_response_carries_both_signals(client, genuine):
    body = client.post("/check/authenticity", json={"scores": genuine}).json()
    names = {s["name"] for s in body["signals"]}
    assert names == {"contrast", "zone_pattern"}
    for signal in body["signals"]:
        assert signal["question"]


def test_baseline_is_declared_provisional(client, genuine):
    """
    Until Step 12 refits on real captures, a caller must be able to see that
    every threshold came from a model of anatomy rather than from measurement.
    """
    body = client.post("/check/authenticity", json={"scores": genuine}).json()
    assert body["detail"]["baseline"]["provisional"] is True


def test_limitations_travel_with_every_verdict(client, genuine):
    """
    The caveat has to be in the payload, not only in the README. A verdict read
    out of the ledger months later arrives without its documentation.
    """
    body = client.post("/check/authenticity", json={"scores": genuine}).json()
    assert any("credential" in line.lower() or "generated" in line.lower()
               for line in body["limitations"])


def test_an_sd_capture_declines_rather_than_passing(client):
    """
    SD mode returns whole-face scores only. If that returned `passed=true`, an
    attacker could bypass check 2 by asking for the cheaper analysis.
    """
    body = client.post("/check/authenticity", json={
        "scores": {"moisture": 62.0, "texture": 55.0, "pore": 48.0}}).json()
    assert body["ran"] is False
    assert body["passed"] is False
    assert body["score"] == 0.0


def test_an_empty_payload_is_refused(client):
    r = client.post("/check/authenticity", json={"scores": {}})
    assert r.status_code == 422


def test_a_flagged_capture_names_the_signal(client):
    """A reviewer needs to know which property failed, not just that one did."""
    smoothed = sz.deviated(20_000_777, contrast=0.15)
    body = client.post("/check/authenticity", json={"scores": smoothed}).json()
    assert body["passed"] is False
    assert "contrast" in body["flagged_by"]


def test_verdict_is_written_to_the_ledger_under_check_two(client, gate, genuine):
    before = client.get(f"/gates/{gate}/audit").json()["events"]
    r = client.post("/check/authenticity",
                    json={"scores": genuine, "gate_id": gate})
    assert r.json()["gate_id"] == gate

    after = client.get(f"/gates/{gate}/audit").json()["events"]
    assert len(after) > len(before)
    assert client.get(f"/gates/{gate}/verify_chain").json()["ok"]

    rows = appmod.store.db.execute(
        "SELECT check_no, detail FROM evidence WHERE gate_id = ?",
        (gate,)).fetchall()
    assert any(row[0] == 2 for row in rows)


def test_evidence_records_whether_the_check_ran(client, gate):
    """
    "Declined to run" and "ran and was satisfied" are different facts, and
    check 3's fusion depends on being able to tell them apart later.
    """
    client.post("/check/authenticity", json={
        "scores": {"moisture": 62.0, "texture": 55.0}, "gate_id": gate})
    rows = appmod.store.db.execute(
        "SELECT detail FROM evidence WHERE gate_id = ? AND check_no = 2",
        (gate,)).fetchall()
    import json
    details = [json.loads(row[0]) for row in rows]
    assert any(d["ran"] is False for d in details)


def test_unknown_gate_is_refused(client, genuine):
    r = client.post("/check/authenticity",
                    json={"scores": genuine, "gate_id": "not-a-gate"})
    assert r.status_code == 404


def test_baseline_is_fitted_once_and_reused(client, genuine):
    """Refitting per request would cost seconds and, worse, let the reference
    population drift between two captures being compared."""
    first = appmod.authenticity_baseline()
    client.post("/check/authenticity", json={"scores": genuine})
    assert appmod.authenticity_baseline() is first
