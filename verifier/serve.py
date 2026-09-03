"""
Production entry point: the verifier, plus enough demo state to be worth
opening.

`app.py` deliberately knows nothing about demos — it has no route that creates
a tenant or a workflow, because those are set up out of band, and no startup
hook that invents data. That is right for the library and wrong for a hosted
demo, which is opened cold by someone who will not run a seeding script first.
This module is the seam between the two: it imports the real application,
fills the review queue if it is empty, and exposes the same object uvicorn
would have served anyway.

    uvicorn serve:app --host 0.0.0.0 --port $PORT

Why a single instance matters
-----------------------------
Every console here is multi-step. The claim flow captures, opens a gate,
sweeps the roster, enrols, then settles that gate — five requests that must
agree about state. `store.py` serialises appends with a threading.RLock, which
is real mutual exclusion inside one process and none at all between them; its
own docstring notes that interleaved appends fork the hash chain, which
verify_chain then reports as tampering that never happened.

So this is meant to run as one process with one database file. That is not a
limitation being worked around, it is the condition the store was written for.
Scaling out needs a database that can hold the lock — a change to store.py's
single connect(), not to anything above it.
"""

from __future__ import annotations

import os

# Before importing app: store.py opens its database at module scope, so the
# path is fixed by the time the import returns.
os.environ.setdefault("STRATUM_DB", "/tmp/stratum.db")
os.environ.setdefault("STRATUM_API_MODE", "replay")

from app import app, store  # noqa: E402


def _allow_browser_origins() -> None:
    """Let the hosted frontend call this service cross-origin.

    `app.py` has no CORS layer, and correctly so: in development vite proxies
    /api to this process, and in a single-origin deployment nothing ever
    crosses. Both are same-origin by construction. It only stops being true
    when the frontend is hosted apart from the API — which is exactly this
    deployment, frontend on one host and verifier on another.

    So the policy lives here rather than in the application, next to the other
    facts that are true of *this host* and not of the software.

    Explicit origins rather than a wildcard, and no credentials: the browser
    refuses `*` with credentials anyway, and every request here is
    unauthenticated. Preview deployments get their own generated hostname, so
    the regex covers them without listing each one.
    """
    from fastapi.middleware.cors import CORSMiddleware

    origins = [o.strip() for o in
               os.environ.get("STRATUM_CORS_ORIGINS", "").split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["http://localhost:5173", "https://localhost:5173"],
        allow_origin_regex=r"https://[a-z0-9-]+\.vercel\.app",
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _seed_review_queue() -> None:
    """Put the demo gates in REVIEW, once, if nothing is there yet.

    Driven through the app's own routes rather than written into the database,
    so each gate travelled the real state machine and left a real chain behind
    it. A row inserted directly would look identical in the list and fall over
    the moment a judge asked it to verify.
    """
    if store.gates_in_state("REVIEW"):
        return

    from fastapi.testclient import TestClient

    import demo_seed

    with TestClient(app) as client:
        demo_seed.seed(client, store)


# Order matters: middleware must be registered before anything starts the app,
# and seeding starts it. Kept as separate attempts so a failure in one is
# reported as itself — a CORS problem and an empty review queue have nothing to
# do with each other, and one message covering both would misdirect whoever
# reads the log.
try:
    _allow_browser_origins()
except Exception as exc:  # noqa: BLE001
    print(f"[serve] CORS not configured: {type(exc).__name__}: {exc}")

try:
    _seed_review_queue()
except Exception as exc:  # noqa: BLE001
    # An empty review queue is a weaker demo; a process that will not boot is
    # no demo at all. Seeding is not load-bearing for any other console, so it
    # is allowed to fail loudly in the log and quietly in the product.
    print(f"[serve] review queue not seeded: {type(exc).__name__}: {exc}")

__all__ = ["app"]
