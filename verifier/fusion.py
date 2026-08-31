"""
Fusion — three checks, one verdict, and an explicit account of what is unknown.

Checks 1, 2 and 3 answer different questions, and the temptation is to average
their scores into a confidence number. That is the wrong shape. A weighted mean
lets a strong result on one check compensate for a failure on another, and these
checks are not substitutes:

    check 1  presence      was a live human in front of a real camera?
    check 2  authenticity  were those pixels camera-native?
    check 3  binding       was it *this* human?

A perfect identity match on an injected video stream is not two-thirds of an
authorisation. It is a fraud with good lighting. So presence and binding are
combined as a **conjunction** — every one must be satisfied — and the score is
reported alongside the verdict rather than being the thing that decides it.

Three outcomes, because two are not enough:

    PASS    every check ran and was satisfied
    REVIEW  the evidence is real but does not settle the question
    FAIL    a check ran and was violated

The distinction that matters most is between *failed* and *did not run*. A check
that could not run has produced no evidence, and treating that as a pass turns
every capture degradation into a bypass: ask for SD analysis and check 2 vanishes
silently. So an absent check sends the gate to REVIEW, never to PASS.

The same holds one step further out. A check that ran against a seeded stand-in
produced real numbers about a fabricated face, and those are not evidence about
the person at the camera. Such a result reaches neither PASS nor FAIL — passing
on a placeholder is the obvious hazard, but failing on one accuses a live human
of flunking a check that never looked at them.

REVIEW is a first-class outcome. Both failure directions cost real money and
they are not symmetric — wrongly blocking a signer stalls a transaction, wrongly
passing a fraudster moves the money — so the cases that the evidence does not
settle are routed to a person instead of being forced through a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PASS, REVIEW, FAIL = "PASS", "REVIEW", "FAIL"

# Reported, not used to decide. Presence and binding carry the weight because
# they are the two checks with measured operating points; authenticity is known
# to be the weakest and its own report says so.
SCORE_WEIGHTS = {"presence": 0.40, "binding": 0.45, "authenticity": 0.15}

# Every check must be accounted for before a gate can pass — including check 2,
# which frequently cannot run on SD captures.
#
# The tempting shortcut is to leave authenticity out of this list, on the
# grounds that it is optional. That creates a bypass, because omitting a check
# then produces a better outcome than honestly reporting that it did not run:
# the caller is rewarded for staying quiet. Both routes lead to REVIEW instead.
#
# Note this is a *reporting* requirement, not an execution one. Check 2 saying
# "I could not run" is a perfectly acceptable submission; it costs the gate an
# automatic PASS but never fails it.
REQUIRED = ("presence", "authenticity", "binding")


def _reason_of(payload: dict) -> str:
    """
    A check's own words for why it decided what it decided.

    Read from the top level first, then from a nested `detail`. The check
    endpoints in app.py return `reason` at the top level, so reading only
    `detail` silently dropped the specific finding and left the reviewer with
    generic boilerplate — the one field a human actually needs, lost for the
    exact payload shape this system produces.
    """
    top = payload.get("reason")
    if isinstance(top, str) and top.strip():
        return top
    nested = (payload.get("detail") or {}).get("reason", "")
    return nested if isinstance(nested, str) else ""


@dataclass
class CheckOutcome:
    """
    One check's contribution, reduced to the three facts fusion needs.

    `ran` and `passed` are separate on purpose. Collapsing them into a single
    boolean is the bug that lets a check which never executed look like a check
    which executed and was happy.
    """

    name: str
    ran: bool
    passed: bool
    score: float
    verdict: str | None = None      # check 3 reports its own three-way verdict
    reason: str = ""
    limitations: list[str] = field(default_factory=list)
    synthetic: bool = False

    @classmethod
    def from_result(cls, name: str, payload: dict) -> "CheckOutcome":
        return cls(
            name=name,
            ran=bool(payload.get("ran", True)),
            passed=bool(payload.get("passed", False)),
            score=float(payload.get("score", 0.0)),
            verdict=payload.get("verdict"),
            reason=_reason_of(payload),
            limitations=[str(x) for x in (payload.get("limitations")
                                          or (payload.get("detail") or {}).get(
                                              "limitations") or [])],
            synthetic=bool(payload.get("synthetic")),
        )

    def as_dict(self) -> dict:
        return {"name": self.name, "ran": self.ran, "passed": self.passed,
                "score": round(self.score, 4), "verdict": self.verdict,
                "reason": self.reason, "limitations": self.limitations,
                "synthetic": self.synthetic}


@dataclass
class Decision:
    verdict: str
    outcomes: list[CheckOutcome]
    reasons: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """
        A weighted mean over the checks that ran — reported, never decisive.

        Present so a reviewer can sort a queue and a demo can show a number.
        The verdict above it is a conjunction, so a high score cannot rescue a
        violated check.
        """
        live = [o for o in self.outcomes if o.ran]
        if not live:
            return 0.0
        total = sum(SCORE_WEIGHTS.get(o.name, 0.0) for o in live)
        if total <= 0:
            return 0.0
        return round(sum(SCORE_WEIGHTS.get(o.name, 0.0) * o.score
                         for o in live) / total, 4)

    @property
    def requires_human(self) -> bool:
        return self.verdict == REVIEW

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "score": self.score,
                "requires_human": self.requires_human,
                "reasons": self.reasons,
                "checks": [o.as_dict() for o in self.outcomes]}


def fuse(results: dict[str, dict]) -> Decision:
    """
    Combine check results into one verdict.

    `results` maps check name to that check's `as_dict()` payload, so callers
    hand over exactly what the endpoints already return and nothing has to be
    reshaped at the boundary.

    Order of judgement matters and is deliberate: a violated check outranks a
    missing one, which outranks an unsettled one. Reporting "check 2 could not
    run" on a gate where presence was actively failed would bury the finding
    that matters under the one that does not.
    """
    outcomes = [CheckOutcome.from_result(name, payload)
                for name, payload in sorted(results.items())]
    by_name = {o.name: o for o in outcomes}
    reasons: list[str] = []

    # A check that ran on a seeded stand-in rather than on the sensor. It is
    # not absent — it produced real numbers, and they are real numbers about a
    # fabricated face. Pulled out before anything else because such a result
    # must reach neither PASS nor FAIL: passing on a placeholder is the
    # obvious hazard, but failing on one accuses the person at the camera of
    # flunking a check that never looked at them, and this system does not get
    # to call that a safe direction.
    standins = [o for o in outcomes if o.ran and o.synthetic]

    violated = [o for o in outcomes if o.ran and not o.passed
                and o.verdict != REVIEW and not o.synthetic]
    for o in violated:
        reasons.append(f"{o.name}: {o.reason or 'check ran and was not satisfied'}")

    missing = [name for name in REQUIRED if name not in by_name]
    absent = [o for o in outcomes if not o.ran]
    # A check that did not run is already accounted for above. Without this it
    # lands in both lists and is reported twice — once as boilerplate and once
    # with the finding that actually matters, which trains a reviewer to skim.
    unsettled = [o for o in outcomes if o.verdict == REVIEW and o.ran
                 and not o.synthetic]

    def standin_reasons() -> list[str]:
        return [f"{o.name}: ran against a seeded stand-in, not the sensor, so "
                f"its result is evidence about a fixture and not about the "
                f"person at the camera" for o in standins]

    if violated:
        # Reported after the violation, not instead of it: the violated check
        # is the finding, but a reviewer upholding it should know which part
        # of the evidence was fabricated.
        return Decision(FAIL, outcomes, reasons + standin_reasons())

    # Everything below lands on REVIEW. Missing, absent and unsettled are peers
    # at that level, so all three are reported rather than the first one found:
    # a gate where the sensor died *and* two checks went unsubmitted was
    # otherwise described only as "not submitted", which names the consequence
    # and hides the cause. The FAIL path above still short-circuits, because a
    # violated check is a different verdict and must not be buried.
    #
    # Ordered cause-first. These are the words a reviewer reads at the top of a
    # queue, and "two checks were not submitted" explains nothing about why.
    #
    # The "could not run" framing is kept even when the check supplied its own
    # reason. A bare finding reads like the verdict of a check that looked —
    # "no enrolled reference exists" and "the faces did not match" are one word
    # apart on a queue and a world apart in meaning.
    for o in absent:
        reasons.append(
            f"{o.name}: could not run — {o.reason}" if o.reason else
            f"{o.name}: could not run, so it produced no evidence. Absence of "
            f"evidence is not evidence of absence, and a check that did not "
            f"look cannot stand in for one that looked and was satisfied")
    reasons.extend(standin_reasons())
    for o in unsettled:
        reasons.append(f"{o.name}: {o.reason or 'evidence does not settle the question'}")

    if missing:
        reasons.append(
            f"no result submitted for {', '.join(missing)} — a gate cannot pass "
            f"on checks that were never attempted")

    if missing or absent or unsettled or standins:
        return Decision(REVIEW, outcomes, reasons)

    return Decision(PASS, outcomes, ["every check ran and was satisfied"])
