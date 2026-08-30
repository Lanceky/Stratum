"""
Populate the review queue so the reviewer console can be demonstrated.

There is no HTTP route that creates a tenant or a workflow — those are set up
out of band — so without this a freshly cloned repo cannot create a gate at
all, and the console has nothing to show. The three gates below are not
decoration: they are the three distinct routes into REVIEW, which is the point
the console exists to make.

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

# The three ways a gate reaches a human, which the console must distinguish.
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
            "reason": ("authenticity check did not run — no enrolled reference "
                       "capture exists for this subject"),
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
]


def main() -> int:
    if not DB:
        print("Set STRATUM_DB to the same file the verifier is using, e.g.\n"
              "  STRATUM_DB=/tmp/stratum.db make verifier\n"
              "  STRATUM_DB=/tmp/stratum.db make review-seed\n"
              "An in-memory store cannot be shared between two processes.",
              file=sys.stderr)
        return 2

    store = Store(DB)
    tenant = store.create_tenant("Acme Financial", "acme.example")
    workflow = store.create_workflow(tenant["id"], "wire_transfer_approval")

    client = httpx.Client(base_url=BASE, timeout=30)
    try:
        client.get("/health").raise_for_status()
    except (httpx.HTTPError, OSError):
        print(f"No verifier answering on {BASE} — start it first with "
              f"`STRATUM_DB={DB} make verifier`.", file=sys.stderr)
        return 1

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
        print(f"  {gate_id}  {label}")

    print(f"\n3 gates awaiting a human. Open the console at /review "
          f"(run `make frontend`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
