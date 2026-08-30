"""
Check 3 — binding. Is the person on this gate the person who enrolled?

Checks 1 and 2 ask whether a live, camera-native human was present. Neither
asks *which* human, and without that a system authorises anyone who can pass a
liveness test. This check closes that hole.

Three channels carry identity, and they are combined rather than picked between
because they fail in different, largely unrelated ways (measured — see
`binding_report.py`):

    constellation  Topology of the spot pattern, matched by Procrustes
                   registration. The strongest channel by a wide margin, and
                   the hardest to imitate: it is a physical layout, not a score.
    ratios         Quotients of facial distances. Already invariant to
                   translation, scale and in-plane rotation, so they survive a
                   pose change that degrades the constellation.
    identity       The z-scored STABLE skin dimensions. The weakest channel,
                   kept because it is the only one that does not depend on
                   landmark geometry at all — when the other two are damaged by
                   a bad capture, this one usually is not.

Volatile dimensions are excluded, not down-weighted. They move with sleep,
hydration and lighting; a weight can be nudged back up by accident, an omission
cannot.

**Two thresholds, not one.** This is the whole point of the check:

    d < lower              PASS
    lower <= d <= upper    REVIEW  — a first-class outcome, not a fallback
    d > upper              FAIL

The REVIEW band exists because both failure directions cost real money and they
are not symmetric. Wrongly blocking a signer stalls a transaction and annoys a
customer. Wrongly passing a fraudster wires the money to someone else. A single
threshold forces those two costs through one number and hides the cases that
deserve a human: identical twins, a shaved beard, a bad capture, genuine
borderline. Those cases exist whether or not the system admits them.

⚠️ **What has NOT been shown.** Every figure here is measured against
`synth_cohort.py`, which builds identities with distinct spot patterns by
construction. That measures the *matcher* — whether Procrustes registration and
the fusion recover an identity the generator put there. It does not establish
that real human skin separates, because no real skin has been scored. That is
Step 12's genuine set, and it is the question that decides whether this check
is worth anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Channel weights. Not hand-picked: each is proportional to the channel's
# measured d-prime on a held-out half of the reference cohort, then normalised
# to sum to 1. Refitting is `binding_report.py --refit`.
#
# The ordering is the interesting part and it is stable across cohort sizes:
# spot topology carries several times the identity information of any scalar
# score, which is the empirical claim this whole design rests on.
CHANNEL_WEIGHTS = {
    "constellation": 0.60,
    "ratios": 0.28,
    "identity": 0.12,
}

# Distances in units of the genuine-pair spread. A same-person comparison sits
# near 0 by construction; these say how far from that a stranger has to be.
#
# Both are set from a fit cohort and checked on a held-out one (see
# `binding_report.py`), under two asymmetric rules:
#
#   lower  no impostor may ever reach PASS. Clean genuine captures top out
#          around 1.1 and the nearest impostor sits near 14, so 3.0 is
#          generous to the user while leaving an order of magnitude of margin.
#   upper  no honest capture may be auto-rejected. This is the harder one: a
#          badly-lit genuine capture reaches 7.1 while a sibling starts at 6.9,
#          so the two OVERLAP and no threshold separates them. FAIL is
#          therefore placed above every sibling rather than between, and the
#          overlap is handed to a human instead of being guessed at.
LOWER_THRESHOLD = 3.0
UPPER_THRESHOLD = 10.0

# A channel needs this many matched points before its distance means anything.
# Below it the registration is fitting noise, and the channel abstains rather
# than contributing a confident-looking number built on four points.
MIN_INLIERS = 6

LIMITATIONS = [
    "No real skin has been scored. Every figure comes from a synthetic cohort "
    "whose identities differ by construction, so what is measured is the "
    "matcher, not whether real faces separate. Step 12's genuine set decides "
    "that, and it is the question this check stands or falls on.",
    "A close relative and a badly-lit genuine capture produce OVERLAPPING "
    "distances, so no threshold separates them. Both land in REVIEW by design. "
    "Anyone quoting a single accuracy figure for this check is hiding that "
    "overlap rather than having eliminated it.",
    "Relatives are separated by the spot constellation rather than by skin "
    "scores or facial ratios, because mole placement is developmental noise "
    "rather than inherited. That is why the constellation carries most of the "
    "weight, and why a capture that loses it degrades to a REVIEW.",
    "A REVIEW verdict is not a soft failure. It is the outcome for every case "
    "the evidence genuinely does not settle, and treating it as a failure "
    "would push borderline genuine users into rejection.",
]

VERDICTS = ("PASS", "REVIEW", "FAIL")


@dataclass
class ChannelResult:
    """One identity channel's opinion, in units of the genuine-pair spread."""

    name: str
    raw: float
    z: float
    weight: float
    ran: bool
    note: str = ""
    # What this channel actually counted for once absent channels were dropped.
    # Kept apart from the declared `weight` so a reviewer can see that a verdict
    # rested on two channels rather than three, and how heavily each one bore.
    effective_weight: float = 0.0

    def as_dict(self) -> dict:
        return {"name": self.name,
                "raw": round(self.raw, 4) if np.isfinite(self.raw) else None,
                "z": round(self.z, 3), "weight": round(self.weight, 3),
                "effective_weight": round(self.effective_weight, 3),
                "ran": self.ran, "note": self.note}


@dataclass
class Calibration:
    """
    Where genuine pairs sit on each channel.

    Every threshold in this check is expressed in genuine-pair spreads rather
    than raw units, because the raw scale of a chamfer distance and the raw
    scale of a z-scored skin vector have nothing to do with each other and
    comparing them directly would be meaningless.
    """

    mean: dict[str, float]
    spread: dict[str, float]
    n_pairs: int
    provisional: bool = True

    def z(self, channel: str, value: float) -> float:
        mu = self.mean.get(channel, 0.0)
        sd = max(self.spread.get(channel, 1.0), 1e-6)
        return (value - mu) / sd

    def as_dict(self) -> dict:
        return {"genuine_pairs": self.n_pairs,
                "mean": {k: round(v, 4) for k, v in self.mean.items()},
                "spread": {k: round(v, 4) for k, v in self.spread.items()},
                "provisional": self.provisional}


DEFAULT_CALIBRATION = Calibration(
    mean={"constellation": 0.249, "ratios": 0.030, "identity": 0.227},
    spread={"constellation": 0.051, "ratios": 0.007, "identity": 0.093},
    n_pairs=36,
)


@dataclass
class BindingResult:
    verdict: str
    distance: float
    channels: list[ChannelResult]
    detail: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """PASS only. REVIEW is not a pass — it is a question."""
        return self.verdict == "PASS"

    @property
    def ran(self) -> bool:
        return any(c.ran for c in self.channels)

    @property
    def reason(self) -> str:
        """Why this verdict, in words. Always populated."""
        return self.detail.get("reason", "")

    @property
    def score(self) -> float:
        """1.0 at a perfect match, falling to 0.0 at the FAIL threshold."""
        if not self.ran:
            return 0.0
        if not np.isfinite(self.distance):
            return 0.0
        return float(np.clip(1.0 - self.distance / UPPER_THRESHOLD, 0.0, 1.0))

    def as_dict(self) -> dict:
        return {"check": 3, "name": "binding", "verdict": self.verdict,
                "passed": self.passed, "ran": self.ran,
                "distance": round(self.distance, 3) if np.isfinite(self.distance) else None,
                "reason": self.reason,
                "score": round(self.score, 4),
                "thresholds": {"lower": LOWER_THRESHOLD, "upper": UPPER_THRESHOLD},
                "channels": [c.as_dict() for c in self.channels],
                "limitations": LIMITATIONS, "detail": self.detail}


def _constellation(a: dict, b: dict) -> tuple[float, bool, str]:
    """Best-registering constellation between two normalised bundles."""
    from normalise import register

    shared = sorted(set(a.get("constellations", {})) & set(b.get("constellations", {})))
    best, note = None, "no constellation shared between the two captures"
    for name in shared:
        pa = np.array(a["constellations"][name]["points"], float)
        pb = np.array(b["constellations"][name]["points"], float)
        if len(pa) < 3 or len(pb) < 3:
            continue
        reg = register(pa, pb)
        if reg.inliers < MIN_INLIERS:
            note = (f"{name}: only {reg.inliers} matched points, below the "
                    f"{MIN_INLIERS} needed for the fit to mean anything")
            continue
        # Lower distance is a better match; keep the most convincing one.
        if best is None or reg.distance < best[0]:
            best, note = (reg.distance, name, reg), ""
    if best is None:
        return 1.0, False, note
    return best[0], True, f"matched on {best[1]} ({best[2].inliers} points)"


def evaluate(enrolment: dict, probe: dict, *,
             calibration: Calibration | None = None) -> BindingResult:
    """
    Compare a probe capture against an enrolment, and decide.

    Both arguments are `normalise.normalise_bundle` output, so this function
    does no network work and no parsing — it only judges.

    A channel that cannot run is dropped and the remaining weights renormalise.
    That is deliberately not the same as scoring it zero: a missing channel is
    an absence of evidence, and scoring it as agreement would let a degraded
    capture manufacture a PASS.
    """
    from normalise import vector_distance

    cal = calibration or DEFAULT_CALIBRATION
    channels: list[ChannelResult] = []

    raw, ran, note = _constellation(enrolment, probe)
    channels.append(ChannelResult("constellation", raw, cal.z("constellation", raw),
                                  CHANNEL_WEIGHTS["constellation"], ran, note))

    for name, key in (("ratios", "ratios"), ("identity", "identity_vector")):
        va, vb = enrolment.get(key) or {}, probe.get(key) or {}
        shared = set(va) & set(vb)
        if not shared:
            channels.append(ChannelResult(name, float("inf"), 0.0,
                                          CHANNEL_WEIGHTS[name], False,
                                          "no shared keys between the captures"))
            continue
        d = vector_distance(va, vb)
        channels.append(ChannelResult(name, d, cal.z(name, d),
                                      CHANNEL_WEIGHTS[name], True,
                                      f"{len(shared)} shared keys"))

    live = [c for c in channels if c.ran]
    if not live:
        # REVIEW, not FAIL. Nothing here says the two captures disagree — it
        # says nothing could be compared, which is a different claim. Calling
        # that a mismatch would auto-reject anyone whose capture came out
        # unusable, which is a camera fault rather than a fraud signal.
        return BindingResult(
            "REVIEW", float("inf"), channels,
            {"reason": "no identity channel could be computed — the captures "
                       "share nothing comparable, so this check has no opinion "
                       "rather than a negative one",
             "channels_used": [], "weights_renormalised": False,
             "channels_absent": [c.name for c in channels],
             "calibration": cal.as_dict()})

    total = sum(c.weight for c in live)
    for c in channels:
        # What the channel actually counted for, after absent ones were
        # dropped. Reported separately from the declared weight so an auditor
        # can see that a PASS rested on two channels rather than three.
        c.effective_weight = c.weight / total if c.ran else 0.0
    distance = float(sum(c.weight * c.z for c in live) / total)

    if distance < LOWER_THRESHOLD:
        verdict = "PASS"
    elif distance <= UPPER_THRESHOLD:
        verdict = "REVIEW"
    else:
        verdict = "FAIL"

    dominant = max(live, key=lambda c: c.weight * abs(c.z))
    return BindingResult(
        verdict, distance, channels,
        {"channels_used": [c.name for c in live],
         "channels_absent": [c.name for c in channels if not c.ran],
         "weights_renormalised": len(live) != len(channels),
         "dominant_channel": dominant.name,
         "reason": _reason(verdict, dominant),
         "calibration": cal.as_dict()})


def _reason(verdict: str, dominant: ChannelResult) -> str:
    """Why a reviewer is being asked, in words rather than numbers."""
    if verdict == "PASS":
        return (f"every channel agrees; {dominant.name} sits "
                f"{dominant.z:.1f} genuine-spreads from a same-person match")
    if verdict == "FAIL":
        return (f"{dominant.name} disagrees by {dominant.z:.1f} genuine-spreads, "
                f"past the {UPPER_THRESHOLD} at which this stops being a "
                f"borderline capture and starts being a different person")
    return (f"{dominant.name} is {dominant.z:.1f} genuine-spreads out — too far "
            f"to accept, too close to call an impostor. Twins, a changed "
            f"appearance and a poor capture all land here, and only a human "
            f"can tell them apart")
