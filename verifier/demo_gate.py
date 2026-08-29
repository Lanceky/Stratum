"""
The Step 3 demo beat, as a runnable transcript.

    make gate-demo

Shows an AI agent driving a $250,000 transfer all the way to the boundary and
being refused at it — then the same move succeeding for a human, and the whole
sequence remaining hash-verifiable including the refusal.

This is the thirty seconds of the pitch that carries the product.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate import Actor, GateMode, GateState, IllegalTransition, reachable_by_agent
from store import Store

G, R, Y, B, DIM, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"


def rule(title: str = "") -> None:
    print(f"\n{DIM}{'─' * 68}{X}" + (f"\n{title}" if title else ""))


def attempt(store: Store, gate_id: str, to: GateState, actor: Actor) -> None:
    label = f"  {actor.value:<7} {'→':<2} {to.value:<11}"
    try:
        store.gate_transition(gate_id, to, actor)
        print(f"{label} {G}accepted{X}")
    except IllegalTransition as exc:
        print(f"{label} {R}REFUSED 409{X}  {DIM}{exc.reason}{X}")


def main() -> None:
    store = Store()
    tenant = store.create_tenant("acme-bank.example", api_key_hash="sha256:…")
    workflow = store.create_workflow(
        tenant["id"], "wire_transfer",
        {"amount": 250_000, "currency": "USD", "beneficiary": "new-payee-0042"},
        agent_session_id="agent-7")
    gate = store.create_gate(workflow["id"], GateMode.AUTHORISE_ACTION)
    gid = gate["id"]

    print(f"\n{B}STRATUM — human authorisation boundary{X}")
    print(f"{DIM}workflow: wire_transfer $250,000 · requested by agent-7{X}")
    print(f"{DIM}gate:     {gid}{X}")
    print(f"{DIM}mode:     {gate['mode']}  ·  state: {gate['state']}{X}")

    rule("1. The agent does the work it is allowed to do.")
    attempt(store, gid, GateState.CHALLENGED, Actor.AGENT)

    rule("2. The agent tries to skip the human and sign.")
    attempt(store, gid, GateState.SIGNED, Actor.AGENT)

    rule("3. A human presents a face. Checks run. The gate reaches PASS.")
    for to, actor in ((GateState.CAPTURED, Actor.HUMAN),
                      (GateState.SCORED, Actor.SYSTEM)):
        attempt(store, gid, to, actor)
    for check_no, score in ((1, 0.94), (2, 0.88), (3, 0.91)):
        store.add_evidence(gid, check_no, score)
    print(f"  {DIM}evidence: check1=0.94  check2=0.88  check3=0.91{X}")
    attempt(store, gid, GateState.PASS, Actor.SYSTEM)

    rule("4. At the boundary, with every check passed, the agent tries again.")
    attempt(store, gid, GateState.SIGNED, Actor.AGENT)
    print(f"  {DIM}passing the checks does not grant the agent authority.{X}")
    print(f"  {DIM}the refusal is about who is acting, not about the score.{X}")

    rule("5. The human signs. Same move, different actor.")
    attempt(store, gid, GateState.SIGNED, Actor.HUMAN)
    attempt(store, gid, GateState.SEALED, Actor.SYSTEM)

    rule("6. The ledger.")
    events = store.chain(gid)
    for i, e in enumerate(events):
        mark = R + "✗" + X if e["type"] == "transition.refused" else G + "✓" + X
        print(f"  {i:>2} {mark} {e['type']:<20} {DIM}{e['hash'][:16]}…{X}")

    result = store.verify_chain(gid)
    colour = G if result.ok else R
    print(f"\n  chain: {colour}{'VERIFIED' if result.ok else 'BROKEN'}{X} "
          f"over {result.length} events")
    print(f"  {DIM}every refusal is permanent evidence — an auditor can prove{X}")
    print(f"  {DIM}the agent tried, and prove that it did not succeed.{X}")

    rule("7. Tamper with one event and re-verify.")
    events[2]["payload"] = '{"actor":"human","from":"REQUESTED","to":"SIGNED"}'
    broken = __import__("ledger").verify_chain(events)
    print(f"  forged event 2 → {R}{'BROKEN' if not broken.ok else 'ok'}{X} "
          f"at index {broken.broken_at}")
    print(f"  {DIM}{broken.reason}{X}")

    rule("8. Structural guarantee, independent of this run.")
    reach = sorted(s.value for s in reachable_by_agent())
    print(f"  states an agent can reach unaided: {Y}{', '.join(reach)}{X}")
    print(f"  {G}SIGNED and SEALED are not reachable by any agent path.{X}")
    print(f"  {DIM}enforced in gate.py:TRANSITIONS, asserted in tests/test_gate.py{X}\n")


if __name__ == "__main__":
    main()
