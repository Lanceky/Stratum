"""
Check 3: does the identity binding hold, and does it admit what it cannot tell?

The cohort is synthetic, so every separation figure here is a statement about
the *matcher* — not evidence that real skin separates. Identities are built with
distinct spot patterns by construction; measuring that they come out distinct
measures the code, not the world. Step 12's genuine set decides the real
question. These tests therefore check behaviour and failure handling, and treat
the numbers as fixtures rather than findings.
"""

import numpy as np
import pytest

from checks.binding import (CHANNEL_WEIGHTS, DEFAULT_CALIBRATION,
                            LOWER_THRESHOLD, MIN_INLIERS, UPPER_THRESHOLD,
                            VERDICTS, evaluate)
from normalise import normalise_bundle
from synth_cohort import (POSES, Identity, capture, changed_appearance,
                          degraded, sibling)


@pytest.fixture(scope="module")
def people():
    return [Identity(seed=7000 + i) for i in range(8)]


def bundle(person, pose=0, seed=0):
    return normalise_bundle(capture(person, seed, **POSES[pose]))


# ── the two directions that must never be wrong ───────────────────────────
def test_same_person_second_capture_passes(people):
    v = [evaluate(bundle(p, 0, seed=i), bundle(p, 1, seed=i + 500)).verdict
         for i, p in enumerate(people)]
    assert v.count("PASS") >= len(v) - 1


def test_a_genuine_person_is_never_auto_rejected(people):
    """
    The invariant that matters more than the pass rate.

    A genuine capture may register badly enough to need a human — that is what
    REVIEW is for — but it must never be turned away by the machine alone.
    """
    for i, p in enumerate(people):
        r = evaluate(bundle(p, 0, seed=i), bundle(p, 1, seed=i + 500))
        assert r.verdict != "FAIL", f"identity {i} auto-rejected itself: {r.reason}"


def test_different_person_fails(people):
    for i in range(len(people) - 1):
        r = evaluate(bundle(people[i], 0, seed=i),
                     bundle(people[i + 1], 1, seed=i + 900))
        assert r.verdict == "FAIL"


def test_no_impostor_reaches_the_pass_band(people):
    """The asymmetric rule: a wrong PASS moves money, a wrong FAIL stalls a deal."""
    worst = min(evaluate(bundle(people[i], 0, seed=i),
                         bundle(people[j], 1, seed=j + 40)).distance
                for i in range(len(people)) for j in range(len(people)) if i != j)
    assert worst > LOWER_THRESHOLD


def test_genuine_distances_sit_well_below_the_pass_line(people):
    """Typical genuine captures, not the worst one — poor registrations exist."""
    d = sorted(evaluate(bundle(p, 0, seed=i), bundle(p, 1, seed=i + 77)).distance
               for i, p in enumerate(people))
    assert d[len(d) // 2] < LOWER_THRESHOLD


# ── the review band, which has to actually fire ───────────────────────────
def test_degraded_capture_reaches_review(people):
    """A REVIEW band no real case lands in is decoration, not a safety feature."""
    seen = {evaluate(bundle(p, 0, seed=i),
                     normalise_bundle(degraded(p, i + 3, **POSES[1])))
            .verdict for i, p in enumerate(people)}
    assert "REVIEW" in seen


def test_degraded_genuine_is_never_auto_rejected(people):
    """Bad lighting is the user's camera, not their fault. A human decides."""
    for i, p in enumerate(people):
        r = evaluate(bundle(p, 0, seed=i),
                     normalise_bundle(degraded(p, i + 3, **POSES[1])))
        assert r.verdict != "FAIL", f"identity {i} auto-rejected for bad lighting"


def test_sibling_does_not_pass(people):
    for i, p in enumerate(people):
        r = evaluate(bundle(p, 0, seed=i),
                     normalise_bundle(sibling(p, i + 11, **POSES[1])))
        assert r.verdict != "PASS"


def test_honest_captures_overlap_siblings():
    """
    The finding that contradicts the plan, pinned so it cannot be quietly lost.

    The plan assumed relatives would need REVIEW because they *look* alike. They
    do land in REVIEW — but not for that reason. The spot constellation
    separates them cleanly, because moles form stochastically in development
    rather than being inherited. What actually forces REVIEW is that the worst
    honest capture reaches further than the nearest sibling, so the two
    populations overlap and no threshold can separate them.

    If this ever stops holding, the thresholds were derived under an assumption
    that no longer applies and must be re-fitted rather than trusted.
    """
    wide = [Identity(seed=9000 + i) for i in range(24)]
    honest, sib = [], []
    for i, p in enumerate(wide):
        e = bundle(p, 0, seed=i)
        pose = (i % 2) + 1
        honest += [
            evaluate(e, bundle(p, pose, seed=i + 500)).distance,
            evaluate(e, normalise_bundle(degraded(p, i + 3, **POSES[pose]))).distance,
            evaluate(e, normalise_bundle(changed_appearance(p, i + 5, **POSES[pose]))).distance,
        ]
        sib.append(evaluate(e, normalise_bundle(sibling(p, i + 11, **POSES[pose]))).distance)
    assert max(honest) > min(sib), "overlap gone — re-derive the thresholds"


def test_no_honest_capture_is_auto_rejected_and_no_impostor_survives():
    """The two thresholds are placed around the overlap, not inside it."""
    wide = [Identity(seed=9000 + i) for i in range(24)]
    for i, p in enumerate(wide):
        e = bundle(p, 0, seed=i)
        pose = (i % 2) + 1
        for probe in (bundle(p, pose, seed=i + 500),
                      normalise_bundle(degraded(p, i + 3, **POSES[pose])),
                      normalise_bundle(changed_appearance(p, i + 5, **POSES[pose]))):
            assert evaluate(e, probe).verdict != "FAIL"
        impostor = bundle(wide[(i + 1) % len(wide)], pose, seed=i + 900)
        assert evaluate(e, impostor).verdict == "FAIL"


def test_changed_appearance_still_passes_or_reviews(people):
    """A haircut and a tan must not read as a different person."""
    for i, p in enumerate(people):
        r = evaluate(bundle(p, 0, seed=i),
                     normalise_bundle(changed_appearance(p, i + 5, **POSES[1])))
        assert r.verdict in ("PASS", "REVIEW")


# ── channels ──────────────────────────────────────────────────────────────
def test_missing_channel_is_dropped_not_scored_zero(people):
    """Scoring an absent channel as agreement would manufacture a match."""
    a, b = bundle(people[0], 0, seed=1), bundle(people[0], 1, seed=2)
    b["constellations"] = {}
    r = evaluate(a, b)
    ran = {c.name for c in r.channels if c.ran}
    assert "constellation" not in ran and r.ran


def test_weights_renormalise_when_a_channel_drops(people):
    a, b = bundle(people[0], 0, seed=1), bundle(people[0], 1, seed=2)
    b["constellations"] = {}
    r = evaluate(a, b)
    assert sum(c.effective_weight for c in r.channels) == pytest.approx(1.0)
    assert sum(c.weight for c in r.channels if c.ran) < 1.0


def test_all_channels_missing_means_did_not_run():
    empty = {"scores": {}, "constellations": {}, "face_attributes": {}}
    r = evaluate(empty, empty)
    assert r.ran is False and r.verdict == "REVIEW"


def test_did_not_run_is_not_a_pass():
    empty = {"scores": {}, "constellations": {}, "face_attributes": {}}
    assert evaluate(empty, empty).passed is False


def test_weights_sum_to_one():
    assert sum(CHANNEL_WEIGHTS.values()) == pytest.approx(1.0)


def test_constellation_carries_the_most_weight():
    """Ordered by measured d-prime, not by intuition about what should matter."""
    assert max(CHANNEL_WEIGHTS, key=CHANNEL_WEIGHTS.get) == "constellation"


def test_too_few_inliers_abstains(people):
    a = bundle(people[0], 0, seed=1)
    b = bundle(people[0], 1, seed=2)
    b["constellations"] = {k: {**v, "points": v["points"][:MIN_INLIERS - 1]}
                           for k, v in b["constellations"].items()}
    r = evaluate(a, b)
    ch = {c.name: c for c in r.channels}["constellation"]
    assert ch.ran is False


# ── shape and reporting ───────────────────────────────────────────────────
def test_verdict_is_always_one_of_three(people):
    r = evaluate(bundle(people[0], 0, seed=1), bundle(people[1], 1, seed=2))
    assert r.verdict in VERDICTS


def test_thresholds_are_ordered():
    assert LOWER_THRESHOLD < UPPER_THRESHOLD


def test_result_is_json_serialisable(people):
    import json
    json.dumps(evaluate(bundle(people[0], 0, seed=1),
                        bundle(people[0], 1, seed=2)).as_dict())


def test_no_numpy_scalars_leak(people):
    d = evaluate(bundle(people[0], 0, seed=1), bundle(people[0], 1, seed=2)).as_dict()

    def walk(o):
        if isinstance(o, dict):
            return all(walk(v) for v in o.values())
        if isinstance(o, list):
            return all(walk(v) for v in o)
        return not isinstance(o, np.generic)

    assert walk(d)


def test_every_verdict_carries_a_reason(people):
    for probe in (bundle(people[0], 1, seed=2), bundle(people[1], 1, seed=3)):
        assert evaluate(bundle(people[0], 0, seed=1), probe).reason


def test_reason_names_the_channel_that_drove_it(people):
    r = evaluate(bundle(people[0], 0, seed=1), bundle(people[1], 1, seed=2))
    assert any(c.name in r.reason for c in r.channels)


def test_limitations_are_reported(people):
    d = evaluate(bundle(people[0], 0, seed=1), bundle(people[0], 1, seed=2)).as_dict()
    assert d["limitations"]


def test_limitations_disclose_the_overlap(people):
    d = evaluate(bundle(people[0], 0, seed=1), bundle(people[0], 1, seed=2)).as_dict()
    assert any("overlap" in x.lower() for x in d["limitations"])


def test_score_is_bounded(people):
    for probe in (bundle(people[0], 1, seed=2), bundle(people[1], 1, seed=3)):
        assert 0.0 <= evaluate(bundle(people[0], 0, seed=1), probe).score <= 1.0


def test_calibration_is_marked_provisional():
    assert DEFAULT_CALIBRATION.as_dict()["provisional"] is True


def test_evaluate_is_symmetric(people):
    """Swapping enrolment and probe must not change whether someone is themself."""
    a, b = bundle(people[0], 0, seed=1), bundle(people[1], 1, seed=2)
    assert evaluate(a, b).verdict == evaluate(b, a).verdict


def test_identical_input_is_maximally_close(people):
    a = bundle(people[0], 0, seed=1)
    assert evaluate(a, a).distance <= evaluate(a, bundle(people[0], 1, seed=2)).distance
