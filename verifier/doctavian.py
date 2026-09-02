"""
Doctavian API client — the attestation certificate (context.md §7.7).

The auth model here was established against the live demo environment, because
two upstream sources disagreed and both were incomplete:

  - `context.md` §7.7 said OAuth 2.0 client credentials.
  - The credential email said "pass it in the x-api-key header".

Neither alone works. The gateway requires **both** an API key and a caller
identity, which the OpenAPI document states precisely but easily misread:

    security: [ {bearerAuth: [], apiKeyHeader: []} ]

Those sit in the *same* object, which in OpenAPI means AND, not OR. Sending
only `X-Api-Key` returns 401 "Authorization header is missing"; sending only a
bearer returns 401 "ApiKeyNotFound". Both verified live.

There are two ways to supply the caller identity, and only one suits a service:

  Authorization: Bearer <jwt>     an end-user token. The public token proxy at
                                  /public/v1/auth/{provider}/token is a real
                                  OAuth2 endpoint, but it accepts only
                                  `authorization_code` and `refresh_token` —
                                  `client_credentials` is explicitly rejected.
                                  It therefore needs an interactive browser
                                  sign-in and cannot be automated. Verified by
                                  trying all five grant types.

  X-Service-Authorization: <tok>  a service identity, carrying the claims
                                  amp_subscription / full_name / email. This is
                                  the one a backend wants. Supplying it removes
                                  the "Authorization header is missing" error,
                                  so it genuinely substitutes for the bearer.

The service token is an **AES-encrypted JWT** issued by the server — the sample
in the spec is `CfDJ8...`, the ASP.NET Core Data Protection prefix. It is not
something a client can construct, only something a client can be handed. On the
demo environment `/v1/common/service/token` is not routed (404
OperationNotFound), so the token has to come from the portal or the supplied
Postman collection.

Everything below therefore works the moment `DOCTAVIAN_SERVICE_TOKEN` is set,
and degrades to recorded fixtures until then, rather than blocking the build.

One more finding worth stating, because it changes the sealing pipeline:
`document.options.pdfSaveOptions` defaults to `{"ConformanceLevel": "PdfA3a"}`.
Doctavian emits **PDF/A-3a directly at generation**. `context.md` §5.1 assigns
PDF/A conversion to Nutrient; that step is redundant for this document, and
converting an already-conformant file is a way to lose conformance rather than
gain it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

import fixtures

BASE_URL = fixtures.env("DOCTAVIAN_BASE_URL", "https://demo.api.doctavian.com")
API_KEY = os.getenv("DOCTAVIAN_API_KEY", "")
SERVICE_TOKEN = os.getenv("DOCTAVIAN_SERVICE_TOKEN", "")
TEAM = fixtures.env("DOCTAVIAN_TEAM", "Team Stratum")
EMAIL = os.getenv("DOCTAVIAN_EMAIL", "")

GENERATE_PATH = "/v1/documents/document/generate"
TEMPLATE_LIST_PATH = "/v1/documents/template/list"
TIMEOUT_S = 60


class DoctavianError(RuntimeError):
    """A refusal from Doctavian, carrying the part of the reply that explains it."""


class NotAuthorised(DoctavianError):
    """
    Kept distinct from other failures because it is the *expected* state until a
    service token is issued, and a demo has to tell "not wired up yet" apart
    from "wired up and broken".
    """


def configured() -> bool:
    return bool(API_KEY and SERVICE_TOKEN)


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise NotAuthorised("DOCTAVIAN_API_KEY is not set")
    if not SERVICE_TOKEN:
        raise NotAuthorised(
            "DOCTAVIAN_SERVICE_TOKEN is not set. The API key alone is refused "
            "with 'Authorization header is missing' — the gateway wants a key "
            "AND a caller identity. Get the service token from the Postman "
            "collection or demo.portal.doctavian.com."
        )
    h = {
        "X-Api-Key": API_KEY,
        "X-Service-Authorization": SERVICE_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if EMAIL:
        h["X-Email"] = EMAIL
    return h


def _unwrap(reply: httpx.Response, op: str) -> Any:
    """
    Pull the payload out, and turn a refusal into an exception that says why.

    Doctavian nests the real cause two levels down, under
    `error.innerErrors[].message`, and the outer `message` is always the
    useless HTTP reason phrase. Surfacing only the outer one costs an hour.
    """
    try:
        body = reply.json()
    except ValueError:
        if not reply.is_error:
            return reply.text
        raise DoctavianError(f"{op}: HTTP {reply.status_code}, non-JSON reply") from None

    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        inner = err.get("innerErrors") or [{}]
        detail = "; ".join(filter(None, (e.get("message") for e in inner)))
        msg = f"{op}: HTTP {reply.status_code} {err.get('message')} — {detail}"
        code = (inner[0] or {}).get("code", "")
        if reply.status_code in (401, 403) or "AUTH" in code:
            raise NotAuthorised(msg)
        raise DoctavianError(msg)

    if reply.is_error:
        raise DoctavianError(f"{op}: HTTP {reply.status_code}")

    # Most endpoints wrap the payload; the token endpoint deliberately does not.
    return body.get("data", body) if isinstance(body, dict) else body


def _post(path: str, payload: dict, op: str) -> Any:
    reply = httpx.post(BASE_URL + path, headers=_headers(),
                       content=json.dumps(payload), timeout=TIMEOUT_S)
    return _unwrap(reply, op)


def list_templates() -> Any:
    """Cheapest authenticated call there is — used by the smoke check."""
    reply = httpx.get(BASE_URL + TEMPLATE_LIST_PATH, headers=_headers(),
                      timeout=TIMEOUT_S)
    return _unwrap(reply, "template/list")


def generate(payload: dict, *, fixture_key: str | None = None) -> Any:
    """
    Render one document.

    Routed through `fixtures.call` for the same reason every other sponsor call
    is: the build has to keep working when the credential is absent, and a
    recorded reply is the honest stand-in. Unlike Perfect Corp this costs no
    metered units, so there is no budget guard — only the record/replay.
    """
    return fixtures.call(
        "doctavian-generate",
        {"key": fixture_key or payload.get("document", {}).get("name", "certificate"),
         "payload": payload},
        lambda: _post(GENERATE_PATH, payload, "document/generate"),
    )
