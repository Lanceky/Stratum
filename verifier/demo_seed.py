"""
Populate the review queue so the reviewer console can be demonstrated.

There is no HTTP route that creates a tenant or a workflow — those are set up
out of band — so without this a freshly cloned repo cannot create a gate at
all, and the console has nothing to show. The gates below are not decoration:
they are the distinct routes into REVIEW, which is the point the console exists
to make — a machine that says "I could not settle this" rather than guessing.

    STRATUM_DB=/tmp/stratum.db make verifier   # in one shell
    make review-seed                           # in another

Writes gate state through the running verifier over HTTP, so every gate here
travelled the same state machine and hash chain as a real one.
"""

import os
import sys

import httpx

from gate import GateState
from store import Store

BASE = os.getenv("STRATUM_VERIFIER", "http://127.0.0.1:8000")
DB = os.getenv("STRATUM_DB", "")

TTL_S = 3600  # long enough to demonstrate; a real gate lives for five minutes

PASSING = {"ran": True, "passed": True, "score": 0.95, "verdict": "PASS",
           "reason": ""}

# The ways a gate reaches a human, which the console must distinguish.
CASES = [
    ("check 3 landed in the overlap band", {
        "binding": {
            "ran": True, "passed": False, "score": 0.55, "verdict": "REVIEW",
            "reason": ("binding distance 7.30 sits inside the overlap between "
                       "the worst honest capture (7.30) and the closest "
                       "sibling (7.05); no single threshold separates them"),
            "limitations": [
                "thresholds fitted on a synthetic cohort (n=24), not on real faces",
                "the overlap is 0.25 wide and cannot be removed by tuning",
            ],
        },
    }),
    ("check 2 never ran", {
        "authenticity": {
            "ran": False, "passed": False, "score": 0.0, "verdict": "REVIEW",
            # Written to complete fusion's "could not run — " prefix, not to
            # restate it. Saying "authenticity check did not run" after that
            # prefix reads as a stutter in the console.
            "reason": ("no enrolled reference capture exists for this subject, "
                       "so there is no baseline to compare this one against."),
            "limitations": ["absence of evidence is not evidence of absence"],
        },
    }),
    ("check 1 was inconclusive", {
        "presence": {
            "ran": True, "passed": False, "score": 0.48, "verdict": "REVIEW",
            "reason": ("only 2 of 4 challenge frames returned a usable "
                       "response; liveness is unproven rather than disproven"),
            "limitations": ["replay mode: responses are recorded, not live"],
        },
    }),
    # The accessibility case, and the one that most needs to be visible.
    #
    # WCAG 2.3.1 makes a flashing challenge unsafe for anyone with
    # photosensitive epilepsy, so the capture screen offers a no-flash path.
    # A person who takes it cannot be tested for a light response — not because
    # they failed it, but because nothing provoked one. The wrong answer here
    # is to refuse them, which builds an accessibility exclusion into the core
    # flow. The right answer is a human, and this is what that looks like in
    # the queue.
    #
    # The reason is `PresenceResult.reason` verbatim rather than a paraphrase.
    # A seeded case that reads differently from the live path would show a
    # reviewer prose they will never see again.
    ("the person chose the no-flash path", {
        "presence": {
            "ran": False, "passed": False, "score": 0.0, "verdict": "REVIEW",
            "reason": ("the light-response test was not part of this capture. "
                       "The person chose the no-flash path, which cannot "
                       "provoke one. Every other signal ran and was satisfied."),
            "limitations": [
                "a no-flash capture cannot establish a light response, by "
                "construction — this is a bounded gap, not a failure",
                "pose, timing and geometry all ran and were satisfied",
            ],
        },
    }),
]


def seed(client, store) -> list[tuple[str, str]]:
    """
    Create the review-queue gates through `client`, returning (id, label) pairs.

    Takes the client rather than building one so the same code serves two
    callers: the CLI below, which talks to a running verifier over the network,
    and the serverless cold start, which passes a `TestClient` wrapping the app
    in its own process. `TestClient` subclasses `httpx.Client`, so neither
    caller needs a special case.

    Going through HTTP in both is the point. These gates travel the same state
    machine, the same transition guards and the same hash chain as a real one —
    a seeder that reached into the store and wrote REVIEW directly would
    populate the console with rows that never passed the checks they claim to
    have been referred by.
    """
    tenant = store.create_tenant("Acme Financial", "acme.example")
    workflow = store.create_workflow(tenant["id"], "wire_transfer_approval")

    made = []
    for label, overrides in CASES:
        gate = client.post("/gates", json={
            "workflow_id": workflow["id"], "mode": "authorise_action",
            "ttl_s": TTL_S}).json()
        gate_id = gate["id"]

        for to, actor in (("CHALLENGED", "system"), ("CAPTURED", "human"),
                          ("SCORED", "system")):
            client.post(f"/gates/{gate_id}/transition",
                        json={"to": to, "actor": actor}).raise_for_status()

        body = {"gate_id": gate_id, "presence": PASSING,
                "authenticity": PASSING, "binding": PASSING, **overrides}
        verdict = client.post("/decide", json=body).json()["verdict"]
        assert verdict == str(GateState.REVIEW), (label, verdict)
        made.append((gate_id, label))
    return made


def main() -> int:
    if not DB:
        print("Set STRATUM_DB to the same file the verifier is using, e.g.\n"
              "  STRATUM_DB=/tmp/stratum.db make verifier\n"
              "  STRATUM_DB=/tmp/stratum.db make review-seed\n"
              "An in-memory store cannot be shared between two processes.",
              file=sys.stderr)
        return 2

    store = Store(DB)
    client = httpx.Client(base_url=BASE, timeout=30)
    try:
        client.get("/health").raise_for_status()
    except (httpx.HTTPError, OSError):
        print(f"No verifier answering on {BASE} — start it first with "
              f"`STRATUM_DB={DB} make verifier`.", file=sys.stderr)
        return 1

    for gate_id, label in seed(client, store):
        print(f"  {gate_id}  {label}")

    print(f"\n{len(CASES)} gates awaiting a human. Open the console at /review "
          f"(run `make frontend`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
