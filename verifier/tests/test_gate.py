"""
Gate state machine tests (implementation.md Step 3, definition of done).

The first two tests are the product. If either fails, STRATUM does not do the
one thing it claims to do, and no amount of scoring accuracy downstream
rescues it.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate import (  # noqa: E402
    TRANSITIONS, Actor, GateMode, GateState, IllegalTransition,
    allowed_actors, check, reachable_by_agent,
)
from store import Store  # noqa: E402


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def gate(store):
    t = store.create_tenant("acme.example", "hash")
    w = store.create_workflow(t["id"], "wire_transfer", {"amount": 250_000},
                              agent_session_id="agent-7")
    return store.create_gate(w["id"], GateMode.AUTHORISE_ACTION)


# ── the thesis ────────────────────────────────────────────────────────────
def test_an_agent_can_never_reach_signed():
    """
    Drive the graph using only agent-permitted edges. SIGNED and SEALED must
    be unreachable. This is the whole product, as a lookup table.
    """
    reachable = reachable_by_agent()
    assert GateState.SIGNED not in reachable
    assert GateState.SEALED not in reachable


def test_no_transition_into_signed_admits_an_agent():
    for (_frm, to), actors in TRANSITIONS.items():
        if to in (GateState.SIGNED, GateState.SEALED):
            assert Actor.AGENT not in actors


def test_requested_to_signed_is_refused(store, gate):
    """The demo beat. Keep this output."""
    with pytest.raises(IllegalTransition) as e:
        store.gate_transition(gate["id"], GateState.SIGNED, Actor.HUMAN)
    assert "no such transition" in str(e.value)
    assert store.get("gates", gate["id"])["state"] == GateState.REQUESTED


def test_even_a_human_cannot_skip_the_checks(store, gate):
    """Human authority is necessary, not sufficient — evidence is still required."""
    store.gate_transition(gate["id"], GateState.CHALLENGED, Actor.AGENT)
    with pytest.raises(IllegalTransition):
        store.gate_transition(gate["id"], GateState.SIGNED, Actor.HUMAN)


def test_agent_is_refused_at_the_boundary(store, gate):
    for to, actor in ((GateState.CHALLENGED, Actor.AGENT),
                      (GateState.CAPTURED, Actor.HUMAN),
                      (GateState.SCORED, Actor.SYSTEM),
                      (GateState.PASS, Actor.SYSTEM)):
        store.gate_transition(gate["id"], to, actor)

    with pytest.raises(IllegalTransition) as e:
        store.gate_transition(gate["id"], GateState.SIGNED, Actor.AGENT)
    assert "only a human may authorise" in str(e.value)

    # The same move by a human succeeds — the refusal was about the actor.
    assert store.gate_transition(
        gate["id"], GateState.SIGNED, Actor.HUMAN)["state"] == GateState.SIGNED


# ── happy paths ───────────────────────────────────────────────────────────
def test_full_pass_route(store, gate):
    steps = [(GateState.CHALLENGED, Actor.AGENT), (GateState.CAPTURED, Actor.HUMAN),
             (GateState.SCORED, Actor.SYSTEM), (GateState.PASS, Actor.SYSTEM),
             (GateState.SIGNED, Actor.HUMAN), (GateState.SEALED, Actor.SYSTEM)]
    for to, actor in steps:
        assert store.gate_transition(gate["id"], to, actor)["state"] == to


def test_review_route_can_approve_or_reject(store, gate):
    for to, actor in ((GateState.CHALLENGED, Actor.AGENT),
                      (GateState.CAPTURED, Actor.HUMAN),
                      (GateState.SCORED, Actor.SYSTEM),
                      (GateState.REVIEW, Actor.SYSTEM)):
        store.gate_transition(gate["id"], to, actor)

    assert allowed_actors(GateState.REVIEW, GateState.SIGNED) == frozenset({Actor.HUMAN})
    assert allowed_actors(GateState.REVIEW, GateState.FAIL) == frozenset({Actor.HUMAN})

    store.add_review(gate["id"], "reviewer-1", "approved", "low check-2 margin")
    assert store.gate_transition(
        gate["id"], GateState.SIGNED, Actor.HUMAN)["state"] == GateState.SIGNED


# ── terminal states and expiry ────────────────────────────────────────────
def test_terminal_states_are_final(store, gate):
    store.gate_transition(gate["id"], GateState.FAIL, Actor.SYSTEM)
    with pytest.raises(IllegalTransition, match="terminal"):
        store.gate_transition(gate["id"], GateState.CHALLENGED, Actor.SYSTEM)


def test_expired_gate_can_only_fail(store, gate):
    later = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(IllegalTransition, match="expired"):
        store.gate_transition(gate["id"], GateState.CHALLENGED, Actor.AGENT, at=later)
    assert store.expire_if_due(gate["id"], at=later)["state"] == GateState.FAIL


def test_expiry_cannot_be_used_to_reach_signed(store, gate):
    """An expired gate must not become a shortcut past the checks."""
    later = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(IllegalTransition):
        store.gate_transition(gate["id"], GateState.SIGNED, Actor.HUMAN, at=later)


# ── exhaustive illegality ─────────────────────────────────────────────────
def test_every_undeclared_transition_is_refused():
    """
    The rule is a whitelist, not a blacklist. Assert that directly, so adding a
    state later cannot silently open a path.
    """
    for frm in GateState:
        for to in GateState:
            if (frm, to) in TRANSITIONS:
                continue
            for actor in Actor:
                with pytest.raises(IllegalTransition):
                    check(frm, to, actor)


def test_no_self_transitions():
    assert not any(frm is to for frm, to in TRANSITIONS)


def test_mode_is_data_not_a_code_fork(store):
    """All three verticals share one machine — that is why Step 11 is cheap."""
    t = store.create_tenant("acme.example", "hash")
    w = store.create_workflow(t["id"], "k")
    for mode in GateMode:
        g = store.create_gate(w["id"], mode)
        assert g["mode"] == mode
        assert g["state"] == GateState.REQUESTED
