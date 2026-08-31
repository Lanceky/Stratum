"""
HTTP surface tests for the capture route (implementation.md Step 2c).

This is the one endpoint a photograph ever reaches, so the properties worth
asserting are about what it refuses and what it declines to keep:

  * the challenge is re-derived from the gate's own nonce, so nothing in the
    request can influence what the capture was supposed to demonstrate;
  * an expired or already-settled gate is refused *before* any Perfect Corp
    call, so a foregone answer never spends units;
  * a sensor that cannot be reached yields `ran=false` and a referral to a
    human — never a FAIL, because absence of evidence is not evidence;
  * nothing resembling an image survives the request in the database.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import challenge as ch  # noqa: E402
import capture as capmod  # noqa: E402
from gate import GateState  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 512


def challenged_gate(client, ttl_s=300):
    """A gate that has been issued its challenge, ready to be captured."""
    gate_id = client.post("/demo/gate").json()["id"]
    r = client.post("/challenge", json={"gate_id": gate_id})
    assert r.status_code == 200, r.text
    return gate_id, r.json()


def frames_for(spec, *, count=None, start_ms=None):
    """Multipart parts for a spec: one blob and one timestamp per frame."""
    n = count if count is not None else len(spec["frames"])
    t0 = start_ms if start_ms is not None else datetime.now(UTC).timestamp() * 1000
    hold = spec.get("hold_ms", 400)
    files = [("frames", (f"frame_{i}.jpg", JPEG, "image/jpeg")) for i in range(n)]
    data = {"captured_at": [str(t0 + i * (hold + 50)) for i in range(n)]}
    return files, data


def post_capture(client, gate_id, spec, **kw):
    files, data = frames_for(spec, **kw)
    return client.post(f"/gates/{gate_id}/capture", files=files, data=data)


# ── the demo gate ─────────────────────────────────────────────────────────

def test_demo_gate_is_openable_without_credentials(client):
    """
    The front page links to /gate/demo. Without this route a freshly cloned
    repo cannot reach the capture screen at all, because tenants and workflows
    are created out of band in a real deployment.
    """
    r = client.post("/demo/gate")
    assert r.status_code == 201
    assert r.json()["state"] == str(GateState.REQUESTED)


def test_demo_gates_do_not_share_a_nonce(client):
    a = client.post("/demo/gate").json()
    b = client.post("/demo/gate").json()
    assert a["id"] != b["id"]
    assert (appmod.store.get("gates", a["id"])["nonce"]
            != appmod.store.get("gates", b["id"])["nonce"])


def test_the_demo_gate_does_not_hand_the_browser_its_nonce(client):
    """
    This route is open to anyone. Returning the nonce would let a client derive
    its own challenge ahead of time — undoing the entire reason /challenge
    accepts a gate_id rather than a secret.
    """
    body = client.post("/demo/gate").json()
    assert "nonce" not in body
    real = appmod.store.get("gates", body["id"])["nonce"]
    assert real not in json.dumps(body)


# ── the challenge ─────────────────────────────────────────────────────────

def test_challenge_by_gate_id_never_returns_the_nonce(client):
    """
    The browser names the gate, not the secret. A client that cannot name the
    nonce cannot substitute one.
    """
    _, spec = challenged_gate(client)
    assert "nonce" not in json.dumps(spec)


def test_challenge_moves_the_gate_and_refuses_a_second_issue(client):
    gate_id, _ = challenged_gate(client)
    assert client.get(f"/gates/{gate_id}").json()["state"] == str(GateState.CHALLENGED)

    again = client.post("/challenge", json={"gate_id": gate_id})
    assert again.status_code == 409


def test_challenge_length_is_not_the_clients_to_choose(client):
    """
    A one-frame challenge is no challenge. The capture route re-derives the
    spec with the default anyway, so a shortened one would fail the frame
    count — but it is refused here, with a reason.
    """
    gate_id = client.post("/demo/gate").json()["id"]
    r = client.post("/challenge", json={"gate_id": gate_id, "n_frames": 1})
    assert r.status_code == 422
    assert "n_frames" in r.text


def test_challenge_needs_a_nonce_or_a_gate(client):
    assert client.post("/challenge", json={}).status_code == 422


def test_challenge_for_an_unknown_gate_is_404(client):
    r = client.post("/challenge", json={"gate_id": "does-not-exist"})
    assert r.status_code == 404


# ── what the capture route refuses ────────────────────────────────────────

def test_capture_on_an_unknown_gate_is_404(client):
    files, data = frames_for({"frames": [1, 2, 3, 4]})
    r = client.post("/gates/nope/capture", files=files, data=data)
    assert r.status_code == 404


def test_wrong_frame_count_is_refused(client):
    gate_id, spec = challenged_gate(client)
    r = post_capture(client, gate_id, spec, count=len(spec["frames"]) - 1)
    assert r.status_code == 422
    assert "frames" in r.text


def test_frames_and_timestamps_must_correspond(client):
    gate_id, spec = challenged_gate(client)
    files, data = frames_for(spec)
    data["captured_at"] = data["captured_at"][:-1]
    r = client.post(f"/gates/{gate_id}/capture", files=files, data=data)
    assert r.status_code == 422


def test_capture_before_the_challenge_is_refused(client):
    """
    REQUESTED → CAPTURED is not a legal transition. A capture with no challenge
    to answer is a photograph, not evidence.
    """
    gate_id = client.post("/demo/gate").json()["id"]
    spec = ch.derive("x" * 16).client_view()
    r = post_capture(client, gate_id, spec)
    assert r.status_code == 409


def test_an_expired_gate_is_refused_before_any_sensor_call(client, monkeypatch):
    """
    Doing the work and refusing the transition afterwards would spend Perfect
    Corp units on an answer that cannot be used. HD skin analysis is 12-22
    units a frame, so this is real money, not tidiness.
    """
    gate_id, spec = challenged_gate(client)
    appmod.store.db.execute(
        "UPDATE gates SET expires_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), gate_id))
    appmod.store.db.commit()

    called = []
    monkeypatch.setattr(capmod, "analyse", lambda *a, **k: called.append(1))

    r = post_capture(client, gate_id, spec)
    assert r.status_code == 409
    assert called == [], "the sensor was called for a gate that could not pass"


def test_an_empty_frame_is_refused(client):
    gate_id, spec = challenged_gate(client)
    n = len(spec["frames"])
    files = [("frames", (f"frame_{i}.jpg", b"" if i == 1 else JPEG, "image/jpeg"))
             for i in range(n)]
    t0 = datetime.now(UTC).timestamp() * 1000
    data = {"captured_at": [str(t0 + i * 500) for i in range(n)]}
    r = client.post(f"/gates/{gate_id}/capture", files=files, data=data)
    assert r.status_code == 422


# ── absence of a sensor is not a failure ──────────────────────────────────

def test_unreachable_sensor_refers_to_a_human_rather_than_failing(client):
    """
    In replay mode a live browser capture has no recorded fixture, so this is
    the path the demo actually takes today. It must produce REVIEW: a FAIL
    would accuse a person of something the system never examined.
    """
    gate_id, spec = challenged_gate(client)
    r = post_capture(client, gate_id, spec)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["verdict"] == str(GateState.REVIEW)
    assert body["state"] == str(GateState.REVIEW)
    assert body["requires_human"] is True

    presence = next(c for c in body["checks"] if c["name"] == "presence")
    assert presence["ran"] is False
    assert presence["verdict"] != str(GateState.FAIL)


def test_the_reason_names_the_cause(client):
    """
    "Check 1 could not run" with no cause leaves a reviewer unable to tell a
    missing credential from an attack.
    """
    gate_id, spec = challenged_gate(client)
    body = post_capture(client, gate_id, spec).json()
    presence = next(c for c in body["checks"] if c["name"] == "presence")
    assert "sensor could not be reached" in presence["reason"].lower()
    assert presence["reason"].rstrip(")").split("(")[-1].strip(), \
        "the underlying error was dropped"

    # And the queue headline must lead with the cause, not the consequence.
    assert "presence" in body["reasons"][0]
    assert "could not run" in body["reasons"][0]


def test_a_failed_capture_still_lands_in_the_reviewer_queue(client):
    gate_id, spec = challenged_gate(client)
    post_capture(client, gate_id, spec)
    queue = client.get("/gates").json()
    assert any(g["gate_id"] == gate_id for g in queue["gates"])


def test_the_capture_is_recorded_even_when_scoring_is_impossible(client):
    """
    A human stood in front of a camera. That happened whether or not the
    sensor was reachable, and the gate's history is wrong without it.
    """
    gate_id, spec = challenged_gate(client)
    post_capture(client, gate_id, spec)
    events = client.get(f"/gates/{gate_id}/audit").json()["events"]
    states = [json.loads(e["payload"]).get("to") for e in events]
    assert str(GateState.CAPTURED) in states


def test_binding_is_reported_unattempted_not_passed(client):
    """
    There is no enrolment for a walk-up gate. Omitting check 3 from fusion is
    how it stays visibly unattempted; silently passing two of three checks is
    how an unexamined gate gets waved through.
    """
    gate_id, spec = challenged_gate(client)
    body = post_capture(client, gate_id, spec).json()
    assert not any(c["name"] == "binding" and c["passed"] for c in body["checks"])
    assert any("binding" in r for r in body["reasons"])


# ── what reaches the database ─────────────────────────────────────────────

def test_no_image_bytes_survive_the_request(client, monkeypatch):
    """
    The privacy claim, asserted rather than described. `captures` has no column
    that could hold an image (schema.py); this checks nothing smuggled one into
    the JSON column either.
    """
    marker = b"\x89SECRET-PIXELS"

    def fake(data, name, **kw):
        assert isinstance(data, bytes)
        return {"scores": {"moisture": 0.5}, "constellations": {"pore": [[1, 2, 3]]},
                "pc_task_id": "t-1", "hd": True}

    monkeypatch.setattr(capmod, "analyse", fake)
    gate_id, spec = challenged_gate(client)
    n = len(spec["frames"])
    files = [("frames", (f"f{i}.jpg", marker + JPEG, "image/jpeg")) for i in range(n)]
    t0 = datetime.now(UTC).timestamp() * 1000
    hold = spec.get("hold_ms", 400)
    data = {"captured_at": [str(t0 + i * (hold + 50)) for i in range(n)]}
    client.post(f"/gates/{gate_id}/capture", files=files, data=data)

    rows = [dict(r) for r in appmod.store.db.execute(
        "SELECT * FROM captures WHERE gate_id = ?", (gate_id,)).fetchall()]
    assert rows, "the capture was not recorded at all"
    blob = json.dumps(rows).encode()
    assert marker not in blob
    assert b"SECRET-PIXELS" not in blob


def test_each_frame_is_analysed_in_its_own_mask_directory(client, monkeypatch):
    """
    Masks cache by concern name and `download_masks` returns early if the file
    exists, so a shared directory would make every frame read frame 0's mask —
    destroying the exact between-frame difference check 1 measures.
    """
    seen = []

    def fake(data, name, *, mask_dir=None, **kw):
        seen.append(mask_dir)
        return {"scores": {"moisture": 0.5}, "constellations": {},
                "pc_task_id": "t", "hd": True}

    monkeypatch.setattr(capmod, "analyse", fake)
    gate_id, spec = challenged_gate(client)
    post_capture(client, gate_id, spec)

    assert len(seen) == len(spec["frames"])
    assert len(set(map(str, seen))) == len(seen), "frames shared a mask directory"


def test_timestamps_are_read_as_milliseconds(client, monkeypatch):
    """
    The browser sends Date.now(); presence works in seconds and multiplies its
    deltas by 1000. Getting this wrong fails every capture on the timing signal
    with an elapsed time a thousand times too long, and says nothing useful
    about why.
    """
    captured = {}

    monkeypatch.setattr(capmod, "analyse", lambda *a, **k: {
        "scores": {"moisture": 0.5}, "constellations": {}, "pc_task_id": "t",
        "hd": True})

    import checks.presence as presmod
    original = presmod.timing

    def spy(frames, challenge, issued_at):
        captured["frames"] = frames
        captured["issued_at"] = issued_at
        return original(frames, challenge, issued_at)

    monkeypatch.setattr(presmod, "timing", spy)

    gate_id, spec = challenged_gate(client)
    t0 = datetime.now(UTC).timestamp() * 1000
    post_capture(client, gate_id, spec, start_ms=t0)

    stamps = [f["captured_at"] for f in captured["frames"]]
    assert abs(stamps[0] - t0 / 1000.0) < 1.0, "timestamps were not converted"
    # And the window the check measures must be seconds apart, not thousands.
    assert (stamps[-1] - stamps[0]) < 60


def test_issued_at_is_the_gates_own_creation_not_the_first_frame(client, monkeypatch):
    """
    Passing the first frame's own timestamp would make presence's "a frame
    predates the challenge" test structurally incapable of firing. A check that
    cannot fail is not a check.
    """
    captured = {}
    monkeypatch.setattr(capmod, "analyse", lambda *a, **k: {
        "scores": {"moisture": 0.5}, "constellations": {}, "pc_task_id": "t",
        "hd": True})

    import checks.presence as presmod
    original = presmod.timing

    def spy(frames, challenge, issued_at):
        captured["issued_at"] = issued_at
        captured["first"] = frames[0]["captured_at"]
        return original(frames, challenge, issued_at)

    monkeypatch.setattr(presmod, "timing", spy)

    gate_id, spec = challenged_gate(client)
    post_capture(client, gate_id, spec)

    assert captured["issued_at"] != captured["first"]
    gate = client.get(f"/gates/{gate_id}").json()
    expected = datetime.fromisoformat(gate["created_at"]).timestamp()
    assert abs(captured["issued_at"] - expected) < 1.0


def test_the_challenge_is_rederived_from_the_gates_nonce(client, monkeypatch):
    """
    The client sends frames and timestamps and nothing else. What those frames
    were supposed to demonstrate is decided entirely on the server.
    """
    seen = {}
    monkeypatch.setattr(capmod, "analyse", lambda *a, **k: {
        "scores": {"moisture": 0.5}, "constellations": {}, "pc_task_id": "t",
        "hd": True})

    # The route imports `evaluate` inside the request, so the patch has to land
    # on the source module rather than on a name bound in app.
    import checks.presence as presmod
    original = presmod.evaluate

    def spy(frames, challenge, **kw):
        seen["colours"] = [f.colour.name for f in challenge.frames]
        return original(frames, challenge, **kw)

    monkeypatch.setattr(presmod, "evaluate", spy)

    gate_id, spec = challenged_gate(client)
    post_capture(client, gate_id, spec)

    gate = appmod.store.get("gates", gate_id)
    expected = [f.colour.name for f in ch.derive(gate["nonce"]).frames]
    assert seen["colours"] == expected, "the capture was scored against another spec"
    assert [f["colour"] for f in spec["frames"]] == expected
