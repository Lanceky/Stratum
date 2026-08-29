"""
Print what check 1 actually measures, and where it stops working.

This is the Step 5 definition of done as a report rather than an assertion. The
tests say pass or fail; this says by how much, against which attack, and — most
usefully — how much of a real skin-score response the check needs before it
breaks. That last number is the one worth publishing: we have no Perfect Corp
credentials yet, so the size of the illumination effect on real skin is a guess.
Sweeping it converts the guess into a stated requirement, which Step 12 can then
confirm or refute against the real API.

Run with `make presence`. Everything here comes from `synth_attacks.py`, a
physical simulation, not from a camera. No physical presentation attack has been
run. Synthetic rejection is evidence that the physics works, not evidence that
the attacks were defeated in the room.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import challenge as ch  # noqa: E402
import synth_attacks as sa  # noqa: E402
from checks.presence import MIN_RESPONSE_Z, evaluate  # noqa: E402

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")

MEDIA = ("live", "print", "screen", "injection")
N = 60

# What each medium is supposed to do. `print` is listed as a known gap rather
# than a pass, because claiming otherwise would be the one dishonest line in
# this file.
EXPECT = {"live": "accept", "print": "known gap", "screen": "reject",
          "injection": "reject"}


def _run(medium: str, n: int = N, n_frames: int | None = None,
         effect: float | None = None) -> list:
    kw = {"n_frames": n_frames} if n_frames else {}
    # `effect` is passed explicitly rather than by rebinding sa.ILLUM_EFFECT:
    # `session` captures it as a default argument, so assigning to the module
    # constant would silently sweep nothing and report a flat line.
    fx = {"effect": effect} if effect is not None else {}
    out = []
    for s in range(n):
        spec = ch.derive(f"report-{s}", **kw)
        frames = sa.session(spec, medium=medium, seed=s, **fx)
        out.append(evaluate(frames, spec,
                            issued_at=frames[0]["captured_at"] - 1.0))
    return out


def _verdict_table() -> dict[str, float]:
    print(f"\n{BOLD}Check 1 — presence, by attack medium{RESET}  "
          f"{DIM}({N} simulated sessions each){RESET}\n")
    print(f"  {DIM}{'medium':<11}{'accepted':>10}   "
          f"{'illum':>7}{'pose':>7}{'timing':>8}{'depth':>7}   expected{RESET}")

    rates = {}
    for medium in MEDIA:
        results = _run(medium)
        rate = sum(r.passed for r in results) / len(results)
        rates[medium] = rate
        per = {}
        for r in results:
            for s in r.signals:
                per[s.name] = per.get(s.name, 0) + s.passed

        good = (rate >= 0.85 if medium == "live"
                else rate <= 0.10 if medium in ("screen", "injection")
                else True)
        colour = GREEN if good else RED
        if medium == "print":
            colour = YELLOW
        print(f"  {BOLD}{medium:<11}{RESET}{colour}{rate:>9.0%}{RESET}   "
              f"{per['illumination'] / N:>6.0%} {per['pose'] / N:>6.0%} "
              f"{per['timing'] / N:>7.0%} {per['geometry'] / N:>6.0%}   "
              f"{DIM}{EXPECT[medium]}{RESET}")

    print(f"\n  {DIM}An injected stream is the attack this project exists to "
          f"stop: it is a recording of a\n  real 3D face, so depth cannot see "
          f"it, and it cannot know what colour the screen flashed.{RESET}")
    print(f"  {YELLOW}Printed photographs are the known gap.{RESET}{DIM} Paper "
          f"reddens under a red flash and can be\n  turned on cue, so only "
          f"depth separates it — and depth is partial, not decisive.{RESET}")
    return rates


def _frame_budget() -> None:
    """Why five frames, and not the three the check was first written for."""
    print(f"\n{BOLD}Frame budget{RESET}  "
          f"{DIM}only same-pose frames are comparable, so pairs grow as "
          f"C(n-1, 2){RESET}\n")
    print(f"  {DIM}{'frames':<8}{'pairs':>7}{'live':>9}{'injection':>11}{RESET}")
    for n in range(ch.MIN_FRAMES, len(ch.PALETTE) + 1):
        pairs = len(ch.derive("report-0", n_frames=n).predictions)
        live = sum(r.passed for r in _run("live", 30, n)) / 30
        inj = sum(r.passed for r in _run("injection", 30, n)) / 30
        print(f"  {n:<8}{pairs:>7}{live:>8.0%}{inj:>10.0%}")
    print(f"\n  {DIM}At three frames there is one comparable pair, which cannot "
          f"clear the evidence bar:\n  the check would abstain on every "
          f"session, honest and attacker alike. `derive` refuses it.{RESET}")


def _sensitivity() -> None:
    """
    The number this report exists for.

    `ILLUM_EFFECT` is how many points a coloured flash moves a volatile skin
    score. We cannot measure it without credentials, so instead of asserting a
    guess, sweep it against the fixed score noise and report the ratio at which
    the check stops working. That turns an unknown into an acceptance criterion
    for the real API.
    """
    print(f"\n{BOLD}Sensitivity to the real illumination effect{RESET}  "
          f"{DIM}the unmeasured constant{RESET}\n")
    print(f"  {DIM}score noise is fixed at {sa.SCORE_NOISE} points; "
          f"the flash response is swept{RESET}\n")
    print(f"  {DIM}{'effect':>7}{'effect/noise':>14}{'live':>9}"
          f"{'injection':>11}   {RESET}")

    baseline = sa.ILLUM_EFFECT
    usable_at = None
    for effect in (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0):
        live = sum(r.passed for r in _run("live", 30, effect=effect)) / 30
        inj = sum(r.passed for r in _run("injection", 30, effect=effect)) / 30
        usable = live >= 0.85 and inj <= 0.10
        if usable and usable_at is None:
            usable_at = effect
        if not usable:
            usable_at = None
        mark = f"{GREEN}usable{RESET}" if usable else f"{RED}too weak{RESET}"
        star = "  <- shipped" if effect == baseline else ""
        print(f"  {effect:>7.1f}{effect / sa.SCORE_NOISE:>13.1f}x"
              f"{live:>8.0%}{inj:>10.0%}   {mark}{DIM}{star}{RESET}")

    if usable_at:
        print(f"\n  {BOLD}Requirement on the real API:{RESET} a coloured flash "
              f"must move a volatile skin score by\n  at least "
              f"{BOLD}{usable_at / sa.SCORE_NOISE:.1f}x its own frame-to-frame "
              f"noise{RESET} for check 1 to be usable.")
        print(f"  {DIM}Below that the deadband swallows the response and the "
              f"check abstains rather than guessing —\n  which is the correct "
              f"failure, but it is a failure. Step 12 measures the real "
              f"ratio.{RESET}")
    else:
        print(f"\n  {RED}No swept effect size made the check usable — "
              f"investigate before trusting any of the above.{RESET}")


def main() -> int:
    t0 = time.time()
    print(f"\n{DIM}{'─' * 74}{RESET}")
    print(f"{BOLD}STRATUM check 1 — presence{RESET}   "
          f"{DIM}simulated physics, no camera, no credentials{RESET}")
    print(f"{DIM}{'─' * 74}{RESET}")

    rates = _verdict_table()
    _frame_budget()
    _sensitivity()

    ok = (rates["live"] >= 0.85 and rates["injection"] <= 0.10
          and rates["screen"] <= 0.10)
    verdict = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"\n  Step 5 gate: {verdict}   {DIM}(live accepted, injection and "
          f"screen rejected; {time.time() - t0:.0f}s){RESET}")
    print(f"  {DIM}Physical presentation attacks against a real camera remain "
          f"unrun — see Step 12.{RESET}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
