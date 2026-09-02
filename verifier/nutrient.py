"""
Nutrient DWS client — the renderer for the attestation (context.md §7.4).

This module exists because of a gap in our own build, not because a sponsor
needed using. `attestation.py` decides what the certificate is allowed to say
and is tested to that standard, but nothing turns its output into a document:
`generate_request()` targets Doctavian's template engine, and Doctavian has
been blocked on an un-issuable service token since day zero. So the evidence
lives in a SQLite hash chain and a React console, and a reviewer who authorises
something cannot walk away holding the record of it.

That matters beyond convenience. The claim this project makes against a
hardware security key is that a key proves possession, binds to no action, and
leaves nothing portable behind. The third is only a real distinction if we
actually produce the portable thing.

Everything below was established against the live API, because the obvious
reading of the docs was wrong in three separate places and each one fails as a
400 that looks like something else:

  1. Keys are scoped per product. The grant issues three, and using the
     extraction key on /build returns a bare `403 Forbidden` — identical to
     what a revoked key returns. Verified by sending all three at
     /analyze_build: processor got through, the other two were refused.

  2. /analyze_build is JSON. /build is multipart. Sending multipart to the
     analyse endpoint returns 415, which reads like a malformed request rather
     than the wrong content type for that particular route.

  3. HTML is not a `file` part. `{"file": "index.html"}` is refused with "is of
     the unsupported mimetype text/html" no matter what Content-Type the part
     declares — the service sniffs the bytes. HTML goes in as `{"html": name}`,
     where `name` refers to an attached part. The tell was an earlier error on
     inline HTML: "`<h1>x</h1>` is invalid, sub-directories are not allowed",
     i.e. the field wanted a filename all along.

Confirmed working end to end on the grant: HTML -> PDF, PDF/A-3b output, and a
CAdES b-lt signature that embeds a real /ByteRange and /ETSI.CAdES. The
evaluation watermark is lifted — a probe PDF contains no evaluation markers.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

import fixtures

BASE_URL = fixtures.env("NUTRIENT_BASE_URL", "https://api.nutrient.io")

# One key per product. `NUTRIENT_API_KEY` is honoured as a fallback so a
# single-key account still works, but the grant issues them separately.
PROCESSOR = "processor"
EXTRACTION = "extraction"
ACCESSIBILITY = "accessibility"

_FALLBACK = "NUTRIENT_API_KEY"
KEY_ENV = {
    PROCESSOR: "NUTRIENT_PROCESSOR_API",
    EXTRACTION: "NUTRIENT_DATA_EXTRACTION_API",
    ACCESSIBILITY: "NUTRIENT_ACCESSIBILITY_API",
}

BUILD_PATH = "/build"
ANALYZE_PATH = "/analyze_build"
SIGN_PATH = "/sign"

MODE_ENV = "NUTRIENT_API_MODE"


def mode() -> str:
    """
    Which record/replay mode document calls run under.

    Separate from `STRATUM_API_MODE` because that flag guards the Perfect Corp
    grant, which is metered and cannot be topped up. Nutrient's is unmetered,
    so replaying it conserves nothing while costing the one thing the seal path
    needs: a document that was actually produced.

    It also cannot be replayed in any useful sense. A certificate embeds its
    issue time and its gate id, so no two renders are the same bytes, and the
    key derived from those bytes never repeats. Replay here does not return a
    stale document — it raises, every time, because the call is always new.

    Defaults to the global flag rather than to `live` all the same. The test
    suite pins `STRATUM_API_MODE=replay` to assert that nothing reaches the
    network, and an integration that quietly exempted itself would make that
    assertion untrue while leaving it green.
    """
    return os.getenv(MODE_ENV, "") or fixtures.MODE
EXTRACT_PATH = "/extraction/extract"

# Generous, because /build is synchronous and does real work: a signature plus
# a PDF/A conversion on one request is not a 10-second operation.
TIMEOUT_S = 120

FilePart = tuple[str, bytes, str]


class NutrientError(RuntimeError):
    """A refusal from Nutrient, carrying the part of the reply that explains it."""


class NotAuthorised(NutrientError):
    """
    Kept distinct because it is the expected state before a key is pasted in,
    and a demo has to tell "not wired up yet" apart from "wired up and broken".
    Same distinction Doctavian draws, for the same reason.
    """


def key_for(product: str) -> str:
    return os.getenv(KEY_ENV[product], "") or os.getenv(_FALLBACK, "")


def configured(product: str = PROCESSOR) -> bool:
    return bool(key_for(product))


def _headers(product: str) -> dict[str, str]:
    key = key_for(product)
    if not key:
        raise NotAuthorised(
            f"{KEY_ENV[product]} is not set. Create a key at "
            f"dashboard.nutrient.io/api/ and put it in .env — it is a plain "
            f"bearer token, no exchange. Keys are scoped per product, so the "
            f"{product} key cannot stand in for another."
        )
    return {"Authorization": f"Bearer {key}"}


def _explain(reply: httpx.Response, op: str, product: str) -> NutrientError:
    """
    Turn a refusal into an exception that says what to change.

    Nutrient reports a bad build as `failingPaths`, each naming the instruction
    that was wrong. Surfacing only the status code turns a one-line fix — a
    part named in `instructions` with no matching file attached — into an
    opaque 400.
    """
    detail = reply.text[:400]
    try:
        body = reply.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        paths = err.get("failingPaths") or body.get("errorDetails", {}).get(
            "failingPaths") or []
        if paths:
            detail = "; ".join(
                f"{p.get('path')}: {p.get('details')}" for p in paths)
        elif err.get("details") or err.get("errorMessage") or err.get("message"):
            detail = str(err.get("details") or err.get("errorMessage")
                         or err.get("message"))

    msg = f"{op}: HTTP {reply.status_code} — {detail}"
    if reply.status_code in (401, 403):
        # A scoped key used on the wrong product returns a bare 403, which is
        # byte-identical to a revoked key. Naming the suspicion here saves the
        # half hour of checking a key that was never the problem.
        return NotAuthorised(
            f"{msg}. This is what a wrong-product key looks like: {op} needs "
            f"{KEY_ENV[product]}, and every Nutrient key is scoped to one "
            f"product."
        )
    return NutrientError(msg)


def _multipart(fields: dict[str, Any], files: dict[str, FilePart] | None):
    """
    The multipart endpoints take one JSON control part plus a named part per
    file. The names in the control JSON are what tie the two together, so they
    have to match exactly — a mismatch is a 400 that reads like an auth error.
    """
    parts: list[tuple[str, Any]] = [
        (name, (None, json.dumps(value), "application/json"))
        for name, value in fields.items()
    ]
    parts.extend((name, spec) for name, spec in (files or {}).items())
    return parts


def _fixture_payload(op: str, control: Any,
                     files: dict[str, FilePart] | None) -> dict:
    """
    A replay key that changes when the *document* would change.

    The file bytes are digested rather than stored, because the key has to
    cover them — a build over different input is a different build, and reusing
    a recording across two inputs would hand back a PDF of a document nobody
    submitted. That is the upload stand-in bug inverted, and worse: a sealed
    attestation of the wrong record is a forgery rather than an outage.

    `op` is in here as well, but note where the real separation comes from:
    `fixtures.fixture_key` already prefixes the key with the operation, so
    build and sign cannot collide over identical bytes even if this field went
    away. It is kept as reinforcement against the prefix scheme changing, not
    as the guarantee — mistaking one for the other is how the actual guard gets
    optimised out later.
    """
    digest = {
        name: hashlib.sha256(spec[1]).hexdigest()[:16]
        for name, spec in sorted((files or {}).items())
    }
    return {"op": op, "control": control, "files": digest}


def analyze(instructions: dict) -> Any:
    """
    Ask what a build would cost, without running it.

    JSON, not multipart — /build and /analyze_build disagree on content type,
    and sending multipart here returns 415.

    Free, so it is not routed through the fixture layer: there is nothing to
    conserve, and a recorded cost estimate would be a stale answer to the one
    question worth asking live. This is also the credential probe, which is the
    stronger reason — a recorded answer would report a key as working after it
    had been revoked.
    """
    reply = httpx.post(BASE_URL + ANALYZE_PATH,
                       headers={**_headers(PROCESSOR),
                                "Content-Type": "application/json"},
                       content=json.dumps(instructions), timeout=TIMEOUT_S)
    if reply.is_error:
        raise _explain(reply, "analyze_build", PROCESSOR)
    return reply.json()


def build(instructions: dict, files: dict[str, FilePart] | None = None, *,
          record: bool = True) -> bytes:
    """
    Run a document workflow and return the finished bytes.

    Recorded through `fixtures.call_binary`, so the build keeps working with no
    credential and no network — the rule every other sponsor call follows.
    Nutrient's grant is unmetered, which makes this the one integration we can
    honestly demonstrate live rather than from a stand-in; the recording exists
    so the suite and an offline reviewer are not hostage to that.
    """
    return fixtures.call_binary(
        "nutrient-build",
        _fixture_payload("build", instructions, files),
        lambda: _post_binary(BUILD_PATH, {"instructions": instructions},
                             files, "build", PROCESSOR),
        ext=".pdf", mode=mode(), record=record,
    )


def sign(pdf: bytes, options: dict | None = None, *,
         record: bool = True) -> bytes:
    """
    Apply a digital signature. Verified to embed a real CAdES b-lt signature —
    /ByteRange, /ETSI.CAdES and an Adobe.PPKLite handler.

    Separate from `build` because signing is the step that must come *last*:
    converting an already-signed file to PDF/A invalidates the signature, so
    the order is render, conform, then seal.
    """
    data = options or {"signatureType": "cades", "cadesLevel": "b-lt"}
    files = {"file": ("document.pdf", pdf, "application/pdf")}
    return fixtures.call_binary(
        "nutrient-sign",
        _fixture_payload("sign", data, files),
        lambda: _post_binary(SIGN_PATH, {"data": data}, files, "sign", PROCESSOR),
        ext=".pdf", mode=mode(), record=record,
    )


def extract(document: bytes, schema: dict, *,
            filename: str = "document.pdf",
            content_type: str = "application/pdf") -> Any:
    """
    Parse a document against a JSON Schema.

    The routing rule this exists to serve lives at the call site, not here:
    Nutrient's own documentation says `confidence` is not a calibrated
    probability, so a field is routed on its grounding label, and an *absent*
    score escalates rather than defaulting to pass. That is the same rule the
    rest of this system runs on — absence is not a verdict.
    """
    files = {"file": (filename, document, content_type)}
    fields = {"schema": schema}
    return fixtures.call(
        "nutrient-extract",
        _fixture_payload("extract", fields, files),
        lambda: _post_json(EXTRACT_PATH, fields, files, "extraction/extract",
                           EXTRACTION),
    )


def _post_binary(path: str, fields: dict, files: dict[str, FilePart] | None,
                 op: str, product: str) -> bytes:
    reply = httpx.post(BASE_URL + path, headers=_headers(product),
                       files=_multipart(fields, files), timeout=TIMEOUT_S)
    if reply.is_error:
        raise _explain(reply, op, product)
    return reply.content


def _post_json(path: str, fields: dict, files: dict[str, FilePart] | None,
               op: str, product: str) -> Any:
    reply = httpx.post(BASE_URL + path, headers=_headers(product),
                       files=_multipart(fields, files), timeout=TIMEOUT_S)
    if reply.is_error:
        raise _explain(reply, op, product)
    return reply.json()


HTML_PART = "index.html"


def html_to_pdf(html: str, *, pdfa: bool = False, record: bool = True) -> bytes:
    """
    The narrow case the attestation needs: one HTML document in, one PDF out.

    The part key is `html`, not `file`. A `file` part carrying HTML is refused
    as "unsupported mimetype text/html" whatever Content-Type it declares,
    because the service sniffs the bytes rather than trusting the header. That
    400 is indistinguishable from a dozen other instruction errors, which is
    exactly why this shape lives in one place instead of at each call site.
    """
    instructions: dict[str, Any] = {"parts": [{"html": HTML_PART}]}
    if pdfa:
        instructions["output"] = {"type": "pdfa", "conformance": "pdfa-3b"}
    return build(instructions,
                 {HTML_PART: (HTML_PART, html.encode(), "text/html")},
                 record=record)
