"""
The Nutrient client, and the binary half of the fixture layer it needed.

Most of this file pins things the live API taught us, because the obvious
reading of the docs was wrong in three places and each one surfaces as a 400 or
403 that points somewhere else:

  - keys are scoped per product, and the wrong one returns a bare 403 that is
    byte-identical to a revoked key;
  - /analyze_build takes JSON while /build takes multipart;
  - HTML is not a `file` part, it is an `html` part naming an attached file.

Each of those cost a round trip to discover. A test that fails loudly is
cheaper than rediscovering them at 3am on submission day.

The other half is the replay key. Nutrient turns input into a document, so its
answer is a function of the bytes sent — the opposite of the upload slot that
caused the last fixture bug, where the answer was *not* a function of the
payload. Getting it wrong in this direction is worse than a missed fixture: it
would serve a recorded PDF of one document in answer to a build over a
different one, and a sealed attestation of the wrong record is a forgery rather
than an outage.
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
    """All three product keys present and distinct, as the grant issues them."""
    monkeypatch.setenv("NUTRIENT_PROCESSOR_API", "proc-key")
    monkeypatch.setenv("NUTRIENT_DATA_EXTRACTION_API", "extract-key")
    monkeypatch.setenv("NUTRIENT_ACCESSIBILITY_API", "access-key")
    monkeypatch.delenv("NUTRIENT_API_KEY", raising=False)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """A fixture tree of our own, so recording does not touch the repo's."""
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path / "fixtures")
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", tmp_path / "fixtures" / "synthetic")
    return tmp_path


@pytest.fixture
def capture(monkeypatch):
    """Record what would have gone on the wire, and answer with a PDF."""
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers", {})
        seen["files"] = dict(kw["files"]) if kw.get("files") else {}
        seen["content"] = kw.get("content")
        return reply(200, PDF)

    monkeypatch.setattr(nutrient.httpx, "post", fake_post)
    return seen


def control(seen, name="instructions"):
    return json.loads(seen["files"][name][1])


# ── keys are scoped per product ───────────────────────────────────────────
def test_each_product_reads_its_own_key(keyed):
    assert nutrient.key_for(nutrient.PROCESSOR) == "proc-key"
    assert nutrient.key_for(nutrient.EXTRACTION) == "extract-key"
    assert nutrient.key_for(nutrient.ACCESSIBILITY) == "access-key"


def test_a_single_key_account_still_works(monkeypatch):
    """The fallback exists so a one-key account is not locked out."""
    monkeypatch.delenv("NUTRIENT_PROCESSOR_API", raising=False)
    monkeypatch.setenv("NUTRIENT_API_KEY", "only-one")
    assert nutrient.key_for(nutrient.PROCESSOR) == "only-one"


def test_a_product_specific_key_wins_over_the_fallback(monkeypatch):
    monkeypatch.setenv("NUTRIENT_API_KEY", "generic")
    monkeypatch.setenv("NUTRIENT_PROCESSOR_API", "specific")
    assert nutrient.key_for(nutrient.PROCESSOR) == "specific"


def test_a_missing_key_is_caught_before_any_network_call(monkeypatch):
    monkeypatch.delenv("NUTRIENT_PROCESSOR_API", raising=False)
    monkeypatch.delenv("NUTRIENT_API_KEY", raising=False)
    with pytest.raises(nutrient.NotAuthorised):
        nutrient._headers(nutrient.PROCESSOR)


def test_the_missing_key_message_names_the_variable_to_set(monkeypatch):
    monkeypatch.delenv("NUTRIENT_DATA_EXTRACTION_API", raising=False)
    monkeypatch.delenv("NUTRIENT_API_KEY", raising=False)
    with pytest.raises(nutrient.NotAuthorised) as e:
        nutrient._headers(nutrient.EXTRACTION)
    assert "NUTRIENT_DATA_EXTRACTION_API" in str(e.value)


def test_auth_is_a_plain_bearer(keyed):
    assert nutrient._headers(nutrient.PROCESSOR)["Authorization"] == "Bearer proc-key"


def test_build_uses_the_processor_key(keyed, isolated, capture, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    nutrient.html_to_pdf("<h1>x</h1>")
    assert capture["headers"]["Authorization"] == "Bearer proc-key"


def test_extraction_uses_the_extraction_key(keyed, isolated, capture, monkeypatch):
    """
    Sending the processor key here returns a bare 403 that looks exactly like a
    revoked credential. The keys are not interchangeable and the client must
    not treat them as if they were.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: (capture.update(headers=kw["headers"]),
                                           reply(200, body={"data": []}))[1])
    nutrient.extract(b"%PDF", {"type": "object"})
    assert capture["headers"]["Authorization"] == "Bearer extract-key"


def test_a_403_says_it_might_be_the_wrong_product_key(keyed):
    """
    Verified live: the extraction key on /analyze_build returns a bare
    `403 Forbidden` with no hint that scope is the problem.
    """
    err = nutrient._explain(reply(403, body={"error": {"details": "Forbidden"}}),
                            "build", nutrient.PROCESSOR)
    assert isinstance(err, nutrient.NotAuthorised)
    assert "NUTRIENT_PROCESSOR_API" in str(err)
    assert "scoped" in str(err)


# ── refusals name the instruction that was wrong ──────────────────────────
def test_a_failing_path_is_surfaced(keyed):
    err = nutrient._explain(reply(400, body={"error": {"failingPaths": [
        {"path": "$.index.html", "details": "is of the unsupported mimetype text/html"}]}}),
        "build", nutrient.PROCESSOR)
    assert "$.index.html" in str(err)
    assert "unsupported mimetype" in str(err)


def test_the_extraction_error_shape_is_also_understood(keyed):
    """Extraction nests failingPaths under errorDetails, not error."""
    err = nutrient._explain(reply(400, body={
        "errorDetails": {"failingPaths": [
            {"path": "$.schema", "details": "schema is required for extraction"}]},
        "errorMessage": "The request is malformed"}),
        "extract", nutrient.EXTRACTION)
    assert "schema is required" in str(err)


def test_an_instruction_error_is_not_mistaken_for_an_auth_error(keyed):
    err = nutrient._explain(reply(400, body={"error": {"details": "bad parts"}}),
                            "build", nutrient.PROCESSOR)
    assert not isinstance(err, nutrient.NotAuthorised)


def test_a_non_json_refusal_still_explains_itself(keyed):
    err = nutrient._explain(
        httpx.Response(502, content=b"upstream died",
                       request=httpx.Request("POST", "https://api.nutrient.io/build")),
        "build", nutrient.PROCESSOR)
    assert "upstream died" in str(err)


# ── the shapes the live API insisted on ───────────────────────────────────
def test_html_goes_in_as_an_html_part_not_a_file_part(keyed, isolated, capture,
                                                      monkeypatch):
    """
    The one that cost a round trip. `{"file": "index.html"}` is refused as
    "unsupported mimetype text/html" whatever Content-Type the part declares,
    because the service sniffs the bytes rather than trusting the header.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    nutrient.html_to_pdf("<h1>x</h1>")
    part = control(capture)["parts"][0]
    assert "html" in part, f"HTML must be an `html` part, got {part}"
    assert "file" not in part


def test_the_html_part_names_an_attached_file(keyed, isolated, capture,
                                              monkeypatch):
    """
    Inline HTML is refused: "`<h1>x</h1>` is invalid, sub-directories are not
    allowed". The field wants a filename that matches an attached part.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    nutrient.html_to_pdf("<h1>x</h1>")
    named = control(capture)["parts"][0]["html"]
    assert named in capture["files"], f"instructions name {named!r} with no such part"
    assert capture["files"][named][1] == b"<h1>x</h1>"


def test_analyze_sends_json_not_multipart(keyed, monkeypatch):
    """
    /build and /analyze_build disagree on content type. Multipart here returns
    415, which reads like a malformed request rather than a wrong-route error.
    """
    seen = {}
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: (seen.update(kw, url=url),
                                           reply(200, body={"cost": 0.5}))[1])
    nutrient.analyze({"parts": [{"html": "index.html"}]})
    assert seen.get("files") is None
    assert seen["headers"]["Content-Type"] == "application/json"
    assert json.loads(seen["content"])["parts"][0]["html"] == "index.html"


def test_analyze_hits_the_free_endpoint(keyed, monkeypatch):
    seen = {}
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: (seen.update(url=url),
                                           reply(200, body={}))[1])
    nutrient.analyze({"parts": []})
    assert seen["url"].endswith("/analyze_build")


def test_pdfa_is_requested_only_when_asked_for(keyed, isolated, capture,
                                               monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    nutrient.html_to_pdf("<h1>a</h1>")
    assert "output" not in control(capture)
    nutrient.html_to_pdf("<h1>b</h1>", pdfa=True)
    assert control(capture)["output"]["type"] == "pdfa"


def test_signing_sends_the_document_as_a_file_part(keyed, isolated, capture,
                                                   monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    nutrient.sign(PDF)
    assert capture["files"]["file"][1] == PDF
    assert control(capture, "data")["signatureType"] == "cades"


def test_instructions_travel_as_their_own_json_part():
    parts = dict(nutrient._multipart({"instructions": {"parts": []}}, None))
    assert parts["instructions"][2] == "application/json"


# ── the replay key covers the document, not just the instructions ─────────
def key(op, ctl, files):
    return fixtures.fixture_key(op, nutrient._fixture_payload(op, ctl, files))


def test_different_input_bytes_are_different_builds():
    inst = {"parts": [{"html": "f"}]}
    a = key("build", inst, {"f": ("f", b"invoice A", "text/html")})
    b = key("build", inst, {"f": ("f", b"invoice B", "text/html")})
    assert a != b, "a recording would be replayed for a document nobody submitted"


def test_identical_input_is_the_same_build():
    inst = {"parts": [{"html": "f"}]}
    files = {"f": ("f", b"same bytes", "text/html")}
    assert key("build", inst, files) == key("build", inst, files)


def test_the_key_survives_reordered_file_parts():
    """Part order is not meaningful; two identical builds must share a key."""
    inst = {"parts": [{"html": "a"}]}
    one = key("build", inst, {"a": ("a", b"1", "text/html"),
                              "b": ("b", b"2", "text/html")})
    two = key("build", inst, {"b": ("b", b"2", "text/html"),
                              "a": ("a", b"1", "text/html")})
    assert one == two


def test_two_operations_over_the_same_bytes_do_not_collide():
    """
    Signing a document and building from it are different acts. Without the
    operation in the key, one could be served the other's recording.
    """
    files = {"file": ("f", PDF, "application/pdf")}
    assert key("build", {}, files) != key("sign", {}, files)


def test_the_operation_separates_the_keys_on_its_own():
    """
    Pins the mechanism, not just the outcome. The separation above is carried
    by fixture_key's op prefix; the `op` field in the payload is reinforcement.
    Asserting only the outcome lets either one be removed silently, because the
    survivor still makes the test pass.
    """
    files = {"file": ("f", PDF, "application/pdf")}
    payload = nutrient._fixture_payload("build", {}, files)
    assert fixtures.fixture_key("build", payload).startswith("build__")
    assert fixtures.fixture_key("sign", payload) != fixtures.fixture_key(
        "build", payload), "the op prefix is the guard and it is gone"
    assert payload["op"] == "build", "the payload records the op as well"


def test_the_raw_bytes_are_not_kept_in_the_key():
    """
    The digest stands in for the bytes. Keeping them would put a whole document
    into anything that logs the payload.
    """
    payload = nutrient._fixture_payload(
        "build", {}, {"f": ("f", b"secret contents", "text/html")})
    assert b"secret contents" not in json.dumps(payload).encode()


# ── binary record/replay ──────────────────────────────────────────────────
def test_replay_returns_the_recorded_pdf_byte_for_byte(isolated, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    k = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.FIXTURE_DIR.mkdir(parents=True)
    (fixtures.FIXTURE_DIR / f"{k}.pdf").write_bytes(PDF)
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
    k = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.FIXTURE_DIR.mkdir(parents=True)
    (fixtures.FIXTURE_DIR / f"{k}.pdf").write_bytes(PDF)
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
    k = fixtures.fixture_key("nutrient-build", {"x": 1})
    fixtures.SYNTHETIC_DIR.mkdir(parents=True)
    (fixtures.SYNTHETIC_DIR / f"{k}.pdf").write_bytes(b"%PDF-placeholder")
    assert fixtures.call_binary("nutrient-build", {"x": 1},
                                lambda: PDF, ext=".pdf") == PDF


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
        nutrient.html_to_pdf("<h1>x</h1>")


def test_signing_goes_through_the_fixture_layer(keyed, isolated, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda *a, **k: pytest.fail("replay hit the network"))
    with pytest.raises(fixtures.FixtureMissing):
        nutrient.sign(PDF)


def test_analyze_is_not_recorded(keyed, isolated, monkeypatch):
    """
    It is free, and it is the credential probe. A recorded answer would report
    a key as working after it had been revoked.
    """
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: reply(200, body={"cost": 0.5}))
    assert nutrient.analyze({"parts": []}) == {"cost": 0.5}


def test_analyze_reports_a_bad_key(keyed, monkeypatch):
    monkeypatch.setattr(nutrient.httpx, "post",
                        lambda url, **kw: reply(401, body={"error": {"details": "nope"}}))
    with pytest.raises(nutrient.NotAuthorised):
        nutrient.analyze({"parts": []})
