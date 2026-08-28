#!/usr/bin/env bash
# Runs every smoke test and prints a summary.
# ALWAYS exits 0 for WAIT states — missing credentials on Day 0 are expected,
# not a build failure. Exits 1 only if a credential is present but REJECTED.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'; DIM='\033[2m'; RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; NC='\033[0m'

echo -e "${BOLD}STRATUM smoke tests${NC}  ${DIM}$(date '+%Y-%m-%d %H:%M')${NC}"
echo -e "${DIM}PASS = working · WAIT = credential not issued yet · FAIL = needs attention${NC}"

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

# Order matters: blocking sponsors first, so the important output is at the top.
for s in local perfectcorp xano foxit nutrient namecom serpapi doctavian; do
  [[ -f "$DIR/$s.sh" ]] && bash "$DIR/$s.sh" 2>&1 | tee -a "$OUT"
done

P=$(grep -c 'PASS' "$OUT" || true)
W=$(grep -c 'WAIT' "$OUT" || true)
F=$(grep -c 'FAIL' "$OUT" || true)

echo
echo -e "${BOLD}─────────────────────────────────────────────${NC}"
echo -e "  ${GRN}PASS $P${NC}   ${YEL}WAIT $W${NC}   ${RED}FAIL $F${NC}"
echo -e "${BOLD}─────────────────────────────────────────────${NC}"

# Step 1's gate: these three must be green before Step 2 starts.
echo
echo -e "${BOLD}Step 2 readiness gate${NC} ${DIM}(implementation.md Step 1)${NC}"
for svc in "Perfect Corp" "Xano" "name.com"; do
  if grep -A6 "▸ $svc" "$OUT" | grep -q 'PASS'; then
    echo -e "  ${GRN}✓${NC} $svc"
  else
    echo -e "  ${RED}✗${NC} $svc — blocking"
  fi
done

if [[ "$F" -gt 0 ]]; then
  echo
  echo -e "${RED}$F check(s) failed with credentials present. Fix before proceeding.${NC}"
  exit 1
fi
exit 0
