"""
Nullifiers and on-chain-verifiable claims.

The signature tests are mostly round-trips, which sounds weak until you notice
what a round-trip proves here: `recover` is the same operation Solidity's
`ecrecover` performs, so a message that recovers to the issuer address in this
process is one a contract can check without an oracle. The tests that tamper
with a field and assert recovery *fails* are the load-bearing ones.

The nullifier tests are about two properties that pull in opposite directions —
the same person must collide with themselves inside a context, and must not be
linkable across contexts — plus the encoding detail that would silently break
the first one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claim as claim_mod  # noqa: E402
from claim import (KEY_ENV, SECRET_ENV, Claim, NotSignable, build,  # noqa: E402
                   issuer_address, nullifier, recover, sign, using_dev_secret)

KEY = "0x" + "11" * 32
ADDR = "0x" + "ab" * 20


@pytest.fixture
def signing(monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv(SECRET_ENV, "test-secret")


def a_claim(**over):
    args = dict(context="airdrop-2026", address=ADDR, enrolment_id="enr-1",
                verdict="UNIQUE", gate_id="gate-1", chain_head="0xfeed",
                roster_size=5, comparisons=5, false_match_bound=0.0023,
                decided_by="machine")
    args.update(over)
    return build(**args)


# ── nullifier: the same person, twice ─────────────────────────────────────
def test_the_same_person_gets_the_same_nullifier_in_one_context():
    """Without this there is no Sybil resistance at all."""
    assert nullifier("enr-1", "airdrop") == nullifier("enr-1", "airdrop")


def test_two_people_get_different_nullifiers():
    assert nullifier("enr-1", "airdrop") != nullifier("enr-2", "airdrop")


def test_the_same_person_is_unlinkable_across_contexts():
    """The privacy claim: two campaigns cannot join up their rosters."""
    assert nullifier("enr-1", "airdrop-a") != nullifier("enr-1", "airdrop-b")


def test_the_encoding_cannot_be_confused_by_a_shifted_boundary():
    """
    'ab'+'c' and 'a'+'bc' must not hash alike. Unprefixed concatenation would
    give two different people one nullifier, and cost one of them their claim.
    """
    assert nullifier("ab", "cde") != nullifier("a", "bcde")


def test_a_nullifier_does_not_contain_the_enrolment_id():
    n = nullifier("enr-secret-1", "airdrop")
    assert "enr-secret-1" not in n
    assert n.startswith("0x") and len(n) == 66


def test_a_different_secret_gives_a_different_nullifier(monkeypatch):
    monkeypatch.setenv(SECRET_ENV, "secret-a")
    a = nullifier("enr-1", "airdrop")
    monkeypatch.setenv(SECRET_ENV, "secret-b")
    assert nullifier("enr-1", "airdrop") != a


def test_the_dev_secret_is_declared(monkeypatch):
    monkeypatch.delenv(SECRET_ENV, raising=False)
    assert using_dev_secret() is True
    monkeypatch.setenv(SECRET_ENV, "real")
    assert using_dev_secret() is False


def test_the_dev_secret_is_stable_across_calls(monkeypatch):
    """
    A random per-process fallback would let the same person claim twice after a
    restart, which is the one failure this module exists to prevent.
    """
    monkeypatch.delenv(SECRET_ENV, raising=False)
    assert nullifier("enr-1", "airdrop") == nullifier("enr-1", "airdrop")


@pytest.mark.parametrize("bad", ["", "ab", "has space", "sla/sh", "x" * 65,
                                 "-leading", "emoji-🙂"])
def test_a_malformed_context_is_refused(bad):
    with pytest.raises(NotSignable, match="domain separator"):
        nullifier("enr-1", bad)


# ── the claim ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["", "0xnothex", "0x" + "ab" * 19,
                                 "0x" + "ab" * 21, "ab" * 20, None])
def test_a_bad_address_is_refused(bad):
    with pytest.raises(NotSignable, match="hex address"):
        a_claim(address=bad)


def test_a_lowercase_address_is_accepted():
    """EIP-55 is a typo guard; refusing a valid lowercase address refuses a valid claim."""
    assert a_claim(address="0x" + "ab" * 20).address == "0x" + "ab" * 20


def test_the_message_is_stable_and_sorted():
    c = a_claim()
    assert c.message() == c.message()
    assert json.loads(c.message())["verdict"] == "UNIQUE"
    assert " " not in c.message(), "whitespace would break byte-identical rebuilds"


def test_the_message_carries_the_whole_decision():
    """
    A contract that sees only "approved" cannot tell an auto-decided claim from
    one a human settled after an ambiguous sweep.
    """
    m = json.loads(a_claim(decided_by="reviewer:alice").message())
    for field in ("verdict", "decided_by", "roster_size", "comparisons",
                  "false_match_bound", "chain_head", "context", "address"):
        assert field in m, f"{field} missing from the signed statement"
    assert m["decided_by"] == "reviewer:alice"


# ── signing ───────────────────────────────────────────────────────────────
def test_unsigned_without_a_key(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(NotSignable, match="no signing key"):
        sign(a_claim())
    assert issuer_address() is None


def test_a_signature_recovers_to_the_issuer(signing):
    out = sign(a_claim())
    assert recover(out["message"], out["signature"]) == out["issuer"]
    assert out["issuer"] == issuer_address()


def test_the_signature_is_the_form_a_contract_expects(signing):
    out = sign(a_claim())
    assert out["scheme"] == "EIP-191 personal_sign"
    assert out["signature"].startswith("0x") and len(out["signature"]) == 132
    assert out["v"] in (27, 28)
    assert out["r"].startswith("0x") and out["s"].startswith("0x")


@pytest.mark.parametrize("field,value", [
    ("verdict", "DUPLICATE"),
    ("address", "0x" + "cd" * 20),
    ("roster_size", 99),
    ("decided_by", "machine-but-actually-not"),
    ("chain_head", "0xbeef"),
])
def test_editing_any_signed_field_breaks_recovery(signing, field, value):
    """The point of signing the whole decision rather than just the verdict."""
    out = sign(a_claim())
    edited = json.loads(out["message"])
    edited[field] = value
    tampered = json.dumps(edited, sort_keys=True, separators=(",", ":"))
    assert recover(tampered, out["signature"]) != out["issuer"]


def test_two_issuers_are_distinguishable(signing, monkeypatch):
    a = sign(a_claim())
    monkeypatch.setenv(KEY_ENV, "0x" + "22" * 32)
    b = sign(a_claim())
    assert a["issuer"] != b["issuer"]
    assert recover(b["message"], b["signature"]) == b["issuer"]


def test_a_signed_claim_declares_its_limits(signing):
    out = sign(a_claim())
    assert out["limitations"], "a claim that states no limits is not honest"
    joined = " ".join(out["limitations"]).lower()
    assert "zero knowledge" in joined, "the Semaphore contrast must be stated"
    assert out["dev_secret"] is False


def test_a_dev_secret_claim_says_so(monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.delenv(SECRET_ENV, raising=False)
    assert sign(a_claim())["dev_secret"] is True
