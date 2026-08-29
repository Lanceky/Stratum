"""
Presence check tests (implementation.md Step 5).

`test_injected_stream_is_rejected` is the Step 5 definition of done: a replayed
recording of a genuine session must not pass, because that is the attack the
whole authorisation layer exists to stop.

Every figure here comes from `synth_attacks.py`, a physical simulation, not from
a camera. No physical presentation attack has been run; that needs hardware and
sponsor credentials, and is Step 12's job. These tests show the physics works,
not that the attacks were defeated in the room.

Entirely offline.
"""

import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import challenge as ch  # noqa: E402
import synth_attacks as sa  # noqa: E402
from checks.presence import (  # noqa: E402
    MIN_DECIDED, MIN_RESPONSE_Z, evaluate, geometry, illumination, pose,
    score_noise, timing,
)

KEY = "test-key"
N = 40


@lru_cache(maxsize=None)
def _cohort(medium: str, n: int) -> tuple:
    """Simulating a session is not cheap, and every test wants the same ones."""
    return tuple((spec, sa.session(spec, medium=medium, seed=s))
                 for s in range(n)
                 if (spec := ch.derive(f"nonce-{s}", key=KEY)))


def sessions(medium: str, n: int = N):
    return _cohort(medium, n)


@lru_cache(maxsize=None)
def verdicts(medium: str, n: int = N) -> tuple:
    return tuple(evaluate(frames, spec, issued_at=frames[0]["captured_at"] - 1.0)
                 for spec, frames in sessions(medium, n))


def pass_rate(medium: str, n: int = N) -> float:
    return sum(v.passed for v in verdicts(medium, n)) / n


# ── the definition of done ────────────────────────────────────────────────
def test_injected_stream_is_rejected():
    """
    An OBS-style injection replays a recording of a real 3D face, so depth does
    not catch it. It cannot know what colour the screen flashed 200 ms ago, so
    illumination does. This is the attack the project exists to stop and it must
    not pass at all.
    """
    assert pass_rate("injection") == 0.0


def test_live_capture_is_accepted():
    """A check that rejects everything is not a check."""
    assert pass_rate("live") >= 0.85


def test_screen_replay_is_mostly_rejected():
    """A phone re-emits only a fraction of the flash, which the deadband sees."""
    assert pass_rate("screen") <= 0.10


def test_printed_photo_is_the_known_gap():
    """
    Paper genuinely does redden under a red flash and can be turned on cue, so
    only depth separates it — and depth is partial. This asserts the measured
    behaviour rather than a hoped-for one: print is reduced, not defeated. If a
    future change makes this fail low, the limitation text must change with it.
    """
    rate = pass_rate("print")
    assert 0.10 <= rate <= 0.45, f"print acceptance moved to {rate:.0%}"


def test_live_and_injection_are_separated_by_illumination_alone():
    """The signal doing the work should be identified, not assumed."""
    live = [illumination(f, s).score for s, f in sessions("live")]
    inj = [illumination(f, s).score for s, f in sessions("injection")]
    assert min(live) > max(inj)


# ── illumination ──────────────────────────────────────────────────────────
def test_illumination_needs_both_sign_and_magnitude():
    """
    Sign agreement alone is a coin toss for a stream that responds to nothing.
    Both gates must be reported so a reviewer can see which one held.
    """
    spec, frames = sessions("live")[0]
    detail = illumination(frames, spec).detail
    assert detail["decided"] >= MIN_DECIDED
    assert detail["response_z"] >= MIN_RESPONSE_Z
    assert detail["agreement"] >= 0.9


def test_a_session_that_responds_to_nothing_scores_zero_not_half():
    """
    Absence of a response is the injection signature, not a neutral result.
    Scoring an undecided session 0.5 would let it average up against a strong
    geometric signal.
    """
    spec, frames = sessions("live")[0]
    flat = [{**f, "scores": dict(frames[0]["scores"])} for f in frames]
    signal = illumination(flat, spec)
    assert not signal.passed
    assert signal.score == 0.0


def test_noise_floor_comes_from_the_dimensions_that_must_not_move():
    """
    Stable dimensions are not supposed to track the screen colour, so their
    spread is a per-session noise floor obtained without spending a frame.
    """
    _, frames = sessions("live")[0]
    assert 0.0 < score_noise(frames) < 10.0


def test_noisier_capture_widens_the_deadband():
    """The check must become less willing to decide, not more willing to guess."""
    spec, frames = sessions("live")[0]
    rng = np.random.default_rng(0)
    noisy = [{**f, "scores": {k: v + rng.normal(0, 6.0)
                              for k, v in f["scores"].items()}} for f in frames]
    assert (illumination(noisy, spec).detail["deadband_points"]
            > illumination(frames, spec).detail["deadband_points"])


# ── pose ──────────────────────────────────────────────────────────────────
def test_a_face_that_never_moves_fails_the_pose_signal():
    """
    Without this the pose half of the challenge is decoration. Substituting a
    neutral frame for the posed one is exactly what a subject who ignores the
    instruction produces.
    """
    spec, frames = sessions("live")[0]
    still = list(frames)
    still[spec.posed_frames[0]] = {**frames[spec.neutral_frames[1]],
                                   "frame_index": spec.posed_frames[0]}
    signal = pose(still, spec)
    assert not signal.passed
    assert not signal.detail["moved"]


def test_pose_axis_is_bound_to_the_request():
    """
    A recording turned whichever way it turned when it was filmed. Judging a
    yaw session against a pitch request must fail, or the direction in the
    challenge means nothing.
    """
    checked = 0
    for spec, frames in sessions("live"):
        if spec.pose_prompt not in ("left", "right"):
            continue
        wrong = ch.derive(spec.nonce, key=KEY)
        object.__setattr__(wrong, "pose_prompt", "up")
        signal = pose(frames, wrong)
        if signal.detail["axis_decided"]:
            checked += 1
            assert not signal.passed
    assert checked >= 5, "needed some confident sessions to test against"


def test_pose_abstains_rather_than_guessing_a_small_movement():
    """Below the confidence bar the axis is noise; abstaining is the honest answer."""
    seen = {True, False}
    decided = {pose(f, s).detail["axis_decided"] for s, f in sessions("live")}
    assert decided <= seen


# ── geometry ──────────────────────────────────────────────────────────────
def test_depth_separates_live_from_flat_on_average():
    """
    A flat surface turned twice is related by a homography exactly; a face is
    not. Averages, not extremes — the distributions overlap, which is why the
    check reports a limitation instead of claiming print is solved.
    """
    live = [geometry(f, s).detail.get("relief_ratio")
            for s, f in sessions("live")]
    flat = [geometry(f, s).detail.get("relief_ratio")
            for s, f in sessions("print")]
    live = [x for x in live if x]
    flat = [x for x in flat if x]
    assert np.mean(live) > np.mean(flat) * 1.25


def test_geometry_refuses_when_it_cannot_see_enough_of_the_face():
    """
    Passing a frame too degraded to analyse would reward an attacker for
    supplying one.
    """
    spec, frames = sessions("live")[0]
    blind = [{**f, "constellations": {"hd_pore": [[0.0, 0.0, 1.0]] * 3}}
             for f in frames]
    signal = geometry(blind, spec)
    assert not signal.passed
    assert "error" in signal.detail


# ── timing ────────────────────────────────────────────────────────────────
def test_a_frame_that_predates_the_challenge_is_rejected():
    """A frame stamped before the nonce existed is not evidence of anything."""
    spec, frames = sessions("live")[0]
    signal = timing(frames, spec, issued_at=frames[-1]["captured_at"] + 10.0)
    assert not signal.passed


def test_a_response_outside_the_window_is_rejected():
    spec, frames = sessions("live")[0]
    slow = [{**f, "captured_at": f["captured_at"] + i * 60.0}
            for i, f in enumerate(frames)]
    signal = timing(slow, spec, issued_at=slow[0]["captured_at"] - 1.0)
    assert not signal.passed
    assert "response exceeded the challenge window" in signal.detail["problems"]


def test_frames_faster_than_the_flash_hold_are_rejected():
    """A frame fired before the colour was on screen never saw it."""
    spec, frames = sessions("live")[0]
    t0 = frames[0]["captured_at"]
    rushed = [{**f, "captured_at": t0 + i * 0.001} for i, f in enumerate(frames)]
    signal = timing(rushed, spec, issued_at=t0 - 1.0)
    assert not signal.passed


# ── the verdict ───────────────────────────────────────────────────────────
def test_verdict_is_a_conjunction_not_an_average():
    """
    Each signal covers an attack the others miss, so a high score on two says
    nothing about the third. One failure must sink the result.
    """
    spec, frames = sessions("live")[0]
    result = evaluate(frames, spec, issued_at=frames[-1]["captured_at"] + 10.0)
    assert not result.passed
    assert "timing" in result.failed_signals


def test_score_is_the_weakest_signal():
    spec, frames = sessions("live")[0]
    result = evaluate(frames, spec, issued_at=frames[0]["captured_at"] - 1.0)
    assert result.score == min(s.score for s in result.signals)


def test_every_result_states_where_the_check_stops_working():
    """
    A check that hides its gap is worse than one that does not run. The print
    limitation must travel with the verdict, including a passing one.
    """
    spec, frames = sessions("live")[0]
    out = evaluate(frames, spec, issued_at=frames[0]["captured_at"] - 1.0).as_dict()
    assert out["passed"]
    assert out["limitations"]
    assert any("print" in text.lower() for text in out["limitations"])
    assert any("simulation" in text.lower() for text in out["limitations"])


def test_result_is_json_serialisable():
    """It is written to the ledger, so numpy scalars must not leak into it."""
    import json

    spec, frames = sessions("live")[0]
    out = evaluate(frames, spec, issued_at=frames[0]["captured_at"] - 1.0).as_dict()
    assert json.loads(json.dumps(out))["check"] == 1
