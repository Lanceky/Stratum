#!/usr/bin/env bash
# Nutrient DWS — Tier 1, extraction / review / seal.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Nutrient DWS" "extraction · review · seal · Tier 1"

require_env NUTRIENT_API_KEY \
  "Create an account at https://dashboard.nutrient.io/api/ — hackathon campaign creds are in the DevPost brief" || exit 0

BASE="${NUTRIENT_BASE_URL:-https://api.nutrient.io}"

# /build with an empty instruction set is the cheapest way to prove auth works.
# A malformed body returns 4xx *after* auth, which still tells us the key is good.
code=$(http_status -X POST "$BASE/build" \
  -H "Authorization: Bearer $NUTRIENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}')

if [[ "$code" == "401" || "$code" == "403" ]]; then
  fail "auth rejected (HTTP $code) — check the key"
elif [[ "$code" == "000" ]]; then
  fail "no response from $BASE"
else
  pass "POST /build — key accepted (HTTP $code; a 4xx here means auth passed, body was empty)"
fi

info "⚠️ Free tier is 50 credits/MONTH. A digital signature costs 10, OCR 2, extraction 3."
info "   Email douglas@nutrient.io for a top-up AND to remove the 'For Evaluation Purposes"
info "   Only' watermark — it will otherwise appear in the demo video."
