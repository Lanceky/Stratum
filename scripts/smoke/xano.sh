#!/usr/bin/env bash
# Xano — Tier 0, system of record.
#
# Three things are checked, because they fail independently: the instance is
# reachable, the API group exists (it does not until you create one), and the
# Metadata API token is actually accepted. A set-but-wrong token is the failure
# mode that wastes the most time, so it is exercised rather than assumed.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Xano" "system of record · Tier 0 · BLOCKING"

require_env XANO_INSTANCE_URL \
  "Create a free Essential instance with the DevPost coupon, then copy the instance URL" || exit 0

# A scheme-less host is the most common paste error: curl falls back to http://
# and the instance answers 308 to https, which looks like an outage rather than
# a typo. Catch it here and say so plainly.
if [[ "$XANO_INSTANCE_URL" != http?(s)://* ]]; then
  fail "XANO_INSTANCE_URL has no scheme — prefix it with https://"
  info "  currently: $XANO_INSTANCE_URL"
  exit 0
fi

INSTANCE="${XANO_INSTANCE_URL%/}"

# 308 is a normal answer from a Xano instance root, not a fault.
code=$(http_status "$INSTANCE")
check_status "200,301,302,307,308,404" "$code" "instance reachable"

if [[ -n "${XANO_AUTH_TOKEN:-}" ]]; then
  # Metadata API is instance-scoped: <instance>/api:meta, not app.xano.com.
  body=$(curl -sS -H "Authorization: Bearer $XANO_AUTH_TOKEN" \
    "$INSTANCE/api:meta/workspace" 2>/dev/null)
  summary=$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    w = json.load(sys.stdin)
except Exception:
    sys.exit()
if not isinstance(w, list):
    sys.exit()
names = ", ".join(str(x.get("id")) + ":" + str(x.get("name")) for x in w)
push  = any((x.get("preferences") or {}).get("allow_push") for x in w)
print(names + "\t" + ("push" if push else "nopush"))' 2>/dev/null)
  names="${summary%%$'\t'*}"
  push="${summary##*$'\t'}"
  if [[ -n "$names" ]]; then
    pass "GET /api:meta/workspace — token accepted (workspace $names)"
    # Step 11 pushes the frontend with `xano static_host build push`, which the
    # workspace refuses unless CLI push is switched on. Silent until it matters.
    if [[ "$push" == "push" ]]; then
      pass "workspace allows CLI push"
    else
      wait_ "workspace has allow_push disabled — Step 11 'xano ... push' will fail"
      info "Enable it: workspace settings > Enable CLI/MCP push"
    fi
  else
    fail "Metadata API token rejected"
    info "$(printf '%s' "$body" | head -c 160)"
    info "Regenerate at: profile icon > Instances > gear > Metadata API > Manage Access Tokens"
  fi
else
  wait_ "XANO_AUTH_TOKEN not set — Metadata API token, needed to push schema"
fi

if [[ -n "${XANO_API_GROUP_BASE:-}" ]]; then
  gcode=$(http_status "$XANO_API_GROUP_BASE/health")
  if [[ "$gcode" == "200" ]]; then
    pass "GET /health — API group live"
  else
    wait_ "GET /health returned $gcode — create the health endpoint in Step 3"
  fi
else
  wait_ "XANO_API_GROUP_BASE not set — Library > APIs > your group, copy the Base URL"
fi

if command -v xano >/dev/null 2>&1; then
  pass "xano CLI installed ($(xano --version 2>/dev/null | head -1))"
else
  wait_ "xano CLI not installed — needed for 'xano static_host build push' in Step 11"
  info "npm i -g @xano/cli && xano login"
fi
