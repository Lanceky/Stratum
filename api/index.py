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

from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: E402

import app as appmod  # noqa: E402


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
    """Best-effort. A cold start that cannot seed should still serve.

    A demo with an empty review queue is a weaker demo; a demo that 500s on the
    first request is no demo at all. So failures here are swallowed rather than
    raised, and the console simply shows an empty queue.
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

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.PREFIX or path.startswith(self.PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.PREFIX):] or "/"
                scope["root_path"] = self.PREFIX
                if scope.get("raw_path"):
                    scope["raw_path"] = scope["path"].encode()
        await self.inner(scope, receive, send)


_warm()

app = StripPrefix(appmod.app)
