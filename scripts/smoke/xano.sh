#!/usr/bin/env bash
# Xano — Tier 0, system of record.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Xano" "system of record · Tier 0 · BLOCKING"

require_env XANO_INSTANCE_URL \
  "Create a free Essential instance with the DevPost coupon, then copy the instance URL" || exit 0

# The instance root should answer even before any API group exists.
code=$(http_status "$XANO_INSTANCE_URL")
check_status "200,301,302,404" "$code" "instance reachable"

if [[ -n "${XANO_API_GROUP_BASE:-}" ]]; then
  gcode=$(http_status "$XANO_API_GROUP_BASE/health")
  if [[ "$gcode" == "200" ]]; then
    pass "GET /health — API group live"
  else
    wait_ "GET /health returned $gcode — create the health endpoint in Step 3"
  fi
else
  wait_ "XANO_API_GROUP_BASE not set — created in Step 3"
fi

if command -v xano >/dev/null 2>&1; then
  pass "xano CLI installed ($(xano --version 2>/dev/null | head -1))"
else
  wait_ "xano CLI not installed — needed for 'xano static_host build push' in Step 11"
  info "npm i -g @xano/cli && xano login"
fi
