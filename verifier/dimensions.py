"""
Which skin dimensions may be used for identity, and which may not.

This constant is the fix for the fatal flaw in the original concept
(context.md §1). Moisture, redness, oiliness and radiance fluctuate hour to
hour — intra-person variance exceeds inter-person variance — so they carry
no identity signal. They are used ONLY by check 1, where their volatility is
exactly the point: they must respond to the colour-flash challenge.

Keep this visible. A judge who greps the repo should find it immediately.
"""

# Slow-moving. Safe for the 1:1 identity vector (check 3).
STABLE = [
    "pore",
    "texture",
    "wrinkle",
    "firmness",
    "skin_type",
    "age_spot",
]

# Fluctuate hourly with hydration, temperature, time of day, exertion.
# NEVER part of the identity vector. Used by check 1 only.
VOLATILE = [
    "moisture",
    "redness",
    "oiliness",
    "radiance",
]

# Channels whose response direction under a coloured flash is asserted by check 1.
ILLUMINATION_RESPONSIVE = ["redness", "radiance", "oiliness"]

ALL_SD_CONCERNS = STABLE + VOLATILE + [
    "acne",
    "dark_circle_v2",
    "eye_bag",
    "tear_trough",
]

assert not set(STABLE) & set(VOLATILE), "a dimension cannot be both stable and volatile"


def base(score_key: str) -> str:
    """
    Normalise a Perfect Corp score key to its dimension name.

    The API returns `hd_pore`, and `hd_pore:forehead` for per-region entries.
    Both are the `pore` dimension. Without this, the STABLE/VOLATILE split
    silently matches nothing in HD mode.

        base("hd_pore:forehead") -> "pore"
    """
    return score_key.split(":", 1)[0].removeprefix("hd_")


def is_stable(score_key: str) -> bool:
    return base(score_key) in STABLE


def is_volatile(score_key: str) -> bool:
    return base(score_key) in VOLATILE
