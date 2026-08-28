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

from fastapi import FastAPI
from pydantic import BaseModel

from dimensions import STABLE, VOLATILE
from fixtures import budget_status

app = FastAPI(title="STRATUM verifier", version="0.1.0")


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


# POST /verify — implemented in Step 4.
# POST /check/presence, /check/authenticity, /check/binding — Steps 5, 6, 7.
