#!/usr/bin/env bash
# Doctavian — Tier 2, attestation certificate.
# ⚠️ Base URL and endpoint paths are UNVERIFIED (developer portal is a JS app
#    behind auth). Download the OpenAPI spec the moment credentials arrive.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Doctavian" "attestation certificate · Tier 2 · CUTTABLE"

if [[ -z "${DOCTAVIAN_CLIENT_ID:-}" ]]; then
  wait_ "credentials not issued yet"
  info "⚠️ Issued MANUALLY. Email hello@doctavian.com in the first hour of Day 0."
  info "   Cut deadline: if nothing arrives by end of Day 4, drop it cleanly."
  info "   Nutrient already covers sealing, so this is the safest thing to lose."
  exit 0
fi

if ! require_env DOCTAVIAN_BASE_URL \
  "Get this from the OpenAPI spec at developers.doctavian.com/openapi/latest/resources"; then
  fail "credentials arrived but base URL is unknown — download the OpenAPI spec FIRST"
  exit 0
fi

code=$(http_status -X POST "$DOCTAVIAN_BASE_URL/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$DOCTAVIAN_CLIENT_ID&client_secret=${DOCTAVIAN_CLIENT_SECRET:-}")

check_status "200,201" "$code" "OAuth2 client_credentials token"
info "Templates are native Word/Excel/PowerPoint with embedded expressions — no DSL to learn."
