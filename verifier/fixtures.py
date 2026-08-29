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
FIXTURE_DIR = REPO_ROOT / "fixtures"

# Synthetic fixtures live apart from recorded ones and are only consulted as a
# fallback. A real recording therefore always wins over a placeholder, and the
# synthetic tree can be gitignored and regenerated with `make seed`.
SYNTHETIC_DIR = FIXTURE_DIR / "synthetic"

UNIT_LOG = FIXTURE_DIR / "units.log"

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
    """Stable key from operation + payload, so identical calls hit the same file."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"{op}__{hashlib.sha256(blob).hexdigest()[:16]}"


def resolve(op: str, payload: Any, *, synthetic_ok: bool = True) -> Path | None:
    """
    Recorded fixture if one exists, else the synthetic stand-in, else None.

    `synthetic_ok=False` asks for recorded data only. That distinction is what
    stops `auto` mode treating a placeholder as something it already has and
    never calling the real API — which would quietly turn a benchmark that
    claims to be measured into one made of stand-ins.
    """
    name = f"{fixture_key(op, payload)}.json"
    roots = (FIXTURE_DIR, SYNTHETIC_DIR) if synthetic_ok else (FIXTURE_DIR,)
    for d in roots:
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def is_synthetic(path: Path) -> bool:
    return SYNTHETIC_DIR in path.parents


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
            raise FileNotFoundError(
                f"No fixture for {op} ({fixture_key(op, payload)}). "
                f"Run `make seed`, or record with STRATUM_API_MODE=auto."
            )
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


def budget_status() -> dict[str, int]:
    spent = _spent()
    return {"spent": spent, "ceiling": CEILING, "remaining": CEILING - spent}
