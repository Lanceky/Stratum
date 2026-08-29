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

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dimensions import STABLE, VOLATILE
from fixtures import budget_status
from gate import Actor, GateMode, GateState, IllegalTransition
from store import Store

app = FastAPI(title="STRATUM verifier", version="0.1.0")

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


# POST /check/authenticity, /check/binding — Steps 6, 7.
