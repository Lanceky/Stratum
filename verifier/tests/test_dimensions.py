"""
Tests for the STABLE/VOLATILE split (context.md §1).

This is the fix for the fatal flaw in the original concept, so it gets tests
of its own. The key normalisation cases matter: HD scores come back as
`hd_pore` and `hd_pore:forehead`, and a naive membership check against
STABLE matches neither.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dimensions import (  # noqa: E402
    ALL_SD_CONCERNS, ILLUMINATION_RESPONSIVE, STABLE, VOLATILE,
    base, is_stable, is_volatile,
)


def test_stable_and_volatile_are_disjoint():
    assert not set(STABLE) & set(VOLATILE)


def test_illumination_responsive_is_all_volatile():
    """Check 1 flashes colour and asserts a response; only volatile channels move."""
    assert set(ILLUMINATION_RESPONSIVE) <= set(VOLATILE)


def test_moisture_is_never_an_identity_dimension():
    """The single most important assertion in the repo."""
    for d in ("moisture", "redness", "oiliness", "radiance"):
        assert d not in STABLE
        assert is_volatile(d)


def test_base_strips_hd_prefix():
    assert base("hd_pore") == "pore"


def test_base_strips_region_suffix():
    assert base("hd_pore:forehead") == "pore"
    assert base("pore:nose") == "pore"


def test_base_leaves_sd_keys_alone():
    assert base("pore") == "pore"


def test_hd_keys_classify_correctly():
    assert is_stable("hd_pore")
    assert is_stable("hd_pore:cheek")
    assert not is_volatile("hd_pore:cheek")
    assert is_volatile("hd_redness")
    assert not is_stable("hd_redness")


def test_unknown_keys_are_neither():
    """An unrecognised dimension must not silently leak into the identity vector."""
    assert not is_stable("hd_acne")
    assert not is_volatile("hd_acne")


def test_all_sd_concerns_contains_the_split():
    assert set(STABLE + VOLATILE) <= set(ALL_SD_CONCERNS)
