#!/usr/bin/env bash
# Foxit — Tier 0, the agent boundary.
# TWO products, TWO different auth schemes. This is the most common Foxit mistake:
#   PDF Services → client_id / client_secret HEADERS (not OAuth)
#   eSign        → OAuth2 bearer token, different base URL
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Foxit" "the agent boundary · Tier 0 · BLOCKING"

# ── PDF Services (self-serve) ──────────────────────────────────────────────
if require_env FOXIT_CLIENT_ID "Sign up at https://app.developer-api.foxit.com/sign-up" \
  && require_env FOXIT_CLIENT_SECRET ""; then
  BASE="${FOXIT_PDF_BASE_URL:-https://na1.fusion.foxit.com/pdf-services/api}"
  code=$(http_status "$BASE/documents/enable" \
    -H "client_id: $FOXIT_CLIENT_ID" \
    -H "client_secret: $FOXIT_CLIENT_SECRET")
  if [[ "$code" == "401" || "$code" == "403" ]]; then
    fail "PDF Services auth rejected (HTTP $code) — these go in HEADERS, not as OAuth"
  elif [[ "$code" == "000" ]]; then
    fail "PDF Services — no response"
  else
    pass "PDF Services — credentials accepted (HTTP $code)"
  fi
fi

# ── eSign (NOT self-serve) ─────────────────────────────────────────────────
if [[ -z "${FOXIT_ESIGN_CLIENT_ID:-}" ]]; then
  wait_ "eSign credentials not issued yet"
  info "⚠️ There is NO self-serve eSign sandbox. The API menu only appears after purchase."
  info "   Email theodore_castro@foxitsoftware.com — this is a Day-0 blocker for Step 8."
else
  EBASE="${FOXIT_ESIGN_BASE_URL:-https://na1.foxitesign.foxit.com/api}"
  code=$(http_status -X POST "$EBASE/oauth2/token" \
    -H "Content-Type: application/json" \
    -d "{\"client_id\":\"$FOXIT_ESIGN_CLIENT_ID\",\"client_secret\":\"${FOXIT_ESIGN_CLIENT_SECRET:-}\",\"grant_type\":\"client_credentials\"}")
  check_status "200,201" "$code" "eSign OAuth2 token"
fi

# ── The MCP server the agent will actually use (Step 8) ────────────────────
if command -v npx >/dev/null 2>&1; then
  pass "npx available — @foxitsoftware/foxit-pdf-api-mcp-server can be launched in Step 8"
else
  fail "npx not found — needed for the Foxit MCP server"
fi
