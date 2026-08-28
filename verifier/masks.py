"""
Perfect Corp mask decoding.

The masks are PNGs with intensity in the ALPHA channel, not RGB. This is
undocumented in the obvious place and costs an hour if discovered late.

Implemented for real in Step 2d.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

DEFAULT_THRESHOLD = 32


def decode(png_bytes: bytes, threshold: int = DEFAULT_THRESHOLD) -> np.ndarray:
    """PNG mask → (N, 3) array of [x, y, intensity]."""
    img = np.array(Image.open(BytesIO(png_bytes)).convert("RGBA"))
    alpha = img[:, :, 3]
    ys, xs = np.nonzero(alpha > threshold)
    return np.stack([xs, ys, alpha[ys, xs]], axis=1).astype(np.float64)


def centroids(points: np.ndarray, min_cluster: int = 4) -> np.ndarray:
    """
    Cluster mask points into discrete spot/pore centroids.

    This point set IS the constellation used by check 1 (geometric consistency
    under pose) and check 3 (Procrustes registration). Implemented in Step 2d.
    """
    raise NotImplementedError("Step 2d")
