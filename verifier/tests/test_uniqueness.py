"""
Check 4: the 1:N sweep.

The cohort is synthetic, so every figure here measures the matcher rather than
real skin — the same caveat check 3 carries. What these tests are really for is
the decision logic on top of the distances, which is where 1:N differs from 1:1
and where the mistakes would be silent:

  * a sweep that could not compare some of the roster must not report a clean
    result for the part it skipped,
  * a duplicate found across a roster large enough for chance collisions must
    not be auto-decided,
  * and the false-match constant that governs the above must fail the build if
    the matcher drifts under it.
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks.binding import LOWER_THRESHOLD, UPPER_THRESHOLD  # noqa: E402
from checks.uniqueness import (  # noqa: E402
    AUTO_DECIDE_MAX_FMR, FMR_TRIALS, PER_COMPARISON_FMR, VERDICTS,
    family_false_match, sweep,
)
from normalise import normalise_bundle  # noqa: E402
from synth_cohort import POSES, Identity, capture, sibling  # noqa: E402


@pytest.fixture(scope="module")
def people():
    return [Identity(seed=9000 + i) for i in range(8)]


def bundle(person, pose=0, seed=0):
    return normalise_bundle(capture(person, seed, **POSES[pose]))


def roster_of(people, pose=0):
    return [(f"enr-{i}", bundle(p, pose, seed=i)) for i, p in enumerate(people)]


# ── the two directions that must never be wrong ───────────────────────────
def test_a_returning_claimant_is_found(people):
    """The point of the check: claiming twice with the same face is detected."""
    roster = roster_of(people)
    for i, p in enumerate(people):
        r = sweep(bundle(p, 1, seed=i + 500), roster)
        assert r.verdict in ("DUPLICATE", "REVIEW"), r.reason
        assert r.nearest.enrolment_id == f"enr-{i}"
        assert not r.passed


def test_a_newcomer_is_not_turned_away(people):
    """
    The asymmetric rule, pointed the other way from check 3. A person nobody
    has seen must never be auto-flagged as a duplicate by the machine alone.
    """
    roster = roster_of(people[:-1])
    newcomer = people[-1]
    r = sweep(bundle(newcomer, 1, seed=777), roster)
    assert r.verdict != "DUPLICATE", r.reason


def test_a_sibling_is_not_a_duplicate(people):
    """
    A lookalike is a different person with a claim of their own. Auto-flagging
    them as a duplicate takes an allocation from someone entitled to it.
    """
    roster = roster_of(people)
    for i, p in enumerate(people):
        r = sweep(normalise_bundle(sibling(p, i + 11, **POSES[1])), roster)
        assert r.verdict != "DUPLICATE", f"sibling {i} auto-flagged: {r.reason}"


# ── the first claim ───────────────────────────────────────────────────────
def test_an_empty_roster_is_unique(people):
    r = sweep(bundle(people[0]), [])
    assert r.verdict == "UNIQUE"
    assert r.ran, "an empty roster is an answer, not a failure to run"
    assert r.roster_size == 0 and r.compared == 0
    assert "nobody yet" in r.reason


def test_verdict_is_always_one_of_the_declared_three(people):
    roster = roster_of(people)
    for i, p in enumerate(people):
        assert sweep(bundle(p, 1, seed=i + 500), roster).verdict in VERDICTS


# ── what the sweep did not cover ──────────────────────────────────────────
def test_an_uncomparable_enrolment_is_not_silently_cleared(people):
    """
    The quiet failure. An enrolment sharing nothing comparable with the probe
    has not been ruled out, and counting it as "did not match" would report a
    uniqueness that was never established.
    """
    roster = roster_of(people[:3]) + [("enr-blank", {})]
    r = sweep(bundle(people[5], 1, seed=41), roster)

    assert r.verdict == "REVIEW"
    assert len(r.skipped) == 1
    assert r.skipped[0].enrolment_id == "enr-blank"
    assert "could not be compared" in r.reason


def test_a_wholly_uncomparable_roster_reviews_rather_than_passing(people):
    r = sweep(bundle(people[0]), [("enr-blank", {}), ("enr-blank-2", {})])
    assert r.verdict == "REVIEW"
    assert not r.ran, "nothing was compared, so nothing ran"
    assert r.compared == 0
    assert r.nearest is None


def test_a_duplicate_still_wins_over_an_incomplete_sweep(people):
    """A positive finding is not weakened by an unrelated gap in the roster."""
    p = people[0]
    roster = [("enr-0", bundle(p, 0, seed=0)), ("enr-blank", {})]
    r = sweep(bundle(p, 1, seed=500), roster)
    assert r.verdict == "DUPLICATE", r.reason
    assert r.nearest.enrolment_id == "enr-0"


# ── the roster size is part of the verdict ────────────────────────────────
def test_family_false_match_grows_with_the_roster():
    assert family_false_match(0) == 0.0
    small, large = family_false_match(10), family_false_match(10_000)
    assert small < large < 1.0
    assert small == pytest.approx(10 * PER_COMPARISON_FMR, rel=0.02)


def test_family_false_match_never_exceeds_certainty():
    """
    The reason for 1-(1-p)^n over n*p. The linear form passes 1.0 at n=435 and
    would report a probability above certainty; this form saturates at it.
    """
    assert 435 * PER_COMPARISON_FMR > 1.0, "the linear form would overflow here"
    assert family_false_match(435) < 1.0
    assert family_false_match(1_000_000) <= 1.0


def test_a_duplicate_is_not_auto_decided_across_a_large_roster(people):
    """
    The policy that makes this honest. Above the ceiling, a duplicate becomes a
    question for a person rather than a finding the machine may act on.
    """
    p = people[0]
    roster = [("enr-0", bundle(p, 0, seed=0))]
    # A per-comparison rate that puts a one-entry sweep over the ceiling, which
    # is the same arithmetic a real roster of thousands reaches on its own.
    r = sweep(bundle(p, 1, seed=500), roster,
              per_comparison_fmr=AUTO_DECIDE_MAX_FMR * 2)

    assert r.verdict == "REVIEW"
    assert r.nearest.distance < LOWER_THRESHOLD, "still inside the duplicate band"
    assert "without a person" in r.reason


def test_the_same_match_is_a_duplicate_on_a_small_roster(people):
    """The contrast to the test above: only the roster size differs."""
    p = people[0]
    r = sweep(bundle(p, 1, seed=500), [("enr-0", bundle(p, 0, seed=0))])
    assert r.verdict == "DUPLICATE"


# ── the constant the policy rests on ──────────────────────────────────────
def test_no_different_person_reaches_the_duplicate_band():
    """
    The measurement behind PER_COMPARISON_FMR, asserted rather than described.

    Zero different-person comparisons below LOWER_THRESHOLD gives a rule-of-
    three 95% upper bound of 3/n. If the matcher drifts and this starts
    failing, the constant governing auto-decision is wrong and the build should
    say so rather than the number quietly becoming a fiction.
    """
    n = 14
    people = [Identity(seed=9000 + i) for i in range(n)]
    enrol = [normalise_bundle(capture(p, i, **POSES[0])) for i, p in enumerate(people)]
    probe = [normalise_bundle(capture(p, i + 400, **POSES[1])) for i, p in enumerate(people)]

    worst, trials = float("inf"), 0
    for i, j in itertools.permutations(range(n), 2):
        r = sweep(probe[j], [(f"enr-{i}", enrol[i])])
        trials += 1
        worst = min(worst, r.nearest.distance)

    assert worst >= LOWER_THRESHOLD, (
        f"a different person reached {worst:.2f}, inside the duplicate band")
    assert PER_COMPARISON_FMR == 3 / FMR_TRIALS, (
        "the declared bound no longer matches the rule of three")
    assert trials == n * (n - 1)


def test_score_is_the_inverse_of_binding(people):
    """Far from everyone is the good outcome here, unlike check 3."""
    roster = roster_of(people[:-1])
    far = sweep(bundle(people[-1], 1, seed=777), roster)
    near = sweep(bundle(people[0], 1, seed=500), roster)
    assert far.score > near.score
    assert 0.0 <= near.score <= 1.0 and 0.0 <= far.score <= 1.0


def test_as_dict_reports_the_roster_and_the_bound(people):
    d = sweep(bundle(people[0], 1, seed=500), roster_of(people)).as_dict()
    assert d["check"] == 4 and d["name"] == "uniqueness"
    assert d["roster_size"] == len(people)
    assert d["comparisons_run"] == len(people)
    assert d["false_match"]["per_comparison_bound"] == PER_COMPARISON_FMR
    assert d["false_match"]["across_this_sweep"] > 0
    assert d["thresholds"] == {"duplicate_below": LOWER_THRESHOLD,
                               "unique_above": UPPER_THRESHOLD}
    assert d["limitations"], "a check that states no limits is not being honest"
