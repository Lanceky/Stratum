#!/usr/bin/env bash
# Perfect Corp — Tier 0, the sensor.
# Verifies the API key is accepted by the file-upload endpoint (step 1 of 4).
# Costs 0 units: we request an upload URL but never create an analysis task.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Perfect Corp" "the sensor · Tier 0 · BLOCKING"

require_env PERFECTCORP_API_KEY \
  "Register at https://yce.makeupar.com/ai-api, then redeem the API World code at
       https://yce.perfectcorp.com/api-console/en/redeem-code/ (needs a real browser)" || exit 0

BASE="${PERFECTCORP_BASE_URL:-https://yce-api-01.makeupar.com}"

# Step 1 of the 4-step flow: request a pre-signed S3 upload URL.
# Auth is a PLAIN Authorization header — no Bearer prefix, no token exchange.
code=$(http_status -X POST "$BASE/s2s/v2.0/file" \
  -H "Authorization: $PERFECTCORP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"files":[{"content_type":"image/jpeg","file_name":"smoke.jpg","file_size":1024}]}')

check_status "200,201" "$code" "POST /s2s/v2.0/file — upload URL request"

if [[ "$code" == "401" || "$code" == "403" ]]; then
  info "Key rejected. Check for whitespace, and confirm the key is Server-to-Server (s2s), not Camera Kit."
fi
if [[ "$code" == "429" ]]; then
  info "Rate limited: 250 req / 300 s, enforced per token AND per IP."
fi

info "Reminder: HD costs 12-22 units/call. Keep STRATUM_API_MODE=replay in dev."
