"""
Normalisation: raw API output becomes comparable numbers (implementation.md Step 4).

Everything in Steps 5-7 depends on this being right, so it is its own module
with its own tests and no network access.

A correction to the plan, found by reading a real response
------------------------------------------------------------------
implementation.md Step 4 says "use the facial-ratio landmarks to build a
similarity transform". Perfect Corp's face-attr-analysis does not return
landmark coordinates — it returns eleven scalar *ratios* (`ratio_01` …
`ratio_11`) plus attributes. See fixtures under `fixtures/`.

That changes the work in a way that is worth stating plainly:

  * Ratios are quotients of distances, so they are already invariant to
    translation, scale and in-plane rotation. They need z-scoring, not a
    geometric transform. They are the strongest identity signal available.
  * The spot constellations *are* in pixel coordinates and do need geometric
    normalisation — but there are no landmarks to anchor them to. So the
    canonical frame is derived from each point set's own weighted moments.

`umeyama` is implemented here regardless, because Step 7's Procrustes matching
needs exactly that, and because if a landmark source appears later
(`anchors=` on `canonical_frame`) it drops straight in.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from dimensions import base, is_stable, is_volatile

REPO_ROOT = Path(__file__).resolve().parent.parent
DERIVED_DIR = REPO_ROOT / "fixtures" / "derived"
STATS_PATH = REPO_ROOT / "fixtures" / "population.json"

# Perfect Corp scores are 0-100. Used only until a genuine cohort exists
# (Step 12); every consumer is told when the stats are still provisional.
PRIOR_MEAN, PRIOR_STD = 50.0, 15.0
MIN_COHORT = 8


# ── geometry ──────────────────────────────────────────────────────────────
def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Least-squares similarity transform mapping src onto dst (Umeyama 1991).

    Returns (scale, rotation, translation) with dst ≈ scale · R @ src + t.
    Requires known correspondences, so it is for Step 7's matched point sets,
    not for raw unordered constellations.
    """
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if src.shape != dst.shape or src.ndim != 2:
        raise ValueError(f"shape mismatch: {src.shape} vs {dst.shape}")
    n, d = src.shape
    if n < 2:
        raise ValueError("need at least 2 correspondences")

    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d

    cov = dc.T @ sc / n
    U, D, Vt = np.linalg.svd(cov)

    # Guard against a reflection: a mirrored face is not a rotated face.
    S = np.eye(d)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[-1, -1] = -1.0

    R = U @ S @ Vt
    var_s = (sc**2).sum() / n
    scale = 1.0 if var_s == 0 else float((D * np.diag(S)).sum() / var_s)
    t = mu_d - scale * R @ mu_s
    return scale, R, t


def apply_transform(pts: np.ndarray, scale: float, R: np.ndarray,
                    t: np.ndarray) -> np.ndarray:
    return (scale * (np.asarray(pts, float) @ R.T)) + t


@dataclass(frozen=True)
class Frame:
    """The transform that took a point set into its canonical frame."""

    centroid: tuple[float, float]
    scale: float
    rotation_deg: float
    n: int

    def as_dict(self) -> dict:
        return {"centroid": list(self.centroid), "scale": self.scale,
                "rotation_deg": self.rotation_deg, "n": self.n}


def canonical_frame(points: np.ndarray, anchors: np.ndarray | None = None,
                    anchor_ref: np.ndarray | None = None) -> tuple[np.ndarray, Frame]:
    """
    Map a constellation into a translation/scale/rotation invariant frame.

    With `anchors` (landmark coordinates) and `anchor_ref` (their canonical
    positions), the transform is solved exactly — the preferred path, unused
    today only because Perfect Corp returns no landmarks.

    Without them, the frame comes from the point set's own moments: centroid →
    origin, RMS radius → 1, principal axis → vertical. Faces are taller than
    wide, so the principal axis is stable enough to use; the sign ambiguity
    inherent in PCA is resolved by third moment, deterministically.

    The moments are deliberately *unweighted*. Weighting positions by spot area
    was measured to be actively harmful: areas span a 20x range and carry 22%
    per-capture noise, so a handful of large spots drag the origin, and two
    captures of one face ended up with frame origins 0.26 apart in frame units
    — five times the matching radius, which no rotation-only restart can
    bridge. An unweighted centroid over a ~80% detection subset is far steadier.
    Spot area is still used, but as a *matching* feature in `register`, where a
    noisy per-spot attribute belongs, not as a framing weight.

    Input columns are (x, y, weight); the weight column is accepted and ignored
    here so callers can pass constellations through unchanged.
    """
    pts = np.asarray(points, float)
    if pts.size == 0:
        return np.zeros((0, 2)), Frame((0.0, 0.0), 1.0, 0.0, 0)
    if pts.ndim != 2 or pts.shape[1] < 2:
        raise ValueError("points must be (N, 2) or (N, 3) with a weight column")

    xy = pts[:, :2]
    w = np.full(len(pts), 1.0 / len(pts))

    if anchors is not None and anchor_ref is not None:
        scale, R, t = umeyama(np.asarray(anchors, float)[:, :2],
                              np.asarray(anchor_ref, float)[:, :2])
        out = apply_transform(xy, scale, R, t)
        ang = math.degrees(math.atan2(R[1, 0], R[0, 0]))
        return out, Frame((float(t[0]), float(t[1])), scale, ang, len(out))

    centroid = (w[:, None] * xy).sum(0)
    centred = xy - centroid

    rms = math.sqrt(float((w * (centred**2).sum(1)).sum()))
    scale = 1.0 / rms if rms > 0 else 1.0
    centred = centred * scale

    if len(pts) < 3:
        return centred, Frame(tuple(centroid), scale, 0.0, len(centred))

    cov = (w[:, None, None] * np.einsum("ni,nj->nij", centred, centred)).sum(0)
    vals, vecs = np.linalg.eigh(cov)
    principal = vecs[:, int(np.argmax(vals))]

    # Rotate the principal axis onto +y.
    theta = math.atan2(principal[0], principal[1])
    R = np.array([[math.cos(theta), -math.sin(theta)],
                  [math.sin(theta), math.cos(theta)]])
    out = centred @ R.T

    # PCA gives an axis, not a direction. Pick the sign by third moment so the
    # same face lands the same way up every time.
    out = _fix_sign(out, w)
    return out, Frame(tuple(centroid), scale, math.degrees(theta), len(out))


def _fix_sign(pts: np.ndarray, w: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """
    Resolve PCA's ±axis ambiguity deterministically.

    Both axes flip together or neither does. Flipping one axis alone is a
    reflection, and a mirrored face is not a rotated face — it would let an
    attacker's mirror image register against a genuine constellation. Flipping
    both is a 180° rotation, which is a legitimate pose.

    The third moment decides the direction. It can be near zero for a nearly
    symmetric cloud, in which case the choice is unstable between captures;
    `register` covers that with 180°-offset restarts rather than pretending
    this is reliable.
    """
    m3 = float((w * pts[:, 1] ** 3).sum())
    if abs(m3) <= eps:
        i = int(np.argmax(np.abs(pts[:, 1])))
        m3 = float(pts[i, 1])
    return -pts if m3 < 0 else pts


def chamfer(a: np.ndarray, b: np.ndarray) -> float:
    """
    Symmetric mean nearest-neighbour distance between two point sets.

    Measures whether two clouds occupy the same *region*, which makes it the
    right tool for checking that a pose was normalised away. It is the wrong
    tool for identity: with ~70 points in a unit disc the mean nearest-
    neighbour distance is ~0.15 whichever points you pick, so it saturates and
    two strangers score almost as well as two captures of one person. Measured,
    not assumed — see tests/test_normalise.py. Use `register` for identity.
    """
    a, b = np.asarray(a, float)[:, :2], np.asarray(b, float)[:, :2]
    if len(a) == 0 or len(b) == 0:
        return float("inf")
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float(d.min(1).mean() + d.min(0).mean()) / 2.0


# ── point-set registration ────────────────────────────────────────────────
@dataclass(frozen=True)
class Registration:
    """Result of aligning one constellation onto another."""

    inlier_ratio: float
    inliers: int
    rmse: float
    rotation_deg: float
    scale: float

    @property
    def distance(self) -> float:
        """1 - inlier_ratio, so that 0 is identical and 1 is unrelated."""
        return 1.0 - self.inlier_ratio

    def as_dict(self) -> dict:
        return {"inlier_ratio": self.inlier_ratio, "inliers": self.inliers,
                "rmse": self.rmse, "rotation_deg": self.rotation_deg,
                "scale": self.scale, "distance": self.distance}


def _rot(deg: float) -> np.ndarray:
    th = math.radians(deg)
    return np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])


def _mutual_inliers(a: np.ndarray, b: np.ndarray,
                    radius: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Indices of point pairs that are each other's nearest neighbour, within radius.

    Mutual nearest neighbours matter: a one-way nearest neighbour lets many
    points in a sparse cloud all claim the same dense-cloud point, which
    inflates the score for two strangers.
    """
    ta, tb = cKDTree(a), cKDTree(b)
    da, ia = tb.query(a, k=1)
    _, ib = ta.query(b, k=1)
    idx = np.arange(len(a))
    keep = (ib[ia] == idx) & (da <= radius)
    return idx[keep], ia[keep]


def icp(src: np.ndarray, dst: np.ndarray, *, radius: float = 0.06,
        iterations: int = 12, tol: float = 1e-7,
        anneal: tuple[float, ...] = (5.0, 3.0, 2.0, 1.4, 1.0)) -> tuple[
            np.ndarray, float, np.ndarray]:
    """
    Iterative closest point under a similarity transform, coarse to fine.

    Correspondences are unknown, so alternate: match nearest neighbours, solve
    the transform over those matches (umeyama), repeat.

    The annealing schedule is not decoration. A 10° residual rotation displaces
    a point at unit radius by ~0.17, which is far outside the 0.06 matching
    radius — so a fixed tight radius finds no correspondences at all and ICP
    cannot start. Each pass multiplies the radius by a shrinking factor, so
    early passes capture the gross misalignment and later ones tighten onto it.
    """
    cur = np.asarray(src, float)[:, :2].copy()
    dst = np.asarray(dst, float)[:, :2]
    total_s, total_R, total_t = 1.0, np.eye(2), np.zeros(2)

    for factor in anneal:
        r = radius * factor
        prev = float("inf")
        for _ in range(iterations):
            i, j = _mutual_inliers(cur, dst, r)
            if len(i) < 3:
                break
            s, R, t = umeyama(cur[i], dst[j])
            cur = apply_transform(cur, s, R, t)
            total_s, total_R, total_t = s * total_s, R @ total_R, s * (R @ total_t) + t

            err = float(np.linalg.norm(cur[i] - dst[j], axis=1).mean())
            if abs(prev - err) < tol:
                break
            prev = err

    return cur, total_s, total_R


def _rel_weights(pts: np.ndarray) -> np.ndarray:
    """
    Spot areas divided by their median.

    Relative, not absolute: a camera held closer makes every spot bigger, so
    only the size of a spot *relative to its neighbours* is a stable feature.
    """
    if pts.ndim != 2 or pts.shape[1] < 3:
        return np.ones(len(pts))
    w = np.asarray(pts[:, 2], float)
    med = float(np.median(w[w > 0])) if np.any(w > 0) else 0.0
    return (w / med) if med > 0 else np.ones(len(pts))


def _seed_translations(a: np.ndarray, b: np.ndarray, wa: np.ndarray,
                       wb: np.ndarray, *, size_tol: float, bin_size: float,
                       top: int) -> list[np.ndarray]:
    """
    Vote for the translation that aligns `a` onto `b` (a Hough transform).

    Necessary because the canonical frame does not give the two clouds a shared
    origin. The frame centroid is computed over whichever spots were detected,
    and two captures of one face were measured to land up to 0.19 apart in frame
    units — nearly four times the matching radius. A rotation-only restart
    cannot close that, and ICP cannot either: with no correspondence inside the
    radius it has nothing to solve from.

    So propose the translation directly. Every candidate correspondence casts
    one vote for the offset it implies. Correct correspondences all imply nearly
    the same offset and pile into one bin; wrong ones scatter. Candidates are
    pre-filtered by relative spot size, which keeps the vote from being drowned
    by pairings that could never be genuine.
    """
    gate = np.abs(np.log(wa)[:, None] - np.log(wb)[None, :]) <= size_tol
    ii, jj = np.nonzero(gate)
    if len(ii) == 0:
        return [np.zeros(2)]

    votes = b[jj] - a[ii]
    keys = np.round(votes / bin_size).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True,
                                   return_counts=True)
    # Averaging the votes inside a bin recovers sub-bin precision, so the bin
    # can stay coarse enough to actually collect the true offset together.
    return [votes[inverse == k].mean(0)
            for k in np.argsort(-counts)[:top]]


def register(a: np.ndarray, b: np.ndarray, *, radius: float = 0.05,
             size_tol: float = 0.6,
             anneal: tuple[float, ...] = (2.0, 1.4, 1.0),
             seeds: int = 3, bin_size: float = 0.10,
             restarts: tuple[float, ...] = tuple(float(d)
                                                 for d in range(0, 360, 30))
             ) -> Registration:
    """
    Align constellation `a` onto `b` and report how much of it actually matches.

    This, not `canonical_frame`, is what carries geometric identity. Framing
    each cloud independently is not accurate enough: the principal axis is
    estimated from whichever spots happened to be detected, and a different
    detection subset moves it by several degrees — measured at ±8° on the
    synthetic cohort. Registration sidesteps that by solving for the transform
    that maximises agreement between the two clouds.

    Restarts cover that residual framing error, because ICP alone will settle
    into a local minimum if the initial rotation is off. `_seed_translations`
    covers the residual *offset*, which restarts cannot: framing gives the two
    clouds a shared scale and roughly a shared orientation, but not a shared
    origin.

    Position alone is not enough to separate identities. A similarity transform
    has four free parameters, so fitting it to maximise overlap will always
    align some subset of two unrelated clouds — measured at ~14% spurious
    inliers, against ~74% for genuine pairs. Tightening the radius does not
    help, because it shrinks the true matches at the same rate as the chance
    ones. `size_tol` adds an independent axis of agreement: two spots must also
    be of comparable relative area, which unrelated clouds have no reason to be.
    """
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if len(a) < 3 or len(b) < 3:
        return Registration(0.0, 0, float("inf"), 0.0, 1.0)

    fa, _ = canonical_frame(a)
    fb, _ = canonical_frame(b)
    wa, wb = _rel_weights(a), _rel_weights(b)
    denom = min(len(fa), len(fb))
    best = Registration(0.0, 0, float("inf"), 0.0, 1.0)

    for deg in restarts:
        rotated = fa @ _rot(deg).T
        for shift in _seed_translations(rotated, fb, wa, wb, size_tol=size_tol,
                                        bin_size=bin_size, top=seeds):
            start = rotated + shift
            moved, s, R = icp(start, fb, radius=radius, anneal=anneal)
            i, j = _mutual_inliers(moved, fb, radius)
            if len(i) == 0:
                continue

            agree = np.abs(np.log(wa[i]) - np.log(wb[j])) <= size_tol
            i, j = i[agree], j[agree]
            if len(i) == 0:
                continue

            rmse = float(np.sqrt(((moved[i] - fb[j]) ** 2).sum(1).mean()))
            cand = Registration(
                inlier_ratio=len(i) / denom, inliers=len(i), rmse=rmse,
                rotation_deg=deg + math.degrees(math.atan2(R[1, 0], R[0, 0])),
                scale=float(s))
            if cand.inlier_ratio > best.inlier_ratio or (
                    cand.inlier_ratio == best.inlier_ratio
                    and cand.rmse < best.rmse):
                best = cand

    return best


def constellation_distance(a: np.ndarray, b: np.ndarray, **kw) -> float:
    """0 when two constellations are the same spots, approaching 1 when unrelated."""
    return register(a, b, **kw).distance


# ── population statistics ─────────────────────────────────────────────────
@dataclass
class PopulationStats:
    """
    Per-dimension mean and standard deviation, for z-scoring.

    Bootstrapped from whatever captures exist and refit in Step 12 against the
    genuine set. `provisional` is carried through to the API response so no
    downstream consumer can mistake a prior for a measurement.
    """

    mean: dict[str, float] = field(default_factory=dict)
    std: dict[str, float] = field(default_factory=dict)
    n: int = 0
    provisional: bool = True

    @classmethod
    def fit(cls, samples: list[dict[str, float]]) -> "PopulationStats":
        keys = sorted({k for s in samples for k in s})
        mean, std = {}, {}
        for k in keys:
            vals = np.array([s[k] for s in samples if k in s], float)
            mean[k] = float(vals.mean())
            # A zero std would divide by zero; it means the dimension carries
            # no information in this cohort, so neutralise rather than explode.
            std[k] = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        return cls(mean, std, len(samples), provisional=len(samples) < MIN_COHORT)

    @classmethod
    def bootstrap(cls) -> "PopulationStats":
        samples = []
        if DERIVED_DIR.exists():
            for p in sorted(DERIVED_DIR.glob("*.json")):
                scores = json.loads(p.read_text()).get("scores") or {}
                if scores:
                    samples.append({k: float(v) for k, v in scores.items()})
        return cls.fit(samples) if samples else cls(n=0, provisional=True)

    def z(self, key: str, value: float) -> float:
        mu = self.mean.get(key, PRIOR_MEAN)
        sd = self.std.get(key) or PRIOR_STD
        return (float(value) - mu) / sd

    def save(self, path: Path = STATS_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mean": self.mean, "std": self.std,
                                    "n": self.n, "provisional": self.provisional},
                                   indent=2))
        return path

    @classmethod
    def load(cls, path: Path = STATS_PATH) -> "PopulationStats":
        if not path.exists():
            return cls.bootstrap()
        d = json.loads(path.read_text())
        return cls(d.get("mean", {}), d.get("std", {}),
                   d.get("n", 0), d.get("provisional", True))

    def as_dict(self) -> dict:
        return {"n": self.n, "provisional": self.provisional,
                "dimensions": len(self.mean)}


# ── bundle normalisation ──────────────────────────────────────────────────
def identity_vector(scores: dict[str, float],
                    stats: PopulationStats) -> dict[str, float]:
    """
    The z-scored STABLE dimensions, and nothing else.

    Volatile dimensions are excluded here rather than down-weighted, because
    a weight can be tuned back up by accident and an omission cannot.
    """
    return {k: stats.z(k, v) for k, v in sorted(scores.items()) if is_stable(k)}


def volatile_vector(scores: dict[str, float],
                    stats: PopulationStats) -> dict[str, float]:
    return {k: stats.z(k, v) for k, v in sorted(scores.items()) if is_volatile(k)}


def ratio_vector(face_attributes: dict) -> dict[str, float]:
    """
    Facial ratios, used as-is.

    Already invariant to translation, scale and in-plane rotation, being
    quotients of distances — which is why they get no geometric transform.
    """
    ratios = (face_attributes or {}).get("face_ratio") or {}
    return {k: float(v) for k, v in sorted(ratios.items())
            if isinstance(v, (int, float))}


def vector_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Euclidean distance over shared keys, normalised by dimension count."""
    shared = sorted(set(a) & set(b))
    if not shared:
        return float("inf")
    d = np.array([a[k] - b[k] for k in shared], float)
    return float(np.linalg.norm(d) / math.sqrt(len(shared)))


def normalise_bundle(bundle: dict, stats: PopulationStats | None = None) -> dict:
    """
    Turn one capture bundle into comparable vectors. Pure; no network, no state.
    """
    stats = stats or PopulationStats.load()
    scores = {k: float(v) for k, v in (bundle.get("scores") or {}).items()}
    warnings: list[str] = []

    unknown = sorted({k for k in scores if not is_stable(k) and not is_volatile(k)})
    if unknown:
        warnings.append(
            f"{len(unknown)} dimension(s) classified as neither stable nor "
            f"volatile and excluded from identity: {', '.join(unknown[:6])}")
    if stats.provisional:
        warnings.append(
            f"population statistics are provisional (n={stats.n}); "
            f"z-scores are indicative until the Step 12 cohort is fitted")

    constellations = {}
    for name, pts in (bundle.get("constellations") or {}).items():
        arr = np.asarray(pts, float)
        if arr.size == 0:
            warnings.append(f"constellation '{name}' is empty")
            constellations[name] = {"points": [], "frame": None, "n": 0}
            continue
        norm, frame = canonical_frame(arr)
        # Carry relative spot size alongside the framed coordinates. `register`
        # uses it as an independent axis of agreement, and it is stored in its
        # camera-distance-invariant form so a stored constellation can be
        # matched later without the original pixel areas.
        out = np.column_stack([norm, _rel_weights(arr)])
        constellations[name] = {"points": out.tolist(),
                                "frame": frame.as_dict(), "n": len(out)}

    identity = identity_vector(scores, stats)
    if not identity:
        warnings.append("no stable dimensions present — identity vector is empty")

    return {
        "source": bundle.get("source"),
        "identity_vector": identity,
        "identity_dimensions": sorted({base(k) for k in identity}),
        "volatile_vector": volatile_vector(scores, stats),
        "ratios": ratio_vector(bundle.get("face_attributes") or {}),
        "constellations": constellations,
        "population": stats.as_dict(),
        "warnings": warnings,
    }
