"""
Capture analysis — image bytes in, derived record out.

This is the narrow waist between the camera and the checks. Everything above it
handles photographs; nothing below it ever does. `checks/presence.py` and
`checks/authenticity.py` read dictionaries of scores and constellations, and
this module is the only thing in the service that turns pixels into one.

The asymmetry is deliberate and is the privacy claim in code rather than in
prose: `analyse` takes `bytes` and returns a dict that cannot reconstruct them.
The bytes are never written to disk, never logged, and never returned. The
`captures` table has no column that could hold them (schema.py), so there is no
accident available to make.

Same four Perfect Corp calls as `pipeline.py`, and deliberately through the same
`fixtures` layer, so a recorded session replays offline for free. The difference
is that `pipeline.py` is a developer tool that writes a bundle to disk, and this
runs inside a request that must not.
"""

from __future__ import annotations

from pathlib import Path

import masks as masklib
import perfectcorp as pc
from fixtures import UnitBudgetExceeded

REPO_ROOT = Path(__file__).resolve().parent.parent
MASK_CACHE = REPO_ROOT / "fixtures" / "masks"

# The constellation is a point set, not an image, but a dense enough one starts
# to describe a face. 64 is what pipeline.py has always used and what the
# registration in normalise.py is tuned for; matching it keeps recorded fixtures
# and live captures comparable.
MAX_POINTS = 64


class SensorUnavailable(RuntimeError):
    """
    The skin sensor could not be reached for this frame.

    A distinct type because the caller must not treat it as a failed check. No
    credentials, an expired fixture and a rate limit are all *absence of
    evidence*; presence has not been refuted, it has not been examined. Check 1
    is recorded as `ran=false` and fusion sends the gate to a human. Collapsing
    this into a failure would be a lie in the safe direction, which is still a
    lie and would teach reviewers to ignore the queue.
    """


def analyse(data: bytes, name: str, *, hd: bool = True,
            mask_dir: Path | None = None) -> dict:
    """
    One frame: upload, score, fetch masks, reduce masks to a point set.

    `mask_dir` is per-capture rather than global. Masks are cached by concern
    name, so two frames of the same gate sharing a directory would have the
    second silently read the first's mask and every frame would look identical
    — which is precisely the signal check 1 measures.
    """
    dest = mask_dir or MASK_CACHE
    try:
        upload = pc.request_upload(name, len(data))
        file_id = pc.upload_bytes(upload, data)
        skin = pc.skin_analysis(file_id, hd=hd)
        mask_paths = pc.download_masks(skin, dest)
    except (FileNotFoundError, pc.PerfectCorpError, UnitBudgetExceeded,
            OSError, KeyError) as exc:
        # `cause` where the exception offers one, so the sentence a reviewer
        # reads names what went wrong without the runbook aimed at whoever
        # deploys this. The exception type is kept either way: "which sensor
        # failed and how" is the difference between a lapsed credential and
        # somebody holding the API down while they try something.
        detail = getattr(exc, "cause", None) or str(exc)
        raise SensorUnavailable(f"{type(exc).__name__}: {detail}") from exc

    constellations = {}
    for concern, path in mask_paths.items():
        pts = masklib.centroids(masklib.decode_file(path))[:MAX_POINTS]
        constellations[concern] = pts.tolist()

    return {
        "scores": skin.scores,
        "constellations": constellations,
        "pc_task_id": skin.task_id,
        "hd": hd,
    }


def frame_record(derived: dict, index: int, captured_at: float) -> dict:
    """
    Shape a derived capture into the frame dict `checks/presence.py` reads.

    Kept here rather than inlined at the call site because the key names are a
    contract between two modules that never import each other, and a typo in
    `frame_index` would not fail — it would fall back to positional order and
    quietly score the wrong frames against each other.
    """
    return {
        "frame_index": index,
        "captured_at": captured_at,
        "scores": derived.get("scores", {}),
        "constellations": derived.get("constellations", {}),
    }
