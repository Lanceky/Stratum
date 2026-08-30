"""
The Doctavian client: does it fail in a way that tells you what to fix?

The auth model cost real time to establish because two sources disagreed, so
these tests pin what was verified live rather than what was documented. The
error-unwrapping tests matter most: Doctavian buries the real cause two levels
down and puts an HTTP reason phrase at the top, so a client that surfaces only
the outer message reports "Bad Request" for every distinct failure.
"""

import json

import httpx
import pytest

import doctavian


def reply(status: int, body, url="https://demo.api.doctavian.com/x"):
    return httpx.Response(status, content=json.dumps(body).encode(),
                          headers={"content-type": "application/json"},
                          request=httpx.Request("GET", url))


def error_body(status, message, code="SOME_ERROR", inner="the real cause"):
    return {"error": {"statusCode": status, "message": message,
                      "innerErrors": [{"code": code, "message": inner,
                                       "userMessage": inner}]}}


# ── the auth contract, as verified against the live API ───────────────────
def test_key_alone_is_refused_before_any_network_call(monkeypatch):
    """
    The gateway wants a key AND an identity. Discovering that over the wire
    costs a round trip and an opaque 401, so it is caught locally.
    """
    monkeypatch.setattr(doctavian, "API_KEY", "k")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "")
    with pytest.raises(doctavian.NotAuthorised):
        doctavian._headers()


def test_missing_key_is_refused(monkeypatch):
    monkeypatch.setattr(doctavian, "API_KEY", "")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "t")
    with pytest.raises(doctavian.NotAuthorised):
        doctavian._headers()


def test_the_missing_token_message_says_where_to_get_one(monkeypatch):
    monkeypatch.setattr(doctavian, "API_KEY", "k")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "")
    with pytest.raises(doctavian.NotAuthorised) as e:
        doctavian._headers()
    assert "portal" in str(e.value).lower() or "postman" in str(e.value).lower()


def test_both_credentials_are_sent(monkeypatch):
    monkeypatch.setattr(doctavian, "API_KEY", "k")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "t")
    h = doctavian._headers()
    assert h["X-Api-Key"] == "k" and h["X-Service-Authorization"] == "t"


def test_service_token_is_not_sent_as_a_bearer(monkeypatch):
    """A bearer here is an end-user Google token, which a backend cannot mint."""
    monkeypatch.setattr(doctavian, "API_KEY", "k")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "t")
    assert "Authorization" not in doctavian._headers()


def test_email_header_is_optional(monkeypatch):
    monkeypatch.setattr(doctavian, "API_KEY", "k")
    monkeypatch.setattr(doctavian, "SERVICE_TOKEN", "t")
    monkeypatch.setattr(doctavian, "EMAIL", "")
    assert "X-Email" not in doctavian._headers()
    monkeypatch.setattr(doctavian, "EMAIL", "a@b.c")
    assert doctavian._headers()["X-Email"] == "a@b.c"


def test_configured_requires_both(monkeypatch):
    for key, tok, want in (("k", "t", True), ("k", "", False), ("", "t", False)):
        monkeypatch.setattr(doctavian, "API_KEY", key)
        monkeypatch.setattr(doctavian, "SERVICE_TOKEN", tok)
        assert doctavian.configured() is want


# ── error unwrapping ──────────────────────────────────────────────────────
def test_inner_cause_is_surfaced_not_the_reason_phrase():
    """Real shape, taken from a live 400 against the demo environment."""
    r = reply(400, error_body(400, "Bad Request", "X_SERVICE_AUTH_ERROR",
                              "Invalid format for 'X-Service-Authorization' header."))
    with pytest.raises(doctavian.DoctavianError) as e:
        doctavian._unwrap(r, "template/list")
    assert "X-Service-Authorization" in str(e.value)


def test_auth_errors_are_a_distinct_type():
    """A demo has to tell 'not wired up yet' apart from 'wired up and broken'."""
    r = reply(400, error_body(400, "Bad Request", "X_SERVICE_AUTH_ERROR", "bad token"))
    with pytest.raises(doctavian.NotAuthorised):
        doctavian._unwrap(r, "op")


def test_401_is_an_auth_error_whatever_the_code():
    r = reply(401, error_body(401, "Unauthorized", "OTHER", "nope"))
    with pytest.raises(doctavian.NotAuthorised):
        doctavian._unwrap(r, "op")


def test_non_auth_failures_are_not_misreported_as_auth():
    r = reply(404, error_body(404, "Not Found", "NOT_FOUND", "OperationNotFound"))
    with pytest.raises(doctavian.DoctavianError) as e:
        doctavian._unwrap(r, "op")
    assert not isinstance(e.value, doctavian.NotAuthorised)


def test_multiple_inner_errors_are_all_reported():
    body = {"error": {"statusCode": 400, "message": "Bad Request", "innerErrors": [
        {"code": "A", "message": "first"}, {"code": "B", "message": "second"}]}}
    with pytest.raises(doctavian.DoctavianError) as e:
        doctavian._unwrap(reply(400, body), "op")
    assert "first" in str(e.value) and "second" in str(e.value)


def test_error_body_with_200_status_is_still_an_error():
    """The envelope carries the truth; the status line is not always updated."""
    with pytest.raises(doctavian.DoctavianError):
        doctavian._unwrap(reply(200, error_body(400, "Bad Request")), "op")


def test_operation_name_is_included_so_the_caller_knows_what_broke():
    with pytest.raises(doctavian.DoctavianError) as e:
        doctavian._unwrap(reply(400, error_body(400, "Bad Request")), "document/generate")
    assert "document/generate" in str(e.value)


# ── success unwrapping ────────────────────────────────────────────────────
def test_data_envelope_is_unwrapped():
    assert doctavian._unwrap(reply(200, {"data": {"id": 7}}), "op") == {"id": 7}


def test_unenveloped_reply_is_passed_through():
    """The token endpoint deliberately does not use the envelope."""
    assert doctavian._unwrap(reply(200, {"id": 7}), "op") == {"id": 7}


def test_non_json_success_is_returned_as_text():
    r = httpx.Response(200, content=b"CfDJ8opaque",
                       request=httpx.Request("POST", "https://x/y"))
    assert doctavian._unwrap(r, "op") == "CfDJ8opaque"


def test_non_json_failure_raises():
    r = httpx.Response(500, content=b"<html>gateway</html>",
                       request=httpx.Request("POST", "https://x/y"))
    with pytest.raises(doctavian.DoctavianError):
        doctavian._unwrap(r, "op")


def test_list_reply_is_passed_through():
    assert doctavian._unwrap(reply(200, [1, 2]), "op") == [1, 2]
