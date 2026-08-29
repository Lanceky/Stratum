"""
Perfect Corp client tests (implementation.md Step 2a).

These encode response shapes verified against docs.perfectcorp.com and a
working third-party implementation — not assumptions. If the API changes,
these fail loudly rather than producing silently wrong scores.

All offline.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perfectcorp as pc  # noqa: E402
from dimensions import STABLE, VOLATILE  # noqa: E402


# ── auth ──────────────────────────────────────────────────────────────────
def test_auth_header_is_plain_not_bearer(monkeypatch):
    monkeypatch.setattr(pc, "API_KEY", "test-key-123")
    h = pc._headers()
    assert h["Authorization"] == "test-key-123"
    assert not h["Authorization"].lower().startswith("bearer")


def test_missing_key_raises_clearly(monkeypatch):
    monkeypatch.setattr(pc, "API_KEY", "")
    with pytest.raises(pc.PerfectCorpError, match="PERFECTCORP_API_KEY"):
        pc._headers()


# ── response envelope: `data`, not `result` ───────────────────────────────
def test_envelope_unwraps_data():
    assert pc._envelope({"status": 200, "data": {"x": 1}}, "t") == {"x": 1}


def test_envelope_raises_on_missing_data():
    """A KeyError here would be opaque. Fail with something readable."""
    with pytest.raises(pc.PerfectCorpError, match="no 'data'"):
        pc._envelope({"status": 200, "result": {"x": 1}}, "t")


# ── action sets ───────────────────────────────────────────────────────────
def test_sd_and_hd_actions_never_overlap():
    """SD and HD dst_actions cannot be mixed in one call."""
    assert not set(pc.SD_DST_ACTIONS) & set(pc.HD_DST_ACTIONS)


def test_hd_actions_are_hd_prefixed():
    assert all(a.startswith("hd_") for a in pc.HD_DST_ACTIONS)
    assert all(a.startswith("hd_") for a in pc.HD_FORENSIC_SET)


def test_forensic_set_is_a_subset_of_hd():
    assert set(pc.HD_FORENSIC_SET) <= set(pc.HD_DST_ACTIONS)


def test_forensic_set_covers_all_three_checks():
    """One HD call must serve presence, authenticity and binding."""
    s = set(pc.HD_FORENSIC_SET)
    assert {"hd_redness", "hd_radiance", "hd_oiliness"} <= s, "check 1 needs volatile channels"
    assert {"hd_pore", "hd_texture"} <= s, "check 2 needs micro-texture"
    assert {"hd_pore", "hd_texture", "hd_wrinkle", "hd_firmness"} <= s, "check 3 needs stable dims"


def test_forensic_set_is_smaller_than_full_hd():
    """Cost scales with work requested; we ask for only what we use."""
    assert len(pc.HD_FORENSIC_SET) < len(pc.HD_DST_ACTIONS)


def test_sd_actions_cover_the_dimension_split():
    for d in STABLE + VOLATILE:
        assert d in pc.SD_DST_ACTIONS, f"{d} is not a requestable SD action"


def test_hd_is_the_expensive_path():
    from fixtures import UNIT_COST
    assert UNIT_COST["skin-analysis-hd"] > UNIT_COST["skin-analysis-sd"] * 3


# ── result parsing ────────────────────────────────────────────────────────
def _result(output):
    return pc.SkinAnalysisResult(task_id="t", raw={
        "status": 200,
        "data": {"task_status": "success", "results": {"output": output}},
    })


def test_scores_parse_from_verified_shape():
    r = _result([
        {"type": "hd_pore", "region": "whole", "raw_score": 61.5, "ui_score": 62, "score": 61.5},
        {"type": "hd_redness", "region": "whole", "score": 44.0},
    ])
    assert r.scores == {"hd_pore": 61.5, "hd_redness": 44.0}


def test_per_region_scores_stay_distinct():
    """The per-zone pore breakdown is the signal check 2 depends on."""
    r = _result([
        {"type": "hd_pore", "region": "forehead", "score": 70.0},
        {"type": "hd_pore", "region": "nose", "score": 40.0},
        {"type": "hd_pore", "region": "whole", "score": 55.0},
    ])
    assert r.scores == {"hd_pore:forehead": 70.0, "hd_pore:nose": 40.0, "hd_pore": 55.0}


def test_mask_urls_is_an_array_per_entry():
    r = _result([
        {"type": "hd_pore", "region": "whole", "score": 61.5,
         "mask_urls": ["https://x/p.png"]},
        {"type": "hd_moisture", "region": "whole", "score": 50.0},
    ])
    assert r.mask_urls == {"hd_pore": "https://x/p.png"}


def test_multiple_masks_get_distinct_keys():
    r = _result([{"type": "hd_texture", "region": "whole", "score": 1.0,
                  "mask_urls": ["https://x/a.png", "https://x/b.png"]}])
    assert set(r.mask_urls) == {"hd_texture#0", "hd_texture#1"}


def test_empty_results_do_not_crash():
    assert pc.SkinAnalysisResult(task_id="t", raw={"data": {}}).scores == {}
    assert pc.SkinAnalysisResult(task_id="t", raw={}).mask_urls == {}


# ── upload ────────────────────────────────────────────────────────────────
def _upload_info():
    return {"status": 200, "data": {"files": [{
        "file_id": "abc123",
        "requests": [{"method": "PUT", "url": "https://s3/presigned",
                      "headers": {"Content-Type": "image/jpeg"}}],
    }]}}


def test_upload_in_replay_mode_skips_s3(monkeypatch):
    """Pre-signed URLs expire; replay must never attempt the PUT."""
    monkeypatch.setenv("STRATUM_API_MODE", "replay")
    assert pc.upload_bytes(_upload_info(), b"not-really-jpeg") == "abc123"


def test_upload_reads_file_id_from_data_envelope():
    assert pc._envelope(_upload_info(), "upload")["files"][0]["file_id"] == "abc123"
