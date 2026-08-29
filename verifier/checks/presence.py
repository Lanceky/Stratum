"""
Check 1 — presence. Is a live human in front of a real camera, right now?

Four signals, deliberately independent, because no one of them covers the
attack surface (implementation.md Step 5, context.md §4):

    illumination  do the volatile scores move the way this nonce's colour
                  sequence says they must?
    pose          did the face move the way this nonce asked it to?
    timing        did the response arrive after the challenge, inside its
                  window?
    geometry      does the face have depth, or is it flat?

Why more than one is needed, which is the part worth saying out loud:

    * An **injected stream** — OBS Virtual Camera replaying a genuine session —
      is a recording of a real 3D face, so depth alone does not catch it. It
      cannot know what colour the screen flashed 200 ms ago, nor which way this
      nonce asked the head to turn, so illumination and pose do. Measured: 0 of
      60 injected sessions pass, against 60 of 60 live ones.
    * A **printed photo** is a physical object. Under a red flash it really does
      get redder, and it can be turned on cue, so neither illumination nor pose
      catches it. It is flat, so geometry does — partially. Measured: enforcing
      depth takes print from 97% accepted to 28%, and costs 4 points of honest
      acceptance. That residual 28% is stated in `LIMITATIONS` rather than
      rounded away. See `geometry` for why the signal is hard to sharpen.

That is a physics argument rather than a heuristic one, including where it runs
out. Every figure above comes from a simulation, not a camera; no physical
presentation attack has been run.

Nothing here uses a stable skin dimension for identity, and nothing here uses a
volatile one for anything but this check. The split in `dimensions.py` is the
whole reason volatile scores are safe to lean on: their instability, which
disqualifies them from identity, is exactly what makes them a light meter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from challenge import Challenge
from dimensions import STABLE, base, is_volatile
from normalise import match

# An illumination prediction only counts once the observed difference clears the
# session's own noise. Below that the sign is a coin toss, and counting coin
# tosses as agreement is how a check that measures nothing scores 50%.
DEADBAND_SIGMA = 1.5
MIN_DEADBAND = 1.0          # points, so a suspiciously quiet session cannot
                            # drive the deadband to zero and manufacture evidence
MIN_DECIDED = 6             # predictions that must clear the deadband at all
MIN_AGREEMENT = 0.90        # of those, the share that must have the right sign

# Sign agreement is robust but throws away magnitude, and a stream that responds
# to nothing still wins half its coin tosses. The response statistic keeps the
# magnitude: the mean predicted-direction difference over every prediction,
# scaled by the standard error implied by the session's own noise. Both gates
# must hold. Calibrated in presence_report.py: at this threshold a replayed
# stream never passes, because it has no response to be large.
MIN_RESPONSE_Z = 7.0

# A flat surface photographed twice from different angles is related by a
# homography exactly, so the residual collapses to the detection noise floor. A
# real face has relief the homography cannot absorb. Partial, not decisive:
# at 1.6 it admits 93% of live sessions and 28% of printed ones. See `geometry`.
MIN_RELIEF = 1.6
MIN_POSED_INLIERS = 12

# Anisotropy of the neutral→posed affine: a turn compresses the face along one
# axis, holding still does not. Measured on the synthetic cohort — a same-pose
# pair reaches 0.013 at worst, a real turn averages 0.049 and its weakest
# session reaches 0.015 — so the gate sits between the two, nearer the
# do-nothing ceiling than the honest floor.
MIN_POSE_ANISO = 0.018
# Below this the compression is too small for its axis to be meaningful and the
# axis test abstains rather than guess. Above it the axis is right 97% of the
# time, which is what makes it safe to enforce.
AXIS_CONFIDENT_ANISO = 0.04
AXIS_SPLIT = 0.5

# A pose change is not a same-pose comparison, and matching it at the identity
# radius is self-defeating: the parallax that proves the face is three
# dimensional is exactly what pushes a pore past a 0.05 gate, so the tight
# radius throws away live faces and keeps flat ones. Measured — at the identity
# radius the pose signal passed 55 of 60 printed sessions and only 22 of 60
# live ones, precisely inverted. Widened to cover a turn's worth of travel.
POSED_RADIUS = 0.12

# Stated in every response, because a check that hides where it stops working
# is worse than one that does not run. See `geometry`.
LIMITATIONS = [
    "A printed photograph responds to coloured light much as skin does and can "
    "be turned on cue, so only the depth signal separates it — and that signal "
    "is partial. Roughly 28% of simulated print attacks still pass. Treat a "
    "pass as evidence against replay and injection, not as proof against a "
    "determined physical presentation attack.",
    "All figures come from a physical simulation, not from a camera. No "
    "physical presentation attack has been run; that requires hardware and "
    "sponsor credentials.",
]


@dataclass
class Signal:
    """One independent line of evidence, and whether it held."""

    name: str
    passed: bool
    score: float
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "score": round(self.score, 4), "detail": self.detail}


@dataclass
class PresenceResult:
    passed: bool
    signals: list[Signal]

    @property
    def score(self) -> float:
        """Lowest signal, not the average — a weak link is not offset by a strong one."""
        return min((s.score for s in self.signals), default=0.0)

    @property
    def failed_signals(self) -> list[str]:
        return [s.name for s in self.signals if not s.passed]

    def as_dict(self) -> dict:
        return {"check": 1, "name": "presence", "passed": self.passed,
                "score": round(self.score, 4),
                "failed": self.failed_signals,
                "limitations": LIMITATIONS,
                "signals": [s.as_dict() for s in self.signals]}


def _scores(frame: dict) -> dict[str, float]:
    return {k: float(v) for k, v in (frame.get("scores") or {}).items()}


def score_noise(frames: list[dict]) -> float:
    """
    Estimate this session's score noise from the dimensions that must not move.

    Stable dimensions are not supposed to track the screen colour — that is the
    claim `dimensions.py` makes and `test_normalise.py` measures. So their
    frame-to-frame spread is a per-session noise floor, obtained without
    spending a frame on it and without assuming a fixed number that a different
    camera or lighting rig would invalidate.

    Using the check's own control as its calibration also makes the check
    self-limiting: if the capture is so noisy that stable scores wander, the
    deadband widens, fewer predictions are decided, and the check declines to
    reach a verdict rather than guessing.
    """
    stable = {f"hd_{d}" for d in STABLE} | set(STABLE)
    spreads = []
    keys = {k for f in frames for k in _scores(f)}
    for k in keys:
        if base(k) not in {base(s) for s in stable}:
            continue
        vals = [s[k] for f in frames if (s := _scores(f)) and k in s]
        if len(vals) > 1:
            spreads.append(float(np.std(vals, ddof=1)))
    return float(np.median(spreads)) if spreads else MIN_DEADBAND


def illumination(frames: list[dict], challenge: Challenge) -> Signal:
    """
    Did the volatile scores respond to *this* nonce's colour sequence?

    Direction only. Magnitude would need an absolute calibration of screen
    brightness, which the open web does not give us — a user can dim their
    display and we would never know. The sign of a difference survives that,
    which is what makes the check deployable outside a lab.
    """
    by_index = {int(f.get("frame_index", i)): f for i, f in enumerate(frames)}
    noise = max(score_noise(frames), 1e-6)
    deadband = max(MIN_DEADBAND, DEADBAND_SIGMA * noise)

    decided, correct, rows = 0, 0, []
    deltas: list[float] = []
    for p in challenge.predictions:
        hi, lo = by_index.get(p.brighter), by_index.get(p.darker)
        if hi is None or lo is None:
            continue

        keys = [k for k in _scores(hi)
                if base(k) == p.dimension and is_volatile(k)
                and k in _scores(lo)]
        if not keys:
            continue

        delta = float(np.mean([_scores(hi)[k] - _scores(lo)[k] for k in keys]))
        # Every prediction feeds the magnitude statistic, including the quiet
        # ones. Dropping them would bias it towards whichever differences
        # happened to be large, which is the sign test's weakness, not a fix.
        deltas.append(delta)
        if abs(delta) < deadband:
            rows.append({**p.as_dict(), "delta": round(delta, 3),
                         "verdict": "abstain"})
            continue

        decided += 1
        ok = delta > 0
        correct += ok
        rows.append({**p.as_dict(), "delta": round(delta, 3),
                     "verdict": "agree" if ok else "contradict"})

    # Predictions are oriented so that a genuine response is positive, which
    # makes the mean a one-sided statistic: a real face drives it well above
    # zero, a stream that ignores the screen leaves it at zero either side.
    response_z = float(np.mean(deltas) * np.sqrt(len(deltas)) / noise) \
        if deltas else 0.0

    agreement = float(correct / decided) if decided else 0.0
    passed = bool(decided >= MIN_DECIDED
                  and agreement >= MIN_AGREEMENT
                  and response_z >= MIN_RESPONSE_Z)
    return Signal(
        "illumination", passed,
        # An undecided session scores zero, not one half. Absence of a response
        # is the injection signature, not a neutral result.
        float(min(agreement, response_z / MIN_RESPONSE_Z)) if decided >= MIN_DECIDED else 0.0,
        {"decided": decided, "correct": correct,
         "abstained": len(challenge.predictions) - decided,
         "deadband_points": round(deadband, 3),
         "noise_points": round(noise, 3),
         "response_z": round(response_z, 3), "required_z": MIN_RESPONSE_Z,
         "agreement": round(agreement, 4), "predictions": rows})


def _homography_rmse(src: np.ndarray, dst: np.ndarray) -> float:
    """
    Residual after the best plane-to-plane map between matched points.

    Least squares over every correspondence, not RANSAC. RANSAC would discard
    the points that fit a plane worst — which on a real face are the nose and
    brow, precisely the evidence of depth this check exists to find.
    """
    if len(src) < 4:
        return float("nan")
    H, _ = cv2.findHomography(src.astype(np.float64).reshape(-1, 1, 2),
                              dst.astype(np.float64).reshape(-1, 1, 2), 0)
    if H is None:
        return float("nan")
    proj = cv2.perspectiveTransform(
        src.astype(np.float64).reshape(-1, 1, 2), H).reshape(-1, 2)
    return float(np.sqrt(((proj - dst) ** 2).sum(1).mean()))


def geometry(frames: list[dict], challenge: Challenge,
             constellation: str = "hd_pore") -> Signal:
    """
    Does the thing in front of the camera have depth? Advisory — see below.

    Turning a flat surface produces a homography — exactly, for any planar
    scene under perspective. Turning a real face does not: the nose travels
    further than the cheeks, and no plane-to-plane map can absorb that. So fit
    the best homography and see what it fails to explain. The two neutral
    frames supply the scale, since they differ by detection noise alone.

    The theory holds and was verified noise-free: a flat plane leaves a residual
    of 0.0000000 of face width, a 25 mm-relief face leaves 0.0175 at a 30° turn.
    The problem is that pore-detection jitter is 0.0067 of face width, so the
    per-point signal-to-noise is under 3, and the statistic is a ratio of two
    RMS estimates each drawn from a few dozen correspondences. Six variants were
    measured — homography residual ratio, iterated homography-ICP, affine
    anchoring with frozen correspondences, spatial coherence of the residual,
    axial concentration, and a smooth/rough basis split — at turns from 6° to
    35° and spot counts from 90 to 800. The best gave live 2.00 (min 1.52)
    against print 1.34 (max 2.33). Overlapping, so it does not separate.

    Two findings are worth more than a threshold here. A projective map absorbs
    quadratic depth to first order, so fitting a homography destroys most of the
    parallax it is meant to expose; and tilting a flat photo produces keystone
    distortion of the same quadratic magnitude as a real face's parallax, so the
    size of the deformation carries no information — only the part a homography
    cannot reach does, and that part is small.

    What finally moved it was not a better statistic but a correct matching
    radius. Registering the posed frame at the identity radius of 0.05 discards
    every pore the parallax displaced — the tight gate was rejecting exactly the
    evidence being looked for. At the wider `POSED_RADIUS` the same ratio reads
    2.02 for live against 1.46 for flat, and a threshold of 1.6 admits 93% of
    live sessions and 28% of printed ones.

    So it is enforced, and it is partial. It cuts printed-photograph acceptance
    roughly fourfold and does not come close to eliminating it; `LIMITATIONS`
    says so in the response rather than leaving the number to be discovered.
    """
    by_index = {int(f.get("frame_index", i)): f for i, f in enumerate(frames)}

    def points(idx: int) -> np.ndarray | None:
        f = by_index.get(idx)
        pts = (f or {}).get("constellations", {}).get(constellation)
        arr = np.asarray(pts, float) if pts else None
        return arr if arr is not None and len(arr) >= 4 else None

    def refuse(detail: dict) -> Signal:
        return Signal("geometry", False, 0.0, detail)

    neutral, posed = challenge.neutral_frames, challenge.posed_frames
    if len(neutral) < 2 or not posed:
        return refuse({"error": "challenge lacks a neutral pair or a posed frame"})

    a, b, c = points(neutral[0]), points(neutral[1]), points(posed[0])
    if a is None or b is None or c is None:
        return refuse({"error": f"missing or too-small '{constellation}' constellation"})

    floor_match = match(a, b)
    posed_match = match(a, c, radius=POSED_RADIUS)
    if posed_match.registration.inliers < MIN_POSED_INLIERS:
        return refuse({"error": "too few correspondences across the pose change",
                       "inliers": posed_match.registration.inliers,
                       "required": MIN_POSED_INLIERS})

    floor = float(np.sqrt((floor_match.residuals() ** 2).mean())) \
        if len(floor_match.i) else float("nan")
    src, dst = posed_match.pairs
    residual = _homography_rmse(src[:, :2], dst[:, :2])

    if not np.isfinite(floor) or floor <= 0 or not np.isfinite(residual):
        return refuse({"error": "could not establish a noise floor",
                       "floor": floor, "homography_rmse": residual})

    relief = float(residual / floor)
    return Signal(
        "geometry", bool(relief >= MIN_RELIEF), float(min(relief / MIN_RELIEF, 1.0)),
        {"relief_ratio": round(relief, 3), "required": MIN_RELIEF,
         "note": "partial signal: admits ~28% of printed photographs",
         "homography_rmse": round(residual, 5),
         "noise_floor_rmse": round(floor, 5),
         "noise_floor_pairs": int(len(floor_match.i)),
         "posed_pairs": int(len(posed_match.i))})


def pose(frames: list[dict], challenge: Challenge,
         constellation: str = "hd_pore") -> Signal:
    """
    Did the face move the way this nonce asked it to?

    Without this the pose half of the challenge is decoration: nothing else
    here notices whether the subject turned at all. A recorded session turned
    whichever way it turned when it was filmed, and the nonce picks a direction
    it cannot have known, so binding the response to the request costs one
    affine fit and removes a whole replay strategy.

    Two things are measured from the neutral→posed affine. Its anisotropy says
    the face was compressed along some axis, which holding still does not do;
    a same-pose pair reaches 0.013 at worst against 0.049 for a real turn, so
    this is enforced. Which axis was compressed says yaw from pitch — but only
    once the compression is big enough for its direction to mean anything,
    below which this abstains instead of guessing. A left/right distinction
    would need the occlusion asymmetry rather than the affine, and is not
    claimed here.

    Every neutral frame is fitted against the posed one and the median taken,
    rather than trusting a single pair. On a real face the parallax itself
    degrades some of those fits, and one unlucky fit reads as no movement at
    all: from a single pair the weakest 5% of honest sessions fell to 0.018,
    below the do-nothing ceiling. Across four pairs the same 5% sits at 0.023,
    which is what makes the gate safe to enforce. The control is taken the same
    way, from the median over every same-pose pair.
    """
    by_index = {int(f.get("frame_index", i)): f for i, f in enumerate(frames)}

    def points(idx: int) -> np.ndarray | None:
        f = by_index.get(idx)
        pts = (f or {}).get("constellations", {}).get(constellation)
        arr = np.asarray(pts, float) if pts else None
        return arr if arr is not None and len(arr) >= 4 else None

    def deform(a: np.ndarray, b: np.ndarray) -> tuple[float, float] | None:
        m = match(a, b, radius=POSED_RADIUS)
        if len(m.i) < MIN_POSED_INLIERS:
            return None
        affine, _ = cv2.estimateAffine2D(
            m.a_aligned[m.i, :2].astype(np.float64).reshape(-1, 1, 2),
            m.b_frame[m.j, :2].astype(np.float64).reshape(-1, 1, 2),
            method=cv2.LMEDS)
        if affine is None:
            return None
        _, sv, vt = np.linalg.svd(affine[:, :2])
        if sv[1] <= 0:
            return None
        # log ratio of the singular values, and how horizontal the stretch is
        return float(np.log(sv[0] / sv[1])), float(abs(vt[0][0]))

    neutral, posed = challenge.neutral_frames, challenge.posed_frames
    if not neutral or not posed:
        return Signal("pose", False, 0.0, {"error": "challenge lacks a posed frame"})

    target = points(posed[0])
    sources = {i: p for i in neutral if (p := points(i)) is not None}
    if target is None or not sources:
        return Signal("pose", False, 0.0,
                      {"error": f"missing or too-small '{constellation}' constellation"})

    fits = [d for p in sources.values() if (d := deform(p, target))]
    if not fits:
        return Signal("pose", False, 0.0,
                      {"error": "too few correspondences across the pose change",
                       "required": MIN_POSED_INLIERS})

    anisotropy = float(np.median([f[0] for f in fits]))
    axis_x = float(np.median([f[1] for f in fits]))

    # One control pair, not all of them. It is reported so a reviewer can see
    # what this session's do-nothing baseline looked like; it does not enter the
    # decision, and fitting every pair would cost six registrations to say the
    # same thing.
    idx = sorted(sources)
    control = (deform(sources[idx[0]], sources[idx[1]]) if len(idx) > 1 else None)

    moved = bool(anisotropy >= MIN_POSE_ANISO)
    expected = "vertical" if challenge.pose_prompt in ("left", "right") else "horizontal"
    observed = "horizontal" if axis_x > AXIS_SPLIT else "vertical"
    axis_decided = bool(anisotropy >= AXIS_CONFIDENT_ANISO)
    axis_ok = bool((not axis_decided) or observed == expected)

    return Signal(
        "pose", bool(moved and axis_ok),
        float(min(anisotropy / MIN_POSE_ANISO, 1.0)) if axis_ok else 0.0,
        {"requested": challenge.pose_prompt,
         "anisotropy": round(anisotropy, 4), "required": MIN_POSE_ANISO,
         "same_pose_control": round(control[0], 4) if control else None,
         "moved": moved, "fits": len(fits),
         "stretch_axis_x": round(axis_x, 3),
         "expected_axis": expected,
         "observed_axis": observed if axis_decided else "abstain",
         "axis_decided": axis_decided})


def timing(frames: list[dict], challenge: Challenge, issued_at: float) -> Signal:
    """
    Did the response arrive after the challenge, and inside its window?

    This is what stops a captured session being replayed later against a
    recomputed spec, and it is cheap. A frame stamped before the nonce existed
    is not evidence of anything.
    """
    stamps = []
    for i, f in enumerate(frames):
        t = f.get("captured_at")
        if t is None:
            return Signal("timing", False, 0.0,
                          {"error": f"frame {i} has no captured_at"})
        stamps.append((int(f.get("frame_index", i)), float(t)))
    stamps.sort()

    times = [t for _, t in stamps]
    problems = []
    if times[0] < issued_at:
        problems.append("a frame predates the challenge")
    if any(b < a for a, b in zip(times, times[1:])):
        problems.append("frames are not in chronological order")

    elapsed_ms = (times[-1] - times[0]) * 1000.0
    if elapsed_ms > challenge.window_ms:
        problems.append("response exceeded the challenge window")

    # Each flash has to be on screen long enough to actually illuminate the
    # face; frames fired faster than the hold time did not see their colour.
    gaps_ms = [(b - a) * 1000.0 for a, b in zip(times, times[1:])]
    if gaps_ms and min(gaps_ms) < challenge.hold_ms:
        problems.append("frames captured faster than the flash hold time")

    return Signal("timing", not problems, 0.0 if problems else 1.0,
                  {"elapsed_ms": round(elapsed_ms, 1),
                   "window_ms": challenge.window_ms,
                   "min_gap_ms": round(min(gaps_ms), 1) if gaps_ms else None,
                   "hold_ms": challenge.hold_ms,
                   "problems": problems})


def evaluate(frames: list[dict], challenge: Challenge,
             issued_at: float = 0.0) -> PresenceResult:
    """
    Run every signal. Every one must hold.

    Conjunction, not a weighted sum. Each signal covers an attack the others
    miss, so a high score on two of them says nothing about the third, and
    averaging would let a stream with an excellent light response through on
    the strength of the response alone.

    Geometry is the only signal that catches a printed photograph, and it does
    so only partially, so `LIMITATIONS` travels with every result rather than
    letting a pass imply more coverage than was measured.
    """
    signals = [
        illumination(frames, challenge),
        pose(frames, challenge),
        timing(frames, challenge, issued_at),
        geometry(frames, challenge),
    ]
    return PresenceResult(all(s.passed for s in signals), signals)
