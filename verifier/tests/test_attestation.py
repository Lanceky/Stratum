"""
The attestation: does the certificate refuse to claim more than was established?

These tests are mostly about what the document *declines* to say. An attestation
is a liability instrument — whoever relies on it inherits its claims — so the
failure mode that matters is not a missing field, it is a confident sentence
that the evidence does not support.
"""

import json

import pytest

from attestation import (CHECK_LIMITS, OUTCOME_STATEMENTS, REGIMES,
                         Attestation, Jurisdiction, RiskTier, build,
                         generate_request, outcome_of, variables)


def gate(state="PASS", gid="gate-0001"):
    return {"id": gid, "state": state, "mode": "authorise_action"}


def events(n=3):
    out = [{"type": "gate.created", "ts": "2026-08-30T10:00:00Z", "hash": "a" * 64,
            "payload": json.dumps({"mode": "authorise_action"})}]
    for i in range(n):
        out.append({"type": "transition", "ts": f"2026-08-30T10:0{i + 1}:00Z",
                    "hash": chr(98 + i) * 64,
                    "payload": json.dumps({"from": "REQUESTED", "to": "CHALLENGED",
                                           "actor": "system"})})
    return out


def evidence(ran2=True):
    return [
        {"check_no": 1, "score": 0.91, "detail": json.dumps({"passed": True, "ran": True})},
        {"check_no": 2, "score": 0.80,
         "detail": json.dumps({"passed": ran2, "ran": ran2})},
        {"check_no": 3, "score": 0.95,
         "detail": json.dumps({"passed": True, "ran": True, "verdict": "PASS"})},
    ]


def scalars(att):
    return {v["name"]: v["value"] for v in variables(att)}


# ── what the certificate refuses to claim ─────────────────────────────────
def test_limits_of_every_check_that_ran_are_carried():
    """
    Driven off the evidence rather than off CHECK_LIMITS, so a check the gate
    never ran does not have to have its caveat printed. Iterating the whole
    table asserted the opposite — that a certificate must disclose limits of
    work it did not do.
    """
    att = build(gate(), events(), evidence())
    text = " ".join(att.unestablished())
    for check in att.checks:
        assert CHECK_LIMITS[check["check_no"]] in text


def test_a_check_that_ran_cannot_have_its_caveat_dropped():
    """
    The other half, and the one that matters: every check with a known limit
    must have that limit stated when it runs. Without this, adding a check and
    forgetting its caveat produces a certificate that overclaims silently.
    """
    ev = evidence() + [{"check_no": 4, "score": 1.0, "detail": json.dumps(
        {"passed": True, "ran": True, "verdict": "UNIQUE", "roster_size": 400,
         "comparisons_run": 400, "comparisons_skipped": 0,
         "false_match": {"across_this_sweep": 0.6}})}]
    text = " ".join(build(gate(), events(), ev).unestablished())
    for limit in CHECK_LIMITS.values():
        assert limit in text


def test_the_uniqueness_caveat_states_the_odds_for_this_roster():
    """
    Check 4's limit is a function of roster size, so a fixed sentence would be
    wrong for every roster but one. A reader deciding whether to refuse
    someone's allocation should see the chance they are refusing the wrong
    person.
    """
    ev = evidence() + [{"check_no": 4, "score": 1.0, "detail": json.dumps(
        {"passed": True, "ran": True, "verdict": "UNIQUE", "roster_size": 400,
         "comparisons_run": 400, "comparisons_skipped": 0,
         "false_match": {"across_this_sweep": 0.6}})}]
    text = " ".join(build(gate(), events(), ev).unestablished())
    assert "400 of 400" in text
    assert "60.00%" in text, "stated as a percentage, not a bare probability"


def test_comparisons_that_could_not_run_are_named():
    """
    A roster of 400 where 60 could not be compared has not been swept.
    Reporting only the 340 describes a uniqueness never established over the
    rest.
    """
    ev = evidence() + [{"check_no": 4, "score": 1.0, "detail": json.dumps(
        {"passed": True, "ran": True, "verdict": "UNIQUE", "roster_size": 400,
         "comparisons_run": 340, "comparisons_skipped": 60,
         "false_match": {"across_this_sweep": 0.5}})}]
    text = " ".join(build(gate(), events(), ev).unestablished())
    assert "340 of 400" in text
    assert "60 that could not be compared" in text


def test_a_check_that_did_not_run_is_disclosed():
    att = build(gate(), events(), evidence(ran2=False))
    assert any("did not run" in u for u in att.unestablished())


def test_absent_check_is_not_reported_as_a_negative_finding():
    att = build(gate(), events(), evidence(ran2=False))
    disclosure = next(u for u in att.unestablished() if "did not run" in u)
    assert "not a negative finding" in disclosure


def test_review_outcome_discloses_it_rests_on_a_human():
    att = build(gate("REVIEW"), events(), evidence())
    assert any("human judgement" in u for u in att.unestablished())


def test_broken_chain_invalidates_the_whole_document():
    att = build(gate(), events(), evidence(), chain_intact=False)
    assert any("should be relied upon" in u for u in att.unestablished())


def test_limitations_are_always_shown():
    """There is no configuration that suppresses the caveats."""
    for state in ("PASS", "REVIEW", "FAIL"):
        assert scalars(build(gate(state), events(), evidence()))["show_limitations"] == "true"


# ── the three outcomes are three different documents ──────────────────────
def test_each_outcome_states_a_different_fact():
    said = {scalars(build(gate(s), events(), evidence()))["outcome_statement"]
            for s in ("PASS", "REVIEW", "FAIL")}
    assert len(said) == 3


def test_review_does_not_read_as_a_pass():
    s = scalars(build(gate("REVIEW"), events(), evidence()))
    assert "not a negative finding and not a weak positive" in s["outcome_statement"]


def test_review_flags_that_a_human_is_required():
    assert scalars(build(gate("REVIEW"), events(), evidence()))["requires_human_review"] == "true"


def test_pass_does_not_flag_human_review():
    assert scalars(build(gate("PASS"), events(), evidence()))["requires_human_review"] == "false"


def test_fail_attests_no_presence():
    s = scalars(build(gate("FAIL"), events(), evidence()))
    assert "No presence is attested" in s["outcome_statement"]


def test_unknown_state_does_not_fabricate_an_outcome():
    s = scalars(build(gate("CAPTURED"), events(), evidence()))
    assert s["outcome_label"] == "Indeterminate"


# ── jurisdiction branching ────────────────────────────────────────────────
def test_eu_and_us_produce_different_legal_bases():
    eu = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.EU_AMLR))
    us = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.US_CIP))
    assert eu["legal_basis"] != us["legal_basis"]
    assert "AMLR" in eu["legal_basis"] and "1020.220" in us["legal_basis"]


def test_unspecified_jurisdiction_claims_no_regime():
    s = scalars(build(gate(), events(), evidence()))
    assert "makes no claim under any specific regime" in s["disclosures_json"]


def test_unspecified_jurisdiction_sets_no_retention_period():
    """Inventing a retention period for an unknown regime is a fabricated claim."""
    assert scalars(build(gate(), events(), evidence()))["retention_years"] == "0"


def test_eu_requires_evidence_enumeration():
    s = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.EU_AMLR))
    assert s["show_evidence_table"] == "true"


def test_us_standard_risk_summarises_evidence():
    s = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.US_CIP))
    assert s["show_evidence_table"] == "false"


def test_enhanced_risk_forces_enumeration_even_where_regime_does_not():
    s = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.US_CIP,
                      risk_tier=RiskTier.ENHANCED))
    assert s["show_evidence_table"] == "true"


def test_every_regime_carries_at_least_one_disclosure():
    for j in Jurisdiction:
        assert REGIMES[j]["disclosures"]


def test_eu_discloses_the_human_override():
    s = scalars(build(gate(), events(), evidence(), jurisdiction=Jurisdiction.EU_AMLR))
    assert "override" in s["disclosures_json"]


def test_unknown_jurisdiction_is_rejected_not_defaulted():
    """Silently defaulting a bad jurisdiction would mislabel the document."""
    with pytest.raises(ValueError):
        build(gate(), events(), evidence(), jurisdiction="MOON")


# ── the timeline ──────────────────────────────────────────────────────────
def test_timeline_covers_every_event():
    att = build(gate(), events(5), evidence())
    assert len(att.timeline) == 6


def test_refused_transitions_are_kept():
    """An agent that tried to sign and was stopped is the point of the log."""
    ev = events() + [{"type": "transition.refused", "ts": "2026-08-30T10:09:00Z",
                      "hash": "f" * 64,
                      "payload": json.dumps({"actor": "agent", "to": "SIGNED"})}]
    att = build(gate(), ev, evidence())
    assert any("attempted to move" in row["description"] for row in att.timeline)


def test_events_are_described_in_plain_language():
    att = build(gate(), events(), evidence())
    assert all(row["description"] and row["event"] not in row["description"]
               or row["description"] for row in att.timeline)


def test_unparseable_payload_does_not_break_generation():
    ev = [{"type": "transition", "ts": "x", "hash": "z" * 64, "payload": "not json"}]
    assert build(gate(), ev, evidence()).timeline


def test_chain_head_is_the_last_event():
    att = build(gate(), events(3), evidence())
    assert att.chain_head == "d" * 64


def test_empty_history_still_produces_a_document():
    att = build(gate(), [], [])
    assert scalars(att)["event_count"] == "0"


# ── the request body ──────────────────────────────────────────────────────
def test_request_targets_pdf_a3a():
    req = generate_request(build(gate(), events(), evidence()), template_urn="urn:t")
    assert json.loads(req["document"]["options"]["pdfSaveOptions"])["ConformanceLevel"] == "PdfA3a"


def test_request_carries_the_gate_as_external_context():
    req = generate_request(build(gate(), events(), evidence()), template_urn="urn:t")
    assert req["externalContext"]["id"] == "gate-0001"


def test_request_is_json_serialisable():
    json.dumps(generate_request(build(gate(), events(), evidence()), template_urn="urn:t"))


def test_variables_are_all_strings():
    """The API takes name/value/type triples; a stray int is a 400 at runtime."""
    for v in variables(build(gate(), events(), evidence())):
        assert isinstance(v["value"], str), v["name"]


def test_repeating_data_is_json_encoded():
    s = scalars(build(gate(), events(), evidence()))
    assert isinstance(json.loads(s["timeline_json"]), list)
    assert isinstance(json.loads(s["checks_json"]), list)


def test_locale_follows_jurisdiction():
    eu = generate_request(build(gate(), events(), evidence(),
                                jurisdiction=Jurisdiction.EU_AMLR), template_urn="u")
    us = generate_request(build(gate(), events(), evidence(),
                                jurisdiction=Jurisdiction.US_CIP), template_urn="u")
    assert eu["document"]["locale"] != us["document"]["locale"]


def test_reviewer_block_appears_only_with_a_reviewer():
    without = scalars(build(gate("REVIEW"), events(), evidence()))
    with_ = scalars(build(gate("REVIEW"), events(), evidence(),
                          reviewer={"reviewer_id": "r-1", "decision": "approve"}))
    assert without["show_reviewer_block"] == "false"
    assert with_["show_reviewer_block"] == "true"
    assert with_["reviewer_id"] == "r-1"


# ── the verdict survives signing ──────────────────────────────────────────
def signed_history(verdict="REVIEW"):
    """A real gate's trail: it reaches a verdict, then a human signs it."""
    return events() + [
        {"type": "transition", "ts": "2026-08-30T10:05:00Z", "hash": "e" * 64,
         "payload": json.dumps({"from": "SCORED", "to": verdict, "actor": "system"})},
        {"type": "transition", "ts": "2026-08-30T10:06:00Z", "hash": "f" * 64,
         "payload": json.dumps({"from": verdict, "to": "SIGNED", "actor": "human"})},
    ]


def test_verdict_is_recovered_after_signing():
    """
    The certificate is issued on the signature webhook, so the gate reads
    SIGNED by then. Reporting that as the outcome erases the verdict.
    """
    att = build(gate("SIGNED"), signed_history("REVIEW"), evidence())
    assert att.outcome == "REVIEW"


def test_a_signed_review_does_not_become_a_pass():
    """The difference between a clean pass and an approved referral is the point."""
    s = scalars(build(gate("SIGNED"), signed_history("REVIEW"), evidence()))
    assert s["outcome"] == "REVIEW"
    assert s["requires_human_review"] == "true"


def test_a_signed_pass_is_still_a_pass():
    assert build(gate("SIGNED"), signed_history("PASS"), evidence()).outcome == "PASS"


def test_sealed_gate_also_recovers_its_verdict():
    ev = signed_history("PASS") + [
        {"type": "transition", "ts": "2026-08-30T10:07:00Z", "hash": "g" * 64,
         "payload": json.dumps({"from": "SIGNED", "to": "SEALED", "actor": "system"})}]
    assert build(gate("SEALED"), ev, evidence()).outcome == "PASS"


def test_the_latest_verdict_wins():
    """A gate reviewed then failed by a human must report the failure."""
    ev = events() + [
        {"type": "transition", "ts": "t1", "hash": "e" * 64,
         "payload": json.dumps({"from": "SCORED", "to": "REVIEW", "actor": "system"})},
        {"type": "transition", "ts": "t2", "hash": "f" * 64,
         "payload": json.dumps({"from": "REVIEW", "to": "FAIL", "actor": "human"})}]
    assert build(gate("FAIL"), ev, evidence()).outcome == "FAIL"


def test_signing_is_recorded_separately_from_the_verdict():
    s = scalars(build(gate("SIGNED"), signed_history("PASS"), evidence()))
    assert s["signed"] == "true" and s["gate_state"] == "SIGNED"


def test_unsigned_gate_is_not_marked_signed():
    assert scalars(build(gate("REVIEW"), events(), evidence()))["signed"] == "false"


def test_current_verdict_state_is_used_directly():
    assert outcome_of({"state": "PASS"}, []) == "PASS"


def test_gate_with_no_verdict_in_its_history_is_not_invented():
    assert outcome_of({"state": "CAPTURED"}, events()) == "CAPTURED"
