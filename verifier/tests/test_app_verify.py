"""
HTTP surface tests for normalisation (implementation.md Step 4).

The maths is covered in test_normalise.py. What matters here is the contract a
caller sees: that the endpoint returns comparable numbers, that it never
returns a verdict, and that it says so when the numbers behind it are still
provisional.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import synth_cohort as sc  # noqa: E402
import app as appmod  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(appmod.app)


@pytest.fixture(scope="module")
def people():
    return sc.cohort(n_people=2, n_captures=2)


def _bundle(capture):
    return {k: capture[k] for k in
            ("source", "scores", "face_attributes", "constellations")}


def test_verify_returns_comparable_vectors(client, people):
    r = client.post("/verify", json=_bundle(people[0][0]))
    assert r.status_code == 200
    body = r.json()

    assert body["identity_vector"], "no identity vector returned"
    assert body["constellations"]["hd_pore"]["n"] > 0
    # (x, y, relative area) — the area column is what register's size gate uses,
    # so a normalised constellation has to keep it to stay matchable.
    assert len(body["constellations"]["hd_pore"]["points"][0]) == 3


def test_verify_excludes_volatile_dimensions_from_identity(client, people):
    body = client.post("/verify", json=_bundle(people[0][0])).json()
    assert body["identity_vector"]
    assert body["volatile_vector"]
    assert not set(body["identity_vector"]) & set(body["volatile_vector"]), (
        "a dimension appears in both identity and volatile vectors")


def test_verify_flags_provisional_population_statistics(client, people):
    """
    A prior must never be mistaken for a measurement. Until the Step 12 cohort
    is fitted, every response has to say so.
    """
    body = client.post("/verify", json=_bundle(people[0][0])).json()
    assert body["population"]["provisional"] is True
    assert any("provisional" in w for w in body["warnings"])


def test_verify_rejects_an_empty_bundle(client):
    assert client.post("/verify", json={}).status_code == 422


def test_compare_separates_same_person_from_different_people(client, people):
    same = client.post("/verify/compare", json={
        "a": _bundle(people[0][0]), "b": _bundle(people[0][1])}).json()
    diff = client.post("/verify/compare", json={
        "a": _bundle(people[0][0]), "b": _bundle(people[1][0])}).json()

    assert same["identity_distance"] < diff["identity_distance"]
    assert (same["geometric"]["hd_pore"]["inlier_ratio"]
            > diff["geometric"]["hd_pore"]["inlier_ratio"])


def test_compare_reports_each_channel_separately(client, people):
    """
    Channels are not fused. A caller that cannot see which channel disagreed
    cannot tell a bad pose from an impostor.
    """
    body = client.post("/verify/compare", json={
        "a": _bundle(people[0][0]), "b": _bundle(people[0][1])}).json()

    for key in ("identity_distance", "volatile_distance", "ratio_distance",
                "geometric"):
        assert key in body
    assert "verdict" not in body and "match" not in body, (
        "Step 4 normalises; it does not decide")


def test_compare_survives_a_constellation_too_small_to_register(client, people):
    a = _bundle(people[0][0])
    b = _bundle(people[0][1])
    b["constellations"] = {"hd_pore": b["constellations"]["hd_pore"][:2]}

    body = client.post("/verify/compare", json={"a": a, "b": b}).json()
    assert body["geometric"]["hd_pore"] is None
