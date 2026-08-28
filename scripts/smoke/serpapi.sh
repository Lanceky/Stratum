#!/usr/bin/env bash
# SerpApi — Tier 2, live-world corroboration.
# Uses the free /account endpoint so this costs ZERO searches.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "SerpApi" "live-world corroboration · Tier 2"

require_env SERPAPI_KEY "Free key at https://serpapi.com — then email alaa@serpapi.com for hackathon credits" || exit 0

# /account does not consume search quota.
body=$(http_body "https://serpapi.com/account?api_key=$SERPAPI_KEY")
code=$(http_status "https://serpapi.com/account?api_key=$SERPAPI_KEY")

if check_status "200" "$code" "GET /account — key valid"; then
  remaining=$(printf '%s' "$body" | python3 -c \
    'import sys,json;d=json.load(sys.stdin);print(d.get("total_searches_left","?"))' 2>/dev/null || echo "?")
  info "Searches remaining this month: $remaining"
  if [[ "$remaining" != "?" && "$remaining" -lt 100 ]] 2>/dev/null; then
    fail "under 100 searches left — email alaa@serpapi.com now"
  fi
fi

info "Identical queries within 1 hour are CACHED, FREE, and do not count against quota."
info "Warm the cache before recording the demo video (Step 12c)."
