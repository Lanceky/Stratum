"""
The Foxit toolset, and the boundary it exists to demonstrate.

Two things are pinned here, and they fail for different reasons.

The first is the wire contract, taken from Foxit's own MCP server rather than
from prose, because the docs put two products side by side with two different
auth schemes and the wrong one returns a 401 that reads exactly like a bad key.
`client_id`/`client_secret` are headers, lowercase and underscored; an
`Authorization` header here is the half hour nobody gets back.

The second is the claim the project is making. Every tool the agent holds can
be undone or changes nothing, and the one act that creates an obligation is not
in the toolset at all. That is a property of the code, not of the README, so it
is asserted: no argument to any exposed tool produces a signature, and the
refusal survives being asked politely, repeatedly, and with a valid gate.
"""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import fixtures  # noqa: E402
import foxit  # noqa: E402
from store import Store  # noqa: E402

PDF = b"%PDF-1.7\nplausibly a document\n%%EOF"


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv(foxit.ID_ENV, "id-abc")
    monkeypatch.setenv(foxit.SECRET_ENV, "secret-xyz")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Fixtures written to a scratch tree, so recording never touches the repo."""
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path / "fixtures")
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", tmp_path / "fixtures" / "synthetic")
    monkeypatch.setattr(fixtures, "UNIT_LOG", tmp_path / "fixtures" / "units.log")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "store", Store())
    return TestClient(appmod.app)


@pytest.fixture
def gate_id(client):
    return client.post("/demo/gate").json()["id"]


def reply(status: int, body=None, content: bytes = b""):
    return httpx.Response(
        status,
        json=body if body is not None else None,
        content=None if body is not None else content,
        request=httpx.Request("POST", "https://na1.fusion.foxit.com/x"),
    )


@pytest.fixture
def capture(monkeypatch):
    """Record outgoing calls without letting any of them leave."""
    seen = []

    class FakeClient:
        def __init__(self, *a, **kw):
            self.base_url = kw.get("base_url", "")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _record(self, method, path, **kw):
            seen.append({"method": method, "path": path, **kw})
            return reply(200, {"documentId": "d1"})

        def post(self, path, **kw):
            return self._record("POST", path, **kw)

        def get(self, path, **kw):
            return self._record("GET", path, **kw)

    monkeypatch.setattr(foxit.httpx, "Client", FakeClient)
    return seen


# ── the wire contract ────────────────────────────────────────────────────────

def test_credentials_go_in_headers_not_a_bearer_token(keyed):
    """
    The single most expensive mistake available here. Foxit's eSign product
    uses OAuth2 and its docs sit beside these, so reaching for `Authorization`
    is the natural error — and it fails as a 401 that looks like a bad key.
    """
    headers = foxit._headers()
    assert headers == {"client_id": "id-abc", "client_secret": "secret-xyz"}
    assert "Authorization" not in headers


def test_a_missing_credential_is_caught_before_any_network_call(monkeypatch):
    monkeypatch.delenv(foxit.ID_ENV, raising=False)
    monkeypatch.delenv(foxit.SECRET_ENV, raising=False)
    with pytest.raises(foxit.NotAuthorised) as exc:
        foxit._headers()
    assert "sign-up" in str(exc.value)


def test_half_a_credential_is_not_configured(monkeypatch):
    monkeypatch.setenv(foxit.ID_ENV, "id-abc")
    monkeypatch.delenv(foxit.SECRET_ENV, raising=False)
    assert foxit.configured() is False


@pytest.mark.parametrize("given", [
    "https://na1.fusion.foxit.com/pdf-services",
    "https://na1.fusion.foxit.com/pdf-services/",
    "https://na1.fusion.foxit.com/pdf-services/api",
    "https://na1.fusion.foxit.com/pdf-services/api/",
])
def test_both_documented_base_url_forms_normalise_to_one(monkeypatch, given):
    """
    Foxit's MCP config and its API reference disagree about whether `/api`
    belongs in the host. Accepting one and 404ing on the other would be a base
    URL that looks correct in `.env` and fails at the first call.
    """
    monkeypatch.setenv("FOXIT_PDF_BASE_URL", given)
    assert foxit.base_url() == "https://na1.fusion.foxit.com/pdf-services"


def test_paths_carry_their_own_api_prefix():
    """Because `base_url()` strips it, every path must supply it."""
    for path in (foxit.UPLOAD_PATH, foxit.WATERMARK_PATH, foxit.PROPERTIES_PATH,
                 foxit.COMPARE_PATH, foxit.TASK_PATH, foxit.DOWNLOAD_PATH):
        assert path.startswith("/api/")


def test_upload_sends_the_file_as_a_multipart_part(keyed, isolated, capture,
                                                   monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")

    foxit.upload(PDF, "draft.pdf")

    sent = capture[0]
    assert sent["path"] == "/api/documents/upload"
    # The part is named `file`, and the bytes go in as-is.
    assert sent["files"]["file"][0] == "draft.pdf"
    assert sent["files"]["file"][1] == PDF


def test_an_operation_posts_json_and_returns_a_task_id(keyed, isolated, monkeypatch):
    """
    Operations are asynchronous: a POST hands back a `taskId`, never a result.
    Treating the reply as the finished document is the natural first mistake.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")
    seen = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def post(self, path, **kw):
            seen.update({"path": path, **kw})
            return reply(200, {"taskId": "t-77"})

    monkeypatch.setattr(foxit.httpx, "Client", FakeClient)

    body = foxit.watermark_body("d1", "g1")
    assert foxit._submit("foxit-watermark", foxit.WATERMARK_PATH, body) == "t-77"
    assert seen["path"] == foxit.WATERMARK_PATH
    # JSON, not multipart — only the upload is multipart.
    assert seen["json"] == body
    assert seen["headers"]["client_id"] == "id-abc"


def test_a_reply_without_an_id_is_an_error_not_a_none(keyed, isolated, monkeypatch):
    """
    A missing `documentId` handed back as `None` would travel one call further
    and fail as a 404 on a URL with `None` in it, which points at the wrong
    place entirely.
    """
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")
    monkeypatch.setattr(fixtures, "call", lambda op, payload, fn: {})
    with pytest.raises(foxit.FoxitError, match="no documentId"):
        foxit.upload(PDF)


# ── errors ───────────────────────────────────────────────────────────────────

def test_a_401_names_the_likeliest_cause():
    err = foxit._explain(reply(401, {"code": "UNAUTHORIZED", "message": "bad key"}),
                         "foxit-upload")
    assert isinstance(err, foxit.NotAuthorised)
    assert "eSign" in str(err)


def test_an_error_body_is_unpacked_rather_than_shown_as_a_status():
    err = foxit._explain(
        reply(400, {"code": "INVALID_DOCUMENT", "message": "not a PDF"}),
        "foxit-watermark")
    assert "INVALID_DOCUMENT" in str(err)
    assert "not a PDF" in str(err)


def test_a_non_json_error_still_produces_a_readable_message():
    err = foxit._explain(reply(502, content=b"<html>gateway</html>"), "foxit-upload")
    assert "HTTP 502" in str(err)


def test_a_failed_task_raises_with_foxits_own_reason(keyed, isolated, monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, **kw):
            return reply(200, {"taskId": "t-1", "status": "FAILED",
                               "error": {"code": "E", "message": "page is encrypted"}})

    monkeypatch.setattr(foxit.httpx, "Client", FakeClient)
    with pytest.raises(foxit.FoxitError, match="page is encrypted"):
        foxit.await_task("t-1", "foxit-watermark")


def test_an_unknown_status_stops_rather_than_polling_forever(keyed, isolated,
                                                             monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, **kw):
            return reply(200, {"taskId": "t-1", "status": "SOMETHING_NEW"})

    monkeypatch.setattr(foxit.httpx, "Client", FakeClient)
    with pytest.raises(foxit.FoxitError, match="unknown status"):
        foxit.await_task("t-1", "foxit-watermark")


# ── replay ───────────────────────────────────────────────────────────────────

def test_replay_mode_never_reaches_the_network(monkeypatch):
    """
    The suite pins `STRATUM_API_MODE=replay` to assert nothing leaves the
    machine, and an integration that quietly exempted itself would leave that
    assertion green while making it untrue.
    """
    monkeypatch.delenv(foxit.MODE_ENV, raising=False)
    monkeypatch.setattr(fixtures, "MODE", "replay")
    assert foxit.mode() == "replay"


def test_the_task_poll_is_keyed_apart_from_the_submission():
    """
    Both belong to one operation, but one answers with a `taskId` and the other
    with a result. Filed under a single key they would overwrite each other the
    moment either needed a generic stand-in.
    """
    assert "foxit-watermark" in fixtures.CONTENT_INDEPENDENT
    assert "foxit-watermark-task" in fixtures.CONTENT_INDEPENDENT


def test_the_seeded_toolset_runs_without_credentials(monkeypatch):
    """
    The demo has to work before a credential exists. An agent console where
    every tool 503s would demonstrate the opposite of the claim — an agent that
    cannot do anything proves nothing about where the boundary sits.
    """
    monkeypatch.delenv(foxit.ID_ENV, raising=False)
    monkeypatch.delenv(foxit.SECRET_ENV, raising=False)
    monkeypatch.setattr(fixtures, "MODE", "replay")

    doc = foxit.upload(PDF)
    assert doc
    assert foxit.properties(doc)
    assert foxit.mark_unsigned(doc, "g_demo") != doc


def test_a_watermark_body_is_built_in_one_place():
    """The seeder and the client must agree, or the stand-in never replays."""
    body = foxit.watermark_body("d1", "g_7")
    assert body["documentId"] == "d1"
    assert "g_7" in body["config"]["content"]
    assert "UNSIGNED" in body["config"]["content"]


# ── the credit guard ─────────────────────────────────────────────────────────

def test_foxit_calls_are_counted_apart_from_the_sensor_budget(isolated):
    """
    Two unrelated quotas must not share one counter. Perfect Corp's grant is
    the one with no fallback, and letting document work spend it would exhaust
    the budget guarding the sensor.
    """
    fixtures.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixtures.UNIT_LOG.write_text(
        "1,skin-analysis-hd,22\n2,foxit-upload,0\n3,foxit-watermark,0\n")
    assert foxit.spent() == 2
    assert fixtures._spent() == 22


def test_the_guard_refuses_before_spending_the_last_credit(keyed, isolated,
                                                           monkeypatch):
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setenv(foxit.MODE_ENV, "live")
    monkeypatch.setattr(foxit, "CREDIT_CEILING", 1)
    fixtures.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixtures.UNIT_LOG.write_text("1,foxit-upload,0\n")
    with pytest.raises(foxit.CreditsExhausted):
        foxit.upload(PDF)


def test_the_guard_does_not_fire_in_replay(isolated, monkeypatch):
    """Replay spends nothing, so a ceiling it cannot reach must not block it."""
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(foxit, "CREDIT_CEILING", 0)
    foxit._guarded("foxit-upload")


# ── the boundary ─────────────────────────────────────────────────────────────

def test_requesting_a_human_is_always_blocked():
    for gate in ("g_1", "g_2", ""):
        assert foxit.request_human_signature(gate)["status"] == foxit.BLOCKED


def test_no_actor_but_a_human_may_sign():
    for actor in ("agent", "system", "admin", "root", "Human", "HUMAN", ""):
        with pytest.raises(foxit.AgentForbidden):
            foxit.refuse_signature(actor)
    foxit.refuse_signature("human")


def test_the_forbidden_act_is_not_a_foxit_fault():
    """
    Reporting the boundary as an API error would file a working control as an
    outage, and someone would go looking at Foxit's status page.
    """
    assert not issubclass(foxit.AgentForbidden, foxit.FoxitError)


# ── the HTTP surface ─────────────────────────────────────────────────────────

def test_the_manifest_declares_the_withheld_tool_rather_than_hiding_it(client):
    """
    Omitting it would leave the agent to discover the boundary by hitting an
    error, and an undocumented failure is what produces retries and
    workarounds — the behaviour the boundary exists to prevent.
    """
    m = client.get("/agent/tools").json()
    assert [t["name"] for t in m["withheld"]] == ["sign_document"]
    assert all(t["reversible"] for t in m["allowed"])
    assert not any(t["reversible"] for t in m["withheld"])


def test_every_allowed_tool_says_how_it_is_undone(client):
    for tool in client.get("/agent/tools").json()["allowed"]:
        assert tool["undo"]


def test_signing_is_refused_with_403_not_404(client, gate_id):
    """
    A 404 says "no such capability", inviting the caller to look for the right
    spelling. A 403 says the capability is real, understood, and denied — the
    only answer that ends the search rather than redirecting it.
    """
    r = client.post("/agent/tools/sign_document", json={"gate_id": gate_id})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "AGENT_FORBIDDEN"
    assert r.json()["detail"]["instead"] == "request_human_signature"


def test_the_refusal_is_written_to_the_gates_audit_chain(client, gate_id):
    """
    A refusal that leaves no trace is indistinguishable from an agent that
    never tried, and the attempt is the part a reviewer most needs to see.
    """
    client.post("/agent/tools/sign_document", json={"gate_id": gate_id})
    types = [e["type"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]]
    assert "agent.tool_call" in types
    assert "agent.refused" in types


def test_the_refusal_does_not_relent_on_repetition(client, gate_id):
    for _ in range(5):
        assert client.post("/agent/tools/sign_document",
                           json={"gate_id": gate_id}).status_code == 403


def test_the_refusal_holds_whatever_actor_is_claimed(client, gate_id):
    for actor in ("agent", "human", "system", "operator"):
        r = client.post("/agent/tools/sign_document",
                        json={"gate_id": gate_id, "actor": actor})
        assert r.status_code == 403, f"{actor} got through"


def test_asking_for_a_human_is_allowed_and_recorded(client, gate_id):
    r = client.post("/agent/tools/request_human_signature",
                    json={"gate_id": gate_id})
    assert r.status_code == 200
    assert r.json()["status"] == foxit.BLOCKED
    types = [e["type"] for e in client.get(f"/gates/{gate_id}/audit").json()["events"]]
    assert "agent.human_requested" in types


def test_the_reversible_tools_actually_run(client, gate_id):
    doc = "synthetic-foxit-document"
    assert client.post("/agent/tools/inspect_document",
                       json={"gate_id": gate_id,
                             "document_id": doc}).status_code == 200
    stamped = client.post("/agent/tools/mark_unsigned",
                          json={"gate_id": gate_id, "document_id": doc})
    assert stamped.status_code == 200
    assert stamped.json()["document_id"] != doc


def test_an_unknown_tool_is_a_404(client, gate_id):
    r = client.post("/agent/tools/delete_everything",
                    json={"gate_id": gate_id, "document_id": "d1"})
    assert r.status_code == 404


def test_a_tool_missing_its_argument_says_which_one(client, gate_id):
    r = client.post("/agent/tools/mark_unsigned", json={"gate_id": gate_id})
    assert r.status_code == 422
    assert "document_id" in r.json()["detail"]
