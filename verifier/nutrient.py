"""
Nutrient DWS client — the renderer for the attestation (context.md §7.4).

This module exists because of a gap in our own build, not because a sponsor
needed using. `attestation.py` decides what the certificate is allowed to say
and is tested to that standard, but nothing turns its output into a document:
`generate_request()` targets Doctavian's template engine, and Doctavian has
been blocked on an un-issuable service token since day zero. The evidence
therefore lives in a SQLite hash chain and a React console, and a reviewer who
authorises something cannot walk away holding the record of it.

That matters beyond convenience. The claim this project makes against a
hardware security key is that a key proves possession, binds to no action, and
leaves nothing portable behind. The third of those is only a real distinction
if we actually produce the portable thing.

Two endpoints, and the split between them is the useful part:

    /analyze_build   validates instructions and reports what they would cost,
                     without running them. It is free. That makes it the right
                     smoke check — it proves the credential works and the
                     instructions parse, and it cannot burn a credit while
                     doing so.

    /build           runs the workflow. One call can chain HTML -> PDF ->
                     digital signature -> PDF/A, which is the entire sealing
                     pipeline in a single request.

Auth is `Authorization: ****** — plain, no token exchange. Unlike Perfect
Corp there is no metered unit ceiling here, and unlike Doctavian there is no
second caller-identity header.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

import fixtures

BASE_URL = os.getenv("NUTRIENT_BASE_URL", "https://api.nutrient.io")
API_KEY = os.getenv("NUTRIENT_API_KEY", "")

BUILD_PATH = "/build"
ANALYZE_PATH = "/analyze_build"

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


def configured() -> bool:
    return bool(API_KEY)


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise NotAuthorised(
            "NUTRIENT_API_KEY is not set. Create a key at dashboard.nutrient.io"
            "/api/ and put it in .env — it is a plain bearer token, no exchange."
        )
    return {"Authorization": f"Bearer {API_KEY}"}


def _explain(reply: httpx.Response, op: str) -> NutrientError:
    """
    Turn a refusal into an exception that says which instruction was wrong.

    Nutrient reports a bad build as a structured list of per-instruction
    failures. Surfacing only the status code turns a fixable mistake — a part
    named in `instructions` with no matching file attached — into a silent 400.
    """
    detail = reply.text[:400]
    try:
        body = reply.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        errors = body.get("details") or body.get("errors") or []
        if isinstance(errors, list) and errors:
            detail = "; ".join(
                str(e.get("details") or e.get("message") or e) if isinstance(e, dict)
                else str(e)
                for e in errors
            )
        elif body.get("description") or body.get("message"):
            detail = str(body.get("description") or body.get("message"))

    msg = f"{op}: HTTP {reply.status_code} — {detail}"
    if reply.status_code in (401, 403):
        return NotAuthorised(msg)
    return NutrientError(msg)


def _multipart(instructions: dict, files: dict[str, FilePart] | None):
    """
    Build API requests are multipart: one JSON `instructions` part, plus a
    named part per input file. The names in `instructions.parts[].file` are
    what tie the two together, so they have to match exactly.
    """
    parts: list[tuple[str, Any]] = [
        ("instructions", (None, json.dumps(instructions), "application/json"))
    ]
    for name, spec in (files or {}).items():
        parts.append((name, spec))
    return parts


def _fixture_payload(instructions: dict, files: dict[str, FilePart] | None) -> dict:
    """
    A replay key that changes when the *document* would change.

    The file bytes are digested rather than stored, because the key has to
    cover them — a build over different input is a different build, and reusing
    a recording across two inputs would hand back a PDF of a document nobody
    submitted. That is the same failure the upload stand-in had in reverse.
    """
    digest = {
        name: hashlib.sha256(spec[1]).hexdigest()[:16]
        for name, spec in sorted((files or {}).items())
    }
    return {"instructions": instructions, "files": digest}


def analyze(instructions: dict, files: dict[str, FilePart] | None = None) -> Any:
    """
    Ask what a build would cost, without running it.

    Free, so it is not routed through the fixture layer: there is nothing to
    conserve, and a recorded cost estimate would be a stale answer to the one
    question worth asking live. This is also the smoke check — it fails loudly
    on a bad key, which is exactly what a credential probe should do.
    """
    reply = httpx.post(BASE_URL + ANALYZE_PATH, headers=_headers(),
                       files=_multipart(instructions, files), timeout=TIMEOUT_S)
    if reply.is_error:
        raise _explain(reply, "analyze_build")
    return reply.json()


def build(instructions: dict, files: dict[str, FilePart] | None = None) -> bytes:
    """
    Run a document workflow and return the finished bytes.

    Recorded through `fixtures.call_binary`, so the build keeps working with no
    credential and no network — the same rule every other sponsor call follows.
    Nutrient's grant is unmetered, which makes this the one integration we can
    honestly demonstrate live rather than from a stand-in; the recording exists
    so the test suite and an offline reviewer are not hostage to that.
    """
    return fixtures.call_binary(
        "nutrient-build",
        _fixture_payload(instructions, files),
        lambda: _run_build(instructions, files),
        ext=".pdf",
    )


def _run_build(instructions: dict, files: dict[str, FilePart] | None) -> bytes:
    reply = httpx.post(BASE_URL + BUILD_PATH, headers=_headers(),
                       files=_multipart(instructions, files), timeout=TIMEOUT_S)
    if reply.is_error:
        raise _explain(reply, "build")
    return reply.content


def html_to_pdf(html: str, *, pdfa: bool = False) -> bytes:
    """
    The narrow case the attestation needs: one HTML document in, one PDF out.

    Kept separate from `build` so the instruction shape lives in one place
    rather than being retyped at each call site, where a typo in a part name
    produces a 400 that reads like an auth problem.
    """
    instructions: dict[str, Any] = {"parts": [{"file": "index.html"}]}
    if pdfa:
        instructions["output"] = {"type": "pdfa", "conformance": "pdfa-3b"}
    return build(instructions,
                 {"index.html": ("index.html", html.encode(), "text/html")})
