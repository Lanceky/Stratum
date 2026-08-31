"""Fusion: does the decision layer refuse to pass on missing evidence?"""

import pytest

from fusion import FAIL, PASS, REVIEW, CheckOutcome, Decision, fuse


def result(ran=True, passed=True, score=0.9, verdict=None, reason=""):
    return {"ran": ran, "passed": passed, "score": score,
            "verdict": verdict, "detail": {"reason": reason}}


def all_good():
    return {"presence": result(score=0.9),
            "authenticity": result(score=0.8),
            "binding": result(score=0.95, verdict=PASS)}


# ── the happy path ────────────────────────────────────────────────────────
def test_every_check_satisfied_passes():
    assert fuse(all_good()).verdict == PASS


def test_pass_states_why():
    assert fuse(all_good()).reasons == ["every check ran and was satisfied"]


def test_pass_does_not_require_human():
    assert fuse(all_good()).requires_human is False


# ── absence is not a pass ─────────────────────────────────────────────────
def test_check_that_did_not_run_blocks_pass():
    """The SD-downgrade bypass: ask for SD, check 2 vanishes, gate still passes."""
    r = all_good()
    r["authenticity"] = result(ran=False, passed=False, score=0.0)
    assert fuse(r).verdict == REVIEW


def test_absent_check_is_explained_not_just_flagged():
    r = all_good()
    r["authenticity"] = result(ran=False, passed=False, score=0.0)
    reason = " ".join(fuse(r).reasons)
    assert "authenticity" in reason and "no evidence" in reason


def test_omitted_required_check_blocks_pass():
    """Not submitting a check at all must not be safer than submitting a bad one."""
    r = all_good()
    del r["binding"]
    d = fuse(r)
    assert d.verdict == REVIEW
    assert "binding" in " ".join(d.reasons)


def test_omitted_authenticity_still_reviews():
    r = all_good()
    del r["authenticity"]
    assert fuse(r).verdict == REVIEW


def test_absent_check_cannot_be_outvoted_by_high_scores():
    """A weighted mean would let two strong checks carry a missing third."""
    r = {"presence": result(score=1.0), "binding": result(score=1.0, verdict=PASS),
         "authenticity": result(ran=False, passed=False, score=0.0)}
    d = fuse(r)
    assert d.score > 0.9 and d.verdict == REVIEW


# ── violations ────────────────────────────────────────────────────────────
def test_failed_presence_fails_the_gate():
    r = all_good()
    r["presence"] = result(passed=False, score=0.1, reason="injected stream")
    assert fuse(r).verdict == FAIL


def test_failure_carries_the_checks_own_reason():
    r = all_good()
    r["presence"] = result(passed=False, score=0.1, reason="injected stream")
    assert "injected stream" in " ".join(fuse(r).reasons)


def test_violation_outranks_absence():
    """Reporting 'c2 could not run' would bury the finding that matters."""
    r = {"presence": result(),
         "authenticity": result(ran=False, passed=False, score=0.0),
         "binding": result(passed=False, score=0.0, verdict=FAIL,
                           reason="different person")}
    d = fuse(r)
    assert d.verdict == FAIL
    assert "different person" in d.reasons[0]


def test_a_perfect_match_on_a_fake_stream_is_not_two_thirds_of_a_pass():
    r = {"presence": result(passed=False, score=0.0, reason="replay"),
         "authenticity": result(score=1.0),
         "binding": result(score=1.0, verdict=PASS)}
    assert fuse(r).verdict == FAIL


def test_all_three_failing_reports_all_three():
    r = {"presence": result(passed=False, reason="a"),
         "authenticity": result(passed=False, reason="b"),
         "binding": result(passed=False, verdict=FAIL, reason="c")}
    assert len(fuse(r).reasons) == 3


def test_violation_without_a_reason_still_explains_itself():
    r = all_good()
    r["presence"] = result(passed=False, score=0.0)
    assert fuse(r).reasons[0].strip() != "presence:"


# ── the review band ───────────────────────────────────────────────────────
def test_binding_review_reviews_the_gate():
    r = all_good()
    r["binding"] = result(passed=False, score=0.5, verdict=REVIEW,
                          reason="degraded capture")
    assert fuse(r).verdict == REVIEW


def test_review_is_not_treated_as_a_violation():
    """REVIEW means unsettled. Folding it into FAIL would auto-reject honest users."""
    r = all_good()
    r["binding"] = result(passed=False, score=0.5, verdict=REVIEW, reason="x")
    assert fuse(r).verdict != FAIL


def test_review_requires_human():
    r = all_good()
    r["binding"] = result(passed=False, score=0.5, verdict=REVIEW, reason="x")
    assert fuse(r).requires_human is True


def test_review_reason_reaches_the_reviewer():
    r = all_good()
    r["binding"] = result(passed=False, score=0.5, verdict=REVIEW,
                          reason="badly lit, constellation dropped")
    assert "badly lit" in " ".join(fuse(r).reasons)


# ── the score is reported, not decisive ───────────────────────────────────
def test_score_ignores_checks_that_did_not_run():
    r = all_good()
    r["authenticity"] = result(ran=False, passed=False, score=0.0)
    assert fuse(r).score > 0.85


def test_score_is_zero_when_nothing_ran():
    r = {k: result(ran=False, passed=False, score=0.0) for k in all_good()}
    assert fuse(r).score == 0.0


def test_high_score_does_not_rescue_a_violation():
    r = {"presence": result(passed=False, score=0.99, reason="x"),
         "authenticity": result(score=0.99),
         "binding": result(score=0.99, verdict=PASS)}
    d = fuse(r)
    assert d.score > 0.9 and d.verdict == FAIL


def test_low_score_does_not_sink_a_clean_run():
    r = {k: result(score=0.05, verdict=PASS if k == "binding" else None)
         for k in all_good()}
    assert fuse(r).verdict == PASS


# ── shape ─────────────────────────────────────────────────────────────────
def test_ran_and_passed_stay_distinct():
    o = CheckOutcome.from_result("x", result(ran=False, passed=False))
    assert (o.ran, o.passed) == (False, False)


def test_missing_ran_defaults_to_true():
    """Older payloads predate `ran`; assuming they did run matches their meaning."""
    o = CheckOutcome.from_result("x", {"passed": True, "score": 1.0})
    assert o.ran is True


def test_as_dict_is_json_serialisable():
    import json
    json.dumps(fuse(all_good()).as_dict())


def test_as_dict_lists_every_check():
    names = {c["name"] for c in fuse(all_good()).as_dict()["checks"]}
    assert names == {"presence", "authenticity", "binding"}


def test_decision_exposes_requires_human_in_dict():
    d = fuse(all_good()).as_dict()
    assert d["requires_human"] is False


def test_empty_input_does_not_pass():
    assert fuse({}).verdict == REVIEW


# ── a missing check must not mask an absent one ───────────────────────────
def test_missing_and_absent_are_both_reported():
    """
    The capture route's real shape: the sensor died, so presence is `ran=false`
    and the two checks that depended on it were never submitted at all.

    Reporting only "not submitted" names the consequence and hides the cause.
    All three routes here land on REVIEW, so they are peers with no ranking to
    preserve between them — unlike a violation, which is a different verdict
    and must not be buried under them.
    """
    d = fuse({"presence": result(ran=False, passed=False, score=0.0,
                                 verdict=REVIEW, reason="sensor unreachable")})
    assert d.verdict == REVIEW

    joined = " ".join(d.reasons)
    assert "no result submitted" in joined, "the unsubmitted checks went unmentioned"
    assert "presence" in joined, "the check that could not run went unmentioned"


def test_a_violation_is_still_reported_alone():
    """
    The ordering that *is* deliberate. A failed check outranks a missing one:
    burying "presence was refuted" under "binding was not submitted" would
    report the wrong finding as the headline.
    """
    d = fuse({"presence": result(passed=False, verdict=FAIL, reason="spoofed")})
    assert d.verdict == FAIL
    assert len(d.reasons) == 1
    assert "no result submitted" not in " ".join(d.reasons)


def test_an_absent_check_is_reported_once_not_twice():
    """
    `ran=False` with `verdict=REVIEW` landed in both the absent and unsettled
    lists, so the same check appeared twice — once as boilerplate and once with
    the finding that mattered. A queue that repeats itself trains reviewers to
    skim, which is the one habit this band cannot afford.
    """
    d = fuse({"presence": result(ran=False, passed=False, score=0.0,
                                 verdict=REVIEW, reason="sensor unreachable"),
              "authenticity": result(),
              "binding": result()})
    assert sum("presence" in r for r in d.reasons) == 1


def test_an_absent_check_keeps_the_could_not_run_framing():
    """
    "no enrolled reference exists" and "the faces did not match" are one word
    apart on a queue and a world apart in meaning. The reason is worth showing,
    but never in place of the fact that nothing was examined.
    """
    d = fuse({"presence": result(),
              "authenticity": result(),
              "binding": result(ran=False, passed=False, verdict=REVIEW,
                                reason="no enrolled reference capture exists")})
    binding = next(r for r in d.reasons if r.startswith("binding"))
    assert "could not run" in binding
    assert "no enrolled reference capture exists" in binding
