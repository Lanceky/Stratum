#!/usr/bin/env bash
# Perfect Corp — Tier 0, the sensor.
#
# Auth is an RSA token exchange, not a plain API-key header. PERFECTCORP_SECRET_KEY
# is an RSA *public* key in base64 DER: you encrypt "client_id=<id>&timestamp=<ms>"
# under it, trade the ciphertext for a short-lived access token, and send that as
# a bearer. Sending the API key directly returns 401 InvalidApiKey.
#
# Costs 0 units: we mint a token and request an upload URL, but never create an
# analysis task.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Perfect Corp" "the sensor · Tier 0 · BLOCKING"

require_env PERFECTCORP_API_KEY \
  "Register at https://yce.makeupar.com/ai-api, then redeem the API World code at
       https://yce.perfectcorp.com/api-console/en/redeem-code/ (needs a real browser)" || exit 0

require_env PERFECTCORP_SECRET_KEY \
  "The base64 RSA public key shown next to the API key in the console. Not a password." || exit 0

BASE="${PERFECTCORP_BASE_URL:-https://yce-api-01.makeupar.com}"

if ! command -v openssl >/dev/null 2>&1; then
  wait_ "openssl not installed — cannot build the id_token"
  exit 0
fi

tmp=$(mktemp -d) && trap 'rm -rf "$tmp"' EXIT
{ echo "-----BEGIN PUBLIC KEY-----"
  printf '%s' "$PERFECTCORP_SECRET_KEY" | tr -d '[:space:]' | fold -w 64
  echo
  echo "-----END PUBLIC KEY-----"; } > "$tmp/pub.pem"

# Milliseconds, and current: the timestamp is what stops a captured id_token
# being replayed, so a badly wrong clock fails auth with a message blaming the key.
ts=$(( $(date +%s) * 1000 ))
id_token=$(printf 'client_id=%s&timestamp=%s' "$PERFECTCORP_API_KEY" "$ts" \
  | openssl pkeyutl -encrypt -pubin -inkey "$tmp/pub.pem" \
      -pkeyopt rsa_padding_mode:pkcs1 2>/dev/null | openssl base64 -A)

if [[ -z "$id_token" ]]; then
  fail "could not encrypt the id_token — is PERFECTCORP_SECRET_KEY the base64 DER public key?"
  exit 0
fi

auth=$(curl -sS -X POST "$BASE/s2s/v1.0/client/auth" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"$PERFECTCORP_API_KEY\",\"id_token\":\"$id_token\"}" 2>/dev/null)

# This endpoint answers under `result`; every other one uses `data`.
token=$(printf '%s' "$auth" | python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("result") or {}).get("access_token",""))' 2>/dev/null)

if [[ -z "$token" ]]; then
  fail "POST /s2s/v1.0/client/auth — no access_token"
  info "$(printf '%s' "$auth" | head -c 200)"
  info "Confirm the key is Server-to-Server (s2s), not Camera Kit, and check for whitespace."
  exit 0
fi
pass "POST /s2s/v1.0/client/auth — access token minted"

code=$(http_status -X POST "$BASE/s2s/v2.0/file" \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  -d '{"files":[{"content_type":"image/jpeg","file_name":"smoke.jpg","file_size":1024}]}')

check_status "200,201" "$code" "POST /s2s/v2.0/file — upload URL request"

if [[ "$code" == "401" || "$code" == "403" ]]; then
  info "Token rejected downstream. The token is a bearer — 'Authorization: <token>' without"
  info "the Bearer prefix returns 401 InvalidApiKey."
fi
if [[ "$code" == "429" ]]; then
  info "Rate limited: 250 req / 300 s, enforced per token AND per IP."
fi

info "Reminder: HD costs 12-22 units/call. Keep STRATUM_API_MODE=replay in dev."
