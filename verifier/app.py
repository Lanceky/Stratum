"""
STRATUM verifier sidecar.

Stateless. Xano is the system of record and owns all state; this service holds
nothing between requests. Called by Xano via External API Request.

Why a Python sidecar rather than a Xano Lambda: Lambdas run JavaScript+NPM,
which is workable but slow to write for mask decoding, landmark normalisation
and point-set registration. numpy/opencv/scipy gets us there in hours instead
of days. See context.md §6.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

import attestation
import certificate
import claim as claim_mod
import nutrient
from dimensions import STABLE, VOLATILE
from fixtures import FixtureMissing, budget_status
from gate import Actor, GateMode, GateState, IllegalTransition
from store import Store

app = FastAPI(title="STRATUM verifier", version="0.1.0")

VERDICTS = {str(GateState.PASS), str(GateState.REVIEW), str(GateState.FAIL)}
CHECK_NUMBERS = {"presence": 1, "authenticity": 2, "binding": 3,
                 "uniqueness": 4}

# Local store. Xano replaces this as system of record once the instance exists;
# the transition rules are identical either way, which is the point of keeping
# them in gate.py rather than in a Xano function stack alone.
store = Store(os.getenv("STRATUM_DB", ":memory:"))

# Set on first use by /demo/gate. Not persisted: if the process restarts the
# next demo gate simply gets a fresh workflow, which costs nothing.
_demo_workflow: str | None = None


class HealthResponse(BaseModel):
    status: str
    api_mode: str
    units: dict
    gates: dict


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    census = store.census()
    return HealthResponse(
        status="ok",
        api_mode=os.getenv("STRATUM_API_MODE", "replay"),
        units=budget_status(),
        # Counted by state rather than totalled, so a caller can see the shape
        # of the queue and not just its size. `total` is derived here so the
        # landing page cannot disagree with the audit trail about it.
        gates={"total": sum(census.values()), "by_state": census,
               "awaiting_review": census.get(str(GateState.REVIEW), 0)},
    )


@app.get("/dimensions")
def dimensions() -> dict:
    """Exposed deliberately: the stable/volatile split is a load-bearing claim."""
    return {
        "stable_used_for_identity": STABLE,
        "volatile_excluded_from_identity": VOLATILE,
        "rationale": "Volatile dimensions fluctuate hourly; intra-person variance "
        "exceeds inter-person variance, so they carry no identity signal. "
        "They are used only by check 1 (illumination response).",
    }


# ── gates (Step 3) ────────────────────────────────────────────────────────
class GateCreate(BaseModel):
    workflow_id: str
    mode: GateMode = GateMode.AUTHORISE_ACTION
    challenge_spec: dict | None = None
    ttl_s: int = Field(300, ge=10, le=3600)


class TransitionRequest(BaseModel):
    to: GateState
    actor: Actor
    detail: dict | None = None


@app.post("/gates", status_code=201)
def create_gate(body: GateCreate) -> dict:
    return store.create_gate(body.workflow_id, body.mode,
                             body.challenge_spec, body.ttl_s)


@app.post("/demo/gate", status_code=201)
def demo_gate(ttl_s: int = 600) -> dict:
    """
    A gate anyone can open, for the demo at /gate/demo.

    Tenants and workflows are created out of band in a real deployment — an
    agent is issued credentials, and it requests a gate. Nothing in the HTTP
    surface creates them, so without this a freshly cloned repo cannot reach
    the capture screen at all.

    Kept as its own route rather than a special case inside `create_gate`, so
    the demo affordance is visible in the route list and cannot be reached by
    accident from the production path.

    Only the fields a browser needs are returned. `/gates` hands back the whole
    row including the nonce, which is defensible there because the caller is
    the credentialed agent that owns the gate — but this route is open to
    anyone, and handing the challenge secret to the client would undo the
    reason `/challenge` accepts a gate_id in the first place.
    """
    global _demo_workflow
    if _demo_workflow is None:
        tenant = store.create_tenant("demo.stratum.local", "demo-no-auth")
        _demo_workflow = store.create_workflow(
            tenant["id"], "wire_transfer_approval",
            {"note": "demo workflow — not a real authorisation"})["id"]
    gate = store.create_gate(_demo_workflow, GateMode.AUTHORISE_ACTION,
                             None, ttl_s)
    return {k: gate[k] for k in ("id", "mode", "state", "expires_at", "created_at")}


class TamperRequest(BaseModel):
    index: int = 0
    payload: dict | None = None


@app.post("/demo/gates/{gate_id}/tamper")
def demo_tamper(gate_id: str, body: TamperRequest) -> dict:
    """
    Rewrite one past event, so the demo can show verify_chain catching it.

    Off unless STRATUM_DEMO_TAMPER is set. It corrupts real data on purpose,
    and an endpoint that does that should have to be asked for by name rather
    than be present in every deployment waiting to be found.

    Worth being precise about what this proves. It is not that the API refuses
    to tamper — the API has no such route, which is the ordinary case and
    proves nothing interesting. It is that an attacker who gets *underneath*
    the API, with rights to drop the trigger and edit the table directly,
    still cannot make the edit look like it was always there.
    """
    if not os.getenv("STRATUM_DEMO_TAMPER"):
        raise HTTPException(
            404, "demo tampering is off; start the server with "
                 "STRATUM_DEMO_TAMPER=1 to enable it")

    if store.get("gates", gate_id) is None:
        raise HTTPException(404, "no such gate")

    payload = body.payload
    if payload is None:
        payload = {"note": "nothing to see here"}

    try:
        change = store.rewrite_past_event(gate_id, body.index, payload)
    except IndexError as e:
        raise HTTPException(400, str(e))

    return {"tampered": change, "chain": store.verify_chain(gate_id).as_dict()}


@app.get("/gates/{gate_id}")
def read_gate(gate_id: str) -> dict:
    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")
    return gate


@app.post("/gates/{gate_id}/transition")
def transition(gate_id: str, body: TransitionRequest) -> dict:
    """
    The only route that changes gate state.

    An illegal move returns 409 and is written to the audit ledger, so a
    refusal is evidence rather than a silent no-op.
    """
    try:
        return store.gate_transition(gate_id, body.to, body.actor, body.detail)
    except KeyError:
        raise HTTPException(404, "no such gate") from None
    except IllegalTransition as exc:
        raise HTTPException(409, {
            "error": "illegal_transition",
            "from": str(exc.frm), "to": str(exc.to),
            "actor": str(exc.actor), "reason": exc.reason,
        }) from None


# ── capture (Step 2c) ─────────────────────────────────────────────────────
# The route the camera talks to. Everything above it is state; this is the one
# place a photograph enters the service, and it leaves again as a score.


def _sensor_absent(reason: str, n_frames: int) -> dict:
    """
    Check 1's result when the skin sensor could not be reached.

    Shaped like a real presence result so fusion needs no special case, but with
    `ran=false`, which fusion already knows means REVIEW rather than FAIL. The
    reason is carried verbatim: a reviewer who sees "check 1 could not run"
    with no cause cannot tell a missing credential from an attack.
    """
    return {
        "ran": False, "passed": False, "score": 0.0, "verdict": str(GateState.REVIEW),
        "reason": f"the skin sensor could not be reached ({reason})",
        "signals": [], "failed": [], "limitations": [
            "no frame was scored; this gate carries no presence evidence at all",
        ],
        "frames_captured": n_frames,
    }


@app.post("/gates/{gate_id}/capture")
async def capture(gate_id: str,
                  frames: list[UploadFile] = File(...),
                  captured_at: list[float] = Form(...)) -> dict:
    """
    A completed challenge sequence: images in, authorisation decision out.

    The whole gate lifecycle runs here because the steps are not independently
    meaningful — a capture that is scored but never fused leaves a gate parked
    in SCORED with nobody responsible for moving it. One request, one outcome.

    The challenge is re-derived from the gate's own nonce, which the client
    never sends. Nothing in this request can influence what the capture was
    supposed to demonstrate; that is the property the whole check rests on.

    `captured_at` is milliseconds since the Unix epoch, because that is what
    `Date.now()` returns and a browser converting units is a browser that can
    get them wrong. The conversion to the seconds `checks/presence.py` works in
    happens once, below.

    Images are read into memory, scored, and dropped. Only `derived` reaches the
    database, and the `captures` table has no column that could hold anything
    else. See capture.py.
    """
    import challenge as ch
    from checks.authenticity import evaluate as authenticity_eval
    from checks.presence import evaluate as presence_eval
    from capture import SensorUnavailable, analyse, frame_record

    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")

    # An expired gate must not be scored. Doing the work first and refusing the
    # transition afterwards would spend Perfect Corp units on a foregone answer.
    gate = store.expire_if_due(gate_id)
    if gate["state"] in (str(GateState.FAIL), str(GateState.SEALED)):
        raise HTTPException(409, f"gate is {gate['state']} and cannot be captured")

    try:
        spec = ch.derive(gate["nonce"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if len(frames) != len(spec.frames):
        raise HTTPException(
            422, f"challenge asked for {len(spec.frames)} frames, got {len(frames)}")
    if len(captured_at) != len(frames):
        raise HTTPException(
            422, f"got {len(frames)} frames but {len(captured_at)} timestamps")

    stamps = [ms / 1000.0 for ms in captured_at]

    # The nonce is minted when the gate is created, so that is the moment the
    # challenge became answerable. Using the first frame's own timestamp here
    # would make presence's "a frame predates the challenge" test structurally
    # incapable of firing — a check that cannot fail is not a check.
    issued_at = datetime.fromisoformat(gate["created_at"]).timestamp()

    # Every frame is read and checked before any is scored. Interleaving the
    # two would spend Perfect Corp units on frames 0..n-1 of a request that is
    # about to be refused because frame n is empty. Four JPEGs in memory is a
    # trade worth making against that.
    images: list[bytes] = []
    for i, upload in enumerate(frames):
        data = await upload.read()
        if not data:
            raise HTTPException(422, f"frame {i} is empty")
        images.append(data)

    # A human physically stood in front of a camera. Recorded before scoring,
    # because it happened whether or not the sensor was reachable, and a gate
    # that fails to score still needs the capture in its history.
    try:
        store.gate_transition(gate_id, GateState.CAPTURED, Actor.HUMAN,
                              {"frames": len(frames)})
    except IllegalTransition as exc:
        raise HTTPException(409, {
            "error": "illegal_transition", "from": str(exc.frm),
            "to": str(exc.to), "reason": exc.reason,
        }) from None

    records: list[dict] = []
    unavailable: str | None = None
    with tempfile.TemporaryDirectory(prefix="stratum-capture-") as tmp:
        for i, data in enumerate(images):
            try:
                derived = analyse(data, f"frame_{i}.jpg",
                                  mask_dir=Path(tmp) / f"frame_{i}")
            except SensorUnavailable as exc:
                unavailable = str(exc)
                break
            store.add_capture(gate_id, i, derived, derived.get("pc_task_id"))
            records.append(frame_record(derived, i, stamps[i]))
    images.clear()

    if unavailable is None:
        presence = presence_eval(records, spec, issued_at=issued_at).as_dict()
        authenticity = authenticity_eval(
            records[0]["scores"], authenticity_baseline()).as_dict()
        # Marked at the point the provenance is known. The checks do not get
        # told where their numbers came from — they measure what they are
        # handed — so the flag is attached here, by the code that knows the
        # sensor was a stand-in, rather than inferred later by a reader who
        # would have to guess.
        if any(r.get("synthetic") for r in records):
            presence["synthetic"] = True
            authenticity["synthetic"] = True
    else:
        presence = _sensor_absent(unavailable, len(frames))
        authenticity = None

    store.gate_transition(gate_id, GateState.SCORED, Actor.SYSTEM,
                          {"frames_scored": len(records)})

    # Binding is deliberately absent: there is no enrolment on file for a
    # walk-up gate, and fusion reports an unattempted check as REVIEW rather
    # than letting the gate pass on the two checks that did run.
    return decide(FusionRequest(presence=presence, authenticity=authenticity,
                                gate_id=gate_id))


class ReviewDecision(BaseModel):
    """
    A named person's ruling on a gate the machine could not settle.

    `reviewer_id` is required and has no default. An anonymous review is not a
    review — the entire purpose of the band is that a person put their name to
    a decision the evidence did not make for them.
    """

    reviewer_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    notes: str = ""


@app.get("/gates")
def list_gates(state: GateState = GateState.REVIEW, limit: int = 50) -> dict:
    """
    The review queue: gates waiting on a human, oldest first.

    Each entry carries the evidence summary a reviewer needs to triage without
    opening it. Raw captures are deliberately absent — see `/gates/{id}/review`.
    """
    gates = store.gates_in_state(state, limit)
    out = []
    for gate in gates:
        evidence = store.evidence_for(gate["id"])
        reasons = fusion_reasons(gate["id"])
        out.append({
            "gate_id": gate["id"],
            "workflow_id": gate["workflow_id"],
            "mode": gate["mode"],
            "state": gate["state"],
            "created_at": gate["created_at"],
            "expires_at": gate["expires_at"],
            "expired": store.is_expired(gate),
            "checks": [_check_summary(row) for row in evidence],
            "reasons": reasons,
            "triggering_signal": _triggering_signal(evidence, reasons),
            # A count, not the events: enough to make the queue entry stand out
            # and force the reviewer to open it, without putting the detail
            # somewhere it can be skimmed past.
            "escalations": len(store.escalations(gate["id"])),
        })
    return {"state": str(state), "count": len(out), "gates": out}


def _detail(row: dict) -> dict:
    raw = row.get("detail")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw or {}


def _check_summary(row: dict) -> dict:
    names = {v: k for k, v in CHECK_NUMBERS.items()}
    d = _detail(row)
    no = int(row["check_no"])
    return {"check_no": no, "name": names.get(no, f"check {no}"),
            "score": float(row["score"]), "ran": bool(d.get("ran", True)),
            "passed": bool(d.get("passed", False)),
            "verdict": d.get("verdict"), "reason": d.get("reason", ""),
            "limitations": d.get("limitations", []),
            "synthetic": bool(d.get("synthetic"))}


def fusion_reasons(gate_id: str) -> list[str]:
    """
    The reasons fusion gave when it referred this gate, read back out of the
    audit chain.

    Not recomputed. Recomputing would answer "what would we decide now?" when
    the reviewer needs "what was decided, and on what basis?" — and if the two
    ever differed, the recomputed answer would quietly hide the discrepancy.
    """
    for event in reversed(store.chain(gate_id)):
        if event["type"] != "transition":
            continue
        payload = _detail({"detail": event.get("payload")})
        if payload.get("to") not in VERDICTS:
            continue
        reasons = payload.get("reasons")
        if reasons:
            return [str(r) for r in reasons]
        # A human rejecting a gate also transitions into a verdict state
        # (REVIEW -> FAIL) but carries no reasons. Stopping there would hide
        # why the gate was referred in the first place, so keep looking back
        # for the transition fusion actually wrote.
    return []


def _triggering_signal(evidence: list[dict], reasons: list[str] | None = None) -> str:
    """
    Why this gate needs a person — one line, at the top of the queue.

    A reviewer facing a queue needs to know what to look at before opening
    anything. Fusion's own words come first because they already rank the
    findings; the evidence rows are a fallback for gates referred by some
    other route. Ordered by severity: a check that could not run is a
    different problem from one that ran and could not decide.
    """
    if reasons:
        return reasons[0]
    for row in evidence:
        d = _detail(row)
        if not d.get("ran", True):
            return (f"check {row['check_no']} could not run — no evidence, "
                    f"which is not the same as a negative result")
    for row in evidence:
        d = _detail(row)
        if d.get("verdict") == "REVIEW":
            return d.get("reason") or f"check {row['check_no']} could not decide"
    return "no automated check objected; referred for another reason"


@app.get("/gates/{gate_id}/review")
def review_packet(gate_id: str) -> dict:
    """
    Everything a reviewer is allowed to see, and nothing else.

    Raw captures, scores-as-images and the constellation coordinates are
    absent by design (context.md §11.8, privacy commitments). A reviewer
    resolving "is this a sibling or a bad photograph?" does not need the
    biometric data to do it — they need the signal that objected, its measured
    limits, and the history. Handing over the face would create a second copy
    of the most sensitive thing in the system in the least controlled place.
    """
    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")

    evidence = store.evidence_for(gate_id)
    reasons = fusion_reasons(gate_id)
    chain = store.verify_chain(gate_id).as_dict()
    return {
        "gate_id": gate_id,
        "state": gate["state"],
        "mode": gate["mode"],
        "created_at": gate["created_at"],
        "expires_at": gate["expires_at"],
        "expired": store.is_expired(gate),
        "triggering_signal": _triggering_signal(evidence, reasons),
        "reasons": reasons,
        "checks": [_check_summary(row) for row in evidence],
        # The hash travels with each event so the console can draw the chain
        # as linked blocks. It is the real digest, not a rendering flourish:
        # the picture is only worth showing if it is the actual structure.
        "timeline": [{"at": e.get("ts"), "type": e["type"],
                      "hash": e.get("hash"), "prev_hash": e.get("prev_hash"),
                      "payload": _detail({"detail": e.get("payload")})}
                     for e in store.chain(gate_id)],
        "chain": chain,
        "reviews": store.reviews_for(gate_id),
        # Every time a non-human reached for a step only a human may take.
        # Promoted out of the timeline: a reviewer scanning thirty rows of
        # ordinary state changes should not have to spot this one for
        # themselves, and it changes what the ruling means.
        "escalations": store.escalations(gate_id),
        "decidable": gate["state"] == str(GateState.REVIEW),
    }


@app.post("/gates/{gate_id}/review")
def submit_review(gate_id: str, body: ReviewDecision) -> dict:
    """
    Record a human ruling, and move the gate accordingly.

    approve -> SIGNED, reject -> FAIL, both as Actor.HUMAN. The transition goes
    through `gate_transition` like everything else, so a reviewer cannot reach a
    state an agent could not, and the ruling lands in the same hash chain.

    The transition is attempted *first*, and the review row is written only if
    it took effect. Writing the row first left the reviews table asserting an
    approval that had been refused — an expired gate would carry a row reading
    "approve" that was indistinguishable from one that authorised something.
    The attempt is not lost by reordering: `gate_transition` records refusals
    as `transition.refused` in the audit chain, and the reviewer's name is
    passed in the detail so the refusal says who tried.
    """
    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")

    # Read the signal before moving the gate: a rejection writes REVIEW -> FAIL,
    # and asking afterwards would describe the reviewer's own decision rather
    # than the finding they were asked to rule on.
    signal = _triggering_signal(store.evidence_for(gate_id),
                               fusion_reasons(gate_id))

    target = GateState.SIGNED if body.decision == "approve" else GateState.FAIL
    detail = {"reviewer_id": body.reviewer_id, "decision": body.decision,
              "notes": body.notes}
    try:
        gate = store.gate_transition(gate_id, target, Actor.HUMAN, detail)
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc

    store.add_review(gate_id, body.reviewer_id, body.decision, signal,
                     body.notes)

    return {"gate_id": gate_id, "state": gate["state"],
            "decision": body.decision, "reviewer_id": body.reviewer_id}


@app.get("/gates/{gate_id}/audit")
def audit(gate_id: str) -> dict:
    return {"gate_id": gate_id, "events": store.chain(gate_id)}

@app.get("/gates/{gate_id}/attestation.pdf")
def attestation_pdf(gate_id: str,
                    jurisdiction: str = "UNSPECIFIED",
                    risk_tier: str = "STANDARD") -> Response:
    """
    The gate, as a sealed document that outlives this server.

    Everything else STRATUM produces is only meaningful while the verifier is
    running and its database is intact. A relying party three years from now
    has neither. This route is the answer to that: the evidence graph, the
    limits of each check, and the hash chain head, rendered into a PDF and
    signed, so the record can be filed by whoever inherits the liability.

    Built from the chain rather than from the gate's current state. By the time
    a certificate is pulled the gate usually reads SIGNED, which is not a
    verdict — `attestation.outcome_of` recovers the actual finding from the
    audit trail, so a REVIEW a human approved never renders as a clean PASS.

    A broken chain does not block issue. It is stated at the top of the
    document and the certificate is still produced, because a portable record
    of a tampered ledger is exactly the artefact an investigator needs, and
    withholding it would leave the tamper visible only from inside the system
    that was tampered with.
    """
    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")

    try:
        jur = attestation.Jurisdiction(jurisdiction)
        tier = attestation.RiskTier(risk_tier)
    except ValueError as exc:
        raise HTTPException(
            422, f"{exc}. A certificate must not claim a regime it was not "
                 f"given.") from exc

    events = store.chain(gate_id)
    reviews = store.reviews_for(gate_id)
    att = attestation.build(
        gate, events, store.evidence_for(gate_id),
        jurisdiction=jur, risk_tier=tier,
        # The standing ruling is the last row, not the first. The state machine
        # does not currently allow a gate back into REVIEW once ruled on, so
        # today there is at most one — but `add_review` is a plain insert and
        # the certificate must report the decision in force rather than the
        # first one anyone recorded, whichever way that changes.
        reviewer=reviews[-1] if reviews else None,
        chain_intact=store.verify_chain(gate_id).ok,
    )

    # Checked here rather than left to `seal`, which also guards. A gate that
    # may not be certified should be refused before anything reaches out over
    # the network — the refusal is a local fact about the gate, and making it
    # depend on the renderer being available would turn a clear 409 into
    # whatever the upstream happened to say that day.
    try:
        certificate.guard(att)
    except certificate.NotSealable as exc:
        raise HTTPException(409, str(exc)) from exc

    try:
        pdf = certificate.seal(att)
    except certificate.NotSealable as exc:
        raise HTTPException(409, str(exc)) from exc
    except nutrient.NotAuthorised as exc:
        raise HTTPException(503, str(exc)) from exc
    except FixtureMissing as exc:
        raise HTTPException(
            503, f"{exc.cause}. A certificate embeds its issue time, so no two "
                 f"are the same bytes and none can be served from a recording. "
                 f"Set NUTRIENT_API_MODE=live — the grant is unmetered, so the "
                 f"flag conserving the sensor budget does not apply here."
        ) from exc
    except nutrient.NutrientError as exc:
        raise HTTPException(502, str(exc)) from exc

    # Issuing is itself an auditable act, and the digest commits the chain to
    # this exact document. Two certificates for one gate can then be told
    # apart, and a PDF presented later can be matched against the record.
    # Written only on success: an attempt that produced nothing must not leave
    # a trail implying a certificate is in circulation.
    store.audit(gate_id, "attestation", {
        "outcome": att.outcome,
        "jurisdiction": str(jur),
        "risk_tier": str(tier),
        "chain_head_at_issue": att.chain_head,
        "chain_intact": att.chain_intact,
        "document_sha256": hashlib.sha256(pdf).hexdigest(),
        "bytes": len(pdf),
    })

    return Response(
        content=pdf, media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="stratum-{gate_id[:8]}-attestation.pdf"',
            # So a caller can verify the download without opening it.
            "X-Stratum-Document-SHA256": hashlib.sha256(pdf).hexdigest(),
            "X-Stratum-Outcome": att.outcome,
        })


@app.get("/gates/{gate_id}/verify_chain")
def verify_chain(gate_id: str) -> dict:
    return store.verify_chain(gate_id).as_dict()


@app.get("/schema")
def schema_export() -> dict:
    """The 9-table model, in the shape used to create it in Xano."""
    import schema as s
    return s.xano_export()


# ── normalisation (Step 4) ────────────────────────────────────────────────
class CaptureBundle(BaseModel):
    """One capture, in the shape `pipeline.py` produces."""

    source: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    face_attributes: dict = Field(default_factory=dict)
    constellations: dict[str, list[list[float]]] = Field(default_factory=dict)


class ComparePair(BaseModel):
    a: CaptureBundle
    b: CaptureBundle


class BindingRequest(BaseModel):
    """
    Two captures of the same claimed person: the one on file, and the one
    presented now.

    Named rather than positional because `a`/`b` invites the caller to swap
    them, and the two are not interchangeable — the enrolment is the reference
    the probe is measured against.
    """

    enrolment: CaptureBundle
    probe: CaptureBundle
    gate_id: str | None = None


class FusionRequest(BaseModel):
    """Results from the checks, in whatever combination the caller has."""

    presence: dict | None = None
    authenticity: dict | None = None
    binding: dict | None = None
    uniqueness: dict | None = None
    gate_id: str | None = None


@app.post("/verify")
def verify(bundle: CaptureBundle) -> dict:
    """
    Turn a raw capture into comparable numbers.

    No decision is made here. Normalisation and judgement are kept apart so the
    checks in Steps 5-7 can be argued about independently of the arithmetic
    that feeds them, and so a stored constellation can be re-scored later
    against better population statistics without recapturing anything.
    """
    from normalise import normalise_bundle

    out = normalise_bundle(bundle.model_dump())
    if not out["identity_vector"] and not out["constellations"]:
        raise HTTPException(422, "bundle carries neither scores nor constellations")
    return out


@app.post("/verify/compare")
def compare(pair: ComparePair) -> dict:
    """
    Distance between two captures, on each channel that carries identity.

    Reported per channel rather than fused into one number: the channels fail
    in different ways, and a caller that cannot see which one disagreed cannot
    tell a pose problem from an impostor. Thresholds belong to Steps 5-7, so
    none are applied here.
    """
    import numpy as np

    from normalise import normalise_bundle, register, vector_distance

    a = normalise_bundle(pair.a.model_dump())
    b = normalise_bundle(pair.b.model_dump())

    geometric = {}
    for name in sorted(set(a["constellations"]) & set(b["constellations"])):
        pa = np.array(a["constellations"][name]["points"], float)
        pb = np.array(b["constellations"][name]["points"], float)
        if len(pa) < 3 or len(pb) < 3:
            geometric[name] = None
            continue
        geometric[name] = register(pa, pb).as_dict()

    return {
        "identity_distance": vector_distance(a["identity_vector"],
                                             b["identity_vector"]),
        "volatile_distance": vector_distance(a["volatile_vector"],
                                             b["volatile_vector"]),
        "ratio_distance": vector_distance(a["ratios"], b["ratios"]),
        "geometric": geometric,
        "population": a["population"],
        "warnings": sorted(set(a["warnings"]) | set(b["warnings"])),
    }


# ── check 1: presence (Step 5) ────────────────────────────────────────────
class ChallengeRequest(BaseModel):
    """
    Either the nonce itself, or the gate that owns one.

    `gate_id` exists so a browser never has to hold the nonce. A client that
    cannot name the nonce cannot substitute one, and the capture route re-reads
    the gate's own nonce anyway — so the two ends of the session are derived
    from the same server-held secret with nothing in between to corrupt.
    """

    nonce: str | None = Field(None, min_length=8)
    gate_id: str | None = None
    n_frames: int | None = None


class AuthenticityRequest(BaseModel):
    """One capture's scores, to be judged against a genuine-population baseline."""

    scores: dict[str, float]
    gate_id: str | None = None


class PresenceRequest(BaseModel):
    """A completed challenge, ready to be judged."""

    nonce: str = Field(min_length=8)
    issued_at: float
    frames: list[dict]
    gate_id: str | None = None
    n_frames: int | None = None


@app.post("/challenge")
def issue_challenge(body: ChallengeRequest) -> dict:
    """
    What the client must do, derived from the nonce it was handed.

    The spec is regenerated from the nonce on every call rather than stored, so
    there is no server state for an attacker to race and nothing to keep in
    sync. The predictions are withheld: telling a client which way each score
    must move is telling a forger what to fake.

    Given a `gate_id`, the gate's nonce is used and the gate moves to
    CHALLENGED, so the audit chain records the moment the demand was made. The
    transition goes through `gate_transition` like every other, which is what
    stops a second call quietly re-issuing a challenge on a gate that has
    already been captured.
    """
    import challenge as ch

    nonce = body.nonce
    gate = None
    if body.gate_id:
        gate = store.get("gates", body.gate_id)
        if gate is None:
            raise HTTPException(404, "no such gate")
        gate = store.expire_if_due(body.gate_id)
        nonce = gate["nonce"]
    if not nonce:
        raise HTTPException(422, "supply either a nonce or a gate_id")

    # A gate's challenge length is not the client's to choose. The capture route
    # re-derives the spec with the default, so a shortened challenge would fail
    # the frame count anyway — but refusing it here says why, instead of letting
    # someone believe they had negotiated an easier test.
    if gate is not None and body.n_frames:
        raise HTTPException(422, "n_frames cannot be set for a gate's challenge")

    kw = {"n_frames": body.n_frames} if body.n_frames else {}
    try:
        spec = ch.derive(nonce, **kw).client_view()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if gate is not None:
        # Re-issuing on a gate that is already CHALLENGED is idempotent, not an
        # error. The spec is derived from the nonce and is therefore identical,
        # so nothing new is disclosed — and refusing it meant a page refresh
        # during capture locked the person out of their own gate for good.
        #
        # Everything past CHALLENGED is still refused by the state machine: a
        # gate that has been captured cannot be handed a fresh challenge, which
        # is the property that actually matters.
        if gate["state"] != str(GateState.CHALLENGED):
            try:
                gate = store.gate_transition(
                    body.gate_id, GateState.CHALLENGED, Actor.SYSTEM,
                    {"n_frames": len(spec["frames"])})
            except IllegalTransition as exc:
                raise HTTPException(409, {
                    "error": "illegal_transition", "from": str(exc.frm),
                    "to": str(exc.to), "reason": exc.reason,
                }) from None
        spec["gate_id"] = body.gate_id
        spec["expires_at"] = gate["expires_at"]

    return spec


@app.post("/check/presence")
def check_presence(body: PresenceRequest) -> dict:
    """
    Was a live human in front of a real camera when this nonce was issued?

    The challenge is re-derived from the nonce here too. Nothing the client
    sends can influence what it was supposed to do, which is the property that
    makes a replayed session detectable at all.

    A `gate_id` writes the verdict to `evidence` against check 1, including the
    signal that decided it — a reviewer opening this gate later needs to know
    which physics failed, not just that something did.
    """
    import challenge as ch
    from checks.presence import evaluate

    kw = {"n_frames": body.n_frames} if body.n_frames else {}
    try:
        spec = ch.derive(body.nonce, **kw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if len(body.frames) != len(spec.frames):
        raise HTTPException(
            422, f"challenge asked for {len(spec.frames)} frames, "
                 f"got {len(body.frames)}")

    result = evaluate(body.frames, spec, issued_at=body.issued_at).as_dict()

    if body.gate_id:
        if store.get("gates", body.gate_id) is None:
            raise HTTPException(404, "no such gate")
        store.add_evidence(body.gate_id, 1, result["score"], {
            "passed": result["passed"],
            "failed": result["failed"],
            "triggering_signal": result["failed"][0] if result["failed"] else None,
            "limitations": result["limitations"],
            "signals": {s["name"]: {"passed": s["passed"], "score": s["score"]}
                        for s in result["signals"]},
        })
        result["gate_id"] = body.gate_id

    return result


_baseline = None


def authenticity_baseline():
    """
    The genuine-population reference check 2 is measured against.

    Fitted once and cached, because fitting is the expensive part and the
    baseline is a property of the population rather than of any request.

    Today it is fitted from `synth_zones`, which encodes dermatological anatomy
    — the T-zone is oilier and more porous than the cheeks in everyone — and
    nothing about any forgery. That makes it a legitimate *null* model and an
    illegitimate source of hit rates, which is why this endpoint reports
    calibrated false-positive rates and marks the baseline `provisional`.

    Step 12 refits this on real captures from the genuine set. The call
    signature does not change, and neither does anything downstream.
    """
    global _baseline
    if _baseline is None:
        import synth_zones as sz
        from checks.authenticity import fit
        _baseline = fit(sz.population(2000, seed=1))
    return _baseline


@app.post("/check/authenticity")
def check_authenticity(body: AuthenticityRequest) -> dict:
    """
    Does this face's per-zone texture look like it came off a real person?

    Two one-sided tests, each asking a different question, because they catch
    different failures and neither subsumes the other: `contrast` asks whether
    there is enough structure across zones at all, `zone_pattern` asks whether
    the structure sits where anatomy puts it.

    An SD capture carries no per-zone breakdown, so the check returns
    `ran=false` and does *not* pass. Check 3 has to be able to tell "looked and
    was satisfied" apart from "could not look", and collapsing the two would let
    a downgrade to SD silently clear the check.
    """
    from checks.authenticity import evaluate

    if not body.scores:
        raise HTTPException(422, "no scores to evaluate")

    result = evaluate(body.scores, authenticity_baseline()).as_dict()

    if body.gate_id:
        if store.get("gates", body.gate_id) is None:
            raise HTTPException(404, "no such gate")
        store.add_evidence(body.gate_id, 2, result["score"], {
            "passed": result["passed"],
            "ran": result["ran"],
            "flagged_by": result["flagged_by"],
            "limitations": result["limitations"],
            "signals": {s["name"]: {"passed": s["passed"],
                                    "p_value": s["p_value"]}
                        for s in result["signals"]},
        })
        result["gate_id"] = body.gate_id

    return result


@app.post("/check/binding")
def check_binding(body: BindingRequest) -> dict:
    """
    Is the person signing now the person who enrolled?

    Three channels, fused: where the spots are, the proportions of the face, and
    the slow-moving skin scores. They are weighted by measured separation rather
    than evenly, and a channel that cannot be computed is dropped and the rest
    renormalised — scoring an absent channel as agreement would let a degraded
    capture manufacture a match.

    Returns three verdicts, not two. A badly-lit genuine capture and a close
    relative produce overlapping distances, so there is a band where the
    evidence genuinely does not settle the question. That band goes to a human.
    """
    from checks.binding import evaluate
    from normalise import normalise_bundle

    if not body.enrolment.scores and not body.enrolment.constellations:
        raise HTTPException(422, "enrolment capture carries no comparable signal")
    if not body.probe.scores and not body.probe.constellations:
        raise HTTPException(422, "probe capture carries no comparable signal")

    result = evaluate(
        normalise_bundle(body.enrolment.model_dump()),
        normalise_bundle(body.probe.model_dump()),
    ).as_dict()

    if body.gate_id:
        if store.get("gates", body.gate_id) is None:
            raise HTTPException(404, "no such gate")
        store.add_evidence(body.gate_id, 3, result["score"], {
            "verdict": result["verdict"],
            "passed": result["passed"],
            "distance": result["distance"],
            "reason": result["reason"],
            "limitations": result["limitations"],
            "channels": {c["name"]: {"raw": c["raw"], "z": c["z"],
                                     "effective_weight": c["effective_weight"],
                                     "ran": c["ran"]}
                         for c in result["channels"]},
        })
        result["gate_id"] = body.gate_id

    return result


@app.post("/decide")
def decide(body: FusionRequest) -> dict:
    """
    Turn three check results into one authorisation decision.

    A conjunction, not an average: every check that ran must be satisfied, and
    a check that could *not* run sends the gate to REVIEW rather than letting
    it pass. The score is reported so a queue can be sorted, but it never
    overrides a check.

    With a `gate_id`, the verdict is written through `gate_transition` — the
    same choke point every other state change uses. Fusion does not get a
    private door into `gates.state`, so a decision here is subject to the same
    legality rules and lands in the same audit chain as everything else.
    """
    from fusion import fuse

    results = {name: payload for name, payload in (
        ("presence", body.presence),
        ("authenticity", body.authenticity),
        ("binding", body.binding),
        ("uniqueness", body.uniqueness),
    ) if payload is not None}

    if not results:
        raise HTTPException(422, "no check results submitted")

    # The mode comes from the gate, never from the request. A caller who could
    # name their own mode could name the one with the shortest list of required
    # checks, which is the whole guard.
    gate = store.get("gates", body.gate_id) if body.gate_id else None
    if body.gate_id and gate is None:
        raise HTTPException(404, "no such gate")

    decision = fuse(results, mode=gate["mode"] if gate else None).as_dict()

    if body.gate_id:
        # Persist what was fused, not just what was concluded. Without this the
        # verdict sits in the audit chain with no record of the evidence it
        # rested on, and a reviewer opening the gate later sees a referral with
        # no visible cause.
        already = {int(r["check_no"]) for r in store.evidence_for(body.gate_id)}
        # Written in check order rather than fusion's alphabetical order, so the
        # ledger reads 1, 2, 3. Fusion sorts by name for its own reasons; that
        # ordering means nothing to someone reading the history.
        for outcome in sorted(decision["checks"],
                              key=lambda o: CHECK_NUMBERS.get(o["name"], 99)):
            no = CHECK_NUMBERS.get(outcome["name"])
            if no is None or no in already:
                continue
            store.add_evidence(body.gate_id, no, outcome["score"], {
                "ran": outcome["ran"], "passed": outcome["passed"],
                "verdict": outcome["verdict"], "reason": outcome["reason"],
                "limitations": outcome.get("limitations", []),
                # Provenance belongs in the record, not just in the response.
                # The reviewer console reads evidence back out of the chain, so
                # a flag that lived only in the live reply would vanish the
                # moment anyone opened the gate afterwards — which is precisely
                # when it matters that these numbers came from a stand-in.
                "synthetic": bool(outcome.get("synthetic")),
            })

        try:
            gate = store.gate_transition(
                body.gate_id, decision["verdict"], Actor.SYSTEM,
                {"score": decision["score"], "reasons": decision["reasons"]})
        except IllegalTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        decision["gate_id"] = body.gate_id
        decision["state"] = gate["state"]

    return decision


# ── one human, one claim ──────────────────────────────────────────────────
class EnrolRequest(BaseModel):
    """Put a person on a campaign's roster."""

    context: str
    subject_ref: str
    capture: CaptureBundle


class ClaimRequest(BaseModel):
    """
    A wallet asking to claim, with the capture that should prove it is a
    person who has not claimed before.
    """

    context: str
    address: str
    capture: CaptureBundle
    gate_id: str | None = None
    presence: dict | None = None
    authenticity: dict | None = None


def _tenant() -> str:
    """The demo tenant. Claims are a demo path until real tenancy exists."""
    global _demo_workflow
    if _demo_workflow is None:
        demo_gate(60)
    row = store.db.execute("SELECT id FROM tenants LIMIT 1").fetchone()
    return row["id"]


@app.post("/claims/enrol", status_code=201)
def claim_enrol(body: EnrolRequest) -> dict:
    """
    Add a capture to a context's roster.

    Enrolment is deliberately separate from claiming. A roster that grew as a
    side effect of claiming could never answer "was this person already here?"
    for the first claimant — they would have just added themselves.
    """
    from normalise import normalise_bundle

    if not claim_mod.CONTEXT.match(body.context):
        raise HTTPException(422, "context is not a valid campaign identifier")

    bundle = normalise_bundle(body.capture.model_dump())
    if not bundle["identity_vector"] and not bundle["constellations"]:
        raise HTTPException(422, "capture carries no comparable signal, so it "
                                 "would sit on the roster without ever being "
                                 "comparable to a claim")

    row = store.create_enrolment(_tenant(), body.subject_ref, bundle,
                                 context=body.context)
    return {"enrolment_id": row["id"], "context": body.context,
            "roster_size": len(store.roster(body.context))}


@app.get("/claims/roster/{context}")
def claim_roster(context: str) -> dict:
    """
    How large the sweep would be, and what that costs in confidence.

    Exposed because the false-match probability is a function of this number,
    so a caller cannot interpret a claim result without it.
    """
    from checks.uniqueness import PER_COMPARISON_FMR, family_false_match

    size = len(store.roster(context))
    return {"context": context, "roster_size": size,
            "per_comparison_false_match_bound": PER_COMPARISON_FMR,
            "false_match_across_a_full_sweep": round(
                family_false_match(size), 5),
            "claims_recorded": store.db.execute(
                "SELECT COUNT(*) AS n FROM claims WHERE context = ?",
                (context,)).fetchone()["n"]}


@app.post("/claims/verify")
def claim_verify(body: ClaimRequest) -> dict:
    """
    Sweep a capture against a context's roster and decide whether it may claim.

    The sweep runs before anything is written. A DUPLICATE is a finding, not an
    error: it is returned with the enrolment it matched and the false-match
    probability that finding carries, because a campaign operator refusing
    someone's allocation should see the number their refusal rests on.

    Nothing is signed here. Signing happens at `/claims/{gate}/signature`,
    after the gate has actually settled — a signature issued alongside a
    REVIEW verdict would be a signature over an unfinished decision.
    """
    from checks.uniqueness import sweep
    from fusion import fuse
    from normalise import normalise_bundle

    if not claim_mod.CONTEXT.match(body.context):
        raise HTTPException(422, "context is not a valid campaign identifier")
    if not claim_mod.ADDRESS.match(body.address or ""):
        raise HTTPException(422, "address is not a 20-byte hex address")

    probe = normalise_bundle(body.capture.model_dump())
    if not probe["identity_vector"] and not probe["constellations"]:
        raise HTTPException(422, "capture carries no comparable signal")

    roster = [(r["id"], json.loads(r["identity_vector"]))
              for r in store.roster(body.context)]
    result = sweep(probe, roster).as_dict()

    gate = None
    if body.gate_id:
        gate = store.get("gates", body.gate_id)
        if gate is None:
            raise HTTPException(404, "no such gate")
        if GateMode(gate["mode"]) is not GateMode.ONE_HUMAN_ONE_CLAIM:
            raise HTTPException(
                409, f"gate is in {gate['mode']} mode; a uniqueness sweep "
                     "belongs to a one_human_one_claim gate")
        store.add_evidence(body.gate_id, 4, result["score"], {
            "verdict": result["verdict"], "passed": result["passed"],
            "ran": result["ran"], "reason": result["reason"],
            "roster_size": result["roster_size"],
            "comparisons_run": result["comparisons_run"],
            "comparisons_skipped": result["comparisons_skipped"],
            "nearest": result["nearest"],
            "false_match": result["false_match"],
            "limitations": result["limitations"],
        })

    # Fused here rather than left to the caller, so the mode's full requirement
    # is applied: a claim that reports a clean sweep but never ran presence is
    # not a claim, and returning the sweep alone would let it look like one.
    submitted = {"uniqueness": result}
    if body.presence is not None:
        submitted["presence"] = body.presence
    if body.authenticity is not None:
        submitted["authenticity"] = body.authenticity
    decision = fuse(submitted, mode=str(GateMode.ONE_HUMAN_ONE_CLAIM)).as_dict()

    out = {"uniqueness": result, "decision": decision,
           "context": body.context, "address": body.address}
    if gate is not None:
        out["gate_id"] = gate["id"]
    return out


class SignatureRequest(BaseModel):
    context: str
    address: str
    enrolment_id: str


@app.post("/claims/{gate_id}/signature")
def claim_signature(gate_id: str, body: SignatureRequest) -> dict:
    """
    Issue the claim as something a contract can verify.

    Only for a settled gate, using the same rule that governs certificates: a
    signature over a gate still in flight would circulate as a finished
    authorisation for a question nobody had answered.

    The verdict is recovered from the audit chain rather than read off the
    gate's current state. A gate a human approved reads SIGNED, which is not a
    finding — signing "SIGNED" would lose the fact that the sweep was
    ambiguous and a named person resolved it, which is the distinction most
    worth putting on chain.

    The write happens before the signature. If the unique index refuses this as
    a double claim, no signature is produced — issuing one and then failing to
    record it would put a valid, unrecorded authorisation into the world.
    """
    import sqlite3

    gate = store.get("gates", gate_id)
    if gate is None:
        raise HTTPException(404, "no such gate")
    if not certificate.sealable(gate["state"]):
        raise HTTPException(
            409, f"gate is {gate['state']}, which is not a settled verdict. A "
                 "signature is an authorisation, and an unfinished gate has "
                 "not authorised anything")

    events = store.chain(gate_id)
    verdict = attestation.outcome_of(gate, events)

    reviews = store.reviews_for(gate_id)
    decided_by = f"reviewer:{reviews[-1]['reviewer_id']}" if reviews else "machine"

    evidence = [e for e in store.evidence_for(gate_id) if int(e["check_no"]) == 4]
    detail = json.loads(evidence[-1]["detail"]) if evidence else {}

    try:
        built = claim_mod.build(
            context=body.context, address=body.address,
            enrolment_id=body.enrolment_id, verdict=verdict, gate_id=gate_id,
            chain_head=events[-1]["hash"] if events else "",
            roster_size=int(detail.get("roster_size", 0)),
            comparisons=int(detail.get("comparisons_run", 0)),
            false_match_bound=float(
                (detail.get("false_match") or {}).get("across_this_sweep", 0.0)),
            decided_by=decided_by,
        )
    except claim_mod.NotSignable as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        signed = claim_mod.sign(built)
    except claim_mod.NotSignable as exc:
        # 501, not 500: the request was valid and the server simply has no key
        # configured. A 500 would send a caller looking for a bug.
        raise HTTPException(501, str(exc)) from exc

    try:
        store.add_claim(gate_id, body.enrolment_id, body.context,
                        built.nullifier, body.address, verdict, decided_by,
                        signed["signature"])
    except sqlite3.IntegrityError as exc:
        existing = store.claim_for(body.context, built.nullifier)
        raise HTTPException(
            409, f"this person has already claimed in {body.context} "
                 f"(claim {existing['id'] if existing else 'unknown'}). One "
                 "human, one claim, is the whole point") from exc

    return signed
