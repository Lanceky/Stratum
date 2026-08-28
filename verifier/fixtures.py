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


def call(op: str, payload: Any, live_fn: Callable[[], Any]) -> Any:
    """
    Return a recorded response if we have one; otherwise call the real API,
    record it, and charge the unit budget.

    MODE=replay  never calls out. Raises if no fixture exists — a loud failure
                 is correct here, because a silent live call costs money.
    MODE=auto    fixture if present, else live + record.
    MODE=live    always live.
    """
    path = FIXTURE_DIR / f"{fixture_key(op, payload)}.json"

    if MODE == "replay":
        if not path.exists():
            raise FileNotFoundError(
                f"No fixture for {op} at {path.name}. "
                f"Run once with STRATUM_API_MODE=auto to record it."
            )
        return json.loads(path.read_text())

    if MODE == "auto" and path.exists():
        return json.loads(path.read_text())

    units = UNIT_COST.get(op, 0)
    if _spent() + units > CEILING:
        raise UnitBudgetExceeded(
            f"{op} would cost {units} units; {_spent()}/{CEILING} already spent. "
            f"Raise UNIT_BUDGET_CEILING deliberately, or work in replay mode."
        )

    resp = live_fn()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(resp, indent=2, default=str))
    _record_units(op, units)
    return resp


def budget_status() -> dict[str, int]:
    spent = _spent()
    return {"spent": spent, "ceiling": CEILING, "remaining": CEILING - spent}
