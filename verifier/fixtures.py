"""
Fixture record/replay + hard unit budget.

This is the single most important file in the repo for surviving the hackathon.
Perfect Corp HD costs 12-22 units per call, and credit exhaustion is the most
likely way this project dies (context.md §11.1).

Every external call goes through `call()`. In replay mode (the dev default)
nothing leaves the machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(os.getenv("STRATUM_FIXTURE_DIR", REPO_ROOT / "fixtures"))

# Synthetic fixtures live apart from recorded ones and are only consulted as a
# fallback. A real recording therefore always wins over a placeholder, and the
# synthetic tree can be gitignored and regenerated with `make seed`.
#
# Both directories are overridable because a read-only deployment cannot write
# where the repo says fixtures live. On Vercel the recorded tree ships with the
# function and stays read-only, while the synthetic tree is regenerated into
# /tmp at cold start; splitting the two env vars is what lets those differ.
SYNTHETIC_DIR = Path(os.getenv("STRATUM_SYNTHETIC_DIR", FIXTURE_DIR / "synthetic"))

UNIT_LOG = Path(os.getenv("STRATUM_UNIT_LOG", FIXTURE_DIR / "units.log"))

MODE = os.getenv("STRATUM_API_MODE", "replay")
CEILING = int(os.getenv("UNIT_BUDGET_CEILING", "200"))

# Published Perfect Corp unit costs. HD is the expensive one — see context.md §11.1.
UNIT_COST = {
    "skin-analysis-hd": 22,
    "skin-analysis-sd": 3,
    "face-attr-analysis": 2,
    "ai_face_swap": 5,
    "file-upload": 0,
}


class UnitBudgetExceeded(RuntimeError):
    """Raised instead of quietly burning the remaining grant."""


def _spent() -> int:
    if not UNIT_LOG.exists():
        return 0
    total = 0
    for line in UNIT_LOG.read_text().splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            try:
                total += int(parts[2])
            except ValueError:
                pass
    return total


def _record_units(op: str, units: int) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    with UNIT_LOG.open("a") as fh:
        fh.write(f"{int(time.time())},{op},{units}\n")


def fixture_key(op: str, payload: Any) -> str:
    """Stable key from operation + payload, so identical calls hit the same key."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"{op}__{hashlib.sha256(blob).hexdigest()[:16]}"


# Operations whose response does not depend on the bytes being sent.
#
# Requesting an upload slot is the clear case: Perfect Corp returns the same
# shape whatever the image is, and the only field anything downstream reads is
# file_id. Keying its stand-in on the payload meant keying it on the file's
# name and size, so a seeded fixture matched exactly one file with exactly one
# name — and every live capture missed at step 1, before reaching the analysis
# fixture that would have hit. The same bytes succeeded as
# `synthetic_face.jpg` and failed as `frame_0.jpg`.
#
# Analysis is emphatically not in this set. Its response *is* a function of the
# image, and serving a generic stand-in for it would be handing back a reading
# of a face nobody looked at.
CONTENT_INDEPENDENT = frozenset({
    "file-upload",
    # Foxit's document operations, for a related but not identical reason.
    # Their answers are not generic — a real watermark is a function of the
    # document it stamps — but every id in the chain is a server-generated
    # random. An upload returns a fresh `documentId`, an operation a fresh
    # `taskId`, and the watermark's own text names the gate, which is itself
    # random. Keyed on payload, not one of these could ever be hit twice: the
    # next run draws different ids and misses every step.
    #
    # So a stand-in here stands in for the *call*, not the document. That is
    # honest about what it proves — that the toolset is wired and reachable,
    # which is the claim the agent console makes — and no further. `auto` mode
    # still ignores synthetic fixtures, so a real credential records real
    # responses and those win from then on.
    "foxit-upload",
    "foxit-properties", "foxit-properties-task",
    "foxit-watermark", "foxit-watermark-task",
    "foxit-compare", "foxit-compare-task",
    "foxit-download",
})

GENERIC_KEY = "any"


def generic_key(op: str) -> str:
    return f"{op}__{GENERIC_KEY}"


def resolve(op: str, payload: Any, *, synthetic_ok: bool = True,
            ext: str = ".json") -> Path | None:
    """
    Recorded fixture if one exists, else the synthetic stand-in, else None.

    `synthetic_ok=False` asks for recorded data only. That distinction is what
    stops `auto` mode treating a placeholder as something it already has and
    never calling the real API — which would quietly turn a benchmark that
    claims to be measured into one made of stand-ins.

    `ext` selects the on-disk form. Not every API answers in JSON: Nutrient's
    /build returns a PDF, and a PDF belongs on disk as a PDF that a reviewer
    can open and read, not as base64 buried in a JSON envelope. The precedence
    rules above are the part that must not be duplicated per format, so the
    extension is a parameter rather than a second copy of this function.
    """
    name = f"{fixture_key(op, payload)}{ext}"
    roots = (FIXTURE_DIR, SYNTHETIC_DIR) if synthetic_ok else (FIXTURE_DIR,)
    for d in roots:
        candidate = d / name
        if candidate.exists():
            return candidate

    # Only ever a synthetic file, and only for operations whose answer does not
    # depend on the payload. `auto` mode asks with synthetic_ok=False, so it
    # never sees this and still goes out to record the real thing.
    if synthetic_ok and op in CONTENT_INDEPENDENT:
        generic = SYNTHETIC_DIR / f"{generic_key(op)}{ext}"
        if generic.exists():
            return generic
    return None


def is_synthetic(path: Path) -> bool:
    return SYNTHETIC_DIR in path.parents


class FixtureMissing(FileNotFoundError):
    """
    Replay mode was asked for a call that has never been recorded.

    Carries the cause and the remedy apart, because they are read by different
    people. The cause — this call has no recorded response — belongs in the
    reviewer's console, where it explains why a check could not run. The
    remedy is a developer instruction; an auditor cannot act on `make seed`,
    and should not be reading it while deciding whether to authorise a
    payment.

    Still a FileNotFoundError, so existing callers that catch that keep
    working. `str()` gives both halves, which is what a terminal wants.
    """

    def __init__(self, op: str, key: str):
        self.op = op
        self.key = key
        self.cause = f"no recorded response for {op}"
        self.remedy = (f"Run `make seed`, or record with "
                       f"STRATUM_API_MODE=auto (key {key}).")
        super().__init__(f"{self.cause} — {self.remedy}")


def call(op: str, payload: Any, live_fn: Callable[[], Any]) -> Any:
    """
    Return a recorded response if we have one; otherwise call the real API,
    record it, and charge the unit budget.

    MODE=replay  never calls out. Uses a synthetic stand-in if that is all there
                 is. Raises if there is nothing — a loud failure is correct,
                 because a silent live call costs money.
    MODE=auto    recorded fixture if present, else live + record. A synthetic
                 stand-in does NOT count: `auto` exists to obtain real data, and
                 treating a placeholder as a hit means the real call never
                 happens and the recording never appears.
    MODE=live    always live.
    """
    if MODE == "replay":
        found = resolve(op, payload)
        if found is None:
            raise FixtureMissing(op, fixture_key(op, payload))
        return json.loads(found.read_text())

    if MODE == "auto":
        recorded = resolve(op, payload, synthetic_ok=False)
        if recorded is not None:
            return json.loads(recorded.read_text())

    units = UNIT_COST.get(op, 0)
    if _spent() + units > CEILING:
        raise UnitBudgetExceeded(
            f"{op} would cost {units} units; {_spent()}/{CEILING} already spent. "
            f"Raise UNIT_BUDGET_CEILING deliberately, or work in replay mode."
        )

    # Charged on attempt, not on success. Perfect Corp bills a task that errors
    # or is abandoned mid-run, so recording only successes lets the local ledger
    # drift below the real spend — the one direction a budget guard must not err.
    _record_units(op, units)
    resp = live_fn()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / f"{fixture_key(op, payload)}.json").write_text(
        json.dumps(resp, indent=2, default=str))
    return resp


def call_binary(op: str, payload: Any, live_fn: Callable[[], bytes], *,
                ext: str = ".bin", mode: str | None = None,
                record: bool = True) -> bytes:
    """
    Record/replay for an API that answers with bytes rather than JSON.

    Nutrient's /build returns a finished PDF. Squeezing that through `call`
    would mean base64 inside a JSON envelope, which defeats the one property
    that makes a fixture tree worth having: you can open the recorded response
    and see for yourself what the API actually said. A recorded attestation
    should be a PDF you can read.

    The mode rules are deliberately identical to `call` — replay never goes
    out, auto ignores synthetic stand-ins so it still records real data, live
    always goes out. They are stated twice because the two functions store
    different things, so if these ever disagree it is a bug in this file and
    not a policy difference.

    No unit accounting. `UNIT_COST` tracks Perfect Corp's metered grant, and
    charging a Nutrient call against that ceiling would let document work
    exhaust the budget guarding the sensor — two unrelated quotas sharing one
    counter, where the sensor is the one that cannot be topped up.

    `mode` lets a caller answer for itself rather than obeying the global flag.
    That flag exists to conserve a metered grant, so an integration whose grant
    is unmetered is being throttled by a rule written about someone else's
    quota. It stays an explicit argument: a caller has to state that its own
    quota is the one being managed, and the default remains the global setting.

    `record=False` is for calls whose key can never repeat. A certificate
    embeds its issue time, so every render is different bytes under a different
    key — the recording could not be served back even in principle, and writing
    one anyway leaves a PDF on disk per document issued, growing without bound
    and replayable never. Recording is for calls that can recur.
    """
    mode = mode or MODE
    if mode == "replay":
        found = resolve(op, payload, ext=ext)
        if found is None:
            raise FixtureMissing(op, fixture_key(op, payload))
        return found.read_bytes()

    if mode == "auto":
        recorded = resolve(op, payload, synthetic_ok=False, ext=ext)
        if recorded is not None:
            return recorded.read_bytes()

    resp = live_fn()
    if record:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURE_DIR / f"{fixture_key(op, payload)}{ext}").write_bytes(resp)
    return resp


def budget_status() -> dict[str, int]:
    spent = _spent()
    return {"spent": spent, "ceiling": CEILING, "remaining": CEILING - spent}
