"""
The Nutrient client, and the binary half of the fixture layer it needed.

Two things are being pinned here.

The first is the replay key. Nutrient's /build turns input into a document, so
its answer is a function of the bytes sent — the opposite of the upload slot
that produced the last fixture bug, where the answer was *not* a function of
the payload and keying it on the file name broke every capture. Getting this
wrong in the other direction is worse than a missed fixture: it would serve a
recorded PDF of one document in answer to a build over a different one, and a
sealed attestation of the wrong record is a forgery rather than an outage.

The second is that a build never lands in the Perfect Corp unit budget. Those
are unrelated quotas, and the sensor's grant is the one that cannot be topped
up. A document workflow exhausting the ceiling that guards the camera would be
a self-inflicted outage in the part of the system that has no fallback.
"""

import json

import httpx
import pytest

import fixtures
import nutrient


PDF = b"%PDF-1.7\nfake but plausibly a document\n%%EOF"


def reply(status: int, content: bytes = b"", body=None,
          url="https://api.nutrient.io/build"):
    if body is not None:
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        headers = {"content-type": "application/pdf"}
    return httpx.Response(status, content=content, headers=headers,
                          request=httpx.Request("POST", url))


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(nutrient, "API_KEY", "test-key")
    return "test-key"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """A fixture tree of our own, so recording does not touch the repo's."""
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path / "fixtures")
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", tmp_path / "fixtures" / "synthetic")
    return tmp_path


# ── auth ──────────────────────────────────────────────────────────────────
def test_a_missing_key_is_caught_before_any_network_call(monkeypatch):
    monkeypatch.setattr(nutrient, "API_KEY", "")
    with pytest.raises(nutrient.NotAuthorised):
        nutrient._headers()


def test_the_missing_key_message_says_where_to_get_one(monkeypatch):
    monkeypatch.setattr(nutrient, "API_KEY", "")
    with pytest.raises(nutrient.NotAuthorised) as e:
        nutrient._headers()
    assert "dashboard.nutrient.io" in str(e.value)


def test_auth_is_a_plain_bearer(keyed):
    assert nutrient._headers()["Authorization"] == "Bearer test-key"


def test_configured_tracks_the_key(monkeypatch):
    monkeypatch.setattr(nutrient, "API_KEY", "")
    assert nutrient.configured() is False
    monkeypatch.setattr(nutrient, "API_KEY", "k")
    assert nutrient.configured() is True


# ── refusals say which instruction was wrong ──────────────────────────────
def test_a_rejected_key_raises_not_authorised(keyed):
    err = nutrient._explain(reply(401, body={"description": "bad key"}), "build")
    assert isinstance(err, nutrient.NotAuthorised)


def test_an_instruction_error_is_not_mistaken_for_an_auth_error(keyed):
    err = nutrient._explain(reply(400, body={"details": [
        {"details": "part 'index.html' has no matching file"}]}), "build")
    assert isinstance(err, nutrient.NutrientError)
    assert not isinstance(err, nutrient.NotAuthorised)
    assert "no matching file" in str(err)


def test_a_non_json_refusal_still_explains_itself(keyed):
    err = nutrient._explain(
        httpx.Response(502, content=b"upstream died",
                       request=httpx.Request("POST", "https://api.nutrient.io/build")),
        "build")
    assert "upstream died" in str(err)


# ── the multipart shape ───────────────────────────────────────────────────
def test_instructions_travel_as_their_own_json_part():
    parts = dict(nutrient._multipart({"parts": [{"file": "a"}]}, None))
    assert parts["instructions"][2] == "application/json"
    assert json.loads(parts["instructions"][1]) == {"parts": [{"file": "a"}]}


def test_each_file_becomes_a_part_named_as_the_instructions_reference_it():
    files = {"index.html": ("index.html", b"<h1>x</h1>", "text/html")}
    parts = dict(nutrient._multipart({"parts": [{"file": "index.html"}]}, files))
    assert parts["index.html"][1] == b"<h1>x</h1>"


def test_html_to_pdf_names_the_part_its_instructions_ask_for(keyed, isolated,
                                                             monkeypatch):
    """
    A mismatch between the part name and the `file` reference is a 400 that
    reads like an auth problem. This is the reason html_to_pdf exists rather
    than the shape being retyped per call site.
    """
    seen = {}
    monkeypatch.setattr(fixtures, "MODE", "live")

    def fake_post(url, **kw):
        seen.update(dict(kw["files"]))
        return reply(200, PDF)

    monkeypatch.setattr(nutrient.httpx, "post", fake_post)
    nutrient.html_to_pdf("<h1>hi</h1>")

    named = json.loads(seen["instructions"][1])["parts"][0]["file"]
    assert named in seen, f"instructions reference {named!r} with no such part"


def test_pdfa_is_requested_only_when_asked_for(keyed, isolated, monkeypatch):
    seen = {}
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: (seen.update(dict(kw["files"])),
                                           reply(200, PDF))[1])

    nutrient.html_to_pdf("<h1>a</h1>")
    assert "output" not in json.loads(seen["instructions"][1])

    nutrient.html_to_pdf("<h1>b</h1>", pdfa=True)
    assert json.loads(seen["instructions"][1])["output"]["type"] == "pdfa"


# ── the replay key covers the document, not just the instructions ─────────
def test_different_input_bytes_are_different_builds():
    same = {"parts": [{"file": "f"}]}
    a = fixtures.fixture_key("nutrient-build", nutrient._fixture_payload(
        same, {"f": ("f", b"invoice A", "text/html")}))
    b = fixtures.fixture_key("nutrient-build", nutrient._fixture_payload(
        same, {"f": ("f", b"invoice B", "text/html")}))
    assert a != b, "a recording would be replayed for a document nobody submitted"


def test_identical_input_is_the_same_build():
    same = {"parts": [{"file": "f"}]}
    key = lambda: fixtures.fixture_key("nutrient-build", nutrient._fixture_payload(
        same, {"f": ("f", b"same bytes", "text/html")}))
    assert key() == key()


def test_the_key_survives_reordered_file_parts():
    """Part order is not meaningful; two identical builds must share a key."""
    inst = {"parts": [{"file": "a"}, {"file": "b"}]}
    one = nutrient._fixture_payload(inst, {"a": ("a", b"1", "text/html"),
                                           "b": ("b", b"2", "text/html")})
    two = nutrient._fixture_payload(inst, {"b": ("b", b"2", "text/html"),
                                           "a": ("a", b"1", "text/html")})
    assert fixtures.fixture_key("nutrient-build", one) == \
           fixtures.fixture_key("nutrient-build", two)


def test_the_raw_bytes_are_not_kept_in_the_key():
    """
    The digest stands in for the bytes. Keeping the bytes themselves would put
    a whole document into every fixture filename's hash input and, worse, into
    anything that logs the payload.
    """
    payload = nutrient._fixture_payload({"parts": []},
                                        {"f": ("f", b"secret contents", "text/html")})
    assert b"secret contents" not in json.dumps(payload).encode()


# ── binary record/replay ──────────────────────────────────────────────────
def test_replay_returns_the_recorded_pdf_byte_for_byte(isolated, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    key = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.FIXTURE_DIR.mkdir(parents=True)
    (fixtures.FIXTURE_DIR / f"{key}.pdf").write_bytes(PDF)

    got = fixtures.call_binary("nutrient-build", {"x": 1},
                               lambda: pytest.fail("replay went to the network"),
                               ext=".pdf")
    assert got == PDF


def test_replay_without_a_recording_fails_loudly(isolated, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    with pytest.raises(fixtures.FixtureMissing):
        fixtures.call_binary("nutrient-build", {"x": 1},
                             lambda: pytest.fail("replay went to the network"),
                             ext=".pdf")


def test_a_recorded_pdf_is_written_as_a_pdf(isolated, monkeypatch):
    """
    Not base64 in a JSON envelope. The point of a fixture tree is that you can
    open the recorded response and see what the API actually said.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    fixtures.call_binary("nutrient-build", {"x": 1}, lambda: PDF, ext=".pdf")
    written = list(fixtures.FIXTURE_DIR.glob("*.pdf"))
    assert len(written) == 1
    assert written[0].read_bytes().startswith(b"%PDF")


def test_auto_mode_replays_a_recording_rather_than_paying_again(isolated,
                                                                monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "auto")
    key = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.FIXTURE_DIR.mkdir(parents=True)
    (fixtures.FIXTURE_DIR / f"{key}.pdf").write_bytes(PDF)
    got = fixtures.call_binary("nutrient-build", {"x": 1},
                               lambda: pytest.fail("had a recording already"),
                               ext=".pdf")
    assert got == PDF


def test_auto_mode_never_settles_for_a_synthetic_stand_in(isolated, monkeypatch):
    """
    Same rule as the JSON path. `auto` exists to obtain real data; treating a
    placeholder as a hit means the real call never happens.
    """
    monkeypatch.setattr(fixtures, "MODE", "auto")
    key = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.SYNTHETIC_DIR.mkdir(parents=True)
    (fixtures.SYNTHETIC_DIR / f"{key}.pdf").write_bytes(b"%PDF-placeholder")

    got = fixtures.call_binary("nutrient-build", {"x": 1}, lambda: PDF, ext=".pdf")
    assert got == PDF


def test_a_build_is_not_charged_against_the_sensor_budget(isolated, monkeypatch):
    """
    Perfect Corp's grant is metered and cannot be topped up; Nutrient's is
    unmetered. Sharing one counter would let document work close the gate on
    the camera.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setattr(fixtures, "UNIT_LOG", isolated / "units.log")
    before = fixtures.budget_status()["spent"]
    fixtures.call_binary("nutrient-build", {"x": 1}, lambda: PDF, ext=".pdf")
    assert fixtures.budget_status()["spent"] == before


def test_build_goes_through_the_fixture_layer(keyed, isolated, monkeypatch):
    """The client must not reach the network directly in replay mode."""
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda *a, **k: pytest.fail("replay hit the network"))
    with pytest.raises(fixtures.FixtureMissing):
        nutrient.build({"parts": []}, {})


# ── analyze is deliberately live ──────────────────────────────────────────
def test_analyze_is_not_recorded(keyed, isolated, monkeypatch):
    """
    It is free, and it is the credential probe. A recorded answer would report
    a key as working after it had been revoked.
    """
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: reply(200, body={"cost": 1}))
    assert nutrient.analyze({"parts": []}) == {"cost": 1}


def test_analyze_reports_a_bad_key(keyed, monkeypatch):
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: reply(401, body={"description": "nope"}))
    with pytest.raises(nutrient.NotAuthorised):
        nutrient.analyze({"parts": []})


def test_analyze_hits_the_free_endpoint(keyed, monkeypatch):
    seen = {}
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: (seen.update(url=url),
                                           reply(200, body={}))[1])
    nutrient.analyze({"parts": []})
    assert seen["url"].endswith("/analyze_build")
