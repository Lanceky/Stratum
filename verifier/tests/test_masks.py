"""
Mask decoding tests (implementation.md Step 2d).

The alpha-channel detail is the one that bites: a mask read from RGB is all
zeros. test_rgb_channels_are_empty locks that in so a future refactor cannot
quietly switch channels and still appear to work.
"""

import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import masks as masklib  # noqa: E402


def make_mask(spots, size=(200, 200), radius=6, peak=220) -> bytes:
    """Build an RGBA PNG with intensity in ALPHA, as Perfect Corp does."""
    w, h = size
    alpha = np.zeros((h, w), dtype=np.float64)
    for (cx, cy) in spots:
        yy, xx = np.mgrid[0:h, 0:w]
        blob = peak * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * radius**2)))
        alpha = np.maximum(alpha, blob)

    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)

    buf = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def test_decode_reads_alpha_not_rgb():
    png = make_mask([(50, 50)])
    pts = masklib.decode(png)
    assert len(pts) > 0, "alpha channel produced no points — wrong channel?"
    assert pts.shape[1] == 3


def test_rgb_channels_are_empty():
    """Guard: the RGB planes are blank. Reading them yields nothing."""
    png = make_mask([(50, 50)])
    img = np.array(Image.open(BytesIO(png)).convert("RGBA"))
    assert img[:, :, :3].max() == 0
    assert img[:, :, 3].max() > 0


def test_empty_mask_returns_empty_array():
    png = make_mask([], peak=0)
    pts = masklib.decode(png)
    assert pts.shape == (0, 3)
    assert masklib.centroids(pts).shape == (0, 3)


def test_centroids_recover_spot_count():
    spots = [(30, 30), (100, 40), (60, 120), (150, 150)]
    pts = masklib.decode(make_mask(spots))
    cents = masklib.centroids(pts)
    assert len(cents) == len(spots), f"expected {len(spots)} clusters, got {len(cents)}"


def test_centroids_land_near_true_positions():
    spots = [(40, 40), (140, 60), (90, 150)]
    cents = masklib.centroids(masklib.decode(make_mask(spots)))
    for cx, cy in spots:
        d = np.min(np.hypot(cents[:, 0] - cx, cents[:, 1] - cy))
        assert d < 4.0, f"centroid for ({cx},{cy}) off by {d:.1f}px"


def test_centroids_sorted_by_intensity_descending():
    pts = masklib.decode(make_mask([(30, 30), (100, 100), (160, 60)]))
    cents = masklib.centroids(pts)
    assert np.all(np.diff(cents[:, 2]) <= 0)


def test_constellation_respects_top_k():
    spots = [(20 + 25 * (i % 7), 20 + 25 * (i // 7)) for i in range(30)]
    c = masklib.constellation(make_mask(spots, size=(240, 240), radius=4), top_k=10)
    assert len(c) == 10


def test_threshold_filters_faint_pixels():
    png = make_mask([(60, 60)], peak=40)
    assert len(masklib.decode(png, threshold=10)) > len(masklib.decode(png, threshold=35))


def test_summarise_shape():
    s = masklib.summarise(masklib.decode(make_mask([(50, 50), (120, 120)])))
    assert s["count"] > 0
    assert len(s["centroid"]) == 2
