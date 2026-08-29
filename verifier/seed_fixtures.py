"""
Seed synthetic fixtures matching the VERIFIED Perfect Corp response shape.

Purpose: make the whole pipeline runnable and testable before any credential
arrives, and keep tests honest without spending units. Every fixture carries
`"_synthetic": true` so it can never be mistaken for recorded ground truth.

    python seed_fixtures.py

Once real credentials land:
    STRATUM_API_MODE=auto python pipeline.py face.jpg
records genuine responses. Delete the synthetic ones at that point.
"""

from __future__ import annotations

import json
import shutil
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import SYNTHETIC_DIR, fixture_key  # noqa: E402
from perfectcorp import HD_FORENSIC_SET, SD_DST_ACTIONS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
MASK_DIR = SYNTHETIC_DIR / "masks" / "synthetic_face"
FACE_JPEG = SYNTHETIC_DIR / "synthetic_face.jpg"

IMG_W, IMG_H = 1080, 1440
FILE_ID = "synthetic-file-0001"

# Concerns that return mask images, and roughly how many features each has.
MASKED = {"hd_pore": (180, 0.13), "hd_texture": (140, 0.15),
          "hd_age_spot": (45, 0.16), "hd_wrinkle": (30, 0.17)}


def _mask_png(n_spots: int, spread: float, seed: int) -> bytes:
    """
    Build a mask PNG with intensity in the ALPHA channel — the layout Perfect
    Corp uses. Blobs are rejection-sampled into an ellipse so they land on a
    plausible face region.
    """
    rng = np.random.default_rng(seed)
    alpha = np.zeros((IMG_H, IMG_W), dtype=np.float64)
    cx, cy = IMG_W / 2, IMG_H / 2

    for _ in range(n_spots):
        for _attempt in range(50):
            x = rng.normal(cx, IMG_W * spread)
            y = rng.normal(cy, IMG_H * spread)
            if ((x - cx) / (IMG_W * 0.34)) ** 2 + ((y - cy) / (IMG_H * 0.40)) ** 2 <= 1:
                break
        else:
            continue

        radius = rng.uniform(3, 9)
        peak = rng.uniform(90, 255)
        x0, x1 = int(max(0, x - radius * 3)), int(min(IMG_W, x + radius * 3))
        y0, y1 = int(max(0, y - radius * 3)), int(min(IMG_H, y + radius * 3))
        if x1 <= x0 or y1 <= y0:
            continue

        yy, xx = np.mgrid[y0:y1, x0:x1]
        blob = peak * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * radius**2)))
        alpha[y0:y1, x0:x1] = np.maximum(alpha[y0:y1, x0:x1], blob)

    rgba = np.zeros((IMG_H, IMG_W, 4), dtype=np.uint8)
    rgba[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)

    buf = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _face_jpeg() -> bytes:
    """
    A crude synthetic face. Not for accuracy — it exists so the pipeline has a
    real image with a real byte count to run against before a camera or a
    credential is available.
    """
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[0:IMG_H, 0:IMG_W]
    cx, cy = IMG_W / 2, IMG_H / 2

    oval = (((xx - cx) / (IMG_W * 0.34)) ** 2 + ((yy - cy) / (IMG_H * 0.40)) ** 2) <= 1
    img = np.zeros((IMG_H, IMG_W, 3), dtype=np.float64)
    img[:] = (28, 30, 36)
    for c, v in enumerate((214, 176, 152)):
        img[:, :, c] = np.where(oval, v, img[:, :, c])

    # Shading plus fine grain, so JPEG compression has something to chew on
    # and the file lands at a realistic size rather than a few KB of flat colour.
    shade = 1 - 0.28 * (((xx - cx) / (IMG_W * 0.5)) ** 2 + ((yy - cy) / (IMG_H * 0.5)) ** 2)
    img *= shade[:, :, None]
    img += rng.normal(0, 7, img.shape)

    for ex in (cx - IMG_W * 0.13, cx + IMG_W * 0.13):
        eye = ((xx - ex) / (IMG_W * 0.055)) ** 2 + ((yy - (cy - IMG_H * 0.10)) / (IMG_H * 0.022)) ** 2 <= 1
        img[eye] = (48, 44, 52)
    mouth = ((xx - cx) / (IMG_W * 0.11)) ** 2 + ((yy - (cy + IMG_H * 0.20)) / (IMG_H * 0.024)) ** 2 <= 1
    img[mouth] = (150, 84, 84)

    buf = BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _write_masks() -> dict[str, str]:
    MASK_DIR.mkdir(parents=True, exist_ok=True)
    urls = {}
    for i, (name, (n, spread)) in enumerate(MASKED.items()):
        path = MASK_DIR / f"{name}.png"
        path.write_bytes(_mask_png(n, spread, seed=1000 + i))
        urls[name] = f"file://{path}"
    return urls


def _skin_response(actions: list[str], mask_urls: dict[str, str], seed: int) -> dict:
    """Mirrors data.results.output[] with type/region/raw_score/ui_score/mask_urls."""
    rng = np.random.default_rng(seed)
    output = []

    for action in actions:
        # hd_pore reports per-region; the per-zone breakdown is what check 2 uses.
        regions = (["forehead", "nose", "cheek", "whole"]
                   if action == "hd_pore" else ["whole"])
        for region in regions:
            raw = float(rng.uniform(35, 88))
            entry = {
                "type": action,
                "region": region,
                "raw_score": round(raw, 2),
                "ui_score": int(round(raw)),
                "score": round(raw, 2),
            }
            if action in mask_urls and region == "whole":
                entry["mask_urls"] = [mask_urls[action]]
            output.append(entry)

    return {
        "_synthetic": True,
        "status": 200,
        "data": {"task_id": f"synthetic-{seed}", "task_status": "success",
                 "results": {"output": output}},
    }


def _face_attr_response() -> dict:
    rng = np.random.default_rng(77)
    ratios = {f"ratio_{i:02d}": round(float(rng.uniform(0.4, 1.9)), 4) for i in range(1, 12)}
    return {
        "_synthetic": True,
        "status": 200,
        "data": {
            "task_id": "synthetic-attr",
            "task_status": "success",
            "results": {
                "face_ratio": ratios,
                "face_attribute": {"age": 31, "gender": "unspecified",
                                   "face_shape": "oval", "eye_distance": 0.31},
            },
        },
    }


def main() -> None:
    # Wipe first. A stale fixture from an earlier seed is worse than none:
    # it is keyed on an input that no longer exists, so it never replays but
    # sits there looking authoritative.
    if SYNTHETIC_DIR.exists():
        shutil.rmtree(SYNTHETIC_DIR)
    SYNTHETIC_DIR.mkdir(parents=True)

    # Written before the upload fixture, because that fixture is keyed on the
    # image's real byte count.
    FACE_JPEG.write_bytes(_face_jpeg())
    print(f"[seed] face → {FACE_JPEG.relative_to(REPO_ROOT)} "
          f"({FACE_JPEG.stat().st_size:,} bytes)")

    mask_urls = _write_masks()
    print(f"[seed] {len(mask_urls)} masks → {MASK_DIR.relative_to(REPO_ROOT)}")

    written = 0

    def put(op: str, payload: dict, body: dict) -> None:
        nonlocal written
        (SYNTHETIC_DIR / f"{fixture_key(op, payload)}.json").write_text(json.dumps(body, indent=2))
        written += 1

    name, size = FACE_JPEG.name, FACE_JPEG.stat().st_size
    put("file-upload",
        {"files": [{"content_type": "image/jpeg", "file_name": name, "file_size": size}]},
        {"_synthetic": True, "status": 200,
         "data": {"files": [{
             "content_type": "image/jpeg", "file_name": name, "file_id": FILE_ID,
             "requests": [{"method": "PUT", "url": "https://example.invalid/upload",
                           "headers": {"Content-Type": "image/jpeg",
                                       "Content-Length": str(size)}}],
         }]}})

    for hd, actions, seed in ((True, HD_FORENSIC_SET, 11), (False, SD_DST_ACTIONS, 22)):
        put("skin-analysis-hd" if hd else "skin-analysis-sd",
            {"src_file_id": FILE_ID, "dst_actions": actions, "format": "json"},
            _skin_response(actions, mask_urls if hd else {}, seed))

    put("face-attr-analysis",
        {"src_file_id": FILE_ID, "dst_actions": ["face_attribute", "face_ratio"],
         "format": "json"},
        _face_attr_response())

    print(f"[seed] {written} fixtures → {SYNTHETIC_DIR.relative_to(REPO_ROOT)}")
    print('[seed] all carry "_synthetic": true — delete once real ones are recorded')


if __name__ == "__main__":
    main()
