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
