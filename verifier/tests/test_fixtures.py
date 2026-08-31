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
import fixtures as fx  # noqa: E402
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


def _missing(monkeypatch, tmp_path):
    monkeypatch.setattr(fixtures, "MODE", "replay")
    monkeypatch.setattr(fixtures, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(fixtures, "SYNTHETIC_DIR", tmp_path / "synthetic")
    with pytest.raises(fixtures.FixtureMissing) as e:
        fixtures.call("skin-analysis-hd", {"a": 1}, lambda: None)
    return e.value


def test_a_missing_fixture_separates_the_cause_from_the_remedy(monkeypatch,
                                                               tmp_path):
    exc = _missing(monkeypatch, tmp_path)
    assert "make seed" not in exc.cause
    assert "make seed" in exc.remedy


def test_the_cause_still_says_which_call_had_no_recording(monkeypatch, tmp_path):
    assert "skin-analysis-hd" in _missing(monkeypatch, tmp_path).cause


def test_the_full_message_keeps_the_remedy_for_a_terminal(monkeypatch, tmp_path):
    exc = _missing(monkeypatch, tmp_path)
    assert exc.cause in str(exc) and exc.remedy in str(exc)


def test_it_is_still_a_file_not_found_error(monkeypatch, tmp_path):
    # Callers catch FileNotFoundError. Narrowing the type here would route a
    # missing recording somewhere it has never been routed before.
    assert isinstance(_missing(monkeypatch, tmp_path), FileNotFoundError)


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


# ── auto mode must not mistake a placeholder for real data ────────────────
def test_auto_mode_ignores_synthetic_fixtures(tmp_path, monkeypatch):
    """
    A synthetic stand-in satisfying `auto` means the live call never happens and
    the real recording never appears — a benchmark that claims to be measured
    would quietly be made of placeholders.
    """
    monkeypatch.setattr(fx, "FIXTURE_DIR", tmp_path / "rec")
    monkeypatch.setattr(fx, "SYNTHETIC_DIR", tmp_path / "rec" / "synthetic")
    fx.SYNTHETIC_DIR.mkdir(parents=True)
    key = fx.fixture_key("skin-analysis-hd", {"a": 1})
    (fx.SYNTHETIC_DIR / f"{key}.json").write_text('{"_synthetic": true}')

    monkeypatch.setattr(fx, "MODE", "auto")
    monkeypatch.setattr(fx, "UNIT_LOG", tmp_path / "units.log")
    called = []
    out = fx.call("skin-analysis-hd", {"a": 1}, lambda: (called.append(1), {"real": True})[1])
    assert called, "auto mode returned the synthetic stand-in instead of calling out"
    assert out == {"real": True}


def test_replay_mode_still_accepts_synthetic_fixtures(tmp_path, monkeypatch):
    """Replay must keep working with no credentials — that is what it is for."""
    monkeypatch.setattr(fx, "FIXTURE_DIR", tmp_path / "rec")
    monkeypatch.setattr(fx, "SYNTHETIC_DIR", tmp_path / "rec" / "synthetic")
    fx.SYNTHETIC_DIR.mkdir(parents=True)
    key = fx.fixture_key("skin-analysis-hd", {"a": 1})
    (fx.SYNTHETIC_DIR / f"{key}.json").write_text('{"_synthetic": true}')

    monkeypatch.setattr(fx, "MODE", "replay")
    assert fx.call("skin-analysis-hd", {"a": 1}, lambda: pytest.fail("called out")) \
        == {"_synthetic": True}


def test_units_are_charged_even_when_the_call_fails(tmp_path, monkeypatch):
    """
    Perfect Corp bills a task that errors mid-run. Recording only successes lets
    the ledger drift below real spend, which is the one direction a budget guard
    must never err in.
    """
    monkeypatch.setattr(fx, "FIXTURE_DIR", tmp_path / "rec")
    monkeypatch.setattr(fx, "SYNTHETIC_DIR", tmp_path / "syn")
    monkeypatch.setattr(fx, "UNIT_LOG", tmp_path / "units.log")
    monkeypatch.setattr(fx, "MODE", "live")

    def boom():
        raise RuntimeError("error_no_face")

    with pytest.raises(RuntimeError):
        fx.call("skin-analysis-hd", {"a": 1}, boom)
    assert fx._spent() == fx.UNIT_COST["skin-analysis-hd"]
