"""
Synthetic cohort for testing normalisation before real captures exist.

This exists to answer one question early, because implementation.md Step 4 says
to stop and debug if the answer is wrong:

    does the same face, captured twice at different angles, land closer
    together than two different faces?

Modelling assumptions, stated plainly so the test cannot be mistaken for proof
about real skin:

  * A person has fixed STABLE dimension values. Repeat captures perturb them by
    INTRA_NOISE; different people differ by the population spread. Real intra-
    person variance is an empirical quantity measured in Step 12 — here it is
    asserted, and the ratio of the two is what the separation result depends on.
  * Facial ratios are fixed per person but re-measured each capture with
    RATIO_NOISE, because landmarks are re-detected every time. Without that they
    would be a noise-free oracle and would flatter the result.
  * VOLATILE dimensions are redrawn from the full population range on every
    capture, which is the entire reason they are excluded from identity.
  * Spot constellations are a fixed per-person pattern. Each capture sees a
    random subset (detection is not perfectly repeatable), with positional
    jitter, then an unknown camera pose: rotation, scale and translation.

The camera pose is the part that is *not* assumed away — recovering invariance
to it is real work that can genuinely fail, which is what makes the geometric
half of the separation test meaningful.
"""

from __future__ import annotations

import math

import numpy as np

from dimensions import STABLE, VOLATILE

STABLE_HD = [f"hd_{d}" for d in STABLE if d != "skin_type"]
VOLATILE_HD = [f"hd_{d}" for d in VOLATILE if d != "moisture"]

POP_MEAN, POP_STD = 55.0, 14.0
INTRA_NOISE = 2.5      # same person, different capture
RATIO_NOISE = 0.02     # relative measurement noise on each facial ratio
N_SPOTS = 90
SPOT_JITTER = 0.004    # fraction of image width
SIZE_NOISE = 0.22      # log-normal sigma on measured spot area, per capture
DETECT_RATE = 0.82     # fraction of spots found in any one capture
IMG_W, IMG_H = 1080.0, 1440.0


class Identity:
    """One synthetic person: fixed stable scores, ratios, and a spot pattern."""

    def __init__(self, seed: int):
        rng = np.random.default_rng(seed)
        self.seed = seed
        self.stable = {k: float(np.clip(rng.normal(POP_MEAN, POP_STD), 1, 99))
                       for k in STABLE_HD}
        self.ratios = {f"ratio_{i:02d}": float(rng.normal(1.0, 0.18))
                       for i in range(1, 12)}

        cx, cy = IMG_W / 2, IMG_H / 2
        theta = rng.uniform(0, 2 * math.pi, N_SPOTS)
        r = np.sqrt(rng.uniform(0, 1, N_SPOTS))
        self.spots = np.column_stack([
            cx + r * np.cos(theta) * IMG_W * 0.30,
            cy + r * np.sin(theta) * IMG_H * 0.36,
            rng.uniform(20, 400, N_SPOTS),
        ])


def capture(identity: Identity, seed: int, *, rotation_deg: float = 0.0,
            scale: float = 1.0, shift: tuple[float, float] = (0.0, 0.0)) -> dict:
    """One capture of a person under a given camera pose, in bundle shape."""
    rng = np.random.default_rng(seed)

    scores = {k: float(np.clip(v + rng.normal(0, INTRA_NOISE), 1, 99))
              for k, v in identity.stable.items()}
    # Redrawn every time. If these ever helped identity, the split would be wrong.
    scores.update({k: float(np.clip(rng.normal(POP_MEAN, POP_STD), 1, 99))
                   for k in VOLATILE_HD})

    # Ratios come from landmark positions, which are re-detected every capture,
    # so they are not reproduced exactly. Perturbing them here keeps them from
    # acting as a noise-free oracle that no real capture could supply.
    ratios = {k: float(v * (1.0 + rng.normal(0, RATIO_NOISE)))
              for k, v in identity.ratios.items()}

    keep = rng.random(len(identity.spots)) < DETECT_RATE
    pts = identity.spots[keep].copy()
    pts[:, :2] += rng.normal(0, SPOT_JITTER * IMG_W, pts[:, :2].shape)
    # Spot area is a real per-spot feature the masks carry, but it is not
    # measured perfectly twice running, so perturb it before anything uses it.
    pts[:, 2] *= np.exp(rng.normal(0, SIZE_NOISE, len(pts)))

    th = math.radians(rotation_deg)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    centre = np.array([IMG_W / 2, IMG_H / 2])
    pts[:, :2] = ((pts[:, :2] - centre) @ R.T) * scale + centre + np.array(shift)

    return {
        "source": f"synthetic_{identity.seed}_{seed}",
        "_synthetic": True,
        "scores": scores,
        "face_attributes": {"face_ratio": ratios},
        "constellations": {"hd_pore": pts.tolist()},
    }


# Deliberately awkward poses: a 5° turn was the plan's stated failure case,
# so test well past it.
POSES = [
    {"rotation_deg": 0.0, "scale": 1.00, "shift": (0.0, 0.0)},
    {"rotation_deg": 12.0, "scale": 1.22, "shift": (85.0, -60.0)},
    {"rotation_deg": -17.0, "scale": 0.81, "shift": (-70.0, 45.0)},
]


def cohort(n_people: int = 12, n_captures: int = 3) -> list[list[dict]]:
    """`n_people` identities, each captured `n_captures` times at varied poses."""
    out = []
    for p in range(n_people):
        ident = Identity(seed=1000 + p)
        out.append([capture(ident, seed=50_000 + p * 100 + c,
                            **POSES[c % len(POSES)])
                    for c in range(n_captures)])
    return out


# ── the cases the REVIEW band exists for ──────────────────────────────────
# A two-threshold decision is only honest if the middle band is reachable. These
# build the situations that put a genuine person there: evidence that is real but
# not sufficient. None of them is an impostor, and none is a clean match.

def degraded(identity: Identity, seed: int, *, detect_rate: float = 0.30,
             jitter_scale: float = 3.0, **pose) -> dict:
    """
    A genuine capture taken badly: poor light, motion, a partly occluded face.

    Few spots survive detection and those that do are poorly localised, so the
    strongest channel is left with little to work with. This is the commonest
    real reason a genuine user cannot be confidently matched, and rejecting it
    outright would mean rejecting people for holding the phone wrong.
    """
    rng = np.random.default_rng(seed)
    keep = rng.random(len(identity.spots)) < detect_rate
    pts = identity.spots[keep].copy()
    pts[:, :2] += rng.normal(0, SPOT_JITTER * IMG_W * jitter_scale, pts[:, :2].shape)
    pts[:, 2] *= np.exp(rng.normal(0, SIZE_NOISE * 1.5, len(pts)))

    base = capture(identity, seed, **pose)
    base["constellations"]["hd_pore"] = pts.tolist()
    base["source"] += "_degraded"
    return base


def changed_appearance(identity: Identity, seed: int, *,
                       drift: float = 3.0, **pose) -> dict:
    """
    The same person, months later: tanned, heavier, older, differently medicated.

    The spot pattern is intact — moles do not rearrange — but the stable skin
    scores have moved well beyond capture noise. Exactly the case where one
    channel says yes and another says no, which is what a REVIEW is for.
    """
    out = capture(identity, seed, **pose)
    rng = np.random.default_rng(seed + 7)
    shift = rng.normal(0, INTRA_NOISE * drift, len(out["scores"]))
    for (k, v), s in zip(sorted(out["scores"].items()), shift):
        out["scores"][k] = float(np.clip(v + s, 1, 99))
    out["source"] += "_changed"
    return out


def sibling(identity: Identity, seed: int, **pose) -> dict:
    """
    A close relative: similar face geometry and skin, independent spot pattern.

    Moles form stochastically in development, so even identical twins do not
    share them. This is the case where the weak channels agree and the strong
    one does not — the opposite shape of disagreement to `changed_appearance`,
    and a useful test that fusion is not just reading one channel.
    """
    rng = np.random.default_rng(seed + 11)
    rel = Identity(seed=identity.seed + 500_000)
    rel.ratios = {k: float(v * (1.0 + rng.normal(0, 0.03)))
                  for k, v in identity.ratios.items()}
    rel.stable = {k: float(np.clip(v + rng.normal(0, INTRA_NOISE * 1.5), 1, 99))
                  for k, v in identity.stable.items()}
    out = capture(rel, seed, **pose)
    out["source"] += "_sibling"
    return out
