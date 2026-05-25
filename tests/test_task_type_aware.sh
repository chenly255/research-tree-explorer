#!/usr/bin/env bash
# v0.1.6 — task-type-aware validation tests.
# Covers: tree_state.py add --task-type / --depends-on / --human-only,
# pick-next dependency + human_only skipping, deps command,
# charter_validator.py task-type dispatch + new schemas.
# Exits non-zero on any failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$REPO_ROOT/scripts/tree_state.py"
V="$REPO_ROOT/scripts/charter_validator.py"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cd "$TMP"

echo "=== test 1: init + node schema includes task_type/depends_on/human_only ==="
python3 "$TS" init "v0.1.6 task type tests" > /dev/null
python3 "$TS" get root | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['task_type'] == 'mixed', f'root task_type expected mixed, got {n[\"task_type\"]}'
assert n['depends_on'] == [], f'root depends_on expected [], got {n[\"depends_on\"]}'
assert n['human_only'] == False, f'root human_only expected false, got {n[\"human_only\"]}'
print('  root schema OK')
"

echo "=== test 2: add child with task_type=audit ==="
ID_AUDIT=$(python3 "$TS" add root experiment "Schulte HLA-DRlo audit" --task-type audit)
python3 "$TS" get "$ID_AUDIT" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['task_type'] == 'audit', f'expected audit, got {n[\"task_type\"]}'
print(f'  audit branch {n[\"id\"]} task_type=audit OK')
"

echo "=== test 3: add child with task_type=training (default) ==="
ID_TRAIN=$(python3 "$TS" add root architecture "GO-aware repair head" --task-type training)
python3 "$TS" get "$ID_TRAIN" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['task_type'] == 'training', f'expected training, got {n[\"task_type\"]}'
print(f'  training branch {n[\"id\"]} OK')
"

echo "=== test 4: add child with depends_on referencing earlier node ==="
ID_DEP=$(python3 "$TS" add root experiment "depends on audit" --task-type training --depends-on "$ID_AUDIT")
python3 "$TS" get "$ID_DEP" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['depends_on'] == ['$ID_AUDIT'], f'depends_on expected [$ID_AUDIT], got {n[\"depends_on\"]}'
print(f'  depends_on correctly recorded: {n[\"depends_on\"]}')
"

echo "=== test 5: add child with human_only=true ==="
ID_HUMAN=$(python3 "$TS" add root narrative "paper headline framing" --task-type framing-decision --human-only)
python3 "$TS" get "$ID_HUMAN" | python3 -c "
import json, sys
n = json.load(sys.stdin)
assert n['human_only'] == True, f'human_only expected true, got {n[\"human_only\"]}'
assert n['task_type'] == 'framing-decision', f'task_type expected framing-decision, got {n[\"task_type\"]}'
print(f'  human-only framing-decision branch {n[\"id\"]} OK')
"

echo "=== test 6: invalid task_type rejected ==="
if python3 "$TS" add root experiment "bad type" --task-type bogus 2>/dev/null; then
    echo "FAIL: invalid task_type should be rejected"
    exit 1
fi
echo "  invalid task_type correctly rejected"

echo "=== test 7: depends_on with unknown node rejected ==="
if python3 "$TS" add root experiment "broken dep" --depends-on does_not_exist 2>/dev/null; then
    echo "FAIL: depends_on with missing node should be rejected"
    exit 1
fi
echo "  broken depends_on correctly rejected"

echo "=== test 8: pick-next skips human_only nodes ==="
NEXT=$(python3 "$TS" pick-next)
test "$NEXT" != "$ID_HUMAN" || {
    echo "FAIL: pick-next returned human-only node $ID_HUMAN"
    exit 1
}
echo "  pick-next skipped human_only=true node ($NEXT picked instead)"

echo "=== test 9: pick-next skips nodes with unmet dependencies ==="
# ID_DEP depends on ID_AUDIT which is not completed; should not be picked.
# Mark the obvious pickable candidates dead so ID_DEP would be next IF it weren't blocked.
python3 "$TS" die "$ID_AUDIT" --reason "test fixture: kill audit so dep stays unmet"
python3 "$TS" die "$ID_TRAIN" --reason "test fixture"
NEXT=$(python3 "$TS" pick-next)
test "$NEXT" != "$ID_DEP" || {
    echo "FAIL: pick-next returned $ID_DEP but its dep $ID_AUDIT is dead/unmet"
    exit 1
}
echo "  pick-next correctly skipped node with unmet dep (returned $NEXT)"

echo "=== test 10: deps command reports satisfied=false for unmet ==="
# `deps` exits 1 when unmet (intentional for shell branching); capture stdout despite non-zero exit
SAT=$(python3 "$TS" deps "$ID_DEP" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['satisfied'])" || true)
test "$SAT" = "False" || { echo "FAIL: deps satisfied expected False, got '$SAT'"; exit 1; }
echo "  deps reports satisfied=false correctly"

echo "=== test 11: deps command exits non-zero when unmet ==="
if python3 "$TS" deps "$ID_DEP" > /dev/null 2>&1; then
    echo "FAIL: deps should exit non-zero for unmet"
    exit 1
fi
echo "  deps exits non-zero for unmet"

echo "=== test 12: set task_type validates enum ==="
ID_X=$(python3 "$TS" add root experiment "set test" --task-type training)
if python3 "$TS" set "$ID_X" task_type=bogus 2>/dev/null; then
    echo "FAIL: set task_type=bogus should be rejected"
    exit 1
fi
python3 "$TS" set "$ID_X" task_type=audit
TT=$(python3 "$TS" get "$ID_X" | python3 -c "import json,sys; print(json.load(sys.stdin)['task_type'])")
test "$TT" = "audit"
echo "  set task_type=<valid> works, set task_type=<bogus> rejected"

echo "=== test 13: set depends_on parses comma-separated list ==="
python3 "$TS" set "$ID_X" depends_on=root,$ID_DEP
DEPS=$(python3 "$TS" get "$ID_X" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin)['depends_on']))")
test "$DEPS" = "root,$ID_DEP"
echo "  set depends_on (comma list) works"

echo ""
echo "=== validator: task-type-aware checks ==="

echo "=== test 14: validator --task-type framing-decision always FAILs ==="
mkdir -p "$TMP/branch_fd"
echo '{}' > "$TMP/branch_fd/dummy.json"
OUT=$(python3 "$V" --task-type framing-decision "$TMP/branch_fd" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q '"verdict": "FAIL"' || { echo "FAIL: framing-decision should FAIL, got: $OUT"; exit 1; }
echo "$OUT" | grep -q "human-only" || { echo "FAIL: framing-decision should mention human-only"; exit 1; }
echo "  framing-decision correctly FAILs"

echo "=== test 15: validator --task-type audit needs audit_report.json ==="
mkdir -p "$TMP/branch_audit"
# Minimal RESULT.md with audit-mode charter table (rules 0/1/4/7/8)
cat > "$TMP/branch_audit/RESULT.md" <<'EOF'
METRIC=24.0
KEY_FINDING=Within-atlas vs cross-batch over-estimation = 24x
COST=2.0 gpu_hours
ARTIFACTS=audit_report.json donor_bootstrap.json protocol_comparison.json
DONE_READY=false

## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS | within-atlas paired, donor bootstrap N=1000 |
| 1. Data rules | PASS | Schulte same-atlas paired control |
| 4. Evaluation rules | PASS | FN delta + 95% CI + p-value reported |
| 7. Reproducibility rules | PASS | requirements.txt + audit script |
| 8. Compute honesty | PASS | 2.0 gpu_hours, 0.5 wall_clock_hours |
EOF
echo "anndata>=0.10" > "$TMP/branch_audit/requirements.txt"
# Without audit_report.json — validator should FAIL
OUT=$(python3 "$V" --task-type audit "$TMP/branch_audit" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q "audit_report.json" || { echo "FAIL: audit mode should require audit_report.json"; exit 1; }
echo "  audit mode requires audit_report.json"

echo "=== test 16: validator audit mode PASS with all 3 audit artifacts ==="
cat > "$TMP/branch_audit/audit_report.json" <<'EOF'
{"cohort_summary": {"n_cohort_cells": 10000, "n_donor_cohort": 25},
 "blindspot_signal": {"fn_delta": 0.014, "ci_low": -0.007, "ci_hi": 0.035, "verdict": "NO_SIGNAL"}}
EOF
cat > "$TMP/branch_audit/donor_bootstrap.json" <<'EOF'
{"n_iter": 1000, "per_donor_leave_one_out": [{"donor_id": "PD1", "fn_delta": 0.013}]}
EOF
cat > "$TMP/branch_audit/protocol_comparison.json" <<'EOF'
{"within_atlas_fn_delta": 0.014, "cross_batch_fn_delta": 0.338, "over_estimation_ratio": 24.1}
EOF
OUT=$(python3 "$V" --task-type audit "$TMP/branch_audit" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q '"verdict": "PASS"' || { echo "FAIL: full audit branch should PASS, got: $OUT"; exit 1; }
echo "  audit mode PASS with full schema"

echo "=== test 17: validator audit does NOT require checkpoint / metrics.json ==="
# Audit branch has zero checkpoint dirs and no metrics.json — must still PASS
test ! -d "$TMP/branch_audit/checkpoints"
test ! -f "$TMP/branch_audit/metrics.json"
echo "  no checkpoint dirs, no metrics.json — and still PASS"

echo "=== test 18: validator data-acquisition needs DATA_MANIFEST.json with valid local_path ==="
mkdir -p "$TMP/branch_data"
cat > "$TMP/branch_data/RESULT.md" <<'EOF'
METRIC=165847
KEY_FINDING=Downloaded COVID Schulte-Schrepping 2020 atlas to local cache
COST=0.1 gpu_hours
ARTIFACTS=DATA_MANIFEST.json data/schulte.h5ad
DONE_READY=false

## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS | full atlas, no subsetting |
| 1. Data rules | PASS | checksum-verified, n_cells matches paper |
| 7. Reproducibility rules | PASS | download script + checksum in manifest |
EOF
echo "cellxgene_census>=1.10" > "$TMP/branch_data/requirements.txt"
mkdir -p "$TMP/branch_data/data"
echo "fake h5ad bytes for test" > "$TMP/branch_data/data/schulte.h5ad"
# v0.3.1: validator now recomputes sha256 of local_path and cross-checks; fake
# checksum no longer passes. Compute the real hash.
REAL_CKSUM=$(sha256sum "$TMP/branch_data/data/schulte.h5ad" | awk '{print $1}')
cat > "$TMP/branch_data/DATA_MANIFEST.json" <<EOF
{"atlas_id": "schulte_2020_covid",
 "source_url": "https://cellxgene.cziscience.com/...",
 "local_path": "data/schulte.h5ad",
 "checksum": "$REAL_CKSUM",
 "n_cells": 165847,
 "downloaded_at": "2026-05-23T15:00:00Z"}
EOF
OUT=$(python3 "$V" --task-type data-acquisition "$TMP/branch_data" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q '"verdict": "PASS"' || { echo "FAIL: data-acquisition should PASS, got: $OUT"; exit 1; }
echo "  data-acquisition mode PASS with manifest + verified local file"

echo "=== test 19: validator data-acquisition FAILs when local_path missing ==="
rm "$TMP/branch_data/data/schulte.h5ad"
OUT=$(python3 "$V" --task-type data-acquisition "$TMP/branch_data" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q '"verdict": "FAIL"' || { echo "FAIL: missing local_path should FAIL"; exit 1; }
echo "  data-acquisition correctly FAILs when manifest's local_path missing"

echo "=== test 20: validator training mode still works (v0.1.5 backward compat) ==="
# Without --task-type, validator should pick `training` and apply old hardline.
# Empty branch dir → should FAIL because no RESULT.md / checkpoints / etc.
mkdir -p "$TMP/branch_train_empty"
OUT=$(python3 "$V" "$TMP/branch_train_empty" 2>&1) && rc=$? || rc=$?
echo "$OUT" | grep -q '"verdict": "FAIL"' || { echo "FAIL: empty training branch should FAIL"; exit 1; }
echo "$OUT" | grep -q "RESULT.md" || { echo "FAIL: should mention missing RESULT.md"; exit 1; }
echo "  training mode (default) still enforces v0.1.5 hardline"

echo ""
echo "✅ ALL 20 v0.1.6 TASK-TYPE TESTS PASSED"
