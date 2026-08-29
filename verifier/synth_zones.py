"""
Per-zone skin profiles, for measuring what check 2 can and cannot see.

Two very different things live in this file, and the difference is the whole
argument of Step 6:

    `genuine()` encodes **anatomy**. Pore density really is highest on the nose,
    high on the forehead, and lowest on the cheek — that is the T-zone, it is
    dermatology, and it is as safe to model as the 25 mm of facial relief that
    check 1 assumes. Wrinkle likewise concentrates on the forehead and glabella.

    `deviated()` encodes **nothing at all**. We do not know how a diffusion
    model's output scores on a skincare CNN, we have no credentials to find
    out, and inventing a plausible-sounding artefact would let check 2 detect
    the artefact we invented. So the deviation is left as a free parameter and
    swept, and the result is stated as a detection limit rather than a hit rate.

That asymmetry is deliberate. `synth_attacks.py` could model attacks directly
because a flat sheet of paper obeys a theorem: its homography residual is
exactly zero. There is no equivalent theorem for "what a GAN does to a pore
score", so this module does not pretend there is one.

Nothing here is evidence about real generated faces. It is evidence about the
sensitivity of the instrument.
"""

from __future__ import annotations

import numpy as np

from dimensions import PORE_ZONES, WRINKLE_ZONES

# Anatomical baselines, on Perfect Corp's 0-100 scale where a *higher* score is
# a better complexion — so the porous nose scores LOW and the smooth cheek
# scores HIGH. Levels are provisional: they set where the population sits, and
# check 2 re-estimates them from the genuine cohort in Step 12. What matters
# here is the *ordering* and the *spacing*, which is what anatomy fixes.
PORE_LEVEL = {"forehead": 52.0, "nose": 38.0, "cheek": 68.0}
WRINKLE_LEVEL = {"forehead": 47.0, "glabella": 44.0,
                 "periorbital_left": 51.0, "periorbital_right": 51.0,
                 "nasolabial_left": 43.0, "nasolabial_right": 43.0}
LEVELS = {"pore": PORE_LEVEL, "wrinkle": WRINKLE_LEVEL}

# How much of a person's score is "this person has porous skin everywhere"
# versus "this person's nose in particular". Both are real: skin type is a
# whole-face trait, but zones vary individually. The split matters because a
# check that only saw the common part would be measuring skin type, not
# texture structure.
PERSON_SPREAD = 11.0        # between people, applied to every zone alike
ZONE_SPREAD = 6.0           # between zones within one person
CAPTURE_NOISE = 2.5         # same person, different capture — matches INTRA_NOISE

# Left/right zones are the same tissue on two sides of one face, so they move
# together. Treating them as independent would inflate the effective sample
# size and overstate check 2's power, which is the failure this constant exists
# to prevent.
BILATERAL_CORRELATION = 0.85


def _keys(dimension: str) -> tuple[str, ...]:
    return PORE_ZONES if dimension == "pore" else WRINKLE_ZONES


def _person(dimension: str, rng: np.random.Generator) -> dict[str, float]:
    """One person's true per-zone levels, before capture noise."""
    levels = LEVELS[dimension]
    common = rng.normal(0.0, PERSON_SPREAD)

    out = {}
    drawn: dict[str, float] = {}
    for zone in _keys(dimension):
        twin = (zone.replace("_left", "_right") if zone.endswith("_left")
                else zone.replace("_right", "_left"))
        if twin in drawn:
            # Correlated with its mirror zone, not redrawn independently.
            partner = drawn[twin]
            idio = (BILATERAL_CORRELATION * partner
                    + np.sqrt(1 - BILATERAL_CORRELATION ** 2)
                    * rng.normal(0.0, ZONE_SPREAD))
        else:
            idio = rng.normal(0.0, ZONE_SPREAD)
        drawn[zone] = idio
        out[zone] = levels[zone] + common + idio
    return out


def _capture(true_levels: dict[str, float],
             rng: np.random.Generator) -> dict[str, float]:
    return {z: float(np.clip(v + rng.normal(0, CAPTURE_NOISE), 1, 99))
            for z, v in true_levels.items()}


def genuine(seed: int, dimensions: tuple[str, ...] = ("pore", "wrinkle"),
            n_captures: int = 1) -> list[dict[str, float]]:
    """
    One real person, captured `n_captures` times, as flat Perfect Corp keys.

    Returns `{"hd_pore:forehead": 52.1, ...}` — the shape `SkinAnalysisResult.
    scores` produces, so the check under test never knows this was simulated.
    """
    rng = np.random.default_rng(seed)
    truth = {d: _person(d, rng) for d in dimensions}
    out = []
    for _ in range(n_captures):
        flat = {}
        for d in dimensions:
            for zone, v in _capture(truth[d], rng).items():
                flat[f"hd_{d}:{zone}"] = v
            flat[f"hd_{d}"] = float(np.mean(list(truth[d].values())))
        out.append(flat)
    return out


def deviated(seed: int, *, contrast: float = 1.0, shuffle: float = 0.0,
             dimensions: tuple[str, ...] = ("pore", "wrinkle")) -> dict[str, float]:
    """
    A face whose zone *structure* departs from anatomy by a stated amount.

    This is not a model of any generator. It is a ruler. Two knobs, each a
    distinct way the per-zone pattern could be wrong:

        contrast  scales every zone's departure from the face's own mean.
                  `contrast=0.5` halves the difference between nose and cheek
                  while leaving the average score untouched — the signature a
                  uniformly over-smoothed image would leave, if generators
                  leave it, which is exactly what we cannot yet say.
        shuffle   mixes the zone pattern towards a random permutation, i.e.
                  anatomically implausible structure at unchanged contrast.

    At `contrast=1.0, shuffle=0.0` the output is drawn from precisely the same
    distribution as `genuine`. That is deliberate: it is the null hypothesis,
    and `authenticity_report.py` uses it to check the test's false-positive
    rate is what it claims to be.
    """
    rng = np.random.default_rng(seed)
    truth = {d: _person(d, rng) for d in dimensions}

    flat = {}
    for d in dimensions:
        zones = list(truth[d])
        vals = np.array([truth[d][z] for z in zones], float)
        centre = vals.mean()
        dev = vals - centre

        if shuffle > 0:
            dev = (1 - shuffle) * dev + shuffle * rng.permutation(dev)
        dev = contrast * dev

        for zone, v in zip(zones, centre + dev):
            flat[f"hd_{d}:{zone}"] = float(
                np.clip(v + rng.normal(0, CAPTURE_NOISE), 1, 99))
        flat[f"hd_{d}"] = float(centre)
    return flat


def population(n: int = 400, dimensions: tuple[str, ...] = ("pore", "wrinkle"),
               seed: int = 20260830) -> list[dict[str, float]]:
    """A genuine reference cohort, for estimating per-zone baselines."""
    rng = np.random.default_rng(seed)
    return [genuine(int(s), dimensions)[0]
            for s in rng.integers(0, 2 ** 31, size=n)]
