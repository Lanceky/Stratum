"""
Guards on the credit-survival layer.

These run offline against fixtures. If they break, the project is one bad
loop away from an empty Perfect Corp grant.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fixtures  # noqa: E402
from dimensions import STABLE, VOLATILE  # noqa: E402


def test_replay_is_the_default():
    """A fresh checkout must not be able to spend units by accident."""
    assert os.getenv("STRATUM_API_MODE", "replay") == "replay"


def test_replay_never_calls_out(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", tmp_path / "synthetic")

    def must_not_run():
        raise AssertionError("replay mode made a live call")

    with pytest.raises(FileNotFoundError):
        fixtures.call("skin-analysis-hd", {"a": 1}, must_not_run)


def _seed(d: Path, op: str, payload: dict, body: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fixtures.fixture_key(op, payload)}.json").write_text(json.dumps(body))


def test_recorded_fixture_beats_synthetic(monkeypatch, tmp_path):
    """
    Once a real response is recorded it must shadow the placeholder, otherwise
    a demo could silently run on invented numbers.
    """
    real, synth = tmp_path / "real", tmp_path / "synth"
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", real)
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", synth)

    payload = {"src_file_id": "f"}
    _seed(synth, "skin-analysis-hd", payload, {"_synthetic": True})
    _seed(real, "skin-analysis-hd", payload, {"_synthetic": False})

    got = fixtures.call("skin-analysis-hd", payload, lambda: None)
    assert got["_synthetic"] is False


def test_synthetic_is_used_when_nothing_is_recorded(monkeypatch, tmp_path):
    real, synth = tmp_path / "real", tmp_path / "synth"
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", real)
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", synth)

    payload = {"src_file_id": "f"}
    _seed(synth, "skin-analysis-hd", payload, {"_synthetic": True})
    assert fixtures.call("skin-analysis-hd", payload, lambda: None)["_synthetic"] is True


def test_live_recordings_never_land_in_the_synthetic_tree(monkeypatch, tmp_path):
    real, synth = tmp_path / "real", tmp_path / "synth"
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", real)
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", synth)
    monkeypatch.setattr(fixtures, "UNIT_LOG", tmp_path / "units.log")

    fixtures.call("face-attr-analysis", {"a": 1}, lambda: {"ok": True})
    assert list(real.glob("*.json"))
    assert not synth.exists()


def test_budget_ceiling_blocks_overspend(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, "MODE", "live")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(fixtures, "UNIT_LOG", tmp_path / "units.log")
    monkeypatch.setattr(fixtures, "CEILING", 10)

    with pytest.raises(fixtures.UnitBudgetExceeded):
        fixtures.call("skin-analysis-hd", {"a": 1}, lambda: {"ok": True})  # 22 units


def test_fixture_key_is_stable():
    a = fixtures.fixture_key("op", {"x": 1, "y": 2})
    b = fixtures.fixture_key("op", {"y": 2, "x": 1})
    assert a == b, "key must not depend on dict ordering"


def test_volatile_dimensions_are_never_identity():
    """The fix for the original concept's fatal flaw. Do not weaken this."""
    assert not set(STABLE) & set(VOLATILE)
    for d in ("moisture", "redness", "oiliness", "radiance"):
        assert d in VOLATILE
        assert d not in STABLE
