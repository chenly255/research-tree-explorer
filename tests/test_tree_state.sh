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
# v0.4.0: task_type is no longer settable via `set` after add (trust kernel —
# agent must not be able to downgrade the validation schema post-hoc). So we
# specify --task-type analysis at add time for ID1 (the one we'll later complete).
ID1=$(python3 "$TS" add root approach "branch A" --task-type analysis)
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
# To complete a node, the branch_dir must really pass the validator. ID1 was
# created with --task-type analysis (the schema with fewest physical files).
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
# v0.4.0 codex-final P1-新1: cmd_complete now requires codex audit by default.
# Tests don't actually call codex; use --no-codex-audit (admin/fixture path,
# warned on stderr) to bypass for unit tests. Requires RESEARCH_TREE_ADMIN_OVERRIDE=1
# so an in-the-wild agent's subprocess can't pass --no-codex-audit on its own.
RESEARCH_TREE_ADMIN_OVERRIDE=1 python3 "$TS" complete "$ID1" --score 0.8 --no-codex-audit > /dev/null 2>&1

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

echo "=== test: session-step counter (v0.1.5, v0.4.0 session_id) ==="
TMP_SS=$(mktemp -d)
cd "$TMP_SS"
python3 "$TS" init "session test" > /dev/null
# v0.4.0: pin RESEARCH_TREE_SESSION_ID so same-session detection works
# under shell test (otherwise each subshell defaults to "default" which is
# also a valid same-session marker)
export RESEARCH_TREE_SESSION_ID="test-session-v04"
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
# v0.4.0 — task_type set at add time, no longer settable via `set`
NID=$(python3 "$TS" add root approach "x" --task-type analysis)
# v0.3.1 — need real branch_dir artifacts to complete (no user-supplied PASS json)
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
RESEARCH_TREE_ADMIN_OVERRIDE=1 python3 "$TS" complete "$NID" --score 0.7 --no-codex-audit > /dev/null 2>&1
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

echo "=== test: v0.4.0 — forked status auto-migrates to expanded on load_state ==="
TMP_V4=$(mktemp -d)
cd "$TMP_V4"
python3 "$TS" init "v0.4 migration" > /dev/null
NMIG=$(python3 "$TS" add root approach "to-be-forked")
# Hand-edit tree.json to simulate a pre-v0.4.0 tree with status=forked +
# the 3 deprecated v0.2.0 node fields. Bypass the CLI here because the
# CLI no longer writes these (which is exactly what we're testing).
python3 <<PYV4
import json
from pathlib import Path
p = Path("$TMP_V4/.research-tree/tree.json")
state = json.loads(p.read_text())
node = state["nodes"]["$NMIG"]
node["status"] = "forked"
node["agent_capable"] = True
node["subtree_origin"] = "agent_fork"
node["max_repair_attempts"] = 2
p.write_text(json.dumps(state, indent=2))
PYV4
# load_state must migrate forked → expanded and drop dead fields
LOADED=$(python3 "$TS" get "$NMIG")
STATUS_AFTER=$(echo "$LOADED" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
HAS_AGENT_CAPABLE=$(echo "$LOADED" | python3 -c "import json,sys; print('agent_capable' in json.load(sys.stdin))")
HAS_SUBTREE_ORIGIN=$(echo "$LOADED" | python3 -c "import json,sys; print('subtree_origin' in json.load(sys.stdin))")
HAS_MAX_REPAIR=$(echo "$LOADED" | python3 -c "import json,sys; print('max_repair_attempts' in json.load(sys.stdin))")
test "$STATUS_AFTER" = "expanded" || { echo "FAIL: status forked should migrate to expanded, got $STATUS_AFTER"; exit 1; }
test "$HAS_AGENT_CAPABLE" = "False" || { echo "FAIL: agent_capable should be dropped, still present"; exit 1; }
test "$HAS_SUBTREE_ORIGIN" = "False" || { echo "FAIL: subtree_origin should be dropped, still present"; exit 1; }
test "$HAS_MAX_REPAIR" = "False" || { echo "FAIL: max_repair_attempts should be dropped, still present"; exit 1; }
echo "  forked → expanded migration + dead-field cleanup OK"
cd "$TMP"
rm -rf "$TMP_V4"

echo "=== test: v0.4.0 — different session_id resets counter ==="
TMP_SID=$(mktemp -d)
cd "$TMP_SID"
python3 "$TS" init "session-id test" > /dev/null
# session A: 2 increments
RESEARCH_TREE_SESSION_ID="session-A" python3 "$TS" session-step increment --threshold 10 > /dev/null
RESEARCH_TREE_SESSION_ID="session-A" python3 "$TS" session-step increment --threshold 10 > /dev/null
COUNT_A=$(RESEARCH_TREE_SESSION_ID="session-A" python3 "$TS" session-step report --threshold 10 | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])")
test "$COUNT_A" = "2" || { echo "FAIL: session A should have count=2, got $COUNT_A"; exit 1; }
# session B: same project, different session_id — counter must reset
COUNT_B=$(RESEARCH_TREE_SESSION_ID="session-B" python3 "$TS" session-step report --threshold 10 | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])")
test "$COUNT_B" = "0" || { echo "FAIL: session B report should see count=0 (different session), got $COUNT_B"; exit 1; }
# But session A still sees its own count after an increment from session B
RESEARCH_TREE_SESSION_ID="session-B" python3 "$TS" session-step increment --threshold 10 > /dev/null
COUNT_B_AFTER=$(RESEARCH_TREE_SESSION_ID="session-B" python3 "$TS" session-step report --threshold 10 | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])")
test "$COUNT_B_AFTER" = "1" || { echo "FAIL: session B after one increment should have count=1, got $COUNT_B_AFTER"; exit 1; }
echo "  session_id-based isolation works"
cd "$TMP"
rm -rf "$TMP_SID"

echo "=== test: v0.4.0 — trust kernel: all scheduling/audit fields unset-able ==="
TMP_TK=$(mktemp -d)
cd "$TMP_TK"
python3 "$TS" init "trust kernel test" > /dev/null
NTK=$(python3 "$TS" add root approach "tk" --task-type training)
# Each `set X=Y` call below must be REFUSED. The list mirrors codex-final's
# P1-5 finding — any field that influences trust kernel decisions, pick-next
# ordering, audit linkage, or sibling DoS must be write-once-at-add or
# write-only-via-dedicated-command.
declare -A ATTACKS=(
    [branch_dir]="/tmp/attacker-controlled"       # complete would run validator in attacker dir
    [task_type]="analysis"                         # downgrade validation schema
    [completion_proof]="fake"                      # overwrite SHA-pinned proof
    [done_ready]="true"                            # force DONE.md without KILL_ARGUMENT.md
    [score]="999.0"                                # game pick-next priority
    [junction_audit_id]="audit-fake"               # rewrite audit history
    [depends_on]="missing-node"                    # sibling-DoS (block competitors)
    [human_only]="true"                            # sibling-DoS (autopilot skips)
)
for FIELD in "${!ATTACKS[@]}"; do
    VALUE="${ATTACKS[$FIELD]}"
    if python3 "$TS" set "$NTK" "$FIELD=$VALUE" 2>/dev/null; then
        echo "FAIL: set $FIELD=$VALUE must be refused (v0.4.0 trust kernel)"
        exit 1
    fi
done
echo "  ${#ATTACKS[@]} trust-relevant fields all rejected via set"
cd "$TMP"
rm -rf "$TMP_TK"

echo "=== test: v0.4.0 — apply-subtree-fork enforces budgets ==="
TMP_FB=$(mktemp -d)
cd "$TMP_FB"
python3 "$TS" init "fork budget test" --max-branches 3 --max-total-nodes 5 > /dev/null
PA=$(python3 "$TS" add root approach "parent-a" --task-type audit)
python3 "$TS" running "$PA" > /dev/null
# Write a SUBTREE_FORK.md that would exceed max_branches_per_junction=3
mkdir -p "$TMP_FB/.research-tree/branches/$PA"
cat > "$TMP_FB/.research-tree/branches/$PA/SUBTREE_FORK.md" <<'EOF'
# fork reason: budget test
```json
{"candidates": [
  {"placeholder_id": "c1", "kind": "experiment", "task_type": "audit", "title": "c1", "description": "d"},
  {"placeholder_id": "c2", "kind": "experiment", "task_type": "audit", "title": "c2", "description": "d"},
  {"placeholder_id": "c3", "kind": "experiment", "task_type": "audit", "title": "c3", "description": "d"},
  {"placeholder_id": "c4", "kind": "experiment", "task_type": "audit", "title": "c4", "description": "d"}
]}
```
EOF
if python3 "$TS" apply-subtree-fork "$PA" 2>/dev/null; then
    echo "FAIL: apply-subtree-fork should refuse 4 candidates when max_branches=3"
    exit 1
fi
echo "  apply-subtree-fork max_branches_per_junction enforcement OK"
# Also test max_total_nodes: trim fork to 3 candidates (within branch limit)
# but project already has root(0) + parent-a(1) = 2 nodes; max_total_nodes=5
# means only 3 more can fit. 3 candidates exactly fit, should pass.
cat > "$TMP_FB/.research-tree/branches/$PA/SUBTREE_FORK.md" <<'EOF'
# fork reason: budget test
```json
{"candidates": [
  {"placeholder_id": "c1", "kind": "experiment", "task_type": "audit", "title": "c1", "description": "d"},
  {"placeholder_id": "c2", "kind": "experiment", "task_type": "audit", "title": "c2", "description": "d"},
  {"placeholder_id": "c3", "kind": "experiment", "task_type": "audit", "title": "c3", "description": "d"}
]}
```
EOF
python3 "$TS" apply-subtree-fork "$PA" > /dev/null
# Parent should now be `expanded`, not `forked` (v0.4.0)
PA_STATUS=$(python3 "$TS" get "$PA" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
test "$PA_STATUS" = "expanded" || { echo "FAIL: post-fork parent should be expanded (v0.4.0), got $PA_STATUS"; exit 1; }
echo "  apply-subtree-fork → parent status=expanded (v0.4.0 unified) OK"
cd "$TMP"
rm -rf "$TMP_FB"

echo
echo "PASS — all tree_state.py smoke tests green."
