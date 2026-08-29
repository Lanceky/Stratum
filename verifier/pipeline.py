"""
End-to-end capture pipeline (implementation.md Step 2, definition of done).

    face image  →  upload  →  skin analysis  →  face attributes
                →  masks downloaded and decoded  →  constellation on disk

Every step goes through the fixture layer, so once this has run for real it
replays forever at zero cost:

    # record (spends units — deliberate opt-in)
    STRATUM_API_MODE=auto python -m pipeline path/to/face.jpg

    # replay (free, offline, the dev default)
    python -m pipeline path/to/face.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import masks as masklib
import perfectcorp as pc
from dimensions import is_stable, is_volatile
from fixtures import budget_status

REPO_ROOT = Path(__file__).resolve().parent.parent
MASK_CACHE = REPO_ROOT / "fixtures" / "masks"
OUT_DIR = REPO_ROOT / "fixtures" / "derived"


def run(image_path: Path, hd: bool = True, plot: bool = False) -> dict:
    mode = os.getenv("STRATUM_API_MODE", "replay")
    data = image_path.read_bytes()
    print(f"[pipeline] {image_path.name}  ({len(data):,} bytes)  mode={mode}")

    upload = pc.request_upload(image_path.name, len(data))
    file_id = pc.upload_bytes(upload, data)
    print(f"[pipeline] file_id={file_id}")

    skin = pc.skin_analysis(file_id, hd=hd)
    scores = skin.scores
    print(f"[pipeline] {len(scores)} skin scores")

    attrs = pc.face_attributes(file_id)

    mask_dir = MASK_CACHE / image_path.stem
    mask_paths = pc.download_masks(skin, mask_dir)
    print(f"[pipeline] {len(mask_paths)} masks on disk")

    # The constellation is the part that matters: this point set drives the
    # geometric consistency test in check 1 and Procrustes matching in check 3.
    constellations = {}
    for name, path in mask_paths.items():
        pts = masklib.centroids(masklib.decode_file(path))[:64]
        constellations[name] = pts.tolist()
        print(f"[pipeline]   {name:16s} {len(pts):3d} centroids")

    bundle = {
        "source": image_path.name,
        "file_id": file_id,
        "hd": hd,
        "scores": scores,
        "stable_scores": {k: v for k, v in scores.items() if is_stable(k)},
        "volatile_scores": {k: v for k, v in scores.items() if is_volatile(k)},
        "face_attributes": attrs.get("data", {}).get("results", {}),
        "constellations": constellations,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{image_path.stem}.json"
    out.write_text(json.dumps(bundle, indent=2))
    print(f"[pipeline] wrote {out.relative_to(REPO_ROOT)}")
    print(f"[pipeline] units: {budget_status()}")

    if plot:
        _plot(constellations, image_path.stem)

    return bundle


def _plot(constellations: dict, stem: str) -> None:
    """First look at real constellation data — Step 2's last DoD item."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    named = {k: np.array(v) for k, v in constellations.items() if len(v)}
    if not named:
        print("[pipeline] nothing to plot")
        return

    fig, axes = plt.subplots(1, len(named), figsize=(4 * len(named), 4), squeeze=False)
    for ax, (name, pts) in zip(axes[0], named.items()):
        ax.scatter(pts[:, 0], -pts[:, 1], s=pts[:, 2] / pts[:, 2].max() * 60,
                   c="#4f7cff", alpha=0.75)
        ax.set_title(f"{name} ({len(pts)})")
        ax.set_aspect("equal")
        ax.axis("off")

    fig.suptitle(f"Spot constellations — {stem}")
    fig.tight_layout()
    dest = REPO_ROOT / "benchmark" / f"constellation_{stem}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=130)
    print(f"[pipeline] plot → {dest.relative_to(REPO_ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="STRATUM capture pipeline")
    ap.add_argument("image", type=Path)
    ap.add_argument("--sd", action="store_true", help="SD instead of HD (cheaper)")
    ap.add_argument("--plot", action="store_true", help="save a constellation scatter")
    args = ap.parse_args()

    if not args.image.exists():
        sys.exit(f"not found: {args.image}")

    run(args.image, hd=not args.sd, plot=args.plot)


if __name__ == "__main__":
    main()
