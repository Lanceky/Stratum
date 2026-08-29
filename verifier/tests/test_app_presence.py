"""
HTTP surface tests for check 1 (implementation.md Step 5).

The physics is covered in test_presence.py. What matters here is the contract a
caller sees: that the spec is derived from the nonce rather than accepted from
the client, that the predictions never cross the wire, and that a verdict
against a gate lands in the ledger with the signal that caused it.
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import challenge as ch  # noqa: E402
import synth_attacks as sa  # noqa: E402

NONCE = "report-nonce-01"


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


@lru_cache(maxsize=None)
def _frames(medium: str) -> tuple[list, float]:
    frames = sa.session(ch.derive(NONCE), medium=medium, seed=1)
    return frames, frames[0]["captured_at"] - 1.0


def _body(medium: str, **extra) -> dict:
    frames, issued = _frames(medium)
    return {"nonce": NONCE, "issued_at": issued, "frames": frames, **extra}


@pytest.fixture(scope="module")
def gate(client):
    r = client.post("/gates", json={"workflow_id": "wf-presence",
                                    "mode": "authorise_action"})
    assert r.status_code == 201
    body = r.json()
    return body.get("gate_id") or body["id"]


def test_challenge_tells_the_client_what_to_do(client):
    r = client.post("/challenge", json={"nonce": NONCE})
    assert r.status_code == 200
    assert set(r.json()) == {"frames", "pose_prompt", "hold_ms", "window_ms"}


def test_challenge_never_ships_the_predictions(client):
    """What the client is about to be measured on is not the client's business."""
    body = client.post("/challenge", json={"nonce": NONCE}).text
    assert "brighter" not in body and "darker" not in body
    assert "prediction" not in body


def test_challenge_is_stateless_and_repeatable(client):
    """
    Regenerated per call, so there is no stored spec to race and no window in
    which two requests disagree about what was asked.
    """
    a = client.post("/challenge", json={"nonce": NONCE}).json()
    b = client.post("/challenge", json={"nonce": NONCE}).json()
    assert a == b


def test_live_capture_passes(client):
    r = client.post("/check/presence", json=_body("live"))
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] and body["failed"] == []
    assert body["check"] == 1


def test_injected_stream_fails_on_illumination(client):
    """The definition of done, over HTTP: the replay attack does not get through."""
    body = client.post("/check/presence", json=_body("injection")).json()
    assert not body["passed"]
    assert body["failed"] == ["illumination"]


def test_every_response_states_the_known_gap(client):
    """A passing verdict must not imply more coverage than was measured."""
    body = client.post("/check/presence", json=_body("live")).json()
    assert any("print" in text.lower() for text in body["limitations"])


def test_frame_count_must_match_the_challenge(client):
    """
    A client that returns fewer frames than it was asked for has not completed
    the challenge, whatever the frames it did return show.
    """
    body = _body("live")
    body["frames"] = body["frames"][:2]
    assert client.post("/check/presence", json=body).status_code == 422


def test_verdict_is_written_to_the_ledger(client, gate):
    """
    DoD: each result reaches `evidence` with its triggering signal. A reviewer
    opening this gate later needs to know which physics failed, not merely that
    something did.
    """
    before = client.get(f"/gates/{gate}/audit").json()
    r = client.post("/check/presence", json=_body("injection", gate_id=gate))
    assert r.json()["gate_id"] == gate

    after = client.get(f"/gates/{gate}/audit").json()
    assert len(after["events"]) == len(before["events"]) + 1

    payload = after["events"][-1]
    written = json.dumps(payload)
    assert "evidence" in written
    assert client.get(f"/gates/{gate}/verify_chain").json()["ok"]


def test_evidence_names_the_signal_that_failed(client, gate):
    store = appmod.store
    client.post("/check/presence", json=_body("injection", gate_id=gate))
    rows = store.db.execute(
        "SELECT check_no, score, detail FROM evidence WHERE gate_id = ?",
        (gate,)).fetchall()
    assert rows
    check_no, _, detail = rows[-1]
    assert check_no == 1
    assert json.loads(detail)["triggering_signal"] == "illumination"


def test_unknown_gate_is_refused(client):
    """Silently dropping the evidence would be worse than refusing the call."""
    r = client.post("/check/presence", json=_body("live", gate_id="not-a-gate"))
    assert r.status_code == 404


def test_a_bad_frame_count_request_is_refused(client):
    assert client.post("/challenge",
                       json={"nonce": NONCE, "n_frames": 3}).status_code == 422
