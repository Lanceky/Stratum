"""
Challenge derivation tests (implementation.md Step 5).

The properties that matter here are not about colours. They are that the spec
cannot be influenced by the client, cannot be predicted without the secret, and
never tells the client what it is about to be measured on.

Entirely offline.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import challenge as ch  # noqa: E402

KEY = "test-key"


def test_derivation_is_deterministic():
    """Same nonce, same secret, same challenge — the server keeps no state."""
    a = ch.derive("nonce-one", key=KEY)
    b = ch.derive("nonce-one", key=KEY)
    assert a.client_view() == b.client_view()
    assert [p.as_dict() for p in a.predictions] == [p.as_dict() for p in b.predictions]


def test_different_nonces_give_different_challenges():
    views = {str(ch.derive(f"nonce-{i}", key=KEY).client_view()) for i in range(20)}
    assert len(views) > 15, "nonce should drive the spec, not decorate it"


def test_secret_is_what_stops_precomputation():
    """
    Without the secret the nonce is public and the spec would be forgeable
    before disclosure. Two keys on the same nonce must disagree.
    """
    a = ch.derive("nonce-one", key="key-a")
    b = ch.derive("nonce-one", key="key-b")
    assert a.client_view() != b.client_view()


def test_client_view_withholds_the_predictions():
    """
    Telling a client which way each score must move is telling a forger what to
    fake. The client learns the colours and the pose; nothing else.
    """
    view = ch.derive("nonce-one", key=KEY).client_view()
    flat = str(view)
    assert "prediction" not in flat
    assert "brighter" not in flat and "darker" not in flat
    assert set(view) == {"frames", "pose_prompt", "hold_ms", "window_ms", "flashing"}


def test_exactly_one_hd_frame_and_it_is_neutral():
    """
    Credit discipline, expressed where it cannot be forgotten: HD analysis costs
    12-22 units. The HD frame is also the one checks 2 and 3 read, so it must
    not be the one taken mid-turn.
    """
    for i in range(30):
        spec = ch.derive(f"nonce-{i}", key=KEY)
        hd = [f for f in spec.frames if f.hd]
        assert len(hd) == 1
        assert hd[0].pose == "neutral"
        assert spec.hd_frame == hd[0].index


def test_enough_neutral_frames_for_a_noise_floor():
    """Check 1 needs same-pose pairs: for its deadband and for the depth scale."""
    for i in range(20):
        spec = ch.derive(f"nonce-{i}", key=KEY)
        assert len(spec.neutral_frames) >= 4
        assert len(spec.posed_frames) == 1


def test_posed_frame_is_last():
    """The client holds still, then moves once — not a dance."""
    for i in range(20):
        spec = ch.derive(f"nonce-{i}", key=KEY)
        assert spec.posed_frames == (len(spec.frames) - 1,)


def test_every_frame_gets_a_distinct_colour():
    """Two frames under the same colour would make an untestable prediction."""
    for i in range(20):
        hexes = [f.colour.hex for f in ch.derive(f"nonce-{i}", key=KEY).frames]
        assert len(set(hexes)) == len(hexes)


def test_predictions_are_pairwise_and_only_compare_same_pose_frames():
    """
    A pose change moves the face relative to the light, so a bright frame taken
    mid-turn is not comparable with a neutral one. Comparing them would fail
    honest captures for a reason that has nothing to do with liveness.
    """
    for i in range(20):
        spec = ch.derive(f"nonce-{i}", key=KEY)
        neutral = set(spec.neutral_frames)
        assert spec.predictions
        for p in spec.predictions:
            assert p.brighter in neutral and p.darker in neutral
            assert p.brighter != p.darker


def test_predictions_carry_a_real_margin():
    """A prediction between two near-identical colours is a coin toss."""
    for i in range(20):
        for p in ch.derive(f"nonce-{i}", key=KEY).predictions:
            assert p.margin > 0


def test_too_few_frames_is_refused():
    """
    Three frames yield one comparable pair, which cannot clear the illumination
    check's evidence bar — so it would abstain on every session, honest and
    attacker alike. Refusing is better than shipping a check that measures
    nothing.
    """
    with pytest.raises(ValueError):
        ch.derive("nonce-one", n_frames=3, key=KEY)


def test_more_frames_than_colours_is_refused():
    with pytest.raises(ValueError):
        ch.derive("nonce-one", n_frames=len(ch.PALETTE) + 1, key=KEY)


def test_more_frames_give_more_predictions():
    """
    Same-pose pairs grow as C(n-1, 2), which is why five frames is the floor and
    not an arbitrary choice.
    """
    counts = [len(ch.derive("nonce-one", n_frames=n, key=KEY).predictions)
              for n in range(ch.MIN_FRAMES, len(ch.PALETTE) + 1)]
    assert counts == sorted(counts)
    assert counts[-1] > counts[0]


# ── WCAG 2.3.1: photosensitive seizure safety ────────────────────────────────
#
# These are the only tests in this file about how the challenge *looks* rather
# than what it proves, and they are here because the failure mode is a seizure
# rather than a false accept. A future palette change that reintroduces a
# saturated red, or a caller that shortens the hold to make the demo snappier,
# has to walk past them.


def test_no_palette_colour_is_a_saturated_red():
    """
    WCAG singles out saturated red because it provokes seizures at lower
    intensities than any other hue. The threshold is on *linearised* light, and
    the distinction is not academic: the red this palette used to contain reads
    0.766 by the raw channel ratio, safely under the 0.8 limit, and 0.960 once
    the display gamma is undone. Asserting on the linear form is the whole test.
    """
    offenders = [c.name for c in ch.PALETTE if c.saturated_red]
    assert offenders == [], f"saturated red in the palette: {offenders}"


def test_the_steady_illuminant_is_not_a_saturated_red_either():
    assert not ch.STEADY.saturated_red


def test_saturated_red_is_measured_on_linearised_light():
    """The naive ratio clears a colour the standard forbids — guard the helper."""
    naive_red = ch.Colour("naive", "#FF2E20")
    assert naive_red.red_ratio < ch.SATURATED_RED_RATIO
    assert naive_red.saturated_red


def test_every_frame_is_held_below_three_flashes_a_second():
    for i in range(50):
        spec = ch.derive(f"nonce-{i}", key=KEY)
        assert spec.hold_ms >= ch.MIN_HOLD_MS
        assert all(f.hold_ms >= ch.MIN_HOLD_MS for f in spec.frames)
        # The rate the guard exists to bound, stated as a rate.
        assert 1000.0 / spec.hold_ms <= 3.0


def test_a_faster_sequence_is_refused_rather_than_clamped():
    """
    Silently clamping would leave the caller believing they had negotiated a
    quicker capture, and the mismatch would surface as a timing failure blamed
    on the person in front of the camera.
    """
    with pytest.raises(ValueError, match="2.3.1"):
        ch.derive("nonce-one", hold_ms=ch.MIN_HOLD_MS - 1, key=KEY)


def test_the_whole_sequence_still_fits_the_response_window():
    """The slower holds must not push an honest capture past its own deadline."""
    spec = ch.derive("nonce-one", key=KEY)
    assert len(spec.frames) * spec.hold_ms < spec.window_ms


# ── the non-flashing path ────────────────────────────────────────────────────


def test_the_non_flashing_challenge_never_changes_colour():
    """One colour for the whole capture: one transition, and one is not a flash."""
    spec = ch.derive("nonce-one", flashing=False, key=KEY)
    assert not spec.flashing
    assert {f.colour.hex for f in spec.frames} == {ch.STEADY.hex}


def test_the_non_flashing_challenge_predicts_nothing():
    """
    No colour change, no light response, nothing to predict. Emitting
    predictions here would be asserting physics that was never provoked.
    """
    assert ch.derive("nonce-one", flashing=False, key=KEY).predictions == ()


def test_an_empty_prediction_set_is_still_refused_when_flashing(monkeypatch):
    """The accessible path relaxes the emptiness guard; it must not remove it."""
    flat = tuple(ch.Colour(f"grey{i}", "#808080") for i in range(len(ch.PALETTE)))
    monkeypatch.setattr(ch, "PALETTE", flat)
    with pytest.raises(ValueError, match="no testable prediction"):
        ch.derive("nonce-one", flashing=True, key=KEY)


def test_the_non_flashing_challenge_still_asks_for_a_pose():
    """
    The light response is gone; the parallax evidence is not. A steady capture
    that also dropped the pose would be four photographs of someone sitting
    still, which is not a reduced test but no test at all.
    """
    spec = ch.derive("nonce-one", flashing=False, key=KEY)
    assert spec.pose_prompt in ch.POSES[1:]
    assert len(spec.posed_frames) == 1
    assert len(spec.neutral_frames) == len(spec.frames) - 1


def test_the_non_flashing_pose_is_still_bound_to_the_nonce():
    """
    Fixing the colours must not fix the pose. If the prompt were predictable a
    recorded clip could satisfy the accessible path forever, and the path
    offered on safety grounds would become the one an attacker picks.
    """
    prompts = {ch.derive(f"nonce-{i}", flashing=False, key=KEY).pose_prompt
               for i in range(40)}
    assert len(prompts) > 1


def test_the_non_flashing_challenge_holds_the_same_rate_floor():
    spec = ch.derive("nonce-one", flashing=False, key=KEY)
    assert spec.hold_ms >= ch.MIN_HOLD_MS
