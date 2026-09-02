"""
Vercel entry point.

Everything here exists to reconcile one mismatch: the verifier was written as a
long-lived process holding a SQLite file and a lock, and Vercel runs it as a
short-lived function on a read-only disk. The reconciliation is deliberate
rather than incidental, so the limits are written down.

What this deployment is
-----------------------
A walkthrough. Every gate, hash chain and refusal is real and computed live by
the same code the tests exercise, but the state behind them does not outlive
the instance that made it.

What it is not
--------------
1. Durable. The database is /tmp/stratum.db on an ephemeral, per-instance disk.
   Two browser tabs may land on two instances and see two different worlds, and
   an idle instance is reclaimed with everything in it.
2. Safely concurrent across instances. store.py serialises appends with a
   threading.RLock, which is real mutual exclusion inside one process and none
   at all between them. Its docstring spells out the failure: interleaved
   appends fork the chain, and verify_chain then reports tampering that never
   happened. One instance per database is what keeps that honest, and giving
   each instance its own /tmp file is what guarantees it.

Both are properties of the demo host, not of the design. The chain, the state
machine and the refusals are the same code either way; a durable deployment
swaps SQLite for a server that can hold the lock, which is a narrow change —
one connect() in store.py and one IntegrityError catch in app.py.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verifier"))

# Set before importing app: store.py opens its database at module scope, so by
# the time the import returns the path is already fixed and unchangeable.
os.environ.setdefault("STRATUM_DB", "/tmp/stratum.db")

# No vendor credentials in the demo environment. Replay is the honest mode:
# it answers from recorded fixtures and never pretends a call was live.
os.environ.setdefault("STRATUM_API_MODE", "replay")

# The repo tree ships read-only. Synthetic fixtures are generated, so they are
# regenerated into /tmp; recorded ones travel with the function and stay put.
os.environ.setdefault("STRATUM_SYNTHETIC_DIR", "/tmp/fixtures/synthetic")
os.environ.setdefault("STRATUM_UNIT_LOG", "/tmp/fixtures/units.log")

from starlette.types import Receive, Scope, Send  # noqa: E402

import anyio.to_thread  # noqa: E402

# Deferred so that an import failure becomes a readable HTTP response instead
# of FUNCTION_INVOCATION_FAILED. A missing wheel or a mis-set path is the most
# likely way this deployment breaks, and it is the failure mode that tells you
# least from the outside — the platform reports only that the function did not
# start. Capturing the traceback and serving it is worth more than letting the
# module raise, because the URL then diagnoses itself.
_BOOT_ERROR: str | None = None
appmod = None
try:
    from fastapi import Request  # noqa: E402
    from fastapi.responses import JSONResponse  # noqa: E402

    import app as appmod  # noqa: E402
except Exception:  # noqa: BLE001 — re-raised to the client, see _Broken
    import traceback

    _BOOT_ERROR = traceback.format_exc()


def _seed_fixtures() -> None:
    """Regenerate the synthetic fixture tree into /tmp.

    Without this the document tools return 503 and the agent console shows an
    agent that can do nothing, which argues the opposite of its point: the
    boundary is only interesting if the agent is otherwise capable.

    Deterministic and about a second, so a cold start can afford it.
    """
    synthetic = Path(os.environ["STRATUM_SYNTHETIC_DIR"])
    if (synthetic / "synthetic_face.jpg").exists():
        return
    synthetic.parent.mkdir(parents=True, exist_ok=True)
    import seed_fixtures

    seed_fixtures.main()


def _seed_review_queue() -> None:
    """Put four gates in REVIEW so the reviewer console has something to show.

    Driven through the app's own routes rather than written into the database,
    so each gate travelled the real state machine and left a real chain behind
    it. A gate inserted directly would look identical in the list and fall over
    the moment a judge asked it to verify.
    """
    if appmod.store.gates_in_state("REVIEW"):
        return
    from fastapi.testclient import TestClient

    import demo_seed

    with TestClient(appmod.app) as client:
        demo_seed.seed(client, appmod.store)


def _warm() -> None:
    """Best-effort seeding, run on first request rather than at import.

    Deliberately *not* called at module scope. Seeding drives a gate through
    /decide, which imports opencv, scipy and scikit-learn and then does real
    image work — several hundred megabytes of shared objects and the most
    expensive thing this deployment can do. Serverless init is the worst place
    for it: the init phase has a tighter budget than a request, and a process
    killed there produces FUNCTION_INVOCATION_FAILED with no traceback, because
    nothing Python-level survives to report it.

    Moving it behind the first request buys the full maxDuration, keeps cold
    start to the fastapi and eth-account imports alone, and — because failures
    here are swallowed — means a deployment that cannot seed still serves every
    route instead of none of them.
    """
    for step in (_seed_fixtures, _seed_review_queue):
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] {step.__name__} skipped: {type(exc).__name__}: {exc}")


class StripPrefix:
    """Serve the app whether or not the request still carries its `/api` prefix.

    The frontend calls `/api/health`; the app declares `/health`. Something has
    to remove the prefix, and locally that is vite's dev proxy (`rewrite: p =>
    p.replace(/^\\/api/, '')`). In the deployment the rewrite in vercel.json
    selects this function but does not rewrite the path the same way, so the
    prefix arrives intact and has to come off here.

    Written as a conditional strip rather than a `Mount("/api", ...)` because a
    mount is all-or-nothing: it 404s anything outside its prefix, so if the
    platform ever *did* strip the prefix first, every route would vanish and
    the failure would look like a broken app rather than a routing mismatch.
    Stripping only what is actually there is correct under both behaviours.

    `raw_path` is updated alongside `path` because some servers prefer it when
    reconstructing the URL, and leaving the two disagreeing produces redirects
    that point at the unstripped path.
    """

    PREFIX = "/api"

    def __init__(self, app) -> None:
        self.inner = app
        self.warmed = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.PREFIX or path.startswith(self.PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.PREFIX):] or "/"
                scope["root_path"] = self.PREFIX
                if scope.get("raw_path"):
                    scope["raw_path"] = scope["path"].encode()
        if not self.warmed:
            # Set before the attempt, not after: seeding that fails halfway
            # would otherwise retry on every subsequent request, turning one
            # slow response into permanently slow ones.
            self.warmed = True
            # In a worker thread because _seed_review_queue drives a
            # TestClient, which starts its own event loop and cannot do that
            # from inside the one already running this request.
            await anyio.to_thread.run_sync(_warm)
        await self.inner(scope, receive, send)


class _Broken:
    """Serve the boot traceback rather than nothing at all.

    Only reachable when the import above failed, which means there is no app to
    delegate to and every route is equally dead. Returning the traceback as
    plain text costs nothing here — replay mode holds no credentials and the
    store is an empty /tmp file — and it converts an opaque platform error into
    the actual missing module.

    Not a FastAPI instance, so the platform's own app detection will not accept
    it. That is unavoidable: if FastAPI could not be imported there is nothing
    to build one from. It still helps under `vercel dev` and any host that
    accepts a bare ASGI callable.
    """

    def __init__(self, detail: str) -> None:
        self.body = f"stratum: verifier failed to start\n\n{detail}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 500,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": self.body})


def _serve_frontend(fastapi_app) -> None:
    """Serve the built single-page app from the same application as the API.

    Vercel routes *every* request to the detected app, so anything not claimed
    here — `/review`, `/agent`, a hard refresh on any deep link — reaches Python
    rather than the CDN's static handler. Without a catch-all those all 404,
    which looks exactly like a broken deployment.

    Registered after the verifier's own routes, so it can only ever match what
    they did not. `/assets` is a real mount rather than part of the catch-all
    because the platform promotes mounted static directories to the CDN at build
    time, which keeps the hashed bundles off the function entirely.
    """
    dist = ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    if (dist / "assets").is_dir():
        fastapi_app.mount("/assets", StaticFiles(directory=dist / "assets"),
                          name="assets")

    @fastapi_app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str, request: Request):
        # An unmatched path that still carries the API prefix is a missing
        # endpoint, not a page. Returning the SPA there would answer a bad API
        # call with 200 and a pile of HTML, which is far harder to debug than a
        # plain 404.
        if request.scope.get("root_path") == StripPrefix.PREFIX:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (dist / path) if path else index
        if path and candidate.is_file() and dist in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(index)


if _BOOT_ERROR is not None:
    app = _Broken(_BOOT_ERROR)
else:
    # Registered as middleware rather than by wrapping the app in a plain
    # callable, so that `app` stays a genuine FastAPI instance. The platform
    # looks for a FastAPI instance named `app` and will not accept a wrapper,
    # which is the whole reason this is middleware and not composition.
    # Starlette applies user middleware outside the router, so rewriting the
    # path here still happens before any route is matched.
    #
    # Must precede _warm(): seeding drives a TestClient, which builds the
    # middleware stack, and Starlette refuses to add middleware once an
    # application has started. Harmless to the seeder either way — it calls
    # bare paths like /gates, and this only strips a prefix that is present.
    appmod.app.add_middleware(StripPrefix)
    _serve_frontend(appmod.app)

    app = appmod.app
