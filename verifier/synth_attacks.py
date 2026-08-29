"""
Physical models of the four things that can be in front of the camera.

Check 1 claims to tell a live human from a print, a phone screen and an injected
stream. That claim is worth nothing unless it is measured against something that
behaves the way those attacks actually behave, so each one is modelled from its
physics rather than described in prose.

    live       3D surface, real light response
    print      flat surface, real light response
    screen     flat surface, emits its own light so barely responds
    injection  3D geometry (it is a recording of a real person), zero response

The table is the whole argument for why check 1 needs two independent signals.
No single signal covers the column:

    * geometry alone passes `injection` — the recording really is of a 3D face
    * illumination alone passes `print` — paper is a physical object, and it
      genuinely does get redder under a red flash

Modelling assumptions, stated plainly so no result here is mistaken for
evidence about the real API:

  * `ILLUM_EFFECT` is how many points a Perfect Corp volatile score moves across
    a full luminance swing. Nobody has measured it — credentials have not
    arrived — so it is a guess, and `presence_report.py` sweeps it rather than
    trusting it. The number that matters is the effect-to-noise ratio at which
    the check stops working, because that is what the real API has to beat.
  * Print response is modelled at 0.9 of live, which is deliberately generous to
    the attacker. Paper really does respond to coloured light, and pretending
    otherwise would let check 1 look stronger than it is.
  * Face relief is ~25mm of depth over a ~140mm width, which is roughly a real
    face and is what the geometric signal lives on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import synth_cohort as sc
from challenge import Challenge

SCORE_NOISE = 1.5          # points, per capture, on a 0-100 score
ILLUM_EFFECT = 8.0         # points across a full luminance / hue swing
FACE_RELIEF = 25.0         # mm of depth, nose plane to ear plane
FACE_WIDTH = 140.0         # mm
CAMERA_DISTANCE = 450.0    # mm, arm's length
POSE_DEG = 28.0            # how far the micro-pose actually turns the head
GRAZING_COS = 0.20         # below this the surface is too oblique to resolve spots

# How much of the live response each medium reproduces.
GAIN = {"live": 1.0, "print": 0.9, "screen": 0.15, "injection": 0.0}


@dataclass
class Subject:
    """A person, plus the depth their face has. Flat media reuse the same face."""

    identity: sc.Identity
    depth: np.ndarray
    normal: np.ndarray

    @classmethod
    def make(cls, seed: int) -> "Subject":
        ident = sc.Identity(seed=seed)
        xy = ident.spots[:, :2]
        cx, cy = sc.IMG_W / 2, sc.IMG_H / 2
        # Normalised offsets from the face centre, in half-widths.
        u = (xy[:, 0] - cx) / (sc.IMG_W * 0.30)
        v = (xy[:, 1] - cy) / (sc.IMG_H * 0.36)

        # A face is convex, and the nose adds a local bump near the centre. Both
        # terms matter: the convexity is what a plane cannot imitate, and the
        # bump is the part a homography has the hardest time absorbing.
        convex = np.clip(1.0 - u**2 - v**2, 0.0, None)
        nose = 0.55 * np.exp(-((u / 0.28) ** 2 + ((v - 0.05) / 0.38) ** 2))
        z = FACE_RELIEF * (0.7 * convex + nose)

        # Outward surface normal, so spots can rotate out of view. Without this
        # a turned head would keep every spot it started with, which flatters
        # the geometric signal by handing it correspondences a real capture
        # would have lost.
        n = np.column_stack([u, v, np.full(len(u), 1.3)])
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        return cls(ident, z, n)


def _project(xy_mm: np.ndarray, z_mm: np.ndarray, *, rot_deg: float,
             axis: str) -> np.ndarray:
    """
    Rotate a 3D point set about the head's own axis and project it.

    Perspective, not orthographic, and about the head centre rather than the
    camera: that is what produces parallax, and parallax is the entire
    geometric signal. Under orthographic projection a flat photo and a real face
    would be indistinguishable no matter how far either turned.
    """
    th = math.radians(rot_deg)
    x, y, z = xy_mm[:, 0], xy_mm[:, 1], z_mm
    if axis == "y":
        xr = x * math.cos(th) + z * math.sin(th)
        yr = y
        zr = -x * math.sin(th) + z * math.cos(th)
    else:
        xr = x
        yr = y * math.cos(th) - z * math.sin(th)
        zr = y * math.sin(th) + z * math.cos(th)

    denom = CAMERA_DISTANCE - zr
    f = CAMERA_DISTANCE
    return np.column_stack([f * xr / denom, f * yr / denom])


_AXIS = {"left": ("y", -1.0), "right": ("y", 1.0), "up": ("x", 1.0),
         "neutral": ("y", 0.0)}


def _visible(normals: np.ndarray, rot_deg: float, axis: str) -> np.ndarray:
    """
    Which spots still face the camera after the head turns.

    A real turn costs you correspondences — the far cheek rotates away. Flat
    media lose nothing, since a plane's normal is uniform, so modelling this
    makes the attacker's job easier and ours harder. That is the right
    direction for a test to be wrong in.
    """
    th = math.radians(rot_deg)
    nx, ny, nz = normals[:, 0], normals[:, 1], normals[:, 2]
    if axis == "y":
        nz_rot = -nx * math.sin(th) + nz * math.cos(th)
    else:
        nz_rot = ny * math.sin(th) + nz * math.cos(th)
    return nz_rot > GRAZING_COS


def _geometry(subject: Subject, pose: str, rng: np.random.Generator,
              *, flat: bool) -> np.ndarray:
    """One frame's detected constellation, in pixels."""
    axis, sign = _AXIS[pose]
    rot = sign * POSE_DEG

    keep = rng.random(len(subject.identity.spots)) < sc.DETECT_RATE
    if not flat:
        keep &= _visible(subject.normal, rot, axis)

    spots = subject.identity.spots[keep]
    depth = np.zeros(int(keep.sum())) if flat else subject.depth[keep]

    cx, cy = sc.IMG_W / 2, sc.IMG_H / 2
    mm_per_px = FACE_WIDTH / (sc.IMG_W * 0.60)
    xy_mm = (spots[:, :2] - np.array([cx, cy])) * mm_per_px

    projected = _project(xy_mm, depth, rot_deg=rot, axis=axis)

    px = projected / mm_per_px + np.array([cx, cy])
    px += rng.normal(0, sc.SPOT_JITTER * sc.IMG_W, px.shape)
    area = spots[:, 2] * np.exp(rng.normal(0, sc.SIZE_NOISE, len(spots)))
    return np.column_stack([px, area])


def _scores(subject: Subject, colour, gain: float, rng: np.random.Generator,
            *, base: dict[str, float], effect: float) -> dict[str, float]:
    """
    Volatile scores under one illuminant, plus unchanged stable scores.

    `base` is drawn once per session and passed in, which matters more than it
    looks. An earlier version redrew it per frame from the population spread,
    so a person's resting redness moved by ±14 points between frames three
    seconds apart. That is not a camera — it buried an 8-point light response
    under 14 points of invented drift and made a live human score at chance.
    Within one session the baseline is fixed and only the illuminant changes.

    Stable dimensions get measurement noise and nothing else: they are not
    supposed to track the screen colour, which is what lets `presence.py` use
    them to calibrate its own deadband.
    """
    out = {k: float(np.clip(v + rng.normal(0, SCORE_NOISE), 1, 99))
           for k, v in subject.identity.stable.items()}

    # Centred so that a mid-grey illuminant is the zero point and the response
    # is signed, not merely additive.
    lum = colour.luminance - 0.5
    red = colour.red_ratio - 1 / 3

    response = {"hd_radiance": effect * lum, "hd_oiliness": effect * lum,
                "hd_redness": effect * red * 3.0}
    for k, v in base.items():
        out[k] = float(np.clip(v + gain * response.get(k, 0.0)
                               + rng.normal(0, SCORE_NOISE), 1, 99))
    return out


def session(challenge: Challenge, *, medium: str = "live", seed: int = 0,
            subject: Subject | None = None, effect: float = ILLUM_EFFECT,
            issued_at: float = 0.0, latency_ms: float = 400.0) -> list[dict]:
    """
    The frames a client returns for one challenge, under one medium.

    `injection` is the interesting case. It is a recording of a genuine session,
    so its geometry is a real 3D face and its scores are internally plausible —
    they simply have no relationship to a colour sequence that did not exist
    when the recording was made.
    """
    if medium not in GAIN:
        raise ValueError(f"unknown medium {medium!r}; expected one of {sorted(GAIN)}")

    rng = np.random.default_rng(seed)
    subject = subject or Subject.make(1000 + seed)
    flat = medium in ("print", "screen")
    gain = GAIN[medium]

    # Resting volatile levels, fixed for the session. Only the light changes.
    base = {k: float(np.clip(rng.normal(sc.POP_MEAN, sc.POP_STD), 1, 99))
            for k in sc.VOLATILE_HD}

    # A replay knows nothing about this nonce, so whatever illuminant its scores
    # do reflect is unrelated to the one being asked for. Drawing it once, here,
    # is what makes it a *recording* rather than a live response.
    stale = rng.choice(len(challenge.frames))

    frames = []
    t = issued_at + latency_ms / 1000.0
    for f in challenge.frames:
        colour = challenge.frames[stale].colour if gain == 0 else f.colour
        frames.append({
            "frame_index": f.index,
            "captured_at": t,
            "scores": _scores(subject, colour, gain if gain else 1.0, rng,
                              base=base, effect=effect),
            "constellations": {"hd_pore": _geometry(subject, f.pose, rng,
                                                    flat=flat).tolist()},
        })
        t += f.hold_ms / 1000.0 + 0.12
    return frames
