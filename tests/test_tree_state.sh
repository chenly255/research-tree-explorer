#!/usr/bin/env bash
# Smoke test for tree_state.py state machine.
# Exits non-zero on any failure. Cleans up its own tmp dir.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$REPO_ROOT/scripts/tree_state.py"
SYN="$REPO_ROOT/scripts/synthesize_report.py"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cd "$TMP"

echo "=== test: init ==="
python3 "$TS" init "test root idea" --max-depth 3 --max-branches 4 --max-total-nodes 10
test -f .research-tree/tree.json

echo "=== test: add branches ==="
ID1=$(python3 "$TS" add root approach "branch A")
ID2=$(python3 "$TS" add root approach "branch B")
ID3=$(python3 "$TS" add root approach "branch C")
test "$ID1" = "1"
test "$ID2" = "2"
test "$ID3" = "3"

echo "=== test: budget exceeded ==="
python3 "$TS" add root approach "branch D"
python3 "$TS" add root approach "branch E" 2>&1 | grep -q "max_branches_per_junction" && echo "  budget gate fired correctly"

echo "=== test: set status + score ==="
python3 "$TS" set "$ID1" status=completed score=0.8 > /dev/null
python3 "$TS" set "$ID2" status=dead death_reason="bad approach" > /dev/null
STATS=$(python3 "$TS" stats)
echo "$STATS" | grep -q "completed   : 1" || { echo "FAIL: expected 1 completed"; exit 1; }
echo "$STATS" | grep -q "dead        : 1" || { echo "FAIL: expected 1 dead"; exit 1; }

echo "=== test: deepen winner ==="
SUB=$(python3 "$TS" add "$ID1" ablation "deeper variant")
test "$SUB" = "1.1"

echo "=== test: pick-next prefers winner descendants ==="
NEXT=$(python3 "$TS" pick-next)
test "$NEXT" = "1.1" || { echo "FAIL: pick-next returned $NEXT, expected 1.1"; exit 1; }

echo "=== test: synthesize report ==="
python3 "$SYN" --project-root "$TMP" > /dev/null
test -f .research-tree/FINAL_REPORT.md
grep -q "Winner" .research-tree/FINAL_REPORT.md
grep -q "branch A" .research-tree/FINAL_REPORT.md
grep -q "What died" .research-tree/FINAL_REPORT.md
grep -q "branch B" .research-tree/FINAL_REPORT.md

echo "=== test: budget-check ==="
python3 "$TS" budget-check | grep -q "OK"

echo
echo "PASS — all tree_state.py smoke tests green."
