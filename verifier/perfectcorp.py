"""
Perfect Corp API client — the sensor (context.md §7.1).

Response shapes here are verified against docs.perfectcorp.com and against a
working third-party implementation, not assumed. The two mistakes that cost
the most time if you get them wrong:

  1. Authentication is an RSA token exchange, NOT a plain API-key header.
     `PERFECTCORP_SECRET_KEY` is an RSA *public* key in base64 DER; you encrypt
     `client_id=<id>&timestamp=<epoch_ms>` under it, trade the result for a
     short-lived access token, and send that as `Bearer`. Sending the API key
     directly returns 401 InvalidApiKey. Verified against the live API.
  2. The response envelope is `data`, NOT `result` — except on the auth
     endpoint, which uses `result`. Both verified against the live API.
  3. The task payload is flat — {"src_file_id": ..., "dst_actions": [...]} —
     not a nested file_sets/actions structure.

Auth, then a four-step flow:
    POST /s2s/v1.0/client/auth                   → {result: {access_token}}
    POST /s2s/v2.0/file                          → {data: {files: [{file_id, requests:[{url, headers}]}]}}
    PUT  <requests[0].url>                       → upload bytes with the given headers
    POST /s2s/v2.0/task/skin-analysis            → {data: {task_id}}
    GET  /s2s/v2.0/task/skin-analysis/{task_id}  → {data: {task_status, results}}

Other constraints, each an hour of debugging if rediscovered late:
  - HD requires >= 1080px on the short side.
  - SD and HD dst_actions CANNOT be mixed in one call.
  - Masks are PNGs with intensity in the ALPHA channel, not RGB.
  - Rate limit 250 req / 300 s, per token AND per IP.
  - Result URLs are valid for 2 hours — download masks immediately.
  - HD costs 12-22 units per call. One HD frame per verification, never three.
  - Do not stop polling a running task: it may expire and still charge units.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from fixtures import call

BASE_URL = os.getenv("PERFECTCORP_BASE_URL", "https://yce-api-01.makeupar.com")
API_KEY = os.getenv("PERFECTCORP_API_KEY", "")
SECRET_KEY = os.getenv("PERFECTCORP_SECRET_KEY", "")
API_PREFIX = "/s2s/v2.0"
AUTH_PATH = "/s2s/v1.0/client/auth"

# The token the API issues is short-lived. Refreshing a minute early costs one
# cheap call and avoids a mid-analysis 401 that would waste the units already
# spent on an in-flight task.
TOKEN_LIFETIME_S = 7200
TOKEN_REFRESH_MARGIN_S = 60

POLL_INTERVAL_S = 3.0
POLL_TIMEOUT_S = 120

# Standard-definition concerns. See dimensions.py for which of these may be
# used for identity (spoiler: not the volatile ones).
SD_DST_ACTIONS = [
    "wrinkle", "pore", "texture", "acne", "age_spot", "dark_circle_v2",
    "eye_bag", "firmness", "moisture", "oiliness", "radiance", "redness",
    "tear_trough", "skin_type", "droopy_upper_eyelid", "droopy_lower_eyelid",
]

HD_DST_ACTIONS = [
    "hd_wrinkle", "hd_pore", "hd_texture", "hd_acne", "hd_age_spot",
    "hd_dark_circle", "hd_eye_bag", "hd_firmness", "hd_moisture",
    "hd_oiliness", "hd_radiance", "hd_redness", "hd_tear_trough",
    "hd_skin_type", "hd_droopy_upper_eyelid", "hd_droopy_lower_eyelid",
]

# The minimum HD set that serves all three checks in ONE call:
#   check 1 needs redness/radiance/oiliness (illumination-responsive)
#   check 2 needs pore/texture              (micro-texture forensics)
#   check 3 needs the stable dimensions     (identity)
# Requesting fewer actions keeps the unit cost at the low end of 12-22.
HD_FORENSIC_SET = [
    "hd_pore", "hd_texture", "hd_wrinkle", "hd_firmness", "hd_age_spot",
    "hd_redness", "hd_radiance", "hd_oiliness",
]


class PerfectCorpError(RuntimeError):
    pass


@dataclass
class SkinAnalysisResult:
    task_id: str
    raw: dict[str, Any]
    masks: dict[str, bytes] = field(default_factory=dict)

    @property
    def _output(self) -> list[dict]:
        return (self.raw.get("data", {}).get("results") or {}).get("output") or []

    @property
    def synthetic(self) -> bool:
        """
        True when this result came from a seeded stand-in, not the sensor.

        `seed_fixtures.py` stamps every placeholder with `_synthetic`, but
        until now nothing read it, so a fabricated face produced eleven
        plausible scores that entered the checks indistinguishably from
        measured ones. Surfaced here so the decision layer can refuse to let a
        stand-in reach PASS.
        """
        return bool(self.raw.get("_synthetic"))

    @property
    def scores(self) -> dict[str, float]:
        """
        Flatten to {concern: score}. Per-region entries are keyed
        "<type>:<region>" so hd_pore on forehead and cheek stay distinct —
        the per-zone breakdown is exactly what check 2 relies on.
        """
        out: dict[str, float] = {}
        for item in self._output:
            kind = item.get("type")
            if not kind:
                continue
            region = item.get("region")
            key = f"{kind}:{region}" if region and region != "whole" else kind
            score = item.get("score", item.get("raw_score", item.get("ui_score")))
            if score is not None:
                out[key] = float(score)
        return out

    @property
    def mask_urls(self) -> dict[str, str]:
        """{concern: url}. `mask_urls` is an array per output entry."""
        out: dict[str, str] = {}
        for item in self._output:
            kind = item.get("type")
            urls = item.get("mask_urls") or []
            if not kind or not urls:
                continue
            region = item.get("region")
            for i, url in enumerate(urls):
                key = kind
                if region and region != "whole":
                    key = f"{key}:{region}"
                if len(urls) > 1:
                    key = f"{key}#{i}"
                out[key] = url
        return out

    @property
    def bundle_url(self) -> str | None:
        """Only present when format=zip."""
        return (self.raw.get("data", {}).get("results") or {}).get("url")


_token: str | None = None
_token_expires_at = 0.0
_token_lock = threading.Lock()


def _id_token() -> str:
    """
    Prove possession of the API key without ever putting it on the wire.

    `PERFECTCORP_SECRET_KEY` is an RSA *public* key, which reads backwards until
    you notice what it is for: only Perfect Corp holds the private half, so a
    payload encrypted under it is readable by them alone. The timestamp is what
    stops a captured id_token being replayed, so it must be milliseconds and it
    must be current — a clock more than a few minutes out will fail auth with a
    message that blames the key.
    """
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    key = load_der_public_key(base64.b64decode(SECRET_KEY))
    payload = f"client_id={API_KEY}&timestamp={int(time.time() * 1000)}"
    return base64.b64encode(key.encrypt(payload.encode(), padding.PKCS1v15())).decode()


def access_token(force: bool = False) -> str:
    """
    A bearer token, minted on demand and cached until it is nearly expired.

    Locked because the capture pipeline may call this from more than one thread,
    and two simultaneous refreshes would burn a request against a rate limit
    that counts per token *and* per IP.
    """
    global _token, _token_expires_at
    if not API_KEY or not SECRET_KEY:
        raise PerfectCorpError(
            "PERFECTCORP_API_KEY and PERFECTCORP_SECRET_KEY must both be set. "
            "The secret is the base64 RSA public key from the API console, not "
            "a password. Run in replay mode if you have neither."
        )
    with _token_lock:
        if not force and _token and time.time() < _token_expires_at:
            return _token
        with _client() as http:
            r = http.post(BASE_URL + AUTH_PATH,
                          json={"client_id": API_KEY, "id_token": _id_token()},
                          headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            raise PerfectCorpError(
                f"auth failed ({r.status_code}): {r.text[:200]}. Check the key is "
                f"Server-to-Server, not Camera Kit, and that the system clock is correct."
            )
        # This endpoint answers under `result`; every other one uses `data`.
        token = (r.json().get("result") or {}).get("access_token")
        if not token:
            raise PerfectCorpError(f"auth returned no access_token: {r.text[:200]}")
        _token, _token_expires_at = token, time.time() + TOKEN_LIFETIME_S - TOKEN_REFRESH_MARGIN_S
        return token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True)


def _envelope(body: dict, what: str) -> dict:
    """Unwrap `data`, raising a legible error rather than a KeyError."""
    if "data" not in body:
        raise PerfectCorpError(f"{what}: no 'data' in response: {str(body)[:300]}")
    return body["data"]


# ── Step 1 of 4: request a pre-signed upload URL ──────────────────────────
def request_upload(file_name: str, file_size: int, content_type: str = "image/jpeg") -> dict:
    payload = {
        "files": [{"content_type": content_type, "file_name": file_name, "file_size": file_size}]
    }

    def live() -> dict:
        with _client() as c:
            r = c.post(f"{BASE_URL}{API_PREFIX}/file", headers=_headers(), json=payload)
            if r.status_code >= 400:
                raise PerfectCorpError(f"upload request failed {r.status_code}: {r.text[:300]}")
            return r.json()

    return call("file-upload", payload, live)


# ── Step 2 of 4: PUT the bytes to S3 ──────────────────────────────────────
def upload_bytes(upload_info: dict, data: bytes) -> str:
    """Upload the raw bytes and return the file_id used to create the task."""
    entry = _envelope(upload_info, "upload")["files"][0]
    file_id = entry["file_id"]

    # In replay the pre-signed URL is long expired and the task response is
    # already recorded, so uploading is a no-op.
    if os.getenv("STRATUM_API_MODE", "replay") == "replay":
        return file_id

    slot = entry["requests"][0]
    with _client() as c:
        # The returned headers must be sent verbatim or S3 rejects the PUT.
        r = c.put(slot["url"], content=data, headers=slot.get("headers", {}))
        if r.status_code >= 400:
            raise PerfectCorpError(f"S3 upload failed {r.status_code}: {r.text[:200]}")
    return file_id


# ── Steps 3 and 4: create task, then poll ─────────────────────────────────
def _run_task(kind: Literal["skin-analysis", "face-attr-analysis"],
              file_id: str, dst_actions: list[str], op: str) -> dict:
    payload = {
        "src_file_id": file_id,
        "dst_actions": dst_actions,
        # json returns scores and mask_urls inline; zip returns a bundle URL.
        # Inline is simpler and avoids a second download + unzip step.
        "format": "json",
    }

    def live() -> dict:
        with _client() as c:
            r = c.post(f"{BASE_URL}{API_PREFIX}/task/{kind}", headers=_headers(), json=payload)
            if r.status_code == 429:
                raise PerfectCorpError("rate limited: 250 req/300s, per token AND per IP")
            if r.status_code >= 400:
                raise PerfectCorpError(f"task create failed {r.status_code}: {r.text[:300]}")
            task_id = _envelope(r.json(), "task create")["task_id"]

            deadline = time.time() + POLL_TIMEOUT_S
            while time.time() < deadline:
                time.sleep(POLL_INTERVAL_S)
                p = c.get(f"{BASE_URL}{API_PREFIX}/task/{kind}/{task_id}", headers=_headers())
                if p.status_code >= 400:
                    raise PerfectCorpError(f"poll failed {p.status_code}: {p.text[:200]}")
                body = p.json()
                data = _envelope(body, "poll")
                status = data.get("task_status") or data.get("status")
                if status == "success":
                    return body
                if status == "error":
                    raise PerfectCorpError(
                        f"task {task_id} failed: {data.get('error')} "
                        f"{data.get('error_message', '')}"
                    )
            # Abandoning a running task can still consume units, so say so.
            raise PerfectCorpError(
                f"task {task_id} did not resolve in {POLL_TIMEOUT_S}s; units may still be charged"
            )

    return call(op, payload, live)


def skin_analysis(file_id: str, hd: bool = True,
                  dst_actions: list[str] | None = None) -> SkinAnalysisResult:
    """
    Run skin analysis on an uploaded file.

    hd=True is the forensic path check 2 depends on, and costs 12-22 units.
    Defaults to HD_FORENSIC_SET rather than every HD action, because unit cost
    scales with the work requested and we only need eight of them.
    """
    if dst_actions is None:
        dst_actions = HD_FORENSIC_SET if hd else SD_DST_ACTIONS
    op = "skin-analysis-hd" if hd else "skin-analysis-sd"
    raw = _run_task("skin-analysis", file_id, dst_actions, op)
    task_id = raw.get("data", {}).get("task_id", "replayed")
    return SkinAnalysisResult(task_id=task_id, raw=raw)


def face_attributes(file_id: str) -> dict:
    """
    50+ facial attributes and 11 facial ratios.

    This geometry — not the skin scores — is the primary identity signal for
    check 3. See dimensions.py for why.
    """
    return _run_task("face-attr-analysis", file_id,
                     ["face_attribute", "face_ratio"], "face-attr-analysis")


def download_masks(result: SkinAnalysisResult, dest: Path) -> dict[str, Path]:
    """
    Fetch mask PNGs. Result URLs are valid for 2 hours, so this runs at once.

    Masks are cached on disk by concern name; in replay we read that cache
    rather than the network.
    """
    dest.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    replay = os.getenv("STRATUM_API_MODE", "replay") == "replay"

    for name, url in result.mask_urls.items():
        safe = name.replace(":", "_").replace("#", "_")
        p = dest / f"{safe}.png"
        if p.exists():
            paths[name] = p
            continue
        if replay:
            # A file:// URL means a seeded synthetic fixture.
            if url.startswith("file://"):
                src = Path(url[7:])
                if src.exists():
                    paths[name] = src
            continue
        with _client() as c:
            r = c.get(url)
            if r.status_code < 400:
                p.write_bytes(r.content)
                result.masks[name] = r.content
                paths[name] = p
    return paths
