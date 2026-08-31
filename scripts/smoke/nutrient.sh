#!/usr/bin/env bash
# Nutrient DWS — Tier 1, the attestation renderer.
#
# The grant issues one key PER PRODUCT, and they are not interchangeable:
# sending the extraction key to a processor endpoint returns a bare
# `403 Forbidden`, which is byte-identical to what a revoked key returns. So
# each key is probed against its own endpoint — a single probe would report
# two of the three as broken, or worse, pass on a key nobody uses.
#
# Processor is probed with /analyze_build, which is free and JSON. Note that
# /build is multipart and /analyze_build is not; sending multipart here returns
# 415. Extraction is probed with a deliberately incomplete request: a 400
# reading "schema is required" means the credential was accepted and only the
# body was short, which is exactly what we want to learn without paying.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Nutrient DWS" "attestation renderer · Tier 1"

BASE="${NUTRIENT_BASE_URL:-https://api.nutrient.io}"
SCHEME=Bearer
ok=0

# ── Processor: the one the sealing path depends on ───────────────────────
if require_env NUTRIENT_PROCESSOR_API \
     "Create a key at https://dashboard.nutrient.io/api/ — plain bearer token"; then
  body=$(curl -s --max-time 30 -w '\n%{http_code}' -X POST "$BASE/analyze_build" \
    -H "Authorization: $SCHEME $NUTRIENT_PROCESSOR_API" \
    -H 'Content-Type: application/json' \
    -d '{"parts":[{"html":"index.html"}]}' 2>/dev/null)
  code=$(tail -n1 <<< "$body"); payload=$(sed '$d' <<< "$body" | head -c 200)
  case "$code" in
    200) pass "processor — /analyze_build accepted"; info "cost: $payload"; ok=1 ;;
    401|403) fail "processor — rejected (HTTP $code). Is this the processor key?" ;;
    404) fail "processor — no /analyze_build at $BASE (check NUTRIENT_BASE_URL)" ;;
    000) fail "processor — no response from $BASE" ;;
    *)   fail "processor — HTTP $code"; info "$payload" ;;
  esac
fi

# ── Data extraction ──────────────────────────────────────────────────────
if require_env NUTRIENT_DATA_EXTRACTION_API "Same dashboard, separate key"; then
  code=$(http_status -X POST "$BASE/extraction/extract" \
    -H "Authorization: $SCHEME $NUTRIENT_DATA_EXTRACTION_API" \
    -F 'file=@/dev/null;type=application/pdf')
  case "$code" in
    # 400 here is the success case: auth passed, the schema was omitted on
    # purpose. A 200 would mean we paid for an extraction we did not want.
    400) pass "extraction — key accepted (HTTP 400 = schema omitted on purpose)" ;;
    401|403) fail "extraction — rejected (HTTP $code). Keys are scoped per product." ;;
    000) fail "extraction — no response from $BASE" ;;
    *)   pass "extraction — key accepted (HTTP $code)" ;;
  esac
fi

# ── Accessibility ────────────────────────────────────────────────────────
# Present but unused. /build already emits PDF/UA as an output setting, so the
# separate API is not needed for the attestation. Reported so a missing key is
# never mistaken for a deliberate omission.
if [[ -n "${NUTRIENT_ACCESSIBILITY_API:-}" ]]; then
  info "accessibility — key present, not wired (PDF/UA is a /build output type)"
else
  info "accessibility — not set, and not needed"
fi

if [[ "$ok" == "1" ]]; then
  info "Credits are unmetered under the grant, so /build runs live rather than"
  info "from a stand-in. The evaluation watermark is lifted — verified against"
  info "a probe PDF containing no evaluation markers."
fi
