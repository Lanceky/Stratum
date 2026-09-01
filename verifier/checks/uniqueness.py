"""
Check 4: has this person already claimed?

Checks 1-3 answer "is a real, live, enrolled person here?" — a 1:1 question,
asked against one enrolment the caller names. Sybil resistance asks a different
one: *has this face already been seen anywhere in this campaign?* That is 1:N,
and it is not the same problem wearing a different hat.

Two things change when you sweep a roster instead of comparing a pair.

**The error that matters flips sides.** In 1:1 binding, the dangerous mistake is
a false accept: a stranger passes as the enrolled person and moves money. Here
the dangerous mistake is a false *match* — an honest newcomer collides with a
stranger already on the roster and is turned away from an allocation they were
entitled to. Check 3 is tuned so that no honest capture is ever auto-rejected;
the same instinct applies here, pointed the other way.

**The error rate grows with the roster.** Every enrolment is another chance to
collide. At a per-comparison false-match probability p, a sweep of N gives
1-(1-p)^N, which is roughly N*p while that stays small. A rate that is
negligible at N=10 is not negligible at N=10,000, and a system that reports the
same confidence at both sizes is not measuring the thing it claims to measure.

So the roster size is part of the verdict here, not context around it. When the
sweep is large enough that a false match stops being unlikely, this check stops
being allowed to auto-decide a duplicate and says so — which is a decision
STRATUM can afford to make because it has somewhere to put an unresolved
question, and most proof-of-personhood systems do not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from checks.binding import (LOWER_THRESHOLD, UPPER_THRESHOLD, BindingResult,
                            Calibration, evaluate)

VERDICTS = ("UNIQUE", "DUPLICATE", "REVIEW")

# Rule-of-three 95% upper bound: zero different-person comparisons landed below
# LOWER_THRESHOLD in 1300 trials on the synthetic cohort (26 identities, all
# ordered pairs, impostors and siblings), so p <= 3/1300. Derived rather than
# typed, so rounding cannot quietly make the published bound tighter than the
# measurement supports. test_uniqueness.py re-runs the measurement.
#
# Provisional in exactly the way check 3's calibration is provisional: the
# cohort is synthetic, identities differ by construction, and what has been
# measured is the matcher rather than whether real faces separate.
FMR_TRIALS = 1300
PER_COMPARISON_FMR = 3 / FMR_TRIALS

# Above this family-wise probability, a DUPLICATE is a question rather than a
# finding. One in a hundred honest claimants wrongly turned away is already a
# poor trade for a campaign that could instead have paid a human to look.
AUTO_DECIDE_MAX_FMR = 0.01

LIMITATIONS = [
    "No real skin has been scored. The false-match bound comes from a "
    "synthetic cohort whose identities differ by construction, so it measures "
    "the matcher and not whether real faces separate.",
    "The bound is an upper limit from zero observed false matches in "
    f"{FMR_TRIALS} comparisons, not a measured rate. The true rate could be "
    "anywhere below it, including much lower.",
    "A sweep only covers enrolments that could be compared. Someone who "
    "enrolled with a capture this one shares no comparable signal with is not "
    "ruled out by a clean sweep, and is reported rather than assumed away.",
    "Uniqueness is scoped to one context. The same person claiming in two "
    "different campaigns is two unique claims by design, and this check makes "
    "no attempt to detect that.",
]


def family_false_match(n_comparisons: int, p: float = PER_COMPARISON_FMR) -> float:
    """
    The chance that *some* honest sweep of this size produces a false match.

    1-(1-p)^n rather than n*p: the linear form is a good approximation while
    n*p is small and an embarrassing one when it is not, since it climbs past
    1.0 and would report a probability above certainty.
    """
    if n_comparisons <= 0:
        return 0.0
    return 1.0 - (1.0 - p) ** n_comparisons


@dataclass
class Match:
    """One roster entry, and how far the probe sat from it."""

    enrolment_id: str
    distance: float
    binding_verdict: str
    compared: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {"enrolment_id": self.enrolment_id,
                "distance": round(self.distance, 3) if self.compared else None,
                "binding_verdict": self.binding_verdict,
                "compared": self.compared, "note": self.note}


@dataclass
class UniquenessResult:
    verdict: str
    roster_size: int
    compared: int
    skipped: list[Match]
    nearest: Match | None
    detail: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """UNIQUE only. A duplicate is a finding, not a pass, and REVIEW is a question."""
        return self.verdict == "UNIQUE"

    @property
    def ran(self) -> bool:
        """
        An empty roster is a sweep that ran and found nothing, not a sweep that
        could not run: the first claimant in a context is unique because there
        is nobody to collide with, which is a real answer.
        """
        return self.compared > 0 or self.roster_size == 0

    @property
    def reason(self) -> str:
        return self.detail.get("reason", "")

    @property
    def score(self) -> float:
        """
        1.0 when the nearest enrolment is far away, falling to 0.0 as it closes.

        Deliberately the inverse of check 3's score. There a small distance is
        the good outcome; here it is the one that costs someone their claim.
        """
        if not self.ran:
            return 0.0
        if self.nearest is None or not self.nearest.compared:
            return 1.0
        d = min(self.nearest.distance, UPPER_THRESHOLD)
        return float(max(0.0, min(1.0, d / UPPER_THRESHOLD)))

    def as_dict(self) -> dict:
        return {"check": 4, "name": "uniqueness", "verdict": self.verdict,
                "passed": self.passed, "ran": self.ran,
                "reason": self.reason, "score": round(self.score, 4),
                "roster_size": self.roster_size,
                "comparisons_run": self.compared,
                "comparisons_skipped": len(self.skipped),
                "nearest": self.nearest.as_dict() if self.nearest else None,
                "thresholds": {"duplicate_below": LOWER_THRESHOLD,
                               "unique_above": UPPER_THRESHOLD},
                "false_match": {
                    "per_comparison_bound": PER_COMPARISON_FMR,
                    "across_this_sweep": round(
                        family_false_match(self.compared), 5),
                    "auto_decide_ceiling": AUTO_DECIDE_MAX_FMR,
                },
                "limitations": LIMITATIONS, "detail": self.detail}


def _empty(roster_size: int) -> UniquenessResult:
    return UniquenessResult(
        "UNIQUE", roster_size, 0, [], None,
        {"reason": "no enrolment on the roster to compare against — the first "
                   "claim in a context is unique because there is nobody yet "
                   "for it to collide with"})


def sweep(probe: dict, roster: list[tuple[str, dict]], *,
          calibration: Calibration | None = None,
          per_comparison_fmr: float = PER_COMPARISON_FMR) -> UniquenessResult:
    """
    Compare one probe capture against every enrolment in a context.

    `roster` is (enrolment_id, normalised bundle) pairs — already normalised,
    so this does no network work and no parsing, exactly like check 3.

    Every comparison is check 3's `evaluate`. This function does not invent a
    second notion of identity distance; it decides what a *set* of check 3
    distances means, which is the only part that is new.
    """
    if not roster:
        return _empty(0)

    matches: list[Match] = []
    for enrolment_id, bundle in roster:
        result: BindingResult = evaluate(bundle, probe, calibration=calibration)
        matches.append(Match(
            enrolment_id=enrolment_id,
            distance=result.distance,
            binding_verdict=result.verdict,
            # A comparison that produced no usable channel has not cleared this
            # enrolment. Treating "could not compare" as "did not match" is how
            # a sweep quietly reports a uniqueness it never established.
            compared=result.ran,
            note="" if result.ran else result.reason,
        ))

    compared = [m for m in matches if m.compared]
    skipped = [m for m in matches if not m.compared]

    if not compared:
        return UniquenessResult(
            "REVIEW", len(roster), 0, skipped, None,
            {"reason": f"none of the {len(roster)} enrolments on the roster "
                       "could be compared with this capture, so nothing about "
                       "whether this person has claimed before was established",
             "skipped": [m.as_dict() for m in skipped]})

    nearest = min(compared, key=lambda m: m.distance)
    family = family_false_match(len(compared), per_comparison_fmr)
    detail: dict = {
        "skipped": [m.as_dict() for m in skipped],
        "family_false_match": round(family, 5),
    }

    if nearest.distance < LOWER_THRESHOLD:
        if family > AUTO_DECIDE_MAX_FMR:
            # The sweep is too wide for this finding to stand on its own. Said
            # in full rather than downgraded silently, because the reviewer is
            # being asked precisely because the number stopped being safe.
            detail["reason"] = (
                f"this capture matches enrolment {nearest.enrolment_id} at "
                f"{nearest.distance:.2f}, inside the duplicate band, but the "
                f"roster is large enough ({len(compared)} comparisons) that "
                f"roughly {family:.1%} of honest newcomers would collide with "
                f"somebody by chance — above the {AUTO_DECIDE_MAX_FMR:.0%} "
                "ceiling for deciding this without a person")
            return UniquenessResult("REVIEW", len(roster), len(compared),
                                    skipped, nearest, detail)
        detail["reason"] = (
            f"this capture matches enrolment {nearest.enrolment_id} at "
            f"{nearest.distance:.2f}, below the {LOWER_THRESHOLD} duplicate "
            f"threshold; across {len(compared)} comparisons the chance of that "
            f"happening to an honest newcomer is about {family:.2%}")
        return UniquenessResult("DUPLICATE", len(roster), len(compared),
                                skipped, nearest, detail)

    if nearest.distance <= UPPER_THRESHOLD:
        detail["reason"] = (
            f"the closest enrolment ({nearest.enrolment_id}) sits at "
            f"{nearest.distance:.2f}, inside the band where a badly-registered "
            f"capture of a new person and a genuine repeat claim are not "
            "separable — no threshold splits them, so a person decides")
        return UniquenessResult("REVIEW", len(roster), len(compared),
                                skipped, nearest, detail)

    if skipped:
        detail["reason"] = (
            f"no match among the {len(compared)} enrolments that could be "
            f"compared, but {len(skipped)} could not be compared at all, so "
            "this claim has not been shown to be the first one")
        return UniquenessResult("REVIEW", len(roster), len(compared),
                                skipped, nearest, detail)

    detail["reason"] = (
        f"no enrolment came closer than {nearest.distance:.2f}, clear of the "
        f"{UPPER_THRESHOLD} threshold, across all {len(compared)} on the roster")
    return UniquenessResult("UNIQUE", len(roster), len(compared),
                            skipped, nearest, detail)
