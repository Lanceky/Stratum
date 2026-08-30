"""
Check 3's operating point, measured — and the overlap it cannot remove.

Two cohorts, kept apart. Thresholds are chosen on the fit cohort and every
number reported comes from the held-out one, because a threshold quoted on the
data that produced it is a description of that data rather than a prediction.

Read the output as a statement about the *matcher*, not about faces. The
cohort is synthetic and its identities are built with distinct spot patterns by
construction, so separation here is the code working as written. Whether real
skin separates is Step 12's question and nothing before it can answer it.

    make binding
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks.binding import LOWER_THRESHOLD, UPPER_THRESHOLD, evaluate
from normalise import normalise_bundle
from synth_cohort import (POSES, Identity, capture, changed_appearance,
                          degraded, sibling)

FIT_SEED, HELD_OUT_SEED, N = 9_000, 4_000, 24

# Honest populations are the ones a wrong FAIL harms; attacks are the ones a
# wrong PASS harms. Siblings sit under "attack" only because they are not the
# enrolled person — the report goes on to show they are not separable.
HONEST = ("genuine", "degraded", "changed")
ATTACK = ("sibling", "impostor")


def measure(seed0: int, n: int) -> dict[str, np.ndarray]:
    people = [Identity(seed=seed0 + i) for i in range(n)]
    out: dict[str, list[float]] = {k: [] for k in HONEST + ATTACK}

    for i, person in enumerate(people):
        pose = (i % 2) + 1
        enrol = normalise_bundle(capture(person, i, **POSES[0]))

        def d(probe):
            return evaluate(enrol, normalise_bundle(probe)).distance

        out["genuine"].append(d(capture(person, i + 500, **POSES[pose])))
        out["degraded"].append(d(degraded(person, i + 3, **POSES[pose])))
        out["changed"].append(d(changed_appearance(person, i + 5, **POSES[pose])))
        out["sibling"].append(d(sibling(person, i + 11, **POSES[pose])))
        out["impostor"].append(
            d(capture(people[(i + 1) % n], i + 900, **POSES[pose])))

    return {k: np.array(v) for k, v in out.items()}


def verdicts(d: np.ndarray) -> dict[str, int]:
    return {"PASS": int((d < LOWER_THRESHOLD).sum()),
            "REVIEW": int(((d >= LOWER_THRESHOLD) & (d <= UPPER_THRESHOLD)).sum()),
            "FAIL": int((d > UPPER_THRESHOLD).sum())}


def roc(honest: np.ndarray, attack: np.ndarray) -> list[tuple]:
    """
    One threshold, swept. Not the operating curve — the operating point uses
    two thresholds and a REVIEW band, which a single-threshold ROC cannot
    express. This is here to show the shape of the trade-off, and to show where
    it breaks down.
    """
    rows = []
    for t in np.arange(1.0, 16.0, 1.0):
        tpr = float((attack > t).mean())          # attacks correctly rejected
        fpr = float((honest > t).mean())          # honest people wrongly rejected
        rows.append((float(t), tpr, fpr))
    return rows


def main() -> int:
    fit, held = measure(FIT_SEED, N), measure(HELD_OUT_SEED, N)

    print(f"\ncheck 3 — identity binding   fit n={N}, held-out n={N}")
    print(f"thresholds: PASS < {LOWER_THRESHOLD}  |  REVIEW  |  "
          f"> {UPPER_THRESHOLD} FAIL\n")

    print(f"{'population':<10} {'mean':>7} {'sd':>6} {'min':>7} {'max':>7}   "
          f"{'PASS':>5} {'REVIEW':>6} {'FAIL':>5}")
    for name in HONEST + ATTACK:
        d, v = held[name], verdicts(held[name])
        print(f"{name:<10} {d.mean():7.2f} {d.std():6.2f} {d.min():7.2f} "
              f"{d.max():7.2f}   {v['PASS']:5} {v['REVIEW']:6} {v['FAIL']:5}")

    honest = np.concatenate([held[k] for k in HONEST])
    attack = np.concatenate([held[k] for k in ATTACK])

    # ── the two claims the thresholds are there to make ───────────────────
    auto_rejected = int((honest > UPPER_THRESHOLD).sum())
    auto_passed = int((attack < LOWER_THRESHOLD).sum())
    print(f"\nhonest captures auto-rejected : {auto_rejected} / {len(honest)}")
    print(f"attacks auto-passed           : {auto_passed} / {len(attack)}")

    # ── the overlap, stated rather than smoothed over ─────────────────────
    gap = float(held["sibling"].min() - honest.max())
    print(f"\nworst honest capture          : {honest.max():.2f}")
    print(f"nearest sibling               : {held['sibling'].min():.2f}")
    print(f"margin between them           : {gap:+.2f}", end="  ")
    print("(OVERLAP — no threshold separates these two populations)"
          if gap < 0 else "(separable)")
    print(f"nearest impostor              : {held['impostor'].min():.2f}")
    print(f"headroom above FAIL line      : "
          f"{held['impostor'].min() - UPPER_THRESHOLD:+.2f}")

    review = int(((honest >= LOWER_THRESHOLD) & (honest <= UPPER_THRESHOLD)).sum())
    print(f"\nhonest captures sent to a human: {review} / {len(honest)} "
          f"({100 * review / len(honest):.0f}%) — the price of not guessing")

    print("\nsingle-threshold sweep (what a one-number system would have to pick)")
    print(f"{'threshold':>9} {'attacks caught':>15} {'honest rejected':>16}")
    for t, tpr, fpr in roc(honest, attack):
        mark = ""
        if fpr == 0.0 and tpr == 1.0:
            mark = "  <- would be clean"
        print(f"{t:9.1f} {100 * tpr:14.0f}% {100 * fpr:15.0f}%{mark}")

    clean = [t for t, tpr, fpr in roc(honest, attack) if fpr == 0.0 and tpr == 1.0]
    print("\n" + ("no single threshold is clean — which is why there are two "
                  "and a human in the middle"
                  if not clean else
                  f"a single threshold would work at {clean[0]:.1f}; the REVIEW "
                  f"band is then unnecessary and should be removed"))

    plot(held)
    print("\nplot: benchmark/binding_separation.png")
    print("\nSynthetic cohort. These figures describe the matcher, not real "
          "faces.\nStep 12's genuine set is what decides whether real skin "
          "separates.\n")
    return 0


def plot(held: dict[str, np.ndarray]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    colours = {"genuine": "#2a9d8f", "degraded": "#e9c46a", "changed": "#8ab17d",
               "sibling": "#f4a261", "impostor": "#e76f51"}

    for i, name in enumerate(HONEST + ATTACK):
        d = held[name]
        ax.scatter(d, np.full_like(d, i) + np.random.uniform(-.12, .12, len(d)),
                   s=18, alpha=.75, color=colours[name], label=name)

    ax.axvline(LOWER_THRESHOLD, color="#264653", ls="--", lw=1)
    ax.axvline(UPPER_THRESHOLD, color="#264653", ls="--", lw=1)
    ax.axvspan(LOWER_THRESHOLD, UPPER_THRESHOLD, color="#264653", alpha=.06)
    ax.text((LOWER_THRESHOLD + UPPER_THRESHOLD) / 2, len(HONEST + ATTACK) - .35,
            "REVIEW\na human decides", ha="center", va="top", fontsize=8,
            color="#264653")

    ax.set_yticks(range(len(HONEST + ATTACK)))
    ax.set_yticklabels(HONEST + ATTACK)
    ax.set_xlabel("distance from a same-person match (genuine-pair spreads)")
    ax.set_title("Check 3: identity binding on held-out synthetic identities\n"
                 "honest captures and siblings overlap — that band goes to a human",
                 fontsize=10)
    ax.grid(axis="x", alpha=.25)
    fig.tight_layout()

    out = Path(__file__).resolve().parent.parent / "benchmark"
    out.mkdir(exist_ok=True)
    fig.savefig(out / "binding_separation.png", dpi=140)


if __name__ == "__main__":
    raise SystemExit(main())
