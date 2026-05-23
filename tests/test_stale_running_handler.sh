#!/usr/bin/env bash
# Smoke test for stale_running_handler.py.
# Sets up a fake tree with running nodes in various states and verifies the
# handler classifies each correctly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$REPO_ROOT/scripts/tree_state.py"
SH="$REPO_ROOT/scripts/stale_running_handler.py"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cd "$TMP"

echo "=== setup: init tree + 5 nodes ==="
python3 "$TS" init "stale handler test" --max-branches 6 > /dev/null
NODES=()
for i in 1 2 3 4 5; do
    nid=$(python3 "$TS" add root approach "branch_$i")
    NODES+=("$nid")
    python3 "$TS" running "$nid" > /dev/null
done
echo "  5 nodes in running state"

# ---- node 1: ALIVE process ----
echo "=== node 1: alive (long-sleep process) ==="
nohup sleep 600 >/dev/null 2>&1 &
ALIVE_PID=$!
cat > ".research-tree/branches/1/EXECUTOR.json" <<EOF
{"pid": $ALIVE_PID, "started_at": "2026-05-23T00:00:00Z", "log_file": "executor.log"}
EOF

# ---- node 2: dead process + RESULT.md exists (ready_for_validation) ----
echo "=== node 2: dead process + RESULT.md present ==="
# pick a guaranteed-dead pid: spawn-and-wait
bash -c 'echo $$' > /tmp/rte_dead_pid
DEAD_PID=$(cat /tmp/rte_dead_pid)
# Make sure that pid is actually gone by waiting briefly
sleep 0.1
cat > ".research-tree/branches/2/EXECUTOR.json" <<EOF
{"pid": $DEAD_PID, "started_at": "2026-05-23T00:00:00Z", "log_file": "executor.log"}
EOF
echo "METRIC=0.85" > ".research-tree/branches/2/RESULT.md"

# ---- node 3: dead process + DEAD.md exists (ready_for_death_from_file) ----
echo "=== node 3: dead process + DEAD.md present ==="
cat > ".research-tree/branches/3/EXECUTOR.json" <<EOF
{"pid": $DEAD_PID, "started_at": "2026-05-23T00:00:00Z", "log_file": "executor.log"}
EOF
echo "honest blocker: dataset format incompatible" > ".research-tree/branches/3/DEAD.md"

# ---- node 4: dead process + no output (abandoned) ----
echo "=== node 4: dead process + no RESULT.md / DEAD.md ==="
cat > ".research-tree/branches/4/EXECUTOR.json" <<EOF
{"pid": $DEAD_PID, "started_at": "2026-05-23T00:00:00Z", "log_file": "executor.log"}
EOF

# ---- node 5: NO EXECUTOR.json (legacy orphan) ----
echo "=== node 5: no EXECUTOR.json (legacy_orphan) ==="
# don't create executor.json — simulates pre-v0.1.4 running node

echo "=== run stale_running_handler.py ==="
OUT=$(python3 "$SH" --project-root "$TMP")
echo "$OUT" | python3 -c "
import json, sys
b = json.load(sys.stdin)
def assert_has(category, node_id):
    ids = [n['node_id'] for n in b[category]]
    assert node_id in ids, f'expected {node_id!r} in {category}, got {ids}'
    print(f'  ✓ node {node_id} → {category}')

assert_has('alive', '1')
assert_has('ready_for_validation', '2')
assert_has('ready_for_death_from_file', '3')
assert_has('abandoned', '4')
assert_has('legacy_orphan', '5')

# Sanity: counts
assert sum(len(v) for v in b.values()) == 5, f'expected 5 classified nodes, got {b}'
print('  PASS: all 5 nodes classified correctly')
"

# cleanup the alive sleep
kill "$ALIVE_PID" 2>/dev/null || true

echo "=== test: pid 0 / invalid pid is treated as dead ==="
TMP2=$(mktemp -d)
cd "$TMP2"
python3 "$TS" init "invalid pid" > /dev/null
nid=$(python3 "$TS" add root approach "x")
python3 "$TS" running "$nid" > /dev/null
cat > ".research-tree/branches/1/EXECUTOR.json" <<EOF
{"pid": 0, "started_at": "x"}
EOF
OUT=$(python3 "$SH" --project-root "$TMP2")
echo "$OUT" | python3 -c "
import json, sys
b = json.load(sys.stdin)
assert len(b['abandoned']) == 1 and b['abandoned'][0]['node_id'] == '1', f'pid=0 should be abandoned, got {b}'
print('  PASS: pid=0 treated as dead → abandoned (no output files)')
"
cd "$TMP"
rm -rf "$TMP2"

echo "=== test: malformed EXECUTOR.json → legacy_orphan ==="
TMP3=$(mktemp -d)
cd "$TMP3"
python3 "$TS" init "bad executor" > /dev/null
nid=$(python3 "$TS" add root approach "x")
python3 "$TS" running "$nid" > /dev/null
echo "not json at all" > ".research-tree/branches/1/EXECUTOR.json"
OUT=$(python3 "$SH" --project-root "$TMP3")
echo "$OUT" | python3 -c "
import json, sys
b = json.load(sys.stdin)
assert len(b['legacy_orphan']) == 1, f'malformed EXECUTOR.json should be legacy_orphan, got {b}'
print('  PASS: malformed EXECUTOR.json → legacy_orphan')
"
cd "$TMP"
rm -rf "$TMP3"

echo "=== test: pending/completed/dead nodes are ignored ==="
TMP4=$(mktemp -d)
cd "$TMP4"
python3 "$TS" init "ignore non-running" > /dev/null
python3 "$TS" add root approach "pending_node" > /dev/null
DEAD_ID=$(python3 "$TS" add root approach "will_die")
python3 "$TS" die "$DEAD_ID" --reason "test" > /dev/null
OUT=$(python3 "$SH" --project-root "$TMP4")
echo "$OUT" | python3 -c "
import json, sys
b = json.load(sys.stdin)
total = sum(len(v) for v in b.values())
assert total == 0, f'no running nodes should mean empty output, got {b}'
print('  PASS: non-running nodes ignored')
"
cd "$TMP"
rm -rf "$TMP4"

echo
echo "PASS — all stale_running_handler.py smoke tests green."
