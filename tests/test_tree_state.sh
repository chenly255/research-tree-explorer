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

echo "=== test: set cannot change status (v0.1.3 lockdown) ==="
if python3 "$TS" set "$ID1" status=completed 2>/dev/null; then
    echo "FAIL: set status=completed should be refused in v0.1.3"
    exit 1
fi
if python3 "$TS" set "$ID1" status=dead 2>/dev/null; then
    echo "FAIL: set status=dead should be refused in v0.1.3"
    exit 1
fi
echo "  set lockdown works"

echo "=== test: complete v0.3.1 — runs validator from disk, rejects empty branch ==="
# v0.3.1 (codex review P0-1): cmd_complete no longer accepts user-supplied
# arbitrary JSON. It runs charter_validator on branch_dir directly. An empty
# branch_dir must FAIL the validator and complete must refuse.
if python3 "$TS" complete "$ID1" --score 0.9 2>/dev/null; then
    echo "FAIL: complete should refuse empty branch_dir (no RESULT.md / metrics.json / etc)"
    exit 1
fi
# To complete a node, the branch_dir must really pass the validator. Mock an
# `analysis` task_type since it has the fewest physical requirements.
python3 "$TS" set "$ID1" task_type=analysis > /dev/null
BR="$TMP/.research-tree/branches/$ID1"
mkdir -p "$BR"
cat > "$BR/RESULT.md" <<'EOF'
# branch A result

METRIC: 0.42

## Charter compliance

| Rule | Verdict |
|---|---|
| 0. Anti-laziness preamble | PASS |
| 4. Evaluation rules | PASS |
| 6. Novelty rules | PASS |
| 7. Reproducibility rules | PASS |
| 8. Compute honesty | PASS |
EOF
echo '{"summary": "analysis ok"}' > "$BR/analysis_output.json"
echo 'numpy==1.26.0' > "$BR/requirements.txt"
python3 "$TS" complete "$ID1" --score 0.8 > /dev/null

echo "=== test: die marks dead with reason ==="
python3 "$TS" die "$ID2" --reason "bad approach" > /dev/null

STATS=$(python3 "$TS" stats)
echo "$STATS" | grep -q "completed   : 1" || { echo "FAIL: expected 1 completed"; echo "$STATS"; exit 1; }
echo "$STATS" | grep -q "dead        : 1" || { echo "FAIL: expected 1 dead"; echo "$STATS"; exit 1; }

echo "=== test: completion_proof recorded ==="
NODE_JSON=$(python3 "$TS" get "$ID1")
echo "$NODE_JSON" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['completion_proof'] is not None, 'completion_proof missing'
assert n['completion_proof']['validator_verdict'] == 'PASS', 'wrong verdict'
assert 'validator_report_sha256' in n['completion_proof'], 'no sha256'
print('  completion_proof recorded')
"

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

echo "=== test: dead children free their slot ==="
TMP2=$(mktemp -d)
cd "$TMP2"
python3 "$TS" init "alive-only test" --max-branches 3 > /dev/null
python3 "$TS" add root approach "A" > /dev/null
python3 "$TS" add root approach "B" > /dev/null
python3 "$TS" add root approach "C" > /dev/null
# 4th must fail (3 alive)
if python3 "$TS" add root approach "D" 2>/dev/null; then
    echo "FAIL: 4th add succeeded when 3 alive (max=3)"
    exit 1
fi
# Kill A, then 4th must succeed (2 alive + 1 dead = 1 slot free)
python3 "$TS" die 1 --reason "testing" > /dev/null
NEW_ID=$(python3 "$TS" add root approach "D")
test "$NEW_ID" = "4" || { echo "FAIL: expected id 4, got $NEW_ID"; exit 1; }
echo "  dead-slot reuse works"
cd "$TMP"
rm -rf "$TMP2"

echo "=== test: init creates progress.log + subdirs ==="
TMP3=$(mktemp -d)
cd "$TMP3"
python3 "$TS" init "log test" > /dev/null
test -f .research-tree/progress.log || { echo "FAIL: progress.log not created"; exit 1; }
test -d .research-tree/audits || { echo "FAIL: audits/ not created"; exit 1; }
test -d .research-tree/reflections || { echo "FAIL: reflections/ not created"; exit 1; }
grep -q "action=init" .research-tree/progress.log || { echo "FAIL: progress.log missing init entry"; exit 1; }
echo "  init scaffolding is complete"
cd "$TMP"
rm -rf "$TMP3"

echo "=== test: concurrent adds get unique IDs (flock) ==="
TMP4=$(mktemp -d)
cd "$TMP4"
python3 "$TS" init "race" --max-branches 20 --max-total-nodes 50 > /dev/null
# 10 parallel adds with flock should each get a unique ID, no duplicates
seq 1 10 | xargs -P 10 -I {} python3 "$TS" add root approach "b{}" > /tmp/rte_concurrent_ids 2>/dev/null
UNIQUE=$(sort -u /tmp/rte_concurrent_ids | wc -l)
TOTAL=$(wc -l < /tmp/rte_concurrent_ids)
if [ "$UNIQUE" != "10" ] || [ "$TOTAL" != "10" ]; then
    echo "FAIL: concurrent add produced $TOTAL ids, $UNIQUE unique (expected 10/10)"
    cat /tmp/rte_concurrent_ids
    exit 1
fi
echo "  flock prevents duplicate IDs under parallel writes"
cd "$TMP"
rm -rf "$TMP4"

echo "=== test: direct_executable field (v0.1.5) ==="
TMP_DE=$(mktemp -d)
cd "$TMP_DE"
python3 "$TS" init "direct exec test" > /dev/null
ND=$(python3 "$TS" add root approach "canonical")
# default should be false
DEFAULT_DE=$(python3 "$TS" get "$ND" | python3 -c "import json,sys; print(json.load(sys.stdin)['direct_executable'])")
test "$DEFAULT_DE" = "False" || { echo "FAIL: default direct_executable should be False, got $DEFAULT_DE"; exit 1; }
# set it via set command
python3 "$TS" set "$ND" direct_executable=true > /dev/null
NEW_DE=$(python3 "$TS" get "$ND" | python3 -c "import json,sys; print(json.load(sys.stdin)['direct_executable'])")
test "$NEW_DE" = "True" || { echo "FAIL: set direct_executable=true didn't stick"; exit 1; }
echo "  direct_executable defaults False, set=true works"
cd "$TMP"
rm -rf "$TMP_DE"

echo "=== test: session-step counter (v0.1.5) ==="
TMP_SS=$(mktemp -d)
cd "$TMP_SS"
python3 "$TS" init "session test" > /dev/null
# First increment: count=1
RC1=$(python3 "$TS" session-step increment --threshold 3 | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])")
test "$RC1" = "1" || { echo "FAIL: first increment should be 1, got $RC1"; exit 1; }
# Second
RC2=$(python3 "$TS" session-step increment --threshold 3 | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])")
test "$RC2" = "2" || { echo "FAIL: second should be 2, got $RC2"; exit 1; }
# Third hits threshold — should exit non-zero
set +e
python3 "$TS" session-step increment --threshold 3 > /tmp/rte_ss.json
SS_EXIT=$?
set -e
test "$SS_EXIT" = "1" || { echo "FAIL: at threshold should exit 1, got $SS_EXIT"; exit 1; }
SHOULD_PAUSE=$(python3 -c "import json; print(json.load(open('/tmp/rte_ss.json'))['should_pause'])")
test "$SHOULD_PAUSE" = "True" || { echo "FAIL: should_pause should be True"; exit 1; }
echo "  session-step: count and threshold work, exit=1 when should_pause"

# Report mode doesn't increment (still at threshold, so exit 1 expected — wrap)
set +e
python3 "$TS" session-step report --threshold 3 > /tmp/rte_ss.json
set -e
REPORT_COUNT=$(python3 -c "import json; print(json.load(open('/tmp/rte_ss.json'))['count'])")
test "$REPORT_COUNT" = "3" || { echo "FAIL: report mode should not increment, expected 3 got $REPORT_COUNT"; exit 1; }
echo "  report mode is non-mutating"

# Reset wipes counter
python3 "$TS" session-step reset > /dev/null
python3 "$TS" session-step report > /tmp/rte_ss.json
RESET_COUNT=$(python3 -c "import json; print(json.load(open('/tmp/rte_ss.json'))['count'])")
test "$RESET_COUNT" = "0" || { echo "FAIL: after reset count should be 0, got $RESET_COUNT"; exit 1; }
echo "  reset clears counter"
cd "$TMP"
rm -rf "$TMP_SS"

echo "=== test: reopen clears completion_proof ==="
TMP5=$(mktemp -d)
cd "$TMP5"
python3 "$TS" init "reopen-test" > /dev/null
NID=$(python3 "$TS" add root approach "x")
# v0.3.1 — need real branch_dir artifacts to complete (no user-supplied PASS json)
python3 "$TS" set "$NID" task_type=analysis > /dev/null
BR5="$TMP5/.research-tree/branches/$NID"
mkdir -p "$BR5"
cat > "$BR5/RESULT.md" <<'EOF'
# x result

METRIC: 0.7

## Charter compliance

| Rule | Verdict |
|---|---|
| 0. Anti-laziness preamble | PASS |
| 4. Evaluation rules | PASS |
| 7. Reproducibility rules | PASS |
| 8. Compute honesty | PASS |
EOF
echo '{"summary": "ok"}' > "$BR5/analysis_output.json"
echo 'numpy==1.26.0' > "$BR5/requirements.txt"
python3 "$TS" complete "$NID" --score 0.7 > /dev/null
python3 "$TS" reopen "$NID" > /dev/null
NODE=$(python3 "$TS" get "$NID")
echo "$NODE" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['status'] == 'pending', f'expected pending, got {n[\"status\"]}'
assert n['completion_proof'] is None, 'completion_proof should be cleared'
assert n['score'] is None, 'score should be cleared'
print('  reopen clears completion_proof and score')
"
cd "$TMP"
rm -rf "$TMP5"

echo
echo "PASS — all tree_state.py smoke tests green."
