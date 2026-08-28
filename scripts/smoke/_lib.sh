#!/usr/bin/env bash
# Shared helpers for STRATUM smoke tests.
# Every smoke script MUST exit 0 with a clear status line — never fail silently.
#
# Status vocabulary (grep-able, used by `make smoke`):
#   PASS  — credential present, endpoint returned the expected status
#   WAIT  — credential not yet issued (expected on Day 0, not a failure)
#   FAIL  — credential present but the endpoint rejected us (needs attention)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Load .env if present, without clobbering already-exported vars.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

BOLD='\033[1m'; RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; DIM='\033[2m'; NC='\033[0m'

pass() { echo -e "  ${GRN}PASS${NC}  $*"; }
wait_() { echo -e "  ${YEL}WAIT${NC}  $*"; }
fail() { echo -e "  ${RED}FAIL${NC}  $*"; }
info() { echo -e "  ${DIM}      $*${NC}"; }

header() { echo -e "\n${BOLD}▸ $1${NC}  ${DIM}$2${NC}"; }

# require_env VAR_NAME "how to get it"
# Returns 1 (and prints WAIT) if the variable is empty or unset.
require_env() {
  local var="$1" hint="${2:-}"
  if [[ -z "${!var:-}" ]]; then
    wait_ "$var is not set"
    [[ -n "$hint" ]] && info "$hint"
    return 1
  fi
  return 0
}

# http_status <curl args...>  → prints the HTTP status code only
http_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@" 2>/dev/null || echo "000"
}

# http_body <curl args...> → prints response body (truncated)
http_body() {
  curl -s --max-time 20 "$@" 2>/dev/null | head -c 400
}

# check_status <expected-csv> <actual> <label>
check_status() {
  local expected="$1" actual="$2" label="$3"
  if [[ ",$expected," == *",$actual,"* ]]; then
    pass "$label (HTTP $actual)"
    return 0
  elif [[ "$actual" == "000" ]]; then
    fail "$label — no response (network, DNS, or timeout)"
    return 1
  else
    fail "$label — HTTP $actual, expected one of [$expected]"
    return 1
  fi
}
