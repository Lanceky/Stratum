#!/usr/bin/env bash
# Nutrient DWS — Tier 1, extraction / review / seal.
#
# Probes /analyze_build, not /build. The distinction matters:
#
#   /analyze_build  validates the instructions and reports what they would
#                   cost, without running them. Documented as free.
#   /build          runs the workflow and charges for it.
#
# The previous version of this script POSTed `{}` to /build as JSON. Three
# things were wrong with that. It poked the metered endpoint. Build is a
# multipart API, so a JSON body fails on shape before proving anything about
# the instruction set. And it treated only 401/403 as failure — a 404 from a
# wrong base URL was reported as PASS.
#
# Sending a real, valid instruction set to the free endpoint proves more and
# risks less: a 200 means the key is good AND the exact HTML->PDF instructions
# the attestation uses are ones the API accepts.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Nutrient DWS" "extraction · review · seal · Tier 1"

require_env NUTRIENT_API_KEY \
  "Create a key at https://dashboard.nutrient.io/api/ — plain bearer token, no exchange" || exit 0

BASE="${NUTRIENT_BASE_URL:-https://api.nutrient.io}"

HTML=$(mktemp --suffix=.html)
trap 'rm -f "$HTML"' EXIT
printf '<h1>STRATUM credential probe</h1>' > "$HTML"

# The same instruction shape nutrient.html_to_pdf() sends, so this checks the
# call we actually make rather than a simplified stand-in for it.
INSTRUCTIONS='{"parts":[{"file":"index.html"}]}'

body=$(curl -s --max-time 30 -w '\n%{http_code}' \
  -X POST "$BASE/analyze_build" \
  -H "Authorization: ******" \
  -F "instructions=$INSTRUCTIONS;type=application/json" \
  -F "index.html=@$HTML;type=text/html" 2>/dev/null)

code=$(tail -n1 <<< "$body")
payload=$(sed '$d' <<< "$body" | head -c 300)

case "$code" in
  200)
    pass "POST /analyze_build — key accepted, instructions valid"
    info "cost estimate: $payload"
    ;;
  401|403)
    fail "auth rejected (HTTP $code) — check NUTRIENT_API_KEY"
    ;;
  404)
    fail "no such endpoint at $BASE/analyze_build — check NUTRIENT_BASE_URL"
    ;;
  000)
    fail "no response from $BASE"
    ;;
  *)
    # Auth passed, or we would have seen 401. A 4xx here is an instruction
    # problem, which is still worth failing on: these are the instructions the
    # sealing path sends.
    fail "HTTP $code — key looks accepted but the instructions were refused"
    info "$payload"
    ;;
esac

info "Credits are unmetered for this build (sponsor grant), so /build can run"
info "live rather than from a stand-in. Confirm the 'For Evaluation Purposes"
info "Only' watermark is lifted — it would otherwise land on the attestation."
