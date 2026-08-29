"""
Print the measured separation between same-person and different-person captures.

This is the Step 4 definition of done as a report rather than an assertion. The
test says pass or fail; this says by how much, which is what Step 12 needs in
order to set thresholds against the genuine cohort, and what a reader needs in
order to judge whether the claim is worth anything.

Run with `make separation`. Today it reads the synthetic cohort, and says so
loudly — synthetic separation is evidence that the maths works, not evidence
about real skin.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth_cohort as sc  # noqa: E402
from normalise import (  # noqa: E402
    PopulationStats, constellation_distance, normalise_bundle, register,
    vector_distance,
)

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def _row(name: str, same: np.ndarray, diff: np.ndarray) -> bool:
    """One channel's distributions, and whether they are separable at all."""
    clean = same.max() < diff.min()
    gap = diff.min() - same.max()
    mark = f"{GREEN}separated{RESET}" if clean else f"{RED}OVERLAP{RESET}"
    print(f"  {BOLD}{name:<22}{RESET} "
          f"same {same.mean():.3f} ±{same.std():.3f} (max {same.max():.3f})   "
          f"diff {diff.mean():.3f} ±{diff.std():.3f} (min {diff.min():.3f})   "
          f"gap {gap:+.3f}  {mark}")
    return clean


def main(n_people: int = 12, n_captures: int = 3) -> int:
    print(f"\n{BOLD}STRATUM — capture separation report{RESET}")
    print(f"{YELLOW}source: synthetic cohort (synth_cohort.py). Not real skin.{RESET}")
    print(f"{DIM}{n_people} identities x {n_captures} captures at "
          f"{len(sc.POSES)} camera poses{RESET}\n")

    t0 = time.time()
    people = sc.cohort(n_people=n_people, n_captures=n_captures)
    stats = PopulationStats.fit([c["scores"] for p in people for c in p])
    norm = [[normalise_bundle(c, stats) for c in caps] for caps in people]

    def pts(c):
        return np.array(c["constellations"]["hd_pore"]["points"], float)

    channels: dict[str, tuple[list, list]] = {
        "identity (stable)": ([], []),
        "ratios": ([], []),
        "volatile (control)": ([], []),
        "constellation": ([], []),
    }
    inliers = ([], [])

    for i, caps in enumerate(norm):
        for a in range(len(caps)):
            for b in range(a + 1, len(caps)):
                channels["identity (stable)"][0].append(
                    vector_distance(caps[a]["identity_vector"],
                                    caps[b]["identity_vector"]))
                channels["ratios"][0].append(
                    vector_distance(caps[a]["ratios"], caps[b]["ratios"]))
                channels["volatile (control)"][0].append(
                    vector_distance(caps[a]["volatile_vector"],
                                    caps[b]["volatile_vector"]))
                channels["constellation"][0].append(
                    constellation_distance(pts(caps[a]), pts(caps[b])))
                inliers[0].append(register(pts(caps[a]), pts(caps[b])).inliers)
        for j in range(i + 1, len(norm)):
            other = norm[j][0]
            channels["identity (stable)"][1].append(
                vector_distance(caps[0]["identity_vector"],
                                other["identity_vector"]))
            channels["ratios"][1].append(
                vector_distance(caps[0]["ratios"], other["ratios"]))
            channels["volatile (control)"][1].append(
                vector_distance(caps[0]["volatile_vector"],
                                other["volatile_vector"]))
            channels["constellation"][1].append(
                constellation_distance(pts(caps[0]), pts(other)))
            inliers[1].append(register(pts(caps[0]), pts(other)).inliers)

    ok = {}
    for name, (s, d) in channels.items():
        ok[name] = _row(name, np.array(s, float), np.array(d, float))

    si, di = np.array(inliers[0], float), np.array(inliers[1], float)
    print(f"\n  {DIM}matched spots: same {si.mean():.0f} vs different "
          f"{di.mean():.0f} — a {si.mean() / max(di.mean(), 1):.1f}x margin over "
          f"the chance alignment a 4-parameter fit always finds{RESET}")

    print(f"\n  {DIM}volatile is the control: it is expected NOT to separate. "
          f"If it does, the stable/volatile split is wrong.{RESET}")
    if ok["volatile (control)"]:
        print(f"  {RED}volatile separated — investigate before trusting "
              f"anything above.{RESET}")

    identity_ok = ok["identity (stable)"] and ok["constellation"]
    verdict = (f"{GREEN}PASS{RESET}" if identity_ok else f"{RED}FAIL{RESET}")
    print(f"\n  Step 4 gate: {verdict}   {DIM}({time.time() - t0:.0f}s){RESET}\n")
    return 0 if identity_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
