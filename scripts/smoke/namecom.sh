#!/usr/bin/env bash
# name.com — Tier 1, verifiable origin.
# Hits the sandbox /hello endpoint, then verifies the checkAvailability call
# that the typosquat sweep depends on (Step 10).
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "name.com" "verifiable origin · Tier 1"

BASE="${NAMECOM_BASE_URL:-https://api.dev.name.com}"

if require_env NAMECOM_TEST_TOKEN \
  "Account → API settings → generate BOTH a production token and a Development/Test token" \
  && require_env NAMECOM_TEST_USERNAME "Sandbox username is your username with a '-test' suffix"; then

  code=$(http_status -u "$NAMECOM_TEST_USERNAME:$NAMECOM_TEST_TOKEN" "$BASE/core/v1/hello")
  check_status "200" "$code" "GET /core/v1/hello — sandbox auth"

  # The typosquat sweep (Step 10a) depends on this exact call shape.
  # NOTE: the ':' must NOT be URL-encoded. Some HTTP clients turn it into %3A
  # and the request silently fails. curl leaves it alone, which is why we use it here.
  avail=$(http_status -u "$NAMECOM_TEST_USERNAME:$NAMECOM_TEST_TOKEN" \
    -X POST "$BASE/core/v1/domains:checkAvailability" \
    -H "Content-Type: application/json" \
    -d '{"domainNames":["stratum.id","getstratum.com","stratumgate.com"]}')
  check_status "200" "$avail" "POST /core/v1/domains:checkAvailability — typosquat sweep primitive"

  info "Sandbox has \$100k auto-refreshing test credit. But sandbox DNS does NOT publicly resolve —"
  info "the live \`dig\` demo beat needs one real production domain (set STRATUM_ATTEST_DOMAIN)."
else
  exit 0
fi

if [[ -z "${NAMECOM_TOKEN:-}" ]]; then
  wait_ "NAMECOM_TOKEN (production) not set — needed for the live dig on Day 4"
fi
