"""
Print what check 2 can detect, and state plainly what it has not been shown to do.

This is Step 6's definition of done, restructured around what is actually
knowable today. The plan asks for 20 synthetic faces and 10 Perfect Corp
face-swaps scored against a genuine baseline. That requires credentials we do
not have, so those rows cannot be filled in, and filling them in from a
simulation would be inventing the answer — `synth_zones.py` explains at length
why the genuine model is defensible and a forgery model would not be.

What *is* knowable without credentials is the sensitivity of the instrument:

    null calibration  when genuine faces are tested against a genuine baseline,
                      does the check flag them at the rate it advertises?
    detection limit   how large must a structural deviation be before the check
                      sees it 80% of the time?

The second number is the deliverable. It converts "we hope generated faces look
different" into "a generated face must differ by at least this much, or we will
not catch it" — which Step 12 can confirm or refute with real samples the moment
credentials arrive, and which a reader can weigh immediately.

Run with `make authenticity`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth_zones as sz  # noqa: E402
from checks.authenticity import TARGET_FLAG_RATE, TESTS, evaluate, fit  # noqa: E402

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")

COHORT = 2000
TRIALS = 500
POWER_TARGET = 0.80
PLOT = Path(__file__).resolve().parent.parent / "benchmark" / "authenticity_power.png"

CONTRAST_GRID = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)
SHUFFLE_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


def _rate(baseline, faces) -> float:
    return sum(not evaluate(f, baseline).passed for f in faces) / len(faces)


def _limit(xs: list[float], powers: list[float]) -> float | None:
    """
    Where the power curve crosses 80%, by linear interpolation.

    Returns None if it never gets there — which is a result, not a gap, and is
    printed as such rather than being rounded up to the nearest grid point.
    """
    for (x0, p0), (x1, p1) in zip(zip(xs, powers), list(zip(xs, powers))[1:]):
        if (p0 - POWER_TARGET) * (p1 - POWER_TARGET) <= 0 and p0 != p1:
            return x0 + (POWER_TARGET - p0) * (x1 - x0) / (p1 - p0)
    return None


def _calibration(baseline) -> float:
    print(f"\n{BOLD}Null calibration{RESET}  "
          f"{DIM}genuine faces, tested against a genuine baseline{RESET}\n")
    held = [sz.genuine(10_000_000 + s)[0] for s in range(800)]
    results = [evaluate(f, baseline) for f in held]

    by_test = {}
    for r in results:
        for name in r.flagged_by:
            by_test[name] = by_test.get(name, 0) + 1

    observed = sum(not r.passed for r in results) / len(results)
    ok = observed <= TARGET_FLAG_RATE * 1.4
    colour = GREEN if ok else RED
    print(f"  advertised flag rate     {TARGET_FLAG_RATE:>6.1%}")
    print(f"  observed on held-out     {colour}{observed:>6.1%}{RESET}   "
          f"{DIM}n={len(results)}{RESET}")
    for name in TESTS:
        print(f"    {DIM}{name:<14} {by_test.get(name, 0) / len(results):>6.1%}{RESET}")
    print(f"\n  {DIM}Held-out means these faces were not used to fit the zone "
          f"baselines or the\n  thresholds. Testing on the fitting set would "
          f"understate this by about two points.{RESET}")
    return observed


def _power(baseline) -> dict:
    print(f"\n{BOLD}Detection limits{RESET}  "
          f"{DIM}how different must a face be before we see it?{RESET}\n")

    curves = {}
    print(f"  {DIM}{'texture contrast retained':<28}{'flagged':>9}{RESET}")
    powers = []
    for c in CONTRAST_GRID:
        p = _rate(baseline, [sz.deviated(20_000_000 + s, contrast=c)
                             for s in range(TRIALS)])
        powers.append(p)
        bar = "█" * int(p * 24)
        mark = GREEN if p >= POWER_TARGET else DIM
        print(f"  {c:>6.0%} of genuine{'':<12}{mark}{p:>8.0%}{RESET} {DIM}{bar}{RESET}")
    curves["contrast"] = (list(CONTRAST_GRID), powers)

    print(f"\n  {DIM}{'zone pattern scrambled':<28}{'flagged':>9}{RESET}")
    powers = []
    for f in SHUFFLE_GRID:
        p = _rate(baseline, [sz.deviated(30_000_000 + s, shuffle=f)
                             for s in range(TRIALS)])
        powers.append(p)
        bar = "█" * int(p * 24)
        mark = GREEN if p >= POWER_TARGET else DIM
        print(f"  {f:>6.0%} scrambled{'':<13}{mark}{p:>8.0%}{RESET} {DIM}{bar}{RESET}")
    curves["shuffle"] = (list(SHUFFLE_GRID), powers)
    return curves


def _verdict(curves: dict) -> None:
    xs, ps = curves["contrast"]
    contrast_limit = _limit(xs, ps)
    xs2, ps2 = curves["shuffle"]
    shuffle_limit = _limit(xs2, ps2)

    print(f"\n{BOLD}What this means{RESET}\n")
    if contrast_limit is not None:
        print(f"  A face must lose {BOLD}more than "
              f"{1 - contrast_limit:.0%}{RESET} of its cross-zone texture "
              f"structure\n  before check 2 catches it "
              f"{POWER_TARGET:.0%} of the time.")
    else:
        print(f"  {RED}Smoothing is never detected at {POWER_TARGET:.0%} power "
              f"anywhere on the grid.{RESET}")

    if shuffle_limit is not None:
        print(f"  Zone patterns must be {BOLD}{shuffle_limit:.0%} "
              f"scrambled{RESET} before it catches them "
              f"{POWER_TARGET:.0%} of the time.")
    else:
        best = max(ps2)
        print(f"  {YELLOW}Anatomically implausible patterns never reach "
              f"{POWER_TARGET:.0%} power{RESET} — even a\n  completely "
              f"scrambled profile is caught only {best:.0%} of the time. "
              f"Nine zones,\n  four of them bilateral pairs, is not much to "
              f"test a distribution's shape with.")

    print(f"\n  {YELLOW}Whether real generated faces exceed these limits is "
          f"unknown.{RESET}{DIM} No synthetic\n  face and no Perfect Corp "
          f"ai_face_swap output has been scored — that needs\n  credentials "
          f"which have not arrived. This is the sensitivity of the "
          f"instrument,\n  not a hit rate against real forgeries.{RESET}")
    print(f"\n  {DIM}Checks 1 and 3 do not depend on this one. If Step 12 shows "
          f"real generators sit\n  below these limits, check 2 is reported as a "
          f"measured negative and the system\n  stands on presence and "
          f"binding.{RESET}")


def _plot(curves: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"\n  {DIM}matplotlib not installed — skipping the plot{RESET}")
        return

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, (key, label, xlabel) in zip(axes, [
            ("contrast", "uniform smoothing",
             "cross-zone texture structure retained"),
            ("shuffle", "implausible zone pattern",
             "fraction of zone pattern scrambled")]):
        xs, ps = curves[key]
        ax.plot(xs, ps, "o-", color="#1f77b4", linewidth=2, markersize=5)
        ax.axhline(POWER_TARGET, color="#d62728", linestyle="--", linewidth=1,
                   label=f"{POWER_TARGET:.0%} power")
        ax.axhline(TARGET_FLAG_RATE, color="#7f7f7f", linestyle=":",
                   linewidth=1, label=f"{TARGET_FLAG_RATE:.0%} flag rate")
        ax.set_title(label, fontsize=11)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_ylabel("detected", fontsize=9)
        ax.set_ylim(-0.03, 1.03)
        ax.legend(fontsize=8, loc="best")
        ax.grid(alpha=0.25)
    if curves["contrast"][0][0] > curves["contrast"][0][-1]:
        axes[0].invert_xaxis()

    fig.suptitle("STRATUM check 2 — detection limits, not hit rates "
                 "(simulated deviations; no generated face has been scored)",
                 fontsize=10)
    fig.tight_layout()
    PLOT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOT, dpi=130)
    plt.close(fig)
    print(f"\n  {DIM}plot written to {PLOT.relative_to(PLOT.parent.parent)}{RESET}")


def main() -> int:
    t0 = time.time()
    print(f"\n{DIM}{'─' * 74}{RESET}")
    print(f"{BOLD}STRATUM check 2 — authenticity{RESET}   "
          f"{DIM}sensitivity analysis, no credentials, no generated faces{RESET}")
    print(f"{DIM}{'─' * 74}{RESET}")

    baseline = fit(sz.population(COHORT, seed=1))
    print(f"\n  {DIM}baseline fitted on {baseline.n_fit} genuine faces, "
          f"calibrated on {baseline.n_null} others{RESET}")
    print(f"  {DIM}dimensions: {', '.join(baseline.dimensions)}   "
          f"cuts: "
          f"{', '.join(f'{t} {baseline.cut(t):.2f}' for t in TESTS)}{RESET}")

    observed = _calibration(baseline)
    curves = _power(baseline)
    _verdict(curves)
    _plot(curves)

    calibrated = observed <= TARGET_FLAG_RATE * 1.4
    verdict = f"{GREEN}CALIBRATED{RESET}" if calibrated else f"{RED}MISCALIBRATED{RESET}"
    print(f"\n  Step 6 gate: {verdict}   "
          f"{DIM}(the check holds its stated flag rate; "
          f"{time.time() - t0:.0f}s){RESET}")
    print(f"  {YELLOW}Not yet answered:{RESET}{DIM} does a real generated face "
          f"exceed the detection limit?\n  That is Step 12, and it needs "
          f"Perfect Corp credentials.{RESET}\n")
    return 0 if calibrated else 1


if __name__ == "__main__":
    raise SystemExit(main())
