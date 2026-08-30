#!/usr/bin/env bash
# Doctavian — Tier 2, attestation certificate.
#
# Auth was established against the live demo environment, because context.md
# (OAuth2 client credentials) and the credential email ("pass it in the
# x-api-key header") each described half of it and neither works alone.
#
# The OpenAPI document says:      security: [ {bearerAuth: [], apiKeyHeader: []} ]
# Both schemes sit in the SAME object, which in OpenAPI means AND, not OR.
#   X-Api-Key alone     -> 401 "Authorization header is missing"
#   bearer alone        -> 401 "ApiKeyNotFound"
#   X-Api-Key + bearer  -> 401 "Google token is invalid or expired"
#
# The bearer is an end-user Google token. /public/v1/auth/{provider}/token is a
# real OAuth2 endpoint but accepts only authorization_code and refresh_token —
# client_credentials is rejected — so it cannot be automated. Backends instead
# send X-Service-Authorization, an AES-encrypted JWT the server issues.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Doctavian" "attestation certificate · Tier 2 · CUTTABLE"

: "${DOCTAVIAN_BASE_URL:=https://demo.api.doctavian.com}"

if [[ -z "${DOCTAVIAN_API_KEY:-}" ]]; then
  wait_ "API key not set"
  info "Issued manually. Set DOCTAVIAN_API_KEY from the credential email."
  exit 0
fi

# Cheapest possible probe: does the gateway know this key at all? A key it does
# not recognise fails differently from one that is merely missing an identity,
# and telling those apart is the whole value of this check.
probe=$(curl -sS -m 20 -o /tmp/doctavian_probe.json -w '%{http_code}' \
  -H "X-Api-Key: $DOCTAVIAN_API_KEY" \
  "$DOCTAVIAN_BASE_URL/v1/documents/template/list" 2>/dev/null || echo 000)
inner=$(python3 -c "
import json,sys
try: d=json.load(open('/tmp/doctavian_probe.json'))
except Exception: print(''); raise SystemExit
e=d.get('error') or {}
print(((e.get('innerErrors') or [{}])[0]).get('message',''))
" 2>/dev/null)

case "$inner" in
  *ApiKeyNotFound*|*"API key"*)
    fail "gateway does not recognise DOCTAVIAN_API_KEY"
    exit 0 ;;
esac

if [[ "$probe" == "000" ]]; then
  fail "no response from $DOCTAVIAN_BASE_URL"
  exit 0
fi

pass "API key accepted by the gateway"

if [[ -z "${DOCTAVIAN_SERVICE_TOKEN:-}" ]]; then
  wait_ "DOCTAVIAN_SERVICE_TOKEN not set — cannot make an authorised call"
  info "The key alone returns: ${inner:-401 Unauthorized}"
  info "A caller identity is required in addition to the key. Get the service"
  info "token from the Postman collection in the credential email, or from"
  info "demo.portal.doctavian.com. It looks like an opaque 'CfDJ8...' blob."
  info "/v1/common/service/token is NOT routed on demo (404), so it cannot be"
  info "minted from here."
  exit 0
fi

code=$(http_status -H "X-Api-Key: $DOCTAVIAN_API_KEY" \
  -H "X-Service-Authorization: $DOCTAVIAN_SERVICE_TOKEN" \
  "$DOCTAVIAN_BASE_URL/v1/documents/template/list")

check_status "200" "$code" "authorised call (template/list)"
info "Templates are native Word/Excel with embedded expressions — no DSL."
info "Generation emits PDF/A-3a directly (ConformanceLevel PdfA3a is the"
info "default), so the archival conversion step is redundant for this document."
