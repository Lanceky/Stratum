"""
The attestation, as a document someone can hold.

`attestation.py` decides what the certificate is *allowed to say*. This module
turns that into HTML, and then into a sealed PDF. The split matters: what may
be claimed is a policy question with legal consequences, and rendering is a
presentation question. Mixing them is how a layout change quietly starts
asserting something new.

Three rules govern the rendering, and each exists because breaking it produces
a document that is worse than no document:

    Limitations are set in the same type as findings. `attestation.py` says so
    in its docstring, and a stylesheet is where that promise is actually kept or
    broken. Shrinking the caveats is the oldest way to publish a claim you have
    not earned, so the rule is expressed as a single CSS rule naming both, and
    a test reads it back.

    Free text is escaped, always. Reviewer notes are typed by a human into a
    console and end up inside a cryptographically signed PDF. A signature makes
    a document harder to alter, not more truthful — it would seal injected
    markup in place with the same authority as the findings.

    The seal goes on last. Converting an already-signed file to PDF/A
    invalidates the signature, so the order is render, conform, then sign, and
    that order is enforced here rather than left to the caller to remember.

The document is deliberately printable and monochrome. A certificate whose
meaning depends on colour stops meaning it the first time someone photocopies
it, and a compliance record's whole life is spent being copied.
"""

from __future__ import annotations

import os
from html import escape

import attestation as att_mod
import nutrient
from attestation import Attestation

# States from which a certificate may be issued. PASS, REVIEW and FAIL are
# verdicts; SIGNED and SEALED are what was done with a verdict, and the verdict
# itself is recovered from the chain by `attestation.outcome_of`.
#
# Everything else is a gate still in flight. Refusing those is the same rule the
# rest of the system runs on: absence never passes. A sealed PDF outlives the
# context that produced it, and one issued mid-flight would circulate as a
# finished record of a question nobody had answered yet.
SEALABLE = frozenset(att_mod.VERDICT_STATES) | {"SIGNED", "SEALED"}

# PDF/A-3b is the archival form, and archival is the whole point of a document
# that outlives the server. It is switchable because the conformance step is a
# separately licensed feature, so an account without it gets a certificate
# stamped as an evaluation copy — which is worse than a plain PDF, since a
# compliance record whose face says it must not be relied upon is not a record.
# Set STRATUM_PDFA=0 to render a plain signed PDF instead.
PDFA = os.getenv("STRATUM_PDFA", "1") not in ("0", "false", "no")


class NotSealable(Exception):
    """Raised when a gate has not reached a verdict worth certifying."""


def sealable(gate_state: str) -> bool:
    return str(gate_state) in SEALABLE


def guard(att: Attestation) -> None:
    if not sealable(att.gate_state):
        raise NotSealable(
            f"gate is {att.gate_state or 'in an unrecorded state'}, which is "
            f"not a verdict. A certificate may be issued once the gate reaches "
            f"{', '.join(sorted(SEALABLE))}. Issuing one now would put a "
            f"finished-looking record of an unfinished decision into "
            f"circulation."
        )


# ── stylesheet ────────────────────────────────────────────────────────────
# `.finding, .limitation` share one rule on purpose. See the module docstring:
# it is the mechanism by which the caveats cannot be quietly demoted, and
# `test_certificate.py` reads this rule back to check it still names both.
STYLE = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; color: #000;
       background: #fff; margin: 0; line-height: 1.45; }
.finding, .limitation { font-size: 10.5pt; }
h1 { font-size: 17pt; margin: 0 0 2mm 0; letter-spacing: .01em; }
h2 { font-size: 11pt; margin: 7mm 0 2mm 0; text-transform: uppercase;
     letter-spacing: .09em; border-bottom: 1px solid #000;
     padding-bottom: 1.2mm; }
.sub { font-size: 9.5pt; margin: 0 0 5mm 0; }
.rule { border: 0; border-top: 2px solid #000; margin: 3mm 0 5mm 0; }
.outcome { border: 1.5px solid #000; padding: 4mm 4.5mm; margin: 0 0 5mm 0; }
.outcome .label { font-size: 14pt; font-weight: bold; margin: 0 0 1.5mm 0; }
.alarm { border: 3px double #000; padding: 4mm 4.5mm; margin: 0 0 5mm 0;
         background: #f2f2f2; }
.alarm .label { font-size: 12pt; font-weight: bold;
                text-transform: uppercase; letter-spacing: .05em;
                margin: 0 0 1.5mm 0; }
table { width: 100%; border-collapse: collapse; font-size: 9.5pt;
        margin: 0 0 3mm 0; }
th { text-align: left; border-bottom: 1px solid #000; padding: 1.6mm 2mm;
     font-size: 8.5pt; text-transform: uppercase; letter-spacing: .06em; }
td { border-bottom: 1px solid #bbb; padding: 1.8mm 2mm;
     vertical-align: top; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.didnotrun td { color: #000; }
.didnotrun .verdict { font-style: italic; }
ul { margin: 0 0 3mm 0; padding-left: 5.5mm; }
li { margin: 0 0 2mm 0; }
.mono { font-family: 'DejaVu Sans Mono', Consolas, monospace;
        font-size: 8.5pt; word-break: break-all; }
.kv { width: 100%; font-size: 9.5pt; border-collapse: collapse; }
.kv td { border: 0; padding: .8mm 0; }
.kv td:first-child { width: 38%; text-transform: uppercase;
                     font-size: 8.5pt; letter-spacing: .05em; }
.foot { margin-top: 7mm; border-top: 1px solid #000; padding-top: 2.5mm;
        font-size: 8.5pt; }
.note { font-size: 9pt; margin: 0 0 3mm 0; }
"""


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "".join(
        f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in pairs)


def _bullets(items: list[str], cls: str = "") -> str:
    if not items:
        return ""
    attr = f' class="{cls}"' if cls else ""
    body = "".join(f"<li{attr}>{escape(i)}</li>" for i in items)
    return f"<ul>{body}</ul>"


def _score(check: dict) -> str:
    """
    A check that did not run has no score, and 0.00 is not the same statement.

    The dataclass defaults an absent score to 0.0, which on a page reads as the
    worst possible result rather than as no result. That is the one confusion
    this document exists to prevent.
    """
    if not check.get("ran", True):
        return "—"
    return f"{float(check.get('score') or 0.0):.2f}"


def _checks_table(att: Attestation) -> str:
    if not att.checks:
        return ('<p class="finding">No checks were recorded against this '
                'gate.</p>')
    rows = []
    for c in att.checks:
        ran = c.get("ran", True)
        verdict = "did not run" if not ran else str(c.get("verdict", ""))
        rows.append(
            f'<tr class="{"didnotrun" if not ran else ""}">'
            f'<td class="num">{escape(str(c.get("check_no", "")))}</td>'
            f'<td>{escape(str(c.get("name", "")))}</td>'
            f'<td class="verdict">{escape(verdict)}</td>'
            f'<td class="num">{escape(_score(c))}</td>'
            f'<td>{escape(str(c.get("reason", "")))}</td></tr>')
    return (
        '<table><thead><tr><th>#</th><th>Check</th><th>Result</th>'
        '<th>Score</th><th>Recorded reason</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>')


def _timeline_table(att: Attestation) -> str:
    if not att.timeline:
        return '<p class="finding">No events are recorded for this gate.</p>'
    rows = "".join(
        f'<tr><td class="num">{escape(str(e.get("at", "")))}</td>'
        f'<td>{escape(str(e.get("event", "")))}</td>'
        f'<td>{escape(str(e.get("description", "")))}</td>'
        f'<td class="mono">{escape(str(e.get("hash", "")))}</td></tr>'
        for e in att.timeline)
    return (
        '<table><thead><tr><th>Time (UTC)</th><th>Event</th>'
        '<th>Description</th><th>Hash</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>')


def _reviewer_block(att: Attestation) -> str:
    r = att.reviewer or {}
    if not r:
        return ""
    notes = str(r.get("notes") or "")
    return (
        "<h2>Human ruling</h2>"
        '<p class="finding">This gate was decided by a named person. The '
        "conclusion below is theirs, and carries their judgement rather than a "
        "measured error rate.</p>"
        '<table class="kv">'
        + _rows([("Reviewer", str(r.get("reviewer_id", ""))),
                 ("Decision", str(r.get("decision", ""))),
                 ("Signal referred", str(r.get("triggering_signal", "") or "—"))])
        + "</table>"
        + (f'<p class="finding"><strong>Notes.</strong> {escape(notes)}</p>'
           if notes else ""))


def _chain_alarm(att: Attestation) -> str:
    """
    A broken chain is stated before the outcome, not after it.

    The certificate is still issued — a tampered ledger is precisely when a
    portable record of the tamper is worth having — but nothing below this
    point can be relied upon, and the reader is told that before they read it.
    """
    if att.chain_intact:
        return ""
    return (
        '<div class="alarm"><p class="label">The audit chain did not '
        'verify</p><p class="finding">The events recorded in this certificate '
        "could not be confirmed against the hash chain that covers them. They "
        "may have been altered after they were written. Nothing in this "
        "document should be relied upon, including the outcome stated below. "
        "This certificate is issued as a record of that failure.</p></div>")


def render(att: Attestation) -> str:
    """One `Attestation`, as a self-contained printable HTML document."""
    regime = att.regime
    label, statement = att_mod.OUTCOME_STATEMENTS.get(
        att.outcome, ("Indeterminate", "The gate did not reach a decision."))
    enumerate_evidence = (regime["requires_evidence_enumeration"]
                          or att.risk_tier == att_mod.RiskTier.ENHANCED)

    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{escape(regime['title'])} — {escape(att.gate_id)}</title>",
        f"<style>{STYLE}</style></head><body>",
        f"<h1>{escape(regime['title'])}</h1>",
        f'<p class="sub">{escape(regime["basis"])}</p>',
        '<hr class="rule">',
        _chain_alarm(att),
        f'<div class="outcome"><p class="label">{escape(label)}</p>'
        f'<p class="finding">{escape(statement)}</p></div>',
        "<h2>Subject of this certificate</h2>",
        '<table class="kv">',
        _rows([
            ("Authorisation gate", att.gate_id),
            ("Outcome", att.outcome),
            ("Gate state when issued", att.gate_state),
            ("Signature applied", "yes" if att.signed else "no"),
            ("Jurisdiction", str(att.jurisdiction)),
            ("Risk tier", str(att.risk_tier)),
            ("Issued at", att.issued_at),
            ("Retention", f"{regime['retention_years']} years"),
        ]),
        "</table>",
    ]

    if enumerate_evidence:
        parts += ["<h2>Evidence</h2>", _checks_table(att)]
    else:
        ran = sum(1 for c in att.checks if c.get("ran", True))
        parts += [
            "<h2>Evidence</h2>",
            f'<p class="finding">{ran} of {len(att.checks)} checks were '
            f"performed. Individual results are not enumerated at this risk "
            f"tier; they are retained in the audit record and available on "
            f"request.</p>"]

    parts.append(_reviewer_block(att))

    parts += [
        "<h2>What this certificate does not establish</h2>",
        '<p class="note">These limitations are part of the finding, not a '
        "disclaimer attached to it. Whoever relies on this document inherits "
        "them.</p>",
        _bullets(att.unestablished(), cls="limitation"),
    ]

    if regime["disclosures"]:
        parts += ["<h2>Regulatory disclosures</h2>",
                  _bullets(regime["disclosures"], cls="limitation")]

    parts += ["<h2>Audit trail</h2>", _timeline_table(att)]

    parts += [
        "<h2>Verifying this certificate</h2>",
        '<p class="finding">The events above are hash-chained. Each entry '
        "commits to the one before it, so any alteration changes every hash "
        "that follows. To confirm this document against the live record, "
        "compare the chain head below.</p>",
        '<table class="kv">',
        _rows([("Chain head", att.chain_head or "—"),
               ("Chain verified at issue",
                "yes" if att.chain_intact else "NO — see notice above"),
               ("Events covered", str(len(att.timeline)))]),
        "</table>",
        '<div class="foot">Issued by STRATUM for authorisation gate '
        f"{escape(att.gate_id)} at {escape(att.issued_at)}. "
        "A certificate records what was established and what was not. Both "
        "halves are the finding.</div>",
        "</body></html>",
    ]
    return "".join(parts)


def seal(att: Attestation) -> bytes:
    """
    Render, conform to PDF/A-3b, then sign — in that order and no other.

    PDF/A conversion rewrites the file, so converting after signing would strip
    the signature's validity while leaving the signature visibly present, which
    is the worst of the three possible outcomes. Callers do not get to choose
    the order.
    """
    guard(att)
    # Not recorded. The key covers the input bytes, and those include the issue
    # time, so no certificate is ever rendered twice under the same key. A
    # recording here could not be replayed even in principle — it would just
    # leave a PDF on disk for every document ever issued.
    pdf = nutrient.html_to_pdf(render(att), pdfa=PDFA, record=False)
    return nutrient.sign(pdf, record=False)
