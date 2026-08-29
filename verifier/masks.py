"""
Perfect Corp mask decoding and constellation extraction (implementation.md Step 2d).

⚠️ The masks are PNGs with intensity in the ALPHA channel, not RGB. This is not
documented in the obvious place. Reading RGB gives you a black image and an hour
of confusion.

The point set produced here is load-bearing for two of the three checks:
  - check 1 (presence): the constellation must transform consistently under a
    requested micro-pose — real 3D structure behaves differently from a flat
    print or a screen.
  - check 3 (binding): Procrustes registration of two constellations is far more
    person-specific than any scalar skin score.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_THRESHOLD = 32
MIN_CLUSTER_PX = 4


def decode(png_bytes: bytes, threshold: int = DEFAULT_THRESHOLD) -> np.ndarray:
    """
    PNG mask → (N, 3) float array of [x, y, intensity].

    Intensity comes from the alpha channel. Pixels at or below `threshold` are
    dropped as background.
    """
    img = np.array(Image.open(BytesIO(png_bytes)).convert("RGBA"))
    alpha = img[:, :, 3]
    ys, xs = np.nonzero(alpha > threshold)
    if len(xs) == 0:
        return np.empty((0, 3), dtype=np.float64)
    return np.stack([xs, ys, alpha[ys, xs]], axis=1).astype(np.float64)


def decode_file(path: Path, threshold: int = DEFAULT_THRESHOLD) -> np.ndarray:
    return decode(Path(path).read_bytes(), threshold)


def centroids(points: np.ndarray, min_cluster: int = MIN_CLUSTER_PX,
              eps: float = 3.0) -> np.ndarray:
    """
    Cluster mask pixels into discrete spot/pore centroids.

    Returns (M, 3): [x, y, total_intensity] per cluster, sorted by intensity
    descending so the strongest features come first and truncation to top-k is
    meaningful.

    DBSCAN is the right tool here: the number of spots varies per face, so
    k-means would require knowing k in advance.
    """
    if len(points) == 0:
        return np.empty((0, 3), dtype=np.float64)

    from sklearn.cluster import DBSCAN

    xy = points[:, :2]
    labels = DBSCAN(eps=eps, min_samples=min_cluster).fit_predict(xy)

    out = []
    for label in set(labels):
        if label == -1:  # DBSCAN noise
            continue
        member = points[labels == label]
        weight = member[:, 2]
        total = float(weight.sum())
        cx = float((member[:, 0] * weight).sum() / total)
        cy = float((member[:, 1] * weight).sum() / total)
        out.append([cx, cy, total])

    if not out:
        return np.empty((0, 3), dtype=np.float64)

    arr = np.array(out, dtype=np.float64)
    return arr[np.argsort(-arr[:, 2])]


def constellation(png_bytes: bytes, top_k: int = 64) -> np.ndarray:
    """
    Mask PNG → the top-k spot centroids that form this face's constellation.

    top_k caps the point set so Procrustes registration in check 3 stays fast
    and is not dominated by faint noise.
    """
    return centroids(decode(png_bytes))[:top_k]


def summarise(points: np.ndarray) -> dict:
    """Cheap descriptive stats — used for logging and sanity checks."""
    if len(points) == 0:
        return {"count": 0}
    return {
        "count": int(len(points)),
        "centroid": [float(points[:, 0].mean()), float(points[:, 1].mean())],
        "spread": [float(points[:, 0].std()), float(points[:, 1].std())],
        "intensity_mean": float(points[:, 2].mean()),
        "intensity_std": float(points[:, 2].std()),
    }
