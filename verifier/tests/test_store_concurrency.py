"""
The store under concurrent callers.

FastAPI runs synchronous route handlers in a threadpool, so two requests that
arrive together reach the same Store on two threads at the same time. Every
test here failed before the lock was added, and each one fails in a different
way, which is why they are separate tests rather than one:

  * concurrent reads crashed the connection outright (HTTP 500),
  * concurrent appends forked the audit chain into two blocks claiming the
    same parent — silent, and indistinguishable afterwards from tampering,
  * concurrent transitions both passed a check that was only true for one.

The third is the one that matters. STRATUM's whole claim is that a gate cannot
reach SIGNED without a human, and a check that two callers can pass at once is
not a check.
"""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ledger  # noqa: E402
from gate import Actor, GateMode, GateState, IllegalTransition  # noqa: E402
from store import Store  # noqa: E402

THREADS = 16


@pytest.fixture
def store(tmp_path):
    # A file, not :memory:. An in-memory database is per-connection, and the
    # bug being tested is one connection shared across threads.
    return Store(tmp_path / "concurrency.db")


def _run(fn, n=THREADS):
    """Start n threads on a barrier so they collide rather than queue."""
    barrier = threading.Barrier(n)
    results, errors = [], []

    def go(i):
        barrier.wait()
        try:
            results.append(fn(i))
        except Exception as exc:  # noqa: BLE001 - recorded, asserted on below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def test_concurrent_reads_do_not_raise(store):
    gate = store.create_gate("w", GateMode.AUTHORISE_ACTION)
    _, errors = _run(lambda _: store.gates_in_state(GateState.REQUESTED))
    assert errors == [], f"reads raised: {errors[:3]}"
    assert gate["state"] == str(GateState.REQUESTED)


def test_reads_racing_writes_do_not_raise(store):
    """
    What the review console actually does: a queue listing arrives while some
    other request is appending. Unlocked this is the HTTP 500 — the reader and
    the writer share one connection, and one of them finds it mid-statement.
    """
    gate = store.create_gate("w", GateMode.AUTHORISE_ACTION)

    def mixed(i):
        if i % 2:
            return store.audit(gate["id"], "probe", {"i": i})
        return store.gates_in_state(GateState.REQUESTED)

    _, errors = _run(mixed)
    assert errors == [], f"mixed read/write raised: {errors[:3]}"
    assert store.verify_chain(gate["id"]).ok


def test_concurrent_appends_do_not_fork_the_chain(store):
    """
    The quiet one. Two appends that read the same head write two blocks with
    the same prev_hash, and nothing raises — the damage is only visible later,
    when verify_chain reports a break that no attacker caused.
    """
    gate = store.create_gate("w", GateMode.AUTHORISE_ACTION)
    _, errors = _run(lambda i: store.audit(gate["id"], "probe", {"i": i}))
    assert errors == [], f"appends raised: {errors[:3]}"

    chain = store.chain(gate["id"])
    assert len(chain) == THREADS + 1  # + gate.created

    parents = [e["prev_hash"] for e in chain]
    assert len(set(parents)) == len(parents), "two events share a parent"

    result = store.verify_chain(gate["id"])
    assert result.ok, f"chain broken at {result.broken_at}: {result.reason}"


def test_concurrent_appends_across_gates_stay_separate(store):
    """Each gate keeps its own chain; parallel gates must not interleave."""
    gates = [store.create_gate(f"w{i}", GateMode.AUTHORISE_ACTION)
             for i in range(4)]
    _run(lambda i: store.audit(gates[i % 4]["id"], "probe", {"i": i}))

    for g in gates:
        assert store.verify_chain(g["id"]).ok
        assert store.chain(g["id"])[0]["prev_hash"] == ledger.GENESIS


def test_only_one_concurrent_transition_is_accepted(store):
    """
    The one that would matter in production. Sixteen callers race the same
    REQUESTED -> CHALLENGED move; exactly one may win, and the other fifteen
    must be refused and recorded as refused.
    """
    gate = store.create_gate("w", GateMode.AUTHORISE_ACTION)

    def attempt(_):
        try:
            store.gate_transition(gate["id"], GateState.CHALLENGED, Actor.AGENT)
            return "accepted"
        except IllegalTransition:
            return "refused"

    results, errors = _run(attempt)
    assert errors == [], f"unexpected errors: {errors[:3]}"
    assert results.count("accepted") == 1, results.count("accepted")
    assert results.count("refused") == THREADS - 1

    assert store.get("gates", gate["id"])["state"] == str(GateState.CHALLENGED)
    assert store.verify_chain(gate["id"]).ok

    refusals = [e for e in store.chain(gate["id"])
                if e["type"] == "transition.refused"]
    assert len(refusals) == THREADS - 1, "a refusal went unrecorded"


def test_agent_cannot_win_a_race_to_signed(store):
    """
    The claim, under load. An agent racing the signature must lose every time,
    not merely usually.
    """
    gate = store.create_gate("w", GateMode.AUTHORISE_ACTION)

    def attempt(_):
        try:
            store.gate_transition(gate["id"], GateState.SIGNED, Actor.AGENT)
            return "signed"
        except IllegalTransition:
            return "refused"

    results, errors = _run(attempt)
    assert errors == [], f"unexpected errors: {errors[:3]}"
    assert "signed" not in results
    assert store.get("gates", gate["id"])["state"] == str(GateState.REQUESTED)
    assert store.verify_chain(gate["id"]).ok
