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

import json
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dimensions import STABLE, VOLATILE
from fixtures import budget_status
from gate import Actor, GateMode, GateState, IllegalTransition
from store import Store

app = FastAPI(title="STRATUM verifier", version="0.1.0")

VERDICTS = {str(GateState.PASS), str(GateState.REVIEW), str(GateState.FAIL)}
CHECK_NUMBERS = {"presence": 1, "authenticity": 2, "binding": 3}

# Local store. Xano replaces this as system of record once the instance exists;
# the transition rules are identical either way, which is the point of keeping
# them in gate.py rather than in a Xano function stack alone.
store = Store(os.getenv("STRATUM_DB", ":memory:"))


class HealthResponse(BaseModel):
    status: str
    api_mode: str
    units: dict


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        api_mode=os.getenv("STRATUM_API_MODE", "replay"),
        units=budget_status(),
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
            "limitations": d.get("limitations", [])}


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
    """Results from checks 1-3, in whatever combination the caller has."""

    presence: dict | None = None
    authenticity: dict | None = None
    binding: dict | None = None
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
    nonce: str = Field(min_length=8)
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
    """
    import challenge as ch

    kw = {"n_frames": body.n_frames} if body.n_frames else {}
    try:
        return ch.derive(body.nonce, **kw).client_view()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
    ) if payload is not None}

    if not results:
        raise HTTPException(422, "no check results submitted")

    decision = fuse(results).as_dict()

    if body.gate_id:
        if store.get("gates", body.gate_id) is None:
            raise HTTPException(404, "no such gate")

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
