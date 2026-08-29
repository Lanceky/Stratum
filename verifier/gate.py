"""
The gate state machine (implementation.md Step 3b).

    REQUESTED → CHALLENGED → CAPTURED → SCORED
             → PASS ──────────────────────────→ SIGNED → SEALED
             → REVIEW → (approved → SIGNED | rejected → FAIL)
             → FAIL

Two properties are enforced here and nowhere else, because a rule that lives in
two places is a rule that will eventually disagree with itself:

  1. Only the transitions in TRANSITIONS are legal. Everything else is a 409.
  2. Every transition names the actor allowed to make it, and no transition
     into SIGNED accepts `agent`. That is the entire product thesis expressed
     as a lookup table: an AI agent can drive a workflow to the boundary and
     no further.

`gate_transition` is the single choke point. Nothing else may write gates.state.
"""

from __future__ import annotations

from enum import StrEnum


class GateState(StrEnum):
    REQUESTED = "REQUESTED"
    CHALLENGED = "CHALLENGED"
    CAPTURED = "CAPTURED"
    SCORED = "SCORED"
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    SIGNED = "SIGNED"
    SEALED = "SEALED"


class GateMode(StrEnum):
    """A column on `gates`, not a fork in the code (context.md §5.1-5.3)."""

    AUTHORISE_ACTION = "authorise_action"
    VERIFY_IDENTITY = "verify_identity"
    ONE_HUMAN_ONE_CLAIM = "one_human_one_claim"


class Actor(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


TERMINAL = frozenset({GateState.SEALED, GateState.FAIL})

# (from, to) -> actors permitted to make that move.
TRANSITIONS: dict[tuple[GateState, GateState], frozenset[Actor]] = {
    (GateState.REQUESTED, GateState.CHALLENGED): frozenset({Actor.AGENT, Actor.SYSTEM}),
    (GateState.CHALLENGED, GateState.CAPTURED): frozenset({Actor.HUMAN}),
    (GateState.CAPTURED, GateState.SCORED): frozenset({Actor.SYSTEM}),
    (GateState.SCORED, GateState.PASS): frozenset({Actor.SYSTEM}),
    (GateState.SCORED, GateState.REVIEW): frozenset({Actor.SYSTEM}),
    (GateState.SCORED, GateState.FAIL): frozenset({Actor.SYSTEM}),
    # The boundary. Human only, in both routes to SIGNED.
    (GateState.PASS, GateState.SIGNED): frozenset({Actor.HUMAN}),
    (GateState.REVIEW, GateState.SIGNED): frozenset({Actor.HUMAN}),
    (GateState.REVIEW, GateState.FAIL): frozenset({Actor.HUMAN}),
    (GateState.SIGNED, GateState.SEALED): frozenset({Actor.SYSTEM}),
    # Expiry is the only way to leave a live gate early, and it can never
    # shortcut toward SIGNED.
    (GateState.REQUESTED, GateState.FAIL): frozenset({Actor.SYSTEM}),
    (GateState.CHALLENGED, GateState.FAIL): frozenset({Actor.SYSTEM}),
    (GateState.CAPTURED, GateState.FAIL): frozenset({Actor.SYSTEM}),
}

SIGNING_STATES = frozenset({GateState.SIGNED, GateState.SEALED})


class IllegalTransition(Exception):
    """Maps to HTTP 409. Always accompanied by an audit_events row."""

    def __init__(self, frm: GateState, to: GateState, actor: Actor, reason: str):
        self.frm, self.to, self.actor, self.reason = frm, to, actor, reason
        super().__init__(f"{frm} -> {to} refused for actor={actor}: {reason}")


def allowed_actors(frm: GateState, to: GateState) -> frozenset[Actor]:
    return TRANSITIONS.get((GateState(frm), GateState(to)), frozenset())


def check(frm: GateState, to: GateState, actor: Actor, *, expired: bool = False) -> None:
    """
    Raise IllegalTransition unless the move is legal for this actor.

    Pure and side-effect free, so it can be reasoned about and tested on its
    own. `gate_transition` in store.py wraps it with persistence and audit.
    """
    frm, to, actor = GateState(frm), GateState(to), Actor(actor)

    if frm in TERMINAL:
        raise IllegalTransition(frm, to, actor, f"{frm} is terminal")

    permitted = TRANSITIONS.get((frm, to))
    if permitted is None:
        raise IllegalTransition(frm, to, actor, "no such transition")

    if expired and to is not GateState.FAIL:
        raise IllegalTransition(frm, to, actor, "gate has expired")

    if actor not in permitted:
        want = "/".join(sorted(permitted))
        detail = ("only a human may authorise" if to in SIGNING_STATES
                  else f"requires {want}")
        raise IllegalTransition(frm, to, actor, detail)


def reachable_by_agent() -> set[GateState]:
    """
    Every state an actor of type `agent` can drive a gate into, unaided.

    Used by a test that asserts SIGNED and SEALED are not in the set. If that
    test ever fails, the product no longer does what it claims.
    """
    seen = {GateState.REQUESTED}
    frontier = [GateState.REQUESTED]
    while frontier:
        cur = frontier.pop()
        for (frm, to), actors in TRANSITIONS.items():
            if frm is cur and Actor.AGENT in actors and to not in seen:
                seen.add(to)
                frontier.append(to)
    return seen
