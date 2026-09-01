"""
Enrolment rosters and the claims table.

The interesting test here is the concurrent one. Every other guarantee in this
file is a lookup working correctly; that one is about what happens when the
lookup is right and still not enough — sixteen simultaneous claims from one
person, which is precisely the shape of attack a rewarded airdrop invites.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schema  # noqa: E402
from gate import GateMode  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "claims.db")


@pytest.fixture
def tenant(store):
    return store.create_tenant("example.test", "hash")


@pytest.fixture
def gate(store, tenant):
    wf = store.create_workflow(tenant["id"], "airdrop")
    return store.create_gate(wf["id"], GateMode.ONE_HUMAN_ONE_CLAIM)


# ── the roster ────────────────────────────────────────────────────────────
def test_a_roster_is_scoped_to_its_context(store, tenant):
    store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop-a")
    store.create_enrolment(tenant["id"], "bob", {"b": 1}, context="drop-b")

    assert len(store.roster("drop-a")) == 1
    assert len(store.roster("drop-b")) == 1
    assert store.roster("drop-c") == []


def test_a_roster_is_ordered_deterministically(store, tenant):
    """Two sweeps must name the same nearest enrolment or a review is not reproducible."""
    for i in range(6):
        store.create_enrolment(tenant["id"], f"s{i}", {"v": i}, context="drop")
    assert [r["id"] for r in store.roster("drop")] == \
           [r["id"] for r in store.roster("drop")]


def test_a_roster_does_not_leak_across_a_context_prefix(store, tenant):
    """'drop' must not sweep up 'drop-2', or two campaigns share a roster."""
    store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    store.create_enrolment(tenant["id"], "bob", {"b": 1}, context="drop-2")
    assert len(store.roster("drop")) == 1


# ── the claim ─────────────────────────────────────────────────────────────
def test_a_claim_is_recorded_and_found(store, gate, tenant):
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    store.add_claim(gate["id"], enr["id"], "drop", "0xnull", "0xabc",
                    "UNIQUE", "machine")

    found = store.claim_for("drop", "0xnull")
    assert found["address"] == "0xabc" and found["verdict"] == "UNIQUE"
    assert store.claim_for("drop", "0xother") is None


def test_a_claim_writes_to_the_audit_chain(store, gate, tenant):
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    store.add_claim(gate["id"], enr["id"], "drop", "0xnull", "0xabc",
                    "UNIQUE", "machine")

    events = [e for e in store.chain(gate["id"]) if e["type"] == "claim"]
    assert len(events) == 1
    assert store.verify_chain(gate["id"]).ok


def test_a_second_claim_on_the_same_nullifier_is_refused(store, gate, tenant):
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    store.add_claim(gate["id"], enr["id"], "drop", "0xnull", "0xabc",
                    "UNIQUE", "machine")

    with pytest.raises(sqlite3.IntegrityError):
        store.add_claim(gate["id"], enr["id"], "drop", "0xnull", "0xdifferent",
                        "UNIQUE", "machine")


def test_the_same_nullifier_in_another_context_is_allowed(store, gate, tenant):
    """Claiming in a second campaign is a different, legitimate claim."""
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    store.add_claim(gate["id"], enr["id"], "drop-a", "0xnull", "0xabc",
                    "UNIQUE", "machine")
    store.add_claim(gate["id"], enr["id"], "drop-b", "0xnull", "0xabc",
                    "UNIQUE", "machine")
    assert store.claim_for("drop-a", "0xnull")["context"] == "drop-a"
    assert store.claim_for("drop-b", "0xnull")["context"] == "drop-b"


def test_the_index_is_declared_in_the_schema():
    assert ("context", "nullifier") in schema.UNIQUE_INDEXES["claims"]
    assert any("ux_claims_context_nullifier" in s for s in schema.create_sql())
    exported = {t["name"]: t for t in schema.xano_export()["tables"]}
    assert ["context", "nullifier"] in exported["claims"]["unique"], \
        "Xano must be told about the constraint too, or production has no guard"


def test_the_index_refuses_every_simultaneous_duplicate(store, gate, tenant):
    """
    Straight at the table, no lookup first. Exactly one insert may survive and
    the other fifteen must raise, because the index is the only thing that
    actually enforces this — an application-level check is a suggestion.
    """
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    n = 16
    barrier = threading.Barrier(n)
    written, refused = [], []

    def claim(i):
        barrier.wait()
        try:
            store.add_claim(gate["id"], enr["id"], "drop", "0xnull",
                            f"0x{i:040x}", "UNIQUE", "machine")
            written.append(i)
        except sqlite3.IntegrityError:
            refused.append(i)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(written) == 1, f"{len(written)} claims got through"
    assert len(refused) == n - 1
    assert store.verify_chain(gate["id"]).ok


def test_only_one_of_sixteen_simultaneous_claims_is_written(store, gate, tenant):
    """
    The realistic path: look first, then insert. Whether a thread is turned
    away by the lookup or by the index is immaterial — what matters is that one
    person ends up with one claim, however the sixteen requests interleave.
    """
    enr = store.create_enrolment(tenant["id"], "alice", {"a": 1}, context="drop")
    n = 16
    barrier = threading.Barrier(n)
    written, errors = [], []

    def claim(i):
        barrier.wait()
        try:
            if store.claim_for("drop", "0xnull") is None:
                store.add_claim(gate["id"], enr["id"], "drop", "0xnull",
                                f"0x{i:040x}", "UNIQUE", "machine")
                written.append(i)
        except sqlite3.IntegrityError:
            pass  # refused by the index; the outcome is the same
        except Exception as exc:  # noqa: BLE001 - asserted on below
            errors.append(exc)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"unexpected errors: {errors[:3]}"
    assert len(written) == 1, f"{len(written)} claims got through"

    claims = store.db.execute(
        "SELECT COUNT(*) AS n FROM claims WHERE context = ?", ("drop",)).fetchone()
    assert claims["n"] == 1
    assert store.verify_chain(gate["id"]).ok
