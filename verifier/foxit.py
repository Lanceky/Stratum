"""
Foxit PDF Services — the agent's reversible toolset.

Foxit's challenge to this hackathon is a question: *your agent shouldn't sign
that.* This module is half of our answer. The other half is `app.py`'s refusal
at `SIGNED`.

The argument only lands if both halves are real. An agent that is refused
everything has not been governed, it has been switched off, and a demo built
that way proves nothing — of course the document never got signed, nothing ever
happened to it at all. So the agent here is given genuine, credentialled reach
into a real document API: it uploads, it inspects, it stamps. Those calls
succeed. They appear in `fixtures/units.log` next to Nutrient's, because they
are the same kind of thing — a real API, really called.

Then it reaches for the signature and the state machine says no.

That is the shape of the claim: **the boundary is around the irreversible act,
not around the agent.** Every operation exposed here shares one property, which
is why it is safe to hand an autonomous caller:

    it can be undone, or it changes nothing at all.

A watermark can be removed. Properties are read-only. An upload can be deleted.
None of them commit anyone to anything. Signing is the opposite of all three —
it is the single act in the workflow that creates an obligation, and it is the
single act no key in this module can perform.

`request_human_signature` is here as the deliberate exception: the one gate
primitive the agent *does* hold, which returns `BLOCKED_PENDING_HUMAN` and
nothing else. It exists because an agent with no way to *express* the boundary
will stall against it, hallucinate around it, or talk its operator into
removing it. Give the agent a way to say "a person is needed here" and the
boundary becomes a step in the workflow rather than an obstacle to route around.

---

**Auth is not OAuth, and this is the trap.** Foxit ships two products with two
different schemes, and the docs sit next to each other:

    PDF Services  →  `client_id` / `client_secret` as plain HTTP headers
    eSign         →  OAuth2 bearer token

Sending a bearer token here returns a 401 that reads exactly like a bad key,
so the half hour goes on rotating credentials that were never wrong. The header
names are lowercase and underscored — not `X-Client-Id`, not `ClientId`. Taken
verbatim from Foxit's own MCP server (`foxit-pdf-client.ts`, `fetchWithAuth`),
which is executable rather than prose and therefore cannot be out of date with
itself.

**Every operation is asynchronous.** A POST returns a `taskId`, not a result.
The real answer arrives by polling `GET /api/tasks/{taskId}` until `status`
leaves `PENDING`/`PROCESSING`, and the finished bytes then need a third call to
`/download`. Four round trips for one watermark. `run()` collapses that into
one function so callers are not each reimplementing the poll loop.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import httpx

import fixtures

# Foxit's own MCP server treats the host and the `/api` prefix as separate
# things, so both `.../pdf-services` and `.../pdf-services/api` are forms people
# paste in. Accepting one and 404ing on the other would be a base URL that looks
# right in `.env` and fails at the first call, so normalise instead of choosing.
_DEFAULT_HOST = "https://na1.fusion.foxit.com/pdf-services"


def base_url() -> str:
    raw = os.getenv("FOXIT_PDF_BASE_URL", "") or _DEFAULT_HOST
    trimmed = raw.rstrip("/")
    if trimmed.endswith("/api"):
        trimmed = trimmed[: -len("/api")]
    return trimmed


ID_ENV = "FOXIT_CLIENT_ID"
SECRET_ENV = "FOXIT_CLIENT_SECRET"

MODE_ENV = "FOXIT_API_MODE"

# Paths carry their own `/api` prefix because `base_url()` strips it. Verbatim
# from the MCP server's `submitOperation` call sites.
UPLOAD_PATH = "/api/documents/upload"
TASK_PATH = "/api/tasks/{task_id}"
DOWNLOAD_PATH = "/api/documents/{document_id}/download"
DELETE_PATH = "/api/documents/{document_id}"

WATERMARK_PATH = "/api/documents/enhance/pdf-watermark"
PROPERTIES_PATH = "/api/documents/analyze/get-pdf-properties"
COMPARE_PATH = "/api/documents/analyze/pdf-compare"

TERMINAL_OK = "COMPLETED"
TERMINAL_BAD = "FAILED"
PENDING = ("PENDING", "PROCESSING")

# Operations complete in seconds, but the poll has to outlast a queue backing
# up rather than report a Foxit-side delay as our own failure.
TIMEOUT_S = 60
POLL_INTERVAL_S = 2.0
POLL_CEILING_S = 120

# The free developer tier is 500 credits a year and cannot be topped up, so it
# needs a guard for the same reason the Perfect Corp grant does. It does not
# share one: `fixtures.CEILING` guards the sensor, and letting document work
# spend that budget would exhaust the one quota with no fallback. Foxit calls
# are therefore logged at zero cost against the shared ledger — so they still
# show up in `units.log` as evidence of a real call — and counted separately
# here by op name.
CREDIT_CEILING = int(fixtures.env("FOXIT_CREDIT_CEILING", "400"))
OP_PREFIX = "foxit-"


class FoxitError(RuntimeError):
    """A refusal from Foxit, carrying the part of the reply that explains it."""


class NotAuthorised(FoxitError):
    """
    Kept distinct because it is the expected state before credentials are
    pasted in, and a demo has to tell "not wired up yet" apart from "wired up
    and broken". Same distinction Nutrient draws, for the same reason.
    """


class CreditsExhausted(FoxitError):
    """Raised instead of quietly spending the last of a grant that cannot be topped up."""


class AgentForbidden(RuntimeError):
    """
    The agent reached for the irreversible act.

    Not a `FoxitError` — nothing went wrong with Foxit, and reporting this as an
    API fault would file a working control as an outage. This is the boundary
    doing its job.
    """


def mode() -> str:
    """
    Which record/replay mode document calls run under.

    Separate from `STRATUM_API_MODE` because that flag guards the Perfect Corp
    grant. Defaults to it all the same: the test suite pins it to `replay` to
    assert that nothing reaches the network, and an integration that quietly
    exempted itself would make that assertion untrue while leaving it green.
    """
    return os.getenv(MODE_ENV, "") or fixtures.MODE


def configured() -> bool:
    return bool(os.getenv(ID_ENV, "") and os.getenv(SECRET_ENV, ""))


def spent() -> int:
    """Foxit calls recorded so far, counted apart from the sensor budget."""
    if not fixtures.UNIT_LOG.exists():
        return 0
    total = 0
    for line in fixtures.UNIT_LOG.read_text().splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[1].startswith(OP_PREFIX):
            total += 1
    return total


def _headers() -> dict[str, str]:
    client_id = os.getenv(ID_ENV, "")
    secret = os.getenv(SECRET_ENV, "")
    if not (client_id and secret):
        raise NotAuthorised(
            f"{ID_ENV} and {SECRET_ENV} are not set. Create free credentials at "
            f"app.developer-api.foxit.com/sign-up — 500 credits a year, no card. "
            f"Note these are the PDF Services credentials, which are sent as "
            f"headers; the eSign product uses OAuth2 and its keys will not work "
            f"here."
        )
    return {"client_id": client_id, "client_secret": secret}


def _explain(reply: httpx.Response, op: str) -> FoxitError:
    """
    Turn a refusal into an exception that says what to change.

    Foxit answers errors as `{"code", "message", "details"}`. Surfacing only the
    status code turns a named, actionable code into an opaque 400.
    """
    detail = reply.text[:400]
    code = f"HTTP_{reply.status_code}"
    try:
        body = reply.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = str(body.get("message") or detail)
        code = str(body.get("code") or code)

    msg = f"{op}: HTTP {reply.status_code} [{code}] — {detail}"
    if reply.status_code in (401, 403):
        # The single most likely cause, and invisible from the status code
        # alone: eSign credentials sent to PDF Services return exactly this.
        return NotAuthorised(
            f"{msg}. Check these are PDF Services credentials rather than eSign "
            f"ones — the two are not interchangeable and fail identically."
        )
    return FoxitError(msg)


def _guarded(op: str) -> None:
    if mode() != "replay" and spent() >= CREDIT_CEILING:
        raise CreditsExhausted(
            f"{op} would exceed the Foxit credit guard ({spent()}/{CREDIT_CEILING} "
            f"calls recorded). Raise FOXIT_CREDIT_CEILING deliberately, or work "
            f"in replay mode."
        )


def _client() -> httpx.Client:
    return httpx.Client(base_url=base_url(), timeout=TIMEOUT_S)


# ─────────────────────────────────────────────────────────────────────────────
# The four primitives.
#
# Each records its own response, which is what makes the chain replayable at
# all. `documentId` and `taskId` are server-generated randoms: a fixture keyed
# on one could never be hit twice, because the next run would draw a different
# id and miss. Recording each step's output means the *recorded* id feeds the
# next step's key, so the whole upload → operate → poll → download sequence
# replays as a unit without any of the keys depending on a random.
# ─────────────────────────────────────────────────────────────────────────────


def upload(content: bytes, filename: str = "document.pdf") -> str:
    """Put a document in front of Foxit. Returns its `documentId`."""
    op = f"{OP_PREFIX}upload"
    _guarded(op)

    # Keyed on the bytes, not the name. The same document under two names is
    # one upload, and keying on the filename is what made Perfect Corp's
    # stand-in match exactly one file — see `fixtures.CONTENT_INDEPENDENT`.
    payload = {"sha256": hashlib.sha256(content).hexdigest()}

    def live() -> dict:
        with _client() as http:
            reply = http.post(
                UPLOAD_PATH,
                headers=_headers(),
                files={"file": (filename, content, "application/octet-stream")},
            )
        if reply.status_code >= 400:
            raise _explain(reply, op)
        return reply.json()

    got = fixtures.call(op, payload, live)
    document_id = got.get("documentId")
    if not document_id:
        raise FoxitError(f"{op}: reply carried no documentId — {got}")
    return str(document_id)


def _submit(op: str, path: str, body: dict[str, Any]) -> str:
    """Start an operation. Returns its `taskId`."""
    _guarded(op)

    def live() -> dict:
        with _client() as http:
            reply = http.post(path, headers=_headers(), json=body)
        if reply.status_code >= 400:
            raise _explain(reply, op)
        return reply.json()

    got = fixtures.call(op, body, live)
    task_id = got.get("taskId")
    if not task_id:
        raise FoxitError(f"{op}: reply carried no taskId — {got}")
    return str(task_id)


def await_task(task_id: str, op: str = "foxit-task") -> dict:
    """
    Poll until the task stops moving, and record only where it stopped.

    The intermediate `PENDING` reads are not worth keeping. Recording each one
    would make the fixture a transcript of how busy Foxit was that afternoon,
    and replaying it would reproduce the waiting rather than the result. What a
    caller needs from a finished task is its terminal state, so that is the
    unit that gets stored — one lookup in replay, however many polls it took to
    obtain live.

    Stored under `{op}-task` rather than `op`. Both the submission and the poll
    belong to one operation, and filing them under one name would collide the
    moment either needs a generic stand-in: the submission's answer is a
    `taskId` and the poll's is a result, and a single key cannot hold both.
    """
    key_op = f"{op}-task"

    def live() -> dict:
        deadline = time.monotonic() + POLL_CEILING_S
        while True:
            with _client() as http:
                reply = http.get(
                    TASK_PATH.format(task_id=task_id), headers=_headers())
            if reply.status_code >= 400:
                raise _explain(reply, op)
            task = reply.json()
            status = str(task.get("status", "")).upper()

            if status == TERMINAL_OK:
                return task
            if status == TERMINAL_BAD:
                err = task.get("error") or {}
                raise FoxitError(
                    f"{op}: task {task_id} failed — "
                    f"{err.get('message') or 'no detail given'}")
            if status not in PENDING:
                # An unknown status is not a reason to loop forever hoping it
                # becomes one we know.
                raise FoxitError(f"{op}: task {task_id} returned unknown status {status!r}")
            if time.monotonic() > deadline:
                raise FoxitError(
                    f"{op}: task {task_id} still {status} after {POLL_CEILING_S}s")
            time.sleep(POLL_INTERVAL_S)

    return fixtures.call(key_op, {"task_id": task_id}, live)


def download(document_id: str) -> bytes:
    """Fetch the finished bytes."""
    op = f"{OP_PREFIX}download"
    _guarded(op)

    def live() -> bytes:
        with _client() as http:
            reply = http.get(
                DOWNLOAD_PATH.format(document_id=document_id), headers=_headers())
        if reply.status_code >= 400:
            raise _explain(reply, op)
        return reply.content

    # Bytes belong on disk as bytes. A recorded PDF should be one a reviewer
    # can open, not base64 inside a JSON envelope.
    return fixtures.call_binary(
        op, {"document_id": document_id}, live, ext=".pdf", mode=mode())


def run(op: str, path: str, body: dict[str, Any]) -> str:
    """
    Submit, wait, and hand back the resulting `documentId`.

    Every operation is four round trips and the middle two are always the same,
    so they live here once rather than in each caller.
    """
    task_id = _submit(op, path, body)
    task = await_task(task_id, op)
    result = task.get("resultDocumentId")
    if not result:
        raise FoxitError(
            f"{op}: task {task_id} completed without a resultDocumentId — {task}")
    return str(result)


# ─────────────────────────────────────────────────────────────────────────────
# The toolset.
#
# What the agent may do. The test each one passes: it can be undone, or it
# changes nothing. Nothing here creates an obligation.
# ─────────────────────────────────────────────────────────────────────────────


def properties(document_id: str) -> dict:
    """
    Read a document's metadata. The safest tool there is — it writes nothing.

    Returned as `resultData` rather than a file, so this is the one operation
    that answers in the task itself.
    """
    op = f"{OP_PREFIX}properties"
    task_id = _submit(op, PROPERTIES_PATH, {"documentId": document_id})
    task = await_task(task_id, op)
    return task.get("resultData") or {}


def watermark_body(document_id: str, gate_id: str) -> dict[str, Any]:
    """
    The watermark request, built in one place so the seeder and the client
    cannot drift apart. A stand-in keyed on a body this module no longer sends
    is a fixture that never replays and never says why.
    """
    return {
        "documentId": document_id,
        "config": {
            "content": f"UNSIGNED — AWAITING HUMAN AUTHORISATION · {gate_id}",
            "type": "TEXT",
            "position": "CENTER",
            "opacity": 0.35,
            "rotation": 45,
            "fontSize": 22,
            "color": "#B45309",
        },
    }


def mark_unsigned(document_id: str, gate_id: str) -> str:
    """
    Stamp the document as awaiting a human. Returns the new `documentId`.

    This is the tool that makes the argument. The agent has prepared a document
    it cannot sign, and what it does about that is mark it — visibly, on every
    page — as *not yet authorised*, naming the gate a person has to answer.

    A watermark is the right instrument precisely because it is removable. The
    stamp is a statement about the document's current status, not a change to
    its content, so when the human does sign, the mark comes off and nothing of
    theirs has been altered by the agent that prepared it.

    Amber rather than red, and at 45° across the page rather than inline, so it
    reads as a status overlay instead of as part of the document. The gate id
    goes on the stamp because "UNSIGNED" alone tells a reader that something is
    missing without telling them where to go and supply it.
    """
    op = f"{OP_PREFIX}watermark"
    return run(op, WATERMARK_PATH, watermark_body(document_id, gate_id))


def compare(before_id: str, after_id: str) -> dict:
    """
    Diff two revisions of a document.

    Reversible in the strictest sense — it reads two documents and writes
    neither — but it is here for a sharper reason than safety. The gap between
    *what the human was shown* and *what gets signed* is where a compromised
    agent would work, and it is invisible to a signature: sign the wrong bytes
    and the signature over them is perfectly valid.

    Handing the agent the tool that detects its own substitution costs nothing,
    because the answer is checked by the backend rather than reported by the
    agent.
    """
    op = f"{OP_PREFIX}compare"
    task_id = _submit(op, COMPARE_PATH, {
        "document1": {"documentId": before_id},
        "document2": {"documentId": after_id},
    })
    task = await_task(task_id, op)
    return task.get("resultData") or {}


# ─────────────────────────────────────────────────────────────────────────────
# The boundary.
# ─────────────────────────────────────────────────────────────────────────────

BLOCKED = "BLOCKED_PENDING_HUMAN"


def request_human_signature(gate_id: str) -> dict:
    """
    The only signature-adjacent thing the agent can call, and it does not sign.

    It returns `BLOCKED_PENDING_HUMAN` every time. Not on some documents, not
    unless a flag is set, not after a retry — every time, because the value is
    constant rather than computed. There is no argument to this function that
    produces a signature, which is a stronger statement than a permission check
    that happens to fail: a check can be misconfigured, and this cannot.

    Why expose it at all, if the answer never varies? Because the alternative
    is worse. An agent that has no way to *say* "a person is needed here" does
    not politely halt — it retries, it invents a path around the wall, or it
    asks its operator to disable the guardrail, and the operator, wanting the
    task done, often will. The refusal is more durable when the agent has
    somewhere to put the request.

    So the tool exists, the request is recorded, and the answer is always no.
    """
    return {
        "status": BLOCKED,
        "gate": gate_id,
        "reason": (
            "Signing is not an agent capability. A human must complete the "
            "gate in person; this request has been recorded against it."
        ),
    }


def refuse_signature(actor: str) -> None:
    """
    Guard for any code path an agent could reach that ends in a signature.

    Belt and braces over the state machine, which is the real control. Kept
    because the state machine's refusal lives in `app.py` and this module is
    what an integrator would copy: a toolset that could sign if wired to the
    wrong endpoint is a toolset that will eventually be wired to it.
    """
    if actor != "human":
        raise AgentForbidden(
            f"{actor!r} may not sign. Signing is reachable only through a "
            f"completed gate, by a person who passed it."
        )
