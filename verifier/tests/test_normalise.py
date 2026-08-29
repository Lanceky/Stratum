"""
Normalisation tests (implementation.md Step 4).

`test_same_face_is_closer_than_a_different_face` is the Step 4 definition of
done. implementation.md says to stop and debug if it fails, because nothing
downstream can rescue a normalisation that does not separate identities.

Entirely offline.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import synth_cohort as sc  # noqa: E402
from normalise import (  # noqa: E402
    PopulationStats, apply_transform, canonical_frame, chamfer,
    constellation_distance,
    identity_vector, normalise_bundle, ratio_vector, umeyama,
    vector_distance, volatile_vector,
)


@pytest.fixture(scope="module")
def stats():
    """Fit on the synthetic cohort, so z-scores have a real spread to work with."""
    people = sc.cohort(n_people=12, n_captures=3)
    return PopulationStats.fit([c["scores"] for p in people for c in p])


# ── umeyama ───────────────────────────────────────────────────────────────
def test_umeyama_recovers_a_known_transform():
    rng = np.random.default_rng(0)
    src = rng.normal(0, 10, (30, 2))
    th = math.radians(23.0)
    R_true = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    dst = 1.7 * (src @ R_true.T) + np.array([12.0, -5.0])

    s, R, t = umeyama(src, dst)
    assert s == pytest.approx(1.7, rel=1e-6)
    assert R == pytest.approx(R_true, abs=1e-6)
    assert t == pytest.approx([12.0, -5.0], abs=1e-6)
    assert apply_transform(src, s, R, t) == pytest.approx(dst, abs=1e-6)


def test_umeyama_refuses_a_reflection():
    """A mirrored face is not a rotated face. det(R) must stay +1."""
    rng = np.random.default_rng(1)
    src = rng.normal(0, 10, (30, 2))
    dst = src * np.array([1.0, -1.0])
    _, R, _ = umeyama(src, dst)
    assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-9)


def test_umeyama_rejects_bad_input():
    with pytest.raises(ValueError):
        umeyama(np.zeros((3, 2)), np.zeros((4, 2)))
    with pytest.raises(ValueError):
        umeyama(np.zeros((1, 2)), np.zeros((1, 2)))


# ── canonical frame ───────────────────────────────────────────────────────
def _pose(pts, deg, scale, shift):
    th = math.radians(deg)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    out = pts.copy()
    out[:, :2] = (out[:, :2] @ R.T) * scale + np.array(shift)
    return out


def test_canonical_frame_is_pose_invariant():
    """The core claim: the same points under any similarity pose normalise alike."""
    ident = sc.Identity(seed=7)
    a, _ = canonical_frame(ident.spots)
    for deg, scale, shift in ((11.0, 1.4, (300.0, -220.0)),
                              (-25.0, 0.6, (-140.0, 90.0))):
        b, _ = canonical_frame(_pose(ident.spots, deg, scale, shift))
        assert chamfer(a, b) < 0.02, f"pose ({deg}°, ×{scale}) was not normalised away"


def test_canonical_frame_normalises_scale_and_position():
    pts, _ = canonical_frame(sc.Identity(seed=3).spots)
    assert np.abs(pts.mean(0)).max() < 0.05
    assert math.sqrt((pts**2).sum(1).mean()) == pytest.approx(1.0, rel=0.15)


def test_canonical_frame_is_deterministic():
    ident = sc.Identity(seed=4)
    a, _ = canonical_frame(ident.spots)
    b, _ = canonical_frame(ident.spots)
    assert a == pytest.approx(b)


def test_canonical_frame_handles_degenerate_input():
    empty, frame = canonical_frame(np.zeros((0, 3)))
    assert len(empty) == 0 and frame.n == 0
    two, _ = canonical_frame(np.array([[0.0, 0.0, 1.0], [10.0, 0.0, 1.0]]))
    assert len(two) == 2


def test_canonical_frame_uses_anchors_when_available():
    """The landmark path, ready for a source of landmarks that does not exist yet."""
    pts = sc.Identity(seed=5).spots
    anchors = pts[:4]
    ref = _pose(anchors, 30.0, 2.0, (10.0, 10.0))
    out, frame = canonical_frame(pts, anchors=anchors, anchor_ref=ref)
    assert out[:4] == pytest.approx(ref[:, :2], abs=1e-6)
    assert frame.scale == pytest.approx(2.0, rel=1e-6)


# ── the Step 4 definition of done ─────────────────────────────────────────
@pytest.fixture(scope="module")
def separation(stats):
    """
    Same-person and different-person distances, on both identity channels.

    The geometric half uses `constellation_distance`, not `chamfer`. Chamfer
    saturates — with ~70 points in a unit disc the mean nearest-neighbour
    distance is ~0.15 whichever points you pick, so it cannot tell a stranger
    from a second capture of the same face. It is kept for the pose-invariance
    checks, where measuring "same region" is exactly the question.

    Module-scoped because registration over the cohort is the slowest thing in
    the suite and both gate tests read the same distributions.
    """
    people = sc.cohort(n_people=12, n_captures=3)
    norm = [[normalise_bundle(c, stats) for c in caps] for caps in people]

    def pts(c):
        return np.array(c["constellations"]["hd_pore"]["points"])

    same_id, diff_id, same_geo, diff_geo = [], [], [], []
    for i, caps in enumerate(norm):
        for a in range(len(caps)):
            for b in range(a + 1, len(caps)):
                same_id.append(vector_distance(caps[a]["identity_vector"],
                                               caps[b]["identity_vector"]))
                same_geo.append(constellation_distance(pts(caps[a]), pts(caps[b])))
        for j in range(i + 1, len(norm)):
            diff_id.append(vector_distance(caps[0]["identity_vector"],
                                           norm[j][0]["identity_vector"]))
            diff_geo.append(constellation_distance(pts(caps[0]), pts(norm[j][0])))
    return (np.array(same_id), np.array(diff_id),
            np.array(same_geo), np.array(diff_geo))


def test_same_face_is_closer_than_a_different_face(separation):
    """
    THE Step 4 gate. Same person at different angles must be materially closer
    than two different people, before any threshold tuning.
    """
    same_id, diff_id, same_geo, diff_geo = separation

    assert same_id.mean() < diff_id.mean() / 2, (
        f"stable vectors do not separate: same={same_id.mean():.3f} "
        f"vs different={diff_id.mean():.3f}")
    assert same_geo.mean() < diff_geo.mean() / 2, (
        f"constellations do not separate: same={same_geo.mean():.4f} "
        f"vs different={diff_geo.mean():.4f}")


def test_the_two_distributions_do_not_overlap(separation):
    """A gap here is what makes a threshold possible at all."""
    same_id, diff_id, same_geo, diff_geo = separation
    assert same_id.max() < diff_id.min(), (
        f"identity distributions overlap: same max {same_id.max():.3f} "
        f"vs different min {diff_id.min():.3f}")
    assert same_geo.max() < diff_geo.min(), (
        f"constellation distributions overlap: same max {same_geo.max():.3f} "
        f"vs different min {diff_geo.min():.3f}")


def test_pose_normalisation_is_what_earns_the_separation():
    """
    Without the canonical frame the same face at a different angle looks like a
    stranger. This is the control: it proves the normalisation is load-bearing
    rather than incidental.
    """
    ident = sc.Identity(seed=11)
    a = sc.capture(ident, 1, **sc.POSES[0])["constellations"]["hd_pore"]
    b = sc.capture(ident, 2, **sc.POSES[1])["constellations"]["hd_pore"]

    raw = chamfer(np.array(a), np.array(b))
    norm = chamfer(canonical_frame(np.array(a))[0], canonical_frame(np.array(b))[0])
    assert raw > 50.0, "the test pose was too gentle to prove anything"
    assert norm < raw / 100


def test_volatile_dimensions_carry_no_identity(stats):
    """
    The claim behind the stable/volatile split, measured rather than asserted.
    Volatile separation should be near chance — same-person no closer than
    different-person.
    """
    people = sc.cohort(n_people=10, n_captures=2)
    norm = [[normalise_bundle(c, stats) for c in caps] for caps in people]

    same = np.array([vector_distance(c[0]["volatile_vector"], c[1]["volatile_vector"])
                     for c in norm])
    diff = np.array([vector_distance(norm[i][0]["volatile_vector"],
                                     norm[j][0]["volatile_vector"])
                     for i in range(len(norm)) for j in range(i + 1, len(norm))])
    assert same.mean() > diff.mean() * 0.7, (
        "volatile dimensions appear to identify people; if that is real, the "
        "STABLE/VOLATILE split needs revisiting")


# ── vectors and bundle shape ──────────────────────────────────────────────
def test_identity_vector_excludes_volatile_dimensions(stats):
    bundle = sc.capture(sc.Identity(seed=2), 1)
    ident = identity_vector(bundle["scores"], stats)
    assert ident, "identity vector must not be empty"
    assert not set(ident) & set(volatile_vector(bundle["scores"], stats))
    for k in ("hd_redness", "hd_radiance", "hd_oiliness"):
        assert k not in ident


def test_normalise_bundle_shape(stats):
    out = normalise_bundle(sc.capture(sc.Identity(seed=6), 1), stats)
    assert set(out) == {"source", "identity_vector", "identity_dimensions",
                        "volatile_vector", "ratios", "constellations",
                        "population", "warnings"}
    assert out["constellations"]["hd_pore"]["n"] > 0
    assert out["identity_dimensions"] == sorted(out["identity_dimensions"])


def test_unknown_dimensions_are_excluded_and_reported(stats):
    out = normalise_bundle({"scores": {"hd_pore": 50.0, "hd_unheard_of": 50.0}}, stats)
    assert "hd_unheard_of" not in out["identity_vector"]
    assert any("neither stable nor volatile" in w for w in out["warnings"])


def test_provisional_statistics_are_declared():
    out = normalise_bundle({"scores": {"hd_pore": 50.0}}, PopulationStats.fit([]))
    assert out["population"]["provisional"] is True
    assert any("provisional" in w for w in out["warnings"])


def test_empty_bundle_does_not_crash(stats):
    out = normalise_bundle({}, stats)
    assert out["identity_vector"] == {}
    assert any("identity vector is empty" in w for w in out["warnings"])


def test_ratios_pass_through_unchanged():
    ident = sc.Identity(seed=9)
    got = ratio_vector({"face_ratio": ident.ratios})
    assert got == pytest.approx(ident.ratios)
    assert len(got) == 11


def test_ratios_ignore_non_numeric_values():
    assert ratio_vector({"face_ratio": {"a": 1.0, "b": "n/a"}}) == {"a": 1.0}


# ── population statistics ─────────────────────────────────────────────────
def test_zscore_centres_the_population(stats):
    people = sc.cohort(n_people=12, n_captures=3)
    zs = [stats.z("hd_pore", c["scores"]["hd_pore"]) for p in people for c in p]
    assert abs(float(np.mean(zs))) < 0.15
    assert float(np.std(zs)) == pytest.approx(1.0, rel=0.15)


def test_zero_variance_dimension_does_not_divide_by_zero():
    s = PopulationStats.fit([{"hd_pore": 50.0}] * 5)
    assert s.std["hd_pore"] == 0.0
    assert math.isfinite(s.z("hd_pore", 60.0))


def test_small_cohort_is_marked_provisional():
    assert PopulationStats.fit([{"hd_pore": 50.0}] * 3).provisional is True
    assert PopulationStats.fit(
        [{"hd_pore": float(i)} for i in range(20)]).provisional is False


def test_stats_round_trip(tmp_path, stats):
    p = stats.save(tmp_path / "population.json")
    back = PopulationStats.load(p)
    assert back.mean == stats.mean and back.std == stats.std
    assert back.n == stats.n


def test_vector_distance_edge_cases():
    assert vector_distance({}, {}) == float("inf")
    assert vector_distance({"a": 1.0}, {"b": 1.0}) == float("inf")
    assert vector_distance({"a": 1.0}, {"a": 1.0}) == 0.0
