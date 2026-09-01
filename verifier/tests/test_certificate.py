"""
The certificate: is the document allowed to say more than the evidence did?

`test_attestation.py` covers what may be claimed. These cover what happens on
the way to paper, where a different set of things go wrong. A renderer can be
faithful to its input and still produce a dishonest document — by shrinking the
caveats, by printing a missing score as zero, or by sealing markup a reviewer
typed into a text box.

The seal is the reason this matters more here than in most rendering code. A
signature does not make a document true; it makes it hard to alter. Whatever is
wrong on the page at the moment of signing is fixed there with the same
authority as the findings.
"""

import pytest

import certificate
import nutrient
from attestation import Attestation, Jurisdiction, RiskTier


def att(**kw):
    base = dict(
        gate_id="gate-0001", outcome="PASS", signed=True,
        jurisdiction=Jurisdiction.EU_AMLR, risk_tier=RiskTier.STANDARD,
        gate_state="PASS", chain_head="f" * 64, chain_intact=True,
        issued_at="2026-09-01T09:00:00+00:00",
        checks=[{"check_no": 1, "name": "presence", "score": 0.95,
                 "ran": True, "verdict": "satisfied", "reason": "responded"}],
        timeline=[{"at": "2026-09-01T08:59:00Z", "event": "gate.created",
                   "description": "Gate opened.", "hash": "abc123"}],
    )
    base.update(kw)
    return Attestation(**base)


# ── the caveats cannot be quietly demoted ─────────────────────────────────
def test_limitations_are_set_in_the_same_type_as_findings():
    """
    attestation.py promises the limits go in "the same type size as the
    findings". A stylesheet is where that is kept or broken, so the rule is
    asserted directly: one declaration naming both classes. Splitting them into
    two rules is how the caveats start shrinking a point at a time.
    """
    assert ".finding, .limitation {" in certificate.STYLE
    body = certificate.STYLE.split(".finding, .limitation {")[1].split("}")[0]
    assert "font-size" in body


def test_the_limitations_actually_reach_the_page():
    html = certificate.render(att())
    for limit in att().unestablished():
        assert limit in html or limit.replace("'", "&#x27;") in html


def test_a_check_that_did_not_run_is_not_printed_as_a_zero():
    """
    The dataclass defaults an absent score to 0.0. On a page, 0.00 reads as the
    worst possible result rather than as no result — which is the exact
    confusion this whole document exists to prevent.
    """
    a = att(risk_tier=RiskTier.ENHANCED,
            checks=[{"check_no": 2, "name": "authenticity", "score": 0.0,
                     "ran": False, "verdict": "", "reason": "sensor down"}])
    html = certificate.render(a)
    assert "0.00" not in html
    assert "did not run" in html


def test_a_check_that_ran_and_scored_zero_still_prints_its_score():
    """The rule above must not swallow a real measurement of zero."""
    a = att(risk_tier=RiskTier.ENHANCED,
            checks=[{"check_no": 2, "name": "authenticity", "score": 0.0,
                     "ran": True, "verdict": "not satisfied", "reason": "x"}])
    assert "0.00" in certificate.render(a)


# ── free text is sealed, so it is escaped ─────────────────────────────────
def test_reviewer_notes_are_escaped():
    """
    Notes are typed by a human into a console and end up inside a signed PDF.
    Sealing injected markup would give it the same authority as the findings.
    """
    a = att(reviewer={"reviewer_id": "r1", "decision": "approve",
                      "notes": "<script>alert(1)</script>"})
    html = certificate.render(a)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_hostile_gate_id_cannot_break_out_of_the_page():
    a = att(gate_id='"><script>x</script>')
    html = certificate.render(a)
    assert "<script>" not in html


def test_a_hostile_check_reason_is_escaped():
    a = att(risk_tier=RiskTier.ENHANCED,
            checks=[{"check_no": 1, "name": "presence", "score": 0.5,
                     "ran": True, "verdict": "satisfied",
                     "reason": "<img src=x onerror=alert(1)>"}])
    assert "<img" not in certificate.render(a)


def test_a_hostile_event_description_is_escaped():
    a = att(timeline=[{"at": "t", "event": "<b>x</b>",
                       "description": "<i>y</i>", "hash": "h"}])
    html = certificate.render(a)
    assert "<b>x</b>" not in html and "<i>y</i>" not in html


# ── a broken chain is stated before anything it would undermine ───────────
def test_a_broken_chain_is_announced_above_the_outcome():
    """
    The reader is told the record cannot be confirmed *before* they read the
    finding, not in a footnote after they have already believed it.
    """
    html = certificate.render(att(chain_intact=False))
    body = html.split("</style>")[1]
    assert body.index("did not verify") < body.index('<div class="outcome">')


def test_an_intact_chain_raises_no_alarm():
    assert "did not verify" not in certificate.render(att(chain_intact=True))


def test_a_broken_chain_still_produces_a_certificate():
    """
    A portable record of a tamper is precisely the artefact an investigator
    needs. Refusing to issue would leave the failure visible only from inside
    the system that failed.
    """
    assert len(certificate.render(att(chain_intact=False))) > 0


# ── what may be issued at all ─────────────────────────────────────────────
@pytest.mark.parametrize("state", ["PASS", "REVIEW", "FAIL", "SIGNED", "SEALED"])
def test_a_gate_that_reached_a_verdict_may_be_certified(state):
    assert certificate.sealable(state)
    certificate.guard(att(gate_state=state))


@pytest.mark.parametrize("state", ["REQUESTED", "CHALLENGED", "CAPTURED", ""])
def test_a_gate_still_in_flight_may_not_be_certified(state):
    """
    A sealed PDF outlives the context that produced it. One issued mid-flight
    would circulate as a finished record of a question nobody had answered.
    """
    assert not certificate.sealable(state)
    with pytest.raises(certificate.NotSealable):
        certificate.guard(att(gate_state=state))


def test_the_refusal_says_what_would_make_it_issuable():
    with pytest.raises(certificate.NotSealable) as exc:
        certificate.guard(att(gate_state="CHALLENGED"))
    assert "CHALLENGED" in str(exc.value)
    assert "PASS" in str(exc.value)


# ── the outcomes are different documents, not one with a word changed ─────
def test_review_never_reads_as_a_clean_pass():
    review = certificate.render(att(outcome="REVIEW", gate_state="REVIEW"))
    assert "Referred to a human reviewer" in review
    assert "Verified" not in review.split("SUBJECT")[0].replace(
        "Referred to a human reviewer", "")


def test_each_outcome_states_its_own_finding():
    rendered = {o: certificate.render(att(outcome=o, gate_state=o))
                for o in ("PASS", "REVIEW", "FAIL")}
    assert len(set(rendered.values())) == 3


def test_an_unrecognised_outcome_does_not_default_to_a_positive_one():
    html = certificate.render(att(outcome="WEIRD", gate_state="PASS"))
    assert "Indeterminate" in html


# ── risk tier decides whether evidence is enumerated ──────────────────────
def test_enhanced_diligence_enumerates_the_evidence():
    html = certificate.render(att(risk_tier=RiskTier.ENHANCED,
                                  jurisdiction=Jurisdiction.US_CIP))
    assert "Recorded reason" in html


def test_a_standard_tier_summarises_but_still_counts_the_checks():
    """
    Not enumerating is a presentation choice; hiding how many checks ran would
    be a claim. The count stays either way.
    """
    a = att(risk_tier=RiskTier.STANDARD, jurisdiction=Jurisdiction.US_CIP,
            checks=[{"check_no": 1, "name": "presence", "score": 0.9,
                     "ran": True, "verdict": "satisfied", "reason": "r"},
                    {"check_no": 2, "name": "authenticity", "score": 0.0,
                     "ran": False, "verdict": "", "reason": ""}])
    html = certificate.render(a)
    assert "1 of 2 checks were performed" in html


def test_a_regime_that_demands_enumeration_gets_it_at_any_tier():
    """The regime's requirement is not overridable by the tier being low."""
    a = att(risk_tier=RiskTier.STANDARD, jurisdiction=Jurisdiction.EU_AMLR)
    if a.regime["requires_evidence_enumeration"]:
        assert "Recorded reason" in certificate.render(a)


# ── the reviewer block ────────────────────────────────────────────────────
def test_no_reviewer_block_when_nobody_ruled():
    assert "Human ruling" not in certificate.render(att(reviewer=None))


def test_the_reviewer_block_names_the_person_and_their_decision():
    html = certificate.render(att(reviewer={
        "reviewer_id": "reviewer-04", "decision": "approve",
        "triggering_signal": "check 3", "notes": "spoke to them"}))
    assert "reviewer-04" in html and "approve" in html and "check 3" in html


def test_the_reviewer_signal_uses_the_column_the_store_writes():
    """
    `store.add_review` writes `triggering_signal`. Reading `signal` here would
    silently print an em dash for every real review while every test using a
    hand-built dict still passed.
    """
    html = certificate.render(att(reviewer={
        "reviewer_id": "r", "decision": "approve",
        "triggering_signal": "check 2 never ran"}))
    assert "check 2 never ran" in html


# ── the document is self-contained and printable ──────────────────────────
def test_the_stylesheet_travels_with_the_document():
    """No network at render time, and none at print time either."""
    html = certificate.render(att())
    assert "<style>" in html
    assert "http://" not in html and "https://" not in html


def test_the_chain_head_is_on_the_document():
    """Without it, the certificate cannot be checked against the live record."""
    assert "f" * 64 in certificate.render(att())


# ── sealing order ─────────────────────────────────────────────────────────
def test_the_seal_goes_on_after_conversion_not_before(monkeypatch):
    """
    Converting an already-signed file to PDF/A invalidates the signature while
    leaving it visibly present — the worst of the three outcomes. Callers do
    not get to choose the order, so it is pinned here.
    """
    order = []
    monkeypatch.setattr(certificate, "PDFA", True)
    monkeypatch.setattr(nutrient, "html_to_pdf",
                        lambda h, pdfa=False, record=True:
                        order.append(("build", pdfa)) or b"%PDF-raw")
    monkeypatch.setattr(nutrient, "sign",
                        lambda p, record=True:
                        order.append(("sign", p)) or b"%PDF-signed")

    assert certificate.seal(att()) == b"%PDF-signed"
    assert [step for step, _ in order] == ["build", "sign"]
    assert order[0][1] is True, "conformance was not requested"
    assert order[1][1] == b"%PDF-raw", "signed something other than the built pdf"


def test_sealing_an_unfinished_gate_never_reaches_the_api(monkeypatch):
    monkeypatch.setattr(nutrient, "html_to_pdf", lambda *a, **k: pytest.fail(
        "an unfinished gate was sent for rendering"))
    with pytest.raises(certificate.NotSealable):
        certificate.seal(att(gate_state="CHALLENGED"))


def test_pdfa_can_be_turned_off_without_losing_the_signature(monkeypatch):
    """
    The conformance step is separately licensed. An account without it stamps
    the page as an evaluation copy, which is worse than a plain PDF — but the
    signature is the part that must never be optional.
    """
    calls = {}
    monkeypatch.setattr(certificate, "PDFA", False)
    monkeypatch.setattr(nutrient, "html_to_pdf",
                        lambda h, pdfa=False, record=True:
                        calls.setdefault("pdfa", pdfa) or b"x")
    monkeypatch.setattr(nutrient, "sign",
                        lambda p, record=True:
                        calls.setdefault("signed", True) or b"s")

    certificate.seal(att())
    assert calls["pdfa"] is False
    assert calls.get("signed") is True


def test_a_certificate_is_never_left_on_disk_as_a_fixture(monkeypatch):
    """
    The replay key covers the input bytes, and those carry the issue time, so
    no two certificates share a key. A recording could not be served back even
    in principle — it would only leave a PDF behind for every document issued,
    accumulating for the life of the deployment.
    """
    seen = {}
    monkeypatch.setattr(nutrient, "html_to_pdf",
                        lambda h, pdfa=False, record=True:
                        seen.setdefault("build", record) or b"x")
    monkeypatch.setattr(nutrient, "sign",
                        lambda p, record=True:
                        seen.setdefault("sign", record) or b"s")

    certificate.seal(att())
    assert seen == {"build": False, "sign": False}
