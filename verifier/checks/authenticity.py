"""
Check 2 — authenticity. Are these pixels camera-native, or generated?

The hypothesis, stated before it is tested (context.md §4, implementation.md
Step 6): a generated face diverges from a real one more in the *structure* of
its per-zone texture scores than in their average. An over-smoothed synthesis
should flatten the difference between a porous nose and a smooth cheek while
leaving the mean roughly intact, so the mean is the one statistic worth
ignoring.

Two independent questions are asked, because they catch different forgeries and
neither subsumes the other (measured — see `authenticity_report.py`):

    contrast     Is there *enough* structure? A real face has a T-zone: the
                 nose is porous, the cheek is not. Uniform smoothing flattens
                 that. Tested in the lower tail — too little structure.
    zone_pattern Is the structure in the *right places*? A face can have
                 plenty of variation across zones and still put it somewhere
                 anatomically impossible. Tested in the upper tail — too far
                 from the population's zone pattern.

Halving every zone's departure from its face mean is caught 79% of the time by
`contrast` and 1% by `zone_pattern`. Scrambling which zone holds which value is
caught 81% by `zone_pattern` and 4% by `contrast`. One statistic would have
missed whichever failure it was not shaped for.

Three things this check does that a naive version would get wrong:

  * **It normalises per zone, not against a pooled baseline.** A face's zone
    scores are not draws from one distribution. The nose is anatomically more
    porous than the cheek in everyone, so testing {forehead, nose, cheek}
    against a pooled genuine baseline rejects *real* faces for having a normal
    T-zone. Each zone is compared against its own population.
  * **It calibrates on the empirical null, not a textbook one.** These
    statistics are neither normal nor independent, so a chi-square with an
    assumed number of degrees of freedom would not hold its false-positive
    rate. Each threshold is a quantile of the statistic's own distribution over
    genuine faces, which is exact by construction.
  * **It calibrates on faces it did not fit on.** The zone means and standard
    deviations are estimated on one half of the reference cohort and the null
    distribution measured on the other. Sharing them inflates the apparent
    threshold and quietly costs about two points of false-positive rate, since
    every face contributes to the mean it is then compared against.

⚠️ **What this check has NOT been shown to do.** Every number it produces has
been measured against `synth_zones.py`, whose genuine model encodes anatomy and
whose "deviated" model encodes *nothing about real generators* — it is a ruler
with a knob, not a forgery. We have no Perfect Corp credentials, so no synthetic
face and no face-swap has ever been scored. What is established here is the
instrument's **detection limit**: the smallest structural deviation it can see.
Whether real generators exceed that limit is unknown, and `authenticity_report.
py` prints the limit rather than a hit rate for exactly that reason.

Checks 1 and 3 do not depend on this one. If Step 12 shows real generators sit
below the detection limit, check 2 is reported as a negative result and the
system still stands on presence and binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dimensions import ZONED, base, region

# The share of genuine faces the check is willing to flag. Set as policy, not
# fitted: flagging 5% of honest users is already expensive, and check 2 escalates
# to REVIEW rather than rejecting, so the cost is a reviewer's minute.
#
# The budget is split across the two tests rather than spent twice. Two
# one-sided tests at 5% each would flag nearly 10% of genuine faces, which is
# the multiple-comparisons mistake that makes a forensic tool look decisive
# while it is merely trigger-happy.
TARGET_FLAG_RATE = 0.05

# Fraction of the reference cohort used to estimate zone baselines; the rest
# calibrates the null.
FIT_SHARE = 0.5

# Below this many usable zones the shape statistics are noise. Three points
# cannot describe a distribution's shape, and pretending otherwise is how a
# check ends up flagging on nothing.
MIN_ZONES = 3

LIMITATIONS = [
    "No generated face has been scored. Perfect Corp credentials have not "
    "arrived, so neither a diffusion sample nor an ai_face_swap output has "
    "passed through this check. The figures describe the instrument's "
    "sensitivity, not its hit rate against real forgeries.",
    "The check reports a detection limit: the smallest per-zone structural "
    "deviation it can distinguish from a genuine face. Whether real generators "
    "exceed that limit is the open question, and it is Step 12's to settle.",
    "SD captures carry no per-zone breakdown, so this check declines to run on "
    "them rather than returning a pass it did not earn.",
]


@dataclass
class ZoneProfile:
    """One face's per-zone scores for one dimension, and their shape."""

    dimension: str
    zones: tuple[str, ...]
    values: np.ndarray

    @property
    def level(self) -> float:
        """Central tendency — deliberately not used as evidence."""
        return float(np.mean(self.values))

    @property
    def deviations(self) -> np.ndarray:
        """Each zone's departure from this face's own mean."""
        return self.values - self.level

    @property
    def contrast(self) -> float:
        """
        How differentiated the zones are. A T-zone is a real anatomical feature,
        so a genuine face has structure here; a uniformly smoothed one has less.
        """
        return float(np.std(self.values, ddof=1)) if len(self.values) > 1 else 0.0

    def ordering(self, reference: np.ndarray) -> float:
        """
        Rank agreement between this face's zone pattern and the population's.

        Spearman by hand — scipy is available, but the tie-free case is three
        lines and avoids a dependency in the hot path. Measures whether the
        *pattern* is anatomically plausible, independent of its magnitude.
        """
        if len(self.values) < 3:
            return 0.0
        a = np.argsort(np.argsort(self.values)).astype(float)
        b = np.argsort(np.argsort(reference)).astype(float)
        a -= a.mean()
        b -= b.mean()
        denom = np.sqrt((a ** 2).sum() * (b ** 2).sum())
        return float((a * b).sum() / denom) if denom > 0 else 0.0


def profiles(scores: dict[str, float]) -> dict[str, ZoneProfile]:
    """
    Pull the per-zone breakdowns out of a flat Perfect Corp score dict.

    Only dimensions with a full set of zones are returned. A partial set would
    change which zones the statistic is computed over from one capture to the
    next, and a statistic whose support moves is not comparable to a baseline.
    """
    out = {}
    for dimension, zones in ZONED.items():
        found = {}
        for key, value in scores.items():
            zone = region(key)
            if zone and base(key) == dimension and zone in zones:
                found[zone] = float(value)
        if len(found) == len(zones):
            out[dimension] = ZoneProfile(
                dimension, zones, np.array([found[z] for z in zones], float))
    return out


@dataclass
class Signal:
    """One directional test, with the direction fixed before any data was seen."""

    name: str
    statistic: float
    p_value: float
    passed: bool
    question: str

    def as_dict(self) -> dict:
        return {"name": self.name, "statistic": round(self.statistic, 4),
                "p_value": round(self.p_value, 4), "passed": self.passed,
                "question": self.question}


# Each statistic, the tail that counts as suspicious, and what it is asking.
# The direction is part of the hypothesis, not something chosen after looking
# at which tail happened to separate.
TESTS = {
    "contrast": ("lower", "is there enough texture structure?"),
    "zone_pattern": ("upper", "is the structure anatomically plausible?"),
}


def _contrast(profile: ZoneProfile, zone_mean: np.ndarray,
              zone_std: np.ndarray) -> float:
    """
    Total spread across zones, in raw score points.

    Deliberately *not* normalised by the per-zone population spread. That
    spread is dominated by how porous the person is overall, which is common to
    every zone and cancels out of a within-face contrast; dividing by it buries
    the signal under between-person variation.
    """
    return float(np.std(profile.values, ddof=1))


def _zone_pattern(profile: ZoneProfile, zone_mean: np.ndarray,
                  zone_std: np.ndarray) -> float:
    """
    How far this face's zone pattern sits from the population's, in z units.

    Each zone is standardised against its own population before the face's own
    level is removed, so what remains is the *shape* of the profile with the
    person's overall skin quality divided out.
    """
    z = (profile.values - zone_mean) / zone_std
    return float(((z - z.mean()) ** 2).sum())


STATISTICS = {"contrast": _contrast, "zone_pattern": _zone_pattern}


@dataclass
class Baseline:
    """
    What genuine faces look like, per zone and per statistic.

    Fitted from a reference cohort rather than hard-coded, because every number
    in it is a property of the population and the camera, not of the maths.
    Step 12 refits this on the genuine set and nothing else has to change.
    """

    zone_mean: dict[str, np.ndarray]
    zone_std: dict[str, np.ndarray]
    null: dict[str, np.ndarray]         # per test, sorted, over held-out faces
    n_fit: int
    n_null: int
    dimensions: tuple[str, ...]

    def cut(self, test: str, flag_rate: float = TARGET_FLAG_RATE) -> float:
        """The value a genuine face exceeds only `flag_rate / n_tests` of the time."""
        alpha = flag_rate / len(TESTS)
        side, _ = TESTS[test]
        q = alpha if side == "lower" else 1.0 - alpha
        return float(np.quantile(self.null[test], q))

    def p_value(self, test: str, value: float) -> float:
        """Share of genuine faces at least this extreme, in the declared tail."""
        null = self.null[test]
        side, _ = TESTS[test]
        if side == "lower":
            return float((np.sum(null <= value) + 1) / (len(null) + 1))
        return float((np.sum(null >= value) + 1) / (len(null) + 1))

    def as_dict(self) -> dict:
        return {"fitted_on": self.n_fit, "calibrated_on": self.n_null,
                "dimensions": list(self.dimensions),
                "cuts": {t: round(self.cut(t), 4) for t in TESTS},
                "flag_rate": TARGET_FLAG_RATE,
                "per_test_alpha": TARGET_FLAG_RATE / len(TESTS),
                "provisional": True}


def _pool(parsed: list[dict[str, ZoneProfile]], dimensions: tuple[str, ...],
          zone_mean: dict, zone_std: dict, test: str) -> np.ndarray:
    """One statistic, summed over dimensions, for every face in a cohort."""
    fn = STATISTICS[test]
    return np.array([
        float(sum(fn(p[d], zone_mean[d], zone_std[d]) for d in dimensions))
        for p in parsed if all(d in p for d in dimensions)], float)


def fit(cohort: list[dict[str, float]]) -> Baseline:
    """
    Estimate per-zone baselines on one half of the cohort, calibrate on the other.

    The split is what makes the stated flag rate honest. Fitting and calibrating
    on the same faces makes each face slightly closer to "average" than a new
    face will be, so the threshold comes out too tight and the check flags more
    genuine users than it advertises.
    """
    parsed = [profiles(s) for s in cohort]
    dimensions = tuple(sorted({d for p in parsed for d in p}))
    if not dimensions:
        raise ValueError("cohort carries no per-zone breakdowns to fit against")

    complete = [p for p in parsed if all(d in p for d in dimensions)]
    split = max(1, int(len(complete) * FIT_SHARE))
    fit_set, null_set = complete[:split], complete[split:]
    if not null_set:
        raise ValueError("cohort too small to calibrate on faces it was not fitted on")

    zone_mean, zone_std = {}, {}
    for d in dimensions:
        stack = np.array([p[d].values for p in fit_set], float)
        zone_mean[d] = stack.mean(axis=0)
        zone_std[d] = np.maximum(stack.std(axis=0, ddof=1), 1e-6)

    null = {t: np.sort(_pool(null_set, dimensions, zone_mean, zone_std, t))
            for t in TESTS}
    return Baseline(zone_mean, zone_std, null,
                    len(fit_set), len(null_set), dimensions)


@dataclass
class AuthenticityResult:
    passed: bool
    ran: bool
    signals: list[Signal]
    detail: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Weakest signal, scaled so 1.0 is a typical genuine face."""
        if not self.ran:
            return 0.0
        alpha = TARGET_FLAG_RATE / len(TESTS)
        return float(min(np.clip(s.p_value / alpha, 0.0, 1.0)
                         for s in self.signals))

    @property
    def flagged_by(self) -> list[str]:
        return [s.name for s in self.signals if not s.passed]

    def as_dict(self) -> dict:
        return {"check": 2, "name": "authenticity", "passed": self.passed,
                "ran": self.ran, "score": round(self.score, 4),
                "flagged_by": self.flagged_by,
                "signals": [s.as_dict() for s in self.signals],
                "limitations": LIMITATIONS, "detail": self.detail}


def evaluate(scores: dict[str, float], baseline: Baseline, *,
             flag_rate: float = TARGET_FLAG_RATE) -> AuthenticityResult:
    """
    Is this face's per-zone texture structurally like a real one?

    Each `p_value` is the share of genuine faces at least this extreme in the
    declared tail. It is a calibrated false-positive rate, not a probability
    that the face is fake — those are different quantities, and conflating them
    is how a forensic tool overstates itself.

    `ran=False` means the capture carried no usable zone breakdown. That is not
    a pass. Check 3's fusion has to treat a check that could not run differently
    from one that ran and was satisfied, because absence of evidence is not
    evidence.
    """
    found = profiles(scores)
    usable = tuple(d for d in baseline.dimensions
                   if d in found and len(found[d].values) >= MIN_ZONES)
    if usable != baseline.dimensions:
        return AuthenticityResult(
            False, False, [],
            {"reason": "capture lacks the per-zone breakdown this baseline was "
                       "built on — SD mode, or a partial HD result",
             "found": list(usable), "needed": list(baseline.dimensions)})

    signals, per_dimension = [], {}
    for test, (side, question) in TESTS.items():
        fn = STATISTICS[test]
        value = float(sum(fn(found[d], baseline.zone_mean[d],
                             baseline.zone_std[d]) for d in usable))
        cut = baseline.cut(test, flag_rate)
        passed = bool(value >= cut if side == "lower" else value <= cut)
        signals.append(Signal(test, value, baseline.p_value(test, value),
                              passed, question))

    for d in usable:
        z = (found[d].values - baseline.zone_mean[d]) / baseline.zone_std[d]
        per_dimension[d] = {
            "zone_z": {zone: round(float(v), 2)
                       for zone, v in zip(found[d].zones, z)},
            "contrast": round(float(np.std(found[d].values, ddof=1)), 3),
            "level": round(found[d].level, 2),
        }

    return AuthenticityResult(
        all(s.passed for s in signals), True, signals,
        {"dimensions_used": list(usable),
         "baseline": baseline.as_dict(),
         "per_dimension": per_dimension})
