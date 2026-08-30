"""
The Human Presence Attestation — an evidence graph, flattened into a document.

Doctavian's job in this system (context.md §7.7): turn a variable-length,
conditional evidence graph into a single legally-shaped attestation. One
template, every jurisdiction and every outcome. This module is the part that
decides *what the document is allowed to say*.

Three things vary, and the certificate has to survive all combinations:

    jurisdiction   EU AMLR and US CIP demand different disclosures, and a
                   certificate that makes an EU claim under US rules is worse
                   than no certificate at all.
    risk tier      enhanced diligence requires the evidence to be enumerated,
                   not summarised.
    outcome        PASS, REVIEW and FAIL each produce a *different document*,
                   not the same document with a different word in it. A REVIEW
                   certificate that reads like a PASS is a forgery with good
                   manners.

The design rule that governs everything here:

    **The certificate states what was not established.**

An attestation is a liability instrument. Whoever relies on it inherits its
claims, so overstating is not a marketing decision but a legal exposure. Every
check in this system has a measured limit — check 2 knows how large a forgery
it would miss, check 3 knows the band where it cannot separate a relative from
a badly-lit photograph — and those limits go *into the document*, in the same
type size as the findings.

That is also the honest reading of the REVIEW outcome. A gate that reached a
human is not a weaker PASS; it is a different fact, and the certificate says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Jurisdiction(StrEnum):
    EU_AMLR = "EU_AMLR"
    US_CIP = "US_CIP"
    # Neither regime claimed. Used when the caller has not told us where the
    # transaction sits, and the certificate must not invent a regime it was
    # never given — an unlabelled document is recoverable, a mislabelled one
    # is not.
    UNSPECIFIED = "UNSPECIFIED"


class RiskTier(StrEnum):
    STANDARD = "STANDARD"
    ENHANCED = "ENHANCED"


# What each regime requires the document to carry. Kept as data rather than as
# branches in code, because a lawyer can read a table and cannot read control
# flow — and because adding a third regime should not mean editing a function.
REGIMES: dict[str, dict] = {
    Jurisdiction.EU_AMLR: {
        "title": "Human Presence Attestation",
        "basis": "Regulation (EU) 2024/1624 (AMLR), Article 22 — "
                 "remote customer identification",
        "retention_years": 5,
        "requires_evidence_enumeration": True,
        # AMLR treats automated decision-making as a controlled activity, so the
        # document has to say a human could intervene and where.
        "disclosures": [
            "This attestation records a remote identification event. It does "
            "not constitute a conclusion on the customer's identity for the "
            "purposes of Article 22; that conclusion remains the obliged "
            "entity's.",
            "An automated verification was performed. A natural person "
            "retained the authority to override its outcome at the point of "
            "signature.",
        ],
    },
    Jurisdiction.US_CIP: {
        "title": "Customer Identification Program — Presence Record",
        "basis": "31 CFR 1020.220 (Customer Identification Program)",
        "retention_years": 5,
        "requires_evidence_enumeration": False,
        "disclosures": [
            "This record documents a non-documentary verification event. It "
            "is not a substitute for the identity verification procedures "
            "required by 31 CFR 1020.220(a)(2).",
        ],
    },
    Jurisdiction.UNSPECIFIED: {
        "title": "Human Presence Attestation",
        "basis": "No regulatory regime was specified by the requesting party",
        "retention_years": 0,
        "requires_evidence_enumeration": True,
        "disclosures": [
            "No jurisdiction was supplied with this request, so this document "
            "makes no claim under any specific regime and should not be relied "
            "on as a compliance record.",
        ],
    },
}

# How each outcome is described. The wording is deliberately not
# interchangeable: each states a different fact about the world.
OUTCOME_STATEMENTS = {
    "PASS": ("Verified", "All checks were performed and satisfied. A natural "
                         "person was present, and the evidence is consistent "
                         "with that person being the enrolled signer."),
    "REVIEW": ("Referred to a human reviewer",
               "The automated checks did not settle the question. This is not "
               "a negative finding and not a weak positive one: the evidence "
               "was insufficient to decide, and the decision was referred to a "
               "named person. Any conclusion recorded below is that person's, "
               "not the system's."),
    "FAIL": ("Not verified", "At least one check was performed and was not "
                             "satisfied. No presence is attested."),
}

# The measured limits of each check, as the checks themselves report them.
# These are transcribed onto the certificate so that a relying party inherits
# the caveats along with the finding.
CHECK_LIMITS = {
    1: "Presence is inferred from illumination response, pose and timing. It "
       "establishes that a physical face responded to an unpredictable prompt; "
       "it does not establish who that face belongs to.",
    2: "Authenticity testing has a measured detection limit. A face must lose "
       "more than 58% of its cross-zone texture structure before this check "
       "flags it 80% of the time, and implausible zone patterns are never "
       "detected at that rate. A generated image below this limit would pass.",
    3: "Identity binding cannot separate a close relative from a poorly "
       "captured genuine photograph: the two populations overlap on measured "
       "data. Cases in that band are referred to a human rather than decided.",
}


@dataclass
class Attestation:
    """One certificate's worth of facts, before it becomes a document."""

    gate_id: str
    outcome: str
    jurisdiction: Jurisdiction
    risk_tier: RiskTier
    timeline: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    reviewer: dict | None = None
    chain_head: str = ""
    chain_intact: bool = True
    issued_at: str = ""

    @property
    def regime(self) -> dict:
        return REGIMES[self.jurisdiction]

    def unestablished(self) -> list[str]:
        """
        What this certificate does *not* claim.

        Built from the checks that did not run plus the standing limits of the
        ones that did, so the list grows automatically when a check degrades
        rather than depending on someone remembering to write a caveat.
        """
        out: list[str] = []
        for c in self.checks:
            if not c.get("ran", True):
                out.append(
                    f"Check {c['check_no']} ({c['name']}) did not run, so it "
                    f"produced no evidence. Its absence is not a negative "
                    f"finding and must not be read as one.")
            elif c["check_no"] in CHECK_LIMITS:
                out.append(CHECK_LIMITS[c["check_no"]])

        if self.outcome == "REVIEW":
            out.append(
                "The automated evidence did not settle this gate. The outcome "
                "recorded here rests on a human judgement, and carries that "
                "person's fallibility rather than a measured error rate.")
        if not self.chain_intact:
            out.append(
                "The audit chain covering this gate did not verify. The events "
                "below may have been altered after the fact, and nothing in "
                "this document should be relied upon.")
        return out


def _payload(event: dict) -> dict:
    """Audit payloads are stored as canonical JSON strings, to keep the hash stable."""
    raw = event.get("payload")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    return raw or {}


def _describe(event: dict) -> str:
    """One audit event, in a sentence a non-engineer can read."""
    p, kind = _payload(event), event.get("type", "")
    if kind == "gate.created":
        return f"Authorisation gate opened in {p.get('mode', 'unknown')} mode."
    if kind == "transition":
        return f"State moved from {p.get('from')} to {p.get('to')}, by {p.get('actor')}."
    if kind == "transition.refused":
        return (f"A {p.get('actor')} attempted to move the gate to "
                f"{p.get('to')} and was refused.")
    if kind == "evidence":
        return f"Check {p.get('check_no')} recorded a score of {p.get('score')}."
    if kind == "review":
        return (f"Reviewer {p.get('reviewer_id')} recorded a decision of "
                f"{p.get('decision')}.")
    if kind == "attestation":
        return "An attestation was issued for this gate."
    return kind or "unrecognised event"


def build(gate: dict, events: list[dict], evidence: list[dict], *,
          jurisdiction: str | Jurisdiction = Jurisdiction.UNSPECIFIED,
          risk_tier: str | RiskTier = RiskTier.STANDARD,
          reviewer: dict | None = None,
          chain_intact: bool = True) -> Attestation:
    """
    Collapse a gate and its history into the facts a certificate may assert.

    Refusals are kept in the timeline deliberately. An agent that tried to sign
    and was stopped is the single most interesting event an auditor can find,
    and a certificate that quietly drops it is describing a different gate.
    """
    names = {1: "presence", 2: "authenticity", 3: "identity binding"}
    checks = []
    for row in sorted(evidence, key=lambda r: r.get("check_no", 0)):
        detail = row.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {}
        detail = detail or {}
        no = int(row.get("check_no", 0))
        checks.append({
            "check_no": no,
            "name": names.get(no, f"check {no}"),
            "score": float(row.get("score") or 0.0),
            "ran": bool(detail.get("ran", True)),
            "verdict": detail.get("verdict") or (
                "satisfied" if detail.get("passed") else "not satisfied"),
            "reason": detail.get("reason", ""),
        })

    timeline = [{
        "at": e.get("ts") or e.get("created_at", ""),
        "event": e.get("type", ""),
        "description": _describe(e),
        "hash": (e.get("hash") or "")[:16],
    } for e in events]

    return Attestation(
        gate_id=gate.get("id", ""),
        outcome=str(gate.get("state", "")),
        jurisdiction=Jurisdiction(jurisdiction),
        risk_tier=RiskTier(risk_tier),
        timeline=timeline,
        checks=checks,
        reviewer=reviewer,
        chain_head=(events[-1].get("hash", "") if events else ""),
        chain_intact=chain_intact,
        issued_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def variables(att: Attestation) -> list[dict]:
    """
    The flat scalars a Doctavian template branches on.

    `data.variables` entries are name/value/type triples and values are strings,
    so anything repeating is JSON-encoded into a single variable rather than
    passed as a nested object. That is a constraint of the API shape, not a
    modelling choice.
    """
    label, statement = OUTCOME_STATEMENTS.get(
        att.outcome, ("Indeterminate", "The gate did not reach a decision."))
    regime = att.regime
    enumerate_evidence = (regime["requires_evidence_enumeration"]
                          or att.risk_tier == RiskTier.ENHANCED)

    scalars = {
        "certificate_title": regime["title"],
        "legal_basis": regime["basis"],
        "jurisdiction": str(att.jurisdiction),
        "risk_tier": str(att.risk_tier),
        "retention_years": str(regime["retention_years"]),
        "gate_id": att.gate_id,
        "outcome": att.outcome,
        "outcome_label": label,
        "outcome_statement": statement,
        "issued_at": att.issued_at,
        "chain_head": att.chain_head,
        "chain_intact": "true" if att.chain_intact else "false",
        "requires_human_review": "true" if att.outcome == "REVIEW" else "false",
        "reviewer_id": (att.reviewer or {}).get("reviewer_id", ""),
        "reviewer_decision": (att.reviewer or {}).get("decision", ""),
        "reviewer_notes": (att.reviewer or {}).get("notes", ""),
        # Template flags. A Word template cannot evaluate a policy, only a
        # boolean, so the policy is decided here and the template only obeys.
        "show_evidence_table": "true" if enumerate_evidence else "false",
        "show_reviewer_block": "true" if att.reviewer else "false",
        "show_limitations": "true",
        "event_count": str(len(att.timeline)),
        "check_count": str(len(att.checks)),
        "disclosures_json": json.dumps(regime["disclosures"]),
        "unestablished_json": json.dumps(att.unestablished()),
        "timeline_json": json.dumps(att.timeline),
        "checks_json": json.dumps(att.checks),
    }
    return [{"name": k, "value": v, "type": "global"} for k, v in scalars.items()]


def generate_request(att: Attestation, *, template_urn: str,
                     template_name: str = "human-presence-attestation",
                     file_format: str = "pdf") -> dict:
    """
    The exact body `POST /v1/documents/document/generate` expects.

    `ConformanceLevel: PdfA3a` is Doctavian's own default and is set explicitly
    here so it survives a change to that default. It also means this document is
    born PDF/A-3a: the archival conversion step in the sealing pipeline is
    redundant for it, and re-converting a conformant file risks *losing*
    conformance rather than gaining it.
    """
    return {
        "externalContext": {"id": att.gate_id},
        "template": {
            "name": template_name,
            "urn": template_urn,
            "fileFormat": "docx",
            "loadMethod": "Storage",
        },
        "data": {
            "loadMethod": "Storage",
            "variables": variables(att),
            "embedded": json.dumps({
                "stratum_gate_id": att.gate_id,
                "stratum_outcome": att.outcome,
                "stratum_chain_head": att.chain_head,
            }),
        },
        "document": {
            "name": f"attestation-{att.gate_id[:8]}" if att.gate_id else "attestation",
            "fileFormat": file_format,
            "deliveryMethod": "Storage",
            "locale": "en-GB" if att.jurisdiction == Jurisdiction.EU_AMLR else "en-US",
            "timezone": "UTC",
            "options": {
                "pdfSaveOptions": json.dumps({"ConformanceLevel": "PdfA3a"}),
            },
        },
    }
