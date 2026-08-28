#!/usr/bin/env bash
# Local toolchain — verifies the machine can actually build this project.
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

header "Local toolchain" "no credentials required"

check_bin() {
  local bin="$1" why="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    pass "$bin — $($bin --version 2>&1 | head -1 | cut -c1-40)"
  else
    fail "$bin missing — $why"
  fi
}

check_bin node   "frontend build (Step 2c) and MCP servers (Step 8)"
check_bin npm    "frontend and Nutrient Viewer"
check_bin python3 "verifier sidecar (Steps 4-7)"
check_bin curl   "every smoke test"
check_bin git    "obviously"

if command -v dig >/dev/null 2>&1; then
  pass "dig — needed for the DNS attestation demo beat (Step 10a)"
else
  fail "dig missing — install dnsutils/bind-utils; this is a DEMO BEAT, not optional"
fi

if [[ -f "$REPO_ROOT/.env" ]]; then
  pass ".env exists"
else
  wait_ ".env not created — run: cp .env.example .env"
fi

# Guard against the single worst mistake in this repo.
if git -C "$REPO_ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
  fail "🚨 .env IS TRACKED BY GIT — run: git rm --cached .env"
else
  pass ".env is not tracked by git"
fi
