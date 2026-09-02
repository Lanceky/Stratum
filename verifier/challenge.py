"""
Challenge generation for check 1 — presence (implementation.md Step 5).

The gate issues a nonce, and the nonce determines a colour-flash sequence plus
a micro-pose prompt. The whole security argument rests on one property: the
client cannot know the sequence before the gate opens, so a stream that was
recorded earlier cannot possibly respond to it correctly.

Two decisions worth stating.

**The spec is derived, not stored-and-trusted.** It comes from
HMAC(secret, nonce), so the server can recompute it at scoring time from the
nonce alone. Nothing the client sends can influence what it was supposed to do.
The secret is what stops an attacker who learns a nonce early from pre-rendering
a video against a predictable sequence.

**Predictions are pairwise, not absolute.** Skin scores have no meaningful
absolute scale — one person's `radiance` at rest is another's under a floodlight
— so check 1 never asserts a value. It asserts the *sign of the difference*
between two frames under two known illuminants, which is a physical claim that
holds for everyone. An earlier design compared each frame against the session
median; that is subtly broken, because a sequence of three bright colours would
still force one frame below the median and fail an honest capture.

**A flashing screen can cause seizures, so the physics is bounded by safety
before it is bounded by anything else.** Content that flashes between roughly 3
and 30 Hz — saturated red worst of all — triggers seizures in people with
photosensitive epilepsy. WCAG 2.3.1 makes that a Level A criterion, which is to
say a floor rather than a refinement. Two constraints follow, and both are
enforced here rather than left to the client: no frame may be held for less than
`MIN_HOLD_MS`, and no palette colour may be a saturated red. `derive` also has a
non-flashing mode for anyone who cannot safely see the sequence at all; it
cannot measure the light response, and says so, rather than quietly scoring a
weaker capture as though it were the full one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import random
from dataclasses import dataclass, field

# Rec. 709 luma coefficients — perceived brightness, not raw channel sum.
LUMA = (0.2126, 0.7152, 0.0722)

# How far apart two illuminants must be on a property before check 1 is willing
# to predict the sign of the difference. Below this the physics is real but too
# small to survive score quantisation, so the pair is scored as "no prediction"
# rather than counted as a failure.
LUMINANCE_MARGIN = 0.15
RED_RATIO_MARGIN = 0.08

POSES = ("neutral", "left", "right", "up")

# WCAG 2.3.1 (Level A), "Three Flashes or Below Threshold". Content passes if it
# flashes no more than three times a second *or* stays under the general and red
# flash thresholds. The second route is closed to us: LUMINANCE_MARGIN requires
# luminance steps of at least 0.15, which is already above the 0.1 general flash
# threshold, and shrinking the steps below it would leave nothing to measure. So
# the rate is the only lever, and sitting exactly on the 3/s line is not a
# margin. 500 ms floors the sequence at two transitions a second.
#
# Five frames at 500 ms is 2 s of holds, comfortably inside WINDOW_MS.
MIN_HOLD_MS = 500
DEFAULT_HOLD_MS = 500

# WCAG's threshold for a saturated red, the hue that is disproportionately
# likely to provoke a seizure: the red share of *linearised* light at or above
# 0.8. Linearised matters. #FF2E20 is 0.766 by the raw sRGB channel ratio and so
# looks safe, but 0.960 once gamma is undone — the naive form clears a colour
# that the standard forbids.
SATURATED_RED_RATIO = 0.8

# Four neutral frames give C(4,2)=6 same-pose pairs, the minimum evidence the
# illumination signal needs to clear its own bar; the fifth carries the pose.
# Calibrated in presence_report.py — at three frames the check abstains on every
# session, honest and attacker alike.
MIN_FRAMES = 5
DEFAULT_FRAMES = 5
SECRET_ENV = "STRATUM_CHALLENGE_SECRET"


@dataclass(frozen=True)
class Colour:
    """One screen illuminant, with the physical properties check 1 reasons over."""

    name: str
    hex: str

    @property
    def rgb(self) -> tuple[float, float, float]:
        h = self.hex.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    @property
    def luminance(self) -> float:
        return sum(c * k for c, k in zip(self.rgb, LUMA))

    @property
    def red_ratio(self) -> float:
        """Red as a share of total emitted light — hue, independent of brightness."""
        total = sum(self.rgb)
        return self.rgb[0] / total if total > 0 else 1 / 3

    @property
    def linear_rgb(self) -> tuple[float, float, float]:
        """
        sRGB with the display gamma undone, i.e. light rather than signal.

        Everything about a *perceptual* judgement — how bright this looks, how
        red it looks to the visual system that a flash is provoking — has to be
        made here. The stored 0-255 values are gamma-encoded, so arithmetic on
        them systematically understates how much of a dark colour's light is
        red.
        """
        return tuple(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                     for c in self.rgb)

    @property
    def saturated_red(self) -> bool:
        """WCAG's red flash test: is this the hue that provokes seizures?"""
        r, g, b = self.linear_rgb
        total = r + g + b
        return total > 0 and r / total >= SATURATED_RED_RATIO


# Chosen to span both axes the check reasons about: bright vs dark, and
# red-dominant vs red-starved. A palette that only varied brightness could not
# test `redness` at all.
#
# The red-dominant end is magenta rather than red, and that is a safety
# constraint rather than an aesthetic one. Every colour with enough red to be
# called red is a WCAG saturated red once linearised — the ceiling under the 0.8
# threshold is a dark orange whose red_ratio lands within RED_RATIO_MARGIN of
# amber, making the pair untestable. Magenta buys the hue separation by adding
# blue instead of removing red. It costs nothing measurable: it yields *more*
# testable predictions than #FF2E20 did, because its luminance sits mid-range
# and so clears LUMINANCE_MARGIN against both ends rather than only the bright
# one. `test_challenge.py` holds both properties.
PALETTE = (
    Colour("white", "#FFFFFF"),
    Colour("magenta", "#D250B9"),
    Colour("cyan", "#20E8FF"),
    Colour("dim", "#12121A"),
    Colour("amber", "#FFB020"),
    Colour("violet", "#7028FF"),
)

# The single illuminant of the non-flashing challenge. Held for the whole
# capture, so the screen makes exactly one transition — and one transition is
# not a flash, which puts this path outside WCAG 2.3.1 rather than merely inside
# its limits. Deliberately not a palette member: it is never drawn against
# another colour, because the point of it is that nothing is.
STEADY = Colour("steady", "#F2F2F2")


@dataclass(frozen=True)
class Frame:
    """What the client is told to do for one frame, and what it costs."""

    index: int
    colour: Colour
    pose: str
    hd: bool
    hold_ms: int

    def as_dict(self) -> dict:
        return {"index": self.index, "colour": self.colour.name,
                "hex": self.colour.hex, "pose": self.pose, "hd": self.hd,
                "hold_ms": self.hold_ms}


@dataclass(frozen=True)
class Prediction:
    """A signed physical claim about two frames under two illuminants."""

    dimension: str
    brighter: int      # frame index expected to score higher
    darker: int        # frame index expected to score lower
    basis: str
    margin: float

    def as_dict(self) -> dict:
        return {"dimension": self.dimension, "higher": self.brighter,
                "lower": self.darker, "basis": self.basis,
                "margin": round(self.margin, 4)}


@dataclass(frozen=True)
class Challenge:
    """A full challenge spec. Derived from the nonce; never taken from a client."""

    nonce: str
    frames: tuple[Frame, ...]
    pose_prompt: str
    hold_ms: int
    window_ms: int
    predictions: tuple[Prediction, ...] = field(default=())
    flashing: bool = True

    @property
    def hd_frame(self) -> int:
        return next(f.index for f in self.frames if f.hd)

    @property
    def neutral_frames(self) -> tuple[int, ...]:
        return tuple(f.index for f in self.frames if f.pose == "neutral")

    @property
    def posed_frames(self) -> tuple[int, ...]:
        return tuple(f.index for f in self.frames if f.pose != "neutral")

    def as_dict(self) -> dict:
        return {
            "frames": [f.as_dict() for f in self.frames],
            "pose_prompt": self.pose_prompt,
            "hold_ms": self.hold_ms,
            "window_ms": self.window_ms,
            "flashing": self.flashing,
            "predictions": [p.as_dict() for p in self.predictions],
        }

    def client_view(self) -> dict:
        """
        What the browser is allowed to see.

        The predictions are withheld. Telling a client which direction each
        score is expected to move would hand an attacker the answer key, and
        the client has no use for them — scoring happens on the server.

        `flashing` is disclosed, because the browser has to know whether it is
        about to strobe: it decides both the warning shown beforehand and
        whether the capture loop repaints between frames at all.
        """
        return {"frames": [f.as_dict() for f in self.frames],
                "pose_prompt": self.pose_prompt,
                "hold_ms": self.hold_ms,
                "window_ms": self.window_ms,
                "flashing": self.flashing}


def _seed(nonce: str, secret: str) -> int:
    mac = hmac.new(secret.encode(), nonce.encode(), hashlib.blake2b).digest()
    return int.from_bytes(mac[:16], "big")


def secret() -> str:
    """
    The key that makes the sequence unpredictable before disclosure.

    Defaulting to a fixed development value is deliberate and visible: tests and
    the offline demo need reproducibility, and a silent random default would
    make every restart invalidate gates that were already in flight. Production
    sets the variable; `app.py` reports whether it did.
    """
    return os.getenv(SECRET_ENV, "stratum-dev-challenge-secret")


def _predict(frames: tuple[Frame, ...]) -> tuple[Prediction, ...]:
    """
    Every sign-of-difference claim the illuminants justify.

    Only frames at the same pose are compared. A pose change moves the face
    relative to the light, which changes the scores for reasons that have
    nothing to do with the screen colour, and a check that ignored that would
    be asserting physics it cannot actually predict.
    """
    out: list[Prediction] = []
    for i, a in enumerate(frames):
        for b in frames[i + 1:]:
            if a.pose != b.pose:
                continue

            dl = a.colour.luminance - b.colour.luminance
            if abs(dl) >= LUMINANCE_MARGIN:
                hi, lo = (a.index, b.index) if dl > 0 else (b.index, a.index)
                # More light reaching the skin raises apparent glow, and raises
                # the specular return that the oiliness score is measuring.
                for dim in ("radiance", "oiliness"):
                    out.append(Prediction(dim, hi, lo, "luminance", abs(dl)))

            dr = a.colour.red_ratio - b.colour.red_ratio
            if abs(dr) >= RED_RATIO_MARGIN:
                hi, lo = (a.index, b.index) if dr > 0 else (b.index, a.index)
                out.append(Prediction("redness", hi, lo, "red_ratio", abs(dr)))
    return tuple(out)


def derive(nonce: str, *, n_frames: int = DEFAULT_FRAMES,
           hold_ms: int = DEFAULT_HOLD_MS, window_ms: int = 9000,
           flashing: bool = True, key: str | None = None) -> Challenge:
    """
    Build the challenge for a nonce. Same nonce and secret, same challenge.

    Exactly one frame is marked HD. That is the credit-discipline rule from
    context.md §11.1 expressed where it cannot be forgotten: HD skin analysis
    costs 12-22 units, so three HD frames per verification would make the whole
    project unaffordable. The other frames are validated locally, which is also
    better engineering — the expensive forensic check runs once, the cheap
    physics check runs on every frame.

    At least two frames are neutral. Check 1 needs a same-pose pair to measure
    how much of the frame-to-frame difference is just detection noise; without
    that reference the geometric signal has no scale to be judged against.

    The default of five frames is measured, not chosen for tidiness. Only
    same-pose frames can be compared, so `n_frames` neutral frames yield
    C(n-1, 2) prediction pairs — 1 at three frames, 6 at five. At three frames
    the illumination check cannot reach its own minimum evidence bar and
    abstains on every session, honest and attacker included. Five is the
    smallest budget at which it separates; see presence_report.py.

    `flashing=False` is the accessible path, for someone who cannot safely watch
    a flashing screen. It holds one steady illuminant for the whole capture, so
    there is no light response to predict and `predictions` comes back empty.
    That is not a degraded version of the same test — it is a different, smaller
    test, and check 1 reports the illumination signal as never having run rather
    than as having failed. The gate then goes to a human. Refusing these
    captures outright would be an accessibility exclusion; passing them on the
    remaining signals alone would be claiming evidence nobody gathered.
    """
    if n_frames < MIN_FRAMES:
        raise ValueError(f"check 1 needs at least {MIN_FRAMES} frames: four "
                         "neutral for the illumination pairs and the noise "
                         "floor, one posed for parallax")
    if n_frames > len(PALETTE):
        raise ValueError(f"n_frames cannot exceed the {len(PALETTE)}-colour "
                         "palette: two frames under the same colour would make "
                         "an untestable prediction")
    # Refused rather than clamped. A caller asking for a faster sequence has a
    # reason, and silently overriding it would leave them believing the capture
    # is quicker than it is — while a clamp that a client-side override later
    # undid would put a strobe on screen with nothing left to catch it.
    if hold_ms < MIN_HOLD_MS:
        raise ValueError(
            f"hold_ms must be at least {MIN_HOLD_MS}ms: below it the screen "
            "flashes faster than three times a second, which WCAG 2.3.1 "
            "forbids because it can trigger a photosensitive seizure")

    rng = random.Random(_seed(nonce, key or secret()))

    if flashing:
        colours = rng.sample(PALETTE, k=n_frames)
    else:
        # Still drawn from the generator so the pose and HD frame stay bound to
        # the nonce and unpredictable; only the illuminant is fixed.
        rng.sample(PALETTE, k=n_frames)
        colours = [STEADY] * n_frames
    pose = rng.choice(POSES[1:])
    # The posed frame is always last: the client holds still, then moves once.
    poses = ["neutral"] * (n_frames - 1) + [pose]
    # HD is spent on a neutral frame — checks 2 and 3 read it, and they want the
    # least distorted view of the face, not the one taken mid-turn.
    hd_index = rng.randrange(n_frames - 1)

    frames = tuple(Frame(i, colours[i], poses[i], i == hd_index, hold_ms)
                   for i in range(n_frames))
    predictions = _predict(frames) if flashing else ()

    if flashing and not predictions:
        # Only reachable if a palette change made every drawn colour alike.
        raise ValueError("challenge palette yielded no testable prediction")

    return Challenge(nonce=nonce, frames=frames, pose_prompt=pose,
                     hold_ms=hold_ms, window_ms=window_ms,
                     predictions=predictions, flashing=flashing)

