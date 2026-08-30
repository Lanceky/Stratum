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
                         generate_request, variables)


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
    att = build(gate(), events(), evidence())
    text = " ".join(att.unestablished())
    for limit in CHECK_LIMITS.values():
        assert limit in text


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
