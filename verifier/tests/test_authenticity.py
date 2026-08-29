"""
Tests for check 2 — the per-zone texture authenticity check.

These lean hard on one distinction. Almost everything here tests that the check
*calibrates* correctly and *declines* correctly. Very little tests that it
catches forgeries, because there are no forgeries to catch — `synth_zones`
deliberately models genuine anatomy and a parameterised deviation, never a real
generator. A test asserting "the check catches deepfakes" would be asserting
that the simulator makes deepfakes, which it does not claim to.

So: does it hold its stated false-positive rate on faces it has never seen, does
it refuse to run rather than pass when the evidence is absent, and does each of
its two signals respond to the failure mode it was built for?
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import synth_zones as sz
from checks.authenticity import (MIN_ZONES, TARGET_FLAG_RATE, TESTS,
                                 AuthenticityResult, Baseline, evaluate, fit,
                                 profiles)
from dimensions import PORE_ZONES, WRINKLE_ZONES, ZONED, region


@pytest.fixture(scope="module")
def baseline() -> Baseline:
    """One fit, shared — it is deterministic and the slowest thing here."""
    return fit(sz.population(1200, seed=1))


@pytest.fixture(scope="module")
def held_out() -> list[dict[str, float]]:
    """Genuine faces the baseline has never seen, from a disjoint seed range."""
    return [sz.genuine(10_000_000 + s)[0] for s in range(400)]


# ---------------------------------------------------------------- zone parsing

def test_region_splits_zoned_keys():
    assert region("hd_pore:forehead") == "forehead"
    assert region("hd_wrinkle:nasolabial_left") == "nasolabial_left"


def test_region_returns_none_for_whole_face():
    """A whole-face score has no zone, and must not be mistaken for one."""
    assert region("hd_pore") is None
    assert region("moisture") is None


def test_profiles_groups_by_dimension(held_out):
    parsed = profiles(held_out[0])
    assert "pore" in parsed and "wrinkle" in parsed
    assert len(parsed["pore"].zones) == len(PORE_ZONES)
    assert len(parsed["wrinkle"].zones) == len(WRINKLE_ZONES)


def test_profiles_ignores_unzoned_scores():
    parsed = profiles({"moisture": 50.0, "texture": 40.0})
    assert parsed == {}


def test_profiles_orders_zones_consistently(held_out):
    """
    Zone order has to be stable across faces or the baseline means line up with
    the wrong zones — a bug that produces plausible-looking numbers.
    """
    first = profiles(held_out[0])["pore"].zones
    for face in held_out[1:20]:
        assert profiles(face)["pore"].zones == first


# ------------------------------------------------------------------- anatomy

def test_genuine_faces_have_a_t_zone():
    """
    The premise the whole check rests on: zones differ systematically, in the
    same direction, in everyone. Lower pore score means more porous.
    """
    cohort = sz.population(300, seed=7)
    nose = np.array([f["hd_pore:nose"] for f in cohort])
    cheek = np.array([f["hd_pore:cheek"] for f in cohort])
    assert nose.mean() < cheek.mean() - 10


def test_bilateral_zones_track_each_other():
    """Left and right of one face are correlated; a check that ignored this
    would treat an ordinary asymmetry as evidence."""
    cohort = sz.population(300, seed=8)
    left = np.array([f["hd_wrinkle:nasolabial_left"] for f in cohort])
    right = np.array([f["hd_wrinkle:nasolabial_right"] for f in cohort])
    assert np.corrcoef(left, right)[0, 1] > 0.6


# ------------------------------------------------------------------- fitting

def test_fit_uses_disjoint_faces_for_calibration(baseline):
    """
    The split is what makes the advertised flag rate honest. Fitting and
    calibrating on the same faces makes each one look closer to average than a
    new face will, so the thresholds come out too tight.
    """
    assert baseline.n_fit > 0 and baseline.n_null > 0
    assert baseline.n_fit + baseline.n_null <= 1200


def test_fit_learns_per_zone_means_not_one_pooled_mean(baseline):
    """
    Pooling would compare a nose against a whole-face average and reject real
    people for having a normal T-zone. The spread across learned zone means is
    the evidence that did not happen.
    """
    means = baseline.zone_mean["pore"]
    assert means.std() > 5.0


def test_fit_rejects_a_cohort_with_no_zones():
    with pytest.raises(ValueError, match="no per-zone"):
        fit([{"moisture": 50.0} for _ in range(50)])


def test_fit_rejects_a_cohort_too_small_to_split():
    with pytest.raises(ValueError, match="too small"):
        fit([sz.genuine(s)[0] for s in range(1)])


def test_baseline_marks_itself_provisional(baseline):
    """Until Step 12 refits on real captures, every consumer must be able to see
    that this came from a model."""
    assert baseline.as_dict()["provisional"] is True


# --------------------------------------------------------------- calibration

def test_held_out_genuine_faces_pass_at_the_advertised_rate(baseline, held_out):
    """
    The headline property. If this drifts, every p-value the check reports is a
    different number from the one it claims.
    """
    flagged = sum(not evaluate(f, baseline).passed for f in held_out)
    rate = flagged / len(held_out)
    assert rate <= TARGET_FLAG_RATE * 1.5, f"flagged {rate:.1%} of genuine faces"


def test_flag_budget_is_split_across_the_two_tests(baseline):
    """
    Two tests at 5% each is a 10% flag rate, not 5%. The per-test cut has to be
    tighter than the total, or the advertised rate is wrong by a factor of two.
    """
    alpha = baseline.as_dict()["per_test_alpha"]
    assert alpha == pytest.approx(TARGET_FLAG_RATE / len(TESTS))


def test_a_face_from_the_null_distribution_is_not_flagged_for_being_average(baseline):
    """The population mean face should be the least suspicious input there is."""
    mean_face = {}
    for dim, means in baseline.zone_mean.items():
        for zone, value in zip(ZONED[dim], means):
            mean_face[f"hd_{dim}:{zone}"] = float(value)
    result = evaluate(mean_face, baseline)
    assert result.ran
    assert result.signals[1].passed, "the average face failed the pattern test"


# ------------------------------------------------------------------- signals

def test_smoothing_is_caught_by_contrast_not_by_pattern(baseline):
    """
    A uniformly smoothed face keeps its zone *ordering* and loses its
    *magnitude*. Only the contrast test should notice, and it must notice often.
    """
    faces = [sz.deviated(20_000_000 + s, contrast=0.3) for s in range(150)]
    flags = [evaluate(f, baseline).flagged_by for f in faces]
    by_contrast = sum("contrast" in f for f in flags)
    assert by_contrast / len(faces) > 0.80


def test_scrambled_anatomy_is_caught_by_pattern_not_by_contrast(baseline):
    """
    A shuffled face keeps its overall spread and destroys its ordering — the
    mirror image of the case above. Two tests exist precisely because neither
    one sees both.
    """
    faces = [sz.deviated(30_000_000 + s, shuffle=1.0) for s in range(150)]
    flags = [evaluate(f, baseline).flagged_by for f in faces]
    by_pattern = sum("zone_pattern" in f for f in flags)
    by_contrast = sum("contrast" in f for f in flags)
    assert by_pattern > by_contrast * 3


def test_detection_rises_monotonically_with_deviation(baseline):
    """
    A statistic that detects harder forgeries less often is measuring an
    artefact. An earlier Mahalanobis version failed exactly this way and was
    discarded for it.
    """
    rates = []
    for c in (0.8, 0.6, 0.4, 0.2):
        faces = [sz.deviated(40_000_000 + s, contrast=c) for s in range(120)]
        rates.append(sum(not evaluate(f, baseline).passed for f in faces) / 120)
    assert rates == sorted(rates), rates


def test_each_signal_declares_its_direction():
    """The tail is part of the hypothesis. Picking it after seeing the data is
    how a check ends up fitting noise."""
    assert set(TESTS) == {"contrast", "zone_pattern"}
    assert TESTS["contrast"][0] == "lower"
    assert TESTS["zone_pattern"][0] == "upper"


# ------------------------------------------------------------------ abstention

def test_an_sd_capture_declines_rather_than_passing(baseline):
    """
    SD mode returns no per-zone breakdown. Returning `passed=True` there would
    make a downgrade to SD a free bypass of check 2.
    """
    result = evaluate({"moisture": 60.0, "texture": 55.0, "pore": 48.0}, baseline)
    assert result.ran is False
    assert result.passed is False
    assert "SD" in result.detail["reason"]


def test_a_partial_hd_result_declines(baseline):
    """One dimension present and the other missing is still not enough to judge
    against a baseline built on both."""
    partial = {f"hd_pore:{z}": 50.0 for z in PORE_ZONES}
    result = evaluate(partial, baseline)
    assert result.ran is False
    assert "wrinkle" in result.detail["needed"]


def test_too_few_zones_declines(baseline):
    face = {"hd_pore:forehead": 50.0, "hd_pore:nose": 40.0}
    assert evaluate(face, baseline).ran is False
    assert MIN_ZONES >= 3


def test_a_declined_check_scores_zero_not_a_half(baseline):
    """
    Scoring an abstention at 0.5 would let it drag a fused verdict upward. It
    carries no information and must contribute none.
    """
    result = evaluate({"moisture": 60.0}, baseline)
    assert result.score == 0.0


# -------------------------------------------------------------------- output

def test_result_is_json_serialisable(baseline, held_out):
    """
    Numpy scalars survive arithmetic and die at the FastAPI boundary. This bit
    Step 5 and would bite here identically.
    """
    payload = evaluate(held_out[0], baseline).as_dict()
    text = json.dumps(payload)
    assert "NaN" not in text and "Infinity" not in text
    for signal in payload["signals"]:
        assert isinstance(signal["passed"], bool)
        assert isinstance(signal["p_value"], float)


def test_result_names_which_signal_objected(baseline):
    """
    A reviewer opening a flagged gate needs to know which property failed, not
    just that something did.
    """
    faces = [sz.deviated(50_000_000 + s, contrast=0.2) for s in range(40)]
    flagged = [r for r in (evaluate(f, baseline) for f in faces) if not r.passed]
    assert flagged
    assert all(r.flagged_by for r in flagged)


def test_result_reports_p_values_not_probabilities_of_forgery(baseline, held_out):
    """
    A p-value is the share of genuine faces this extreme. It is not the
    probability the face is fake, and the check must never present it as one.
    """
    payload = evaluate(held_out[0], baseline).as_dict()
    assert all(0.0 <= s["p_value"] <= 1.0 for s in payload["signals"])
    assert any("not" in line.lower() or "cannot" in line.lower()
               for line in payload["limitations"])


def test_limitations_state_that_no_generated_face_was_scored(baseline, held_out):
    """
    The one claim that must never be lost in transit: this check has never been
    run against a real generator's output.
    """
    text = " ".join(evaluate(held_out[0], baseline).as_dict()["limitations"]).lower()
    assert "generat" in text or "synthetic" in text


def test_per_dimension_detail_exposes_the_offending_zone(baseline, held_out):
    detail = evaluate(held_out[0], baseline).as_dict()["detail"]
    assert "pore" in detail["per_dimension"]
    assert set(detail["per_dimension"]["pore"]["zone_z"]) == set(PORE_ZONES)


def test_score_is_bounded(baseline, held_out):
    for face in held_out[:60]:
        assert 0.0 <= evaluate(face, baseline).score <= 1.0
