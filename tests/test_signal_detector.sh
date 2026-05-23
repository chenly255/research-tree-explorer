#!/usr/bin/env bash
# v0.1.7 — signal_detector tests.
# Covers: classify (audit_report.json source, metrics.json source,
# RESULT.md fallback), CI overrides p_value semantics, threshold edge
# cases, aggregate sibling verdicts, check-pivot writes proposal +
# returns exit 10. Exits non-zero on any failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$REPO_ROOT/scripts/tree_state.py"
SD="$REPO_ROOT/scripts/signal_detector.py"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cd "$TMP"

mk_branch() {
  local name="$1"; mkdir -p "$name"; echo "$name"
}

assert_verdict() {
  local branch="$1" expected="$2"
  local got
  got=$(python3 "$SD" classify "$branch" | python3 -c "import json,sys;print(json.load(sys.stdin)['verdict'])")
  if [[ "$got" != "$expected" ]]; then
    echo "FAIL: $branch expected verdict=$expected got=$got" >&2
    python3 "$SD" classify "$branch" >&2
    exit 1
  fi
  echo "  $branch → $got OK"
}

echo "=== test 1: STRONG signal from audit_report.json (sc-bias Krishna POC scenario) ==="
B=$(mk_branch krishna)
cat > "$B/audit_report.json" <<'JSON'
{"cohort_summary": {"n_cohort_cells": 5000},
 "blindspot_signal": {"fn_delta": 0.338, "ci_low": 0.316, "ci_hi": 0.357,
                       "p_value": 1.0, "verdict": "blindspot_confirmed"}}
JSON
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.338
EOF
# p_value=1.0 here is bootstrap reproducibility (high=good), NOT failure
# to reject null. The detector must IGNORE p_value when CI is informative.
assert_verdict "$B" STRONG

echo "=== test 2: NULL signal from audit_report.json (sc-bias Li 2022 scenario) ==="
B=$(mk_branch li2022)
cat > "$B/audit_report.json" <<'JSON'
{"cohort_summary": {"n_cohort_cells": 99000},
 "blindspot_signal": {"fn_delta": 0.014, "ci_low": -0.007, "ci_hi": 0.035,
                       "verdict": "no_signal"}}
JSON
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.014
EOF
assert_verdict "$B" NULL

echo "=== test 3: WEAK signal from RESULT.md fields (per-FM ablation case) ==="
B=$(mk_branch per_fm)
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.064
EFFECT_SIZE=0.064
CI_LOW=0.040
CI_HI=0.090
P_VALUE=0.02
EOF
assert_verdict "$B" WEAK

echo "=== test 4: NULL via CI crossing zero (large effect but unreliable) ==="
B=$(mk_branch cross_zero)
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.15
EFFECT_SIZE=0.15
CI_LOW=-0.05
CI_HI=0.35
EOF
assert_verdict "$B" NULL

echo "=== test 5: NULL via tiny effect overrides CI exclusion ==="
B=$(mk_branch tiny_effect)
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.02
EFFECT_SIZE=0.02
CI_LOW=0.018
CI_HI=0.022
EOF
assert_verdict "$B" NULL

echo "=== test 6: STRONG when no CI but high effect (provisional) ==="
B=$(mk_branch no_ci)
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.30
EOF
assert_verdict "$B" STRONG

echo "=== test 7: UNKNOWN when no parseable info ==="
B=$(mk_branch noinfo)
cat > "$B/RESULT.md" <<'EOF'
# branch result
KEY_FINDING=something
EOF
assert_verdict "$B" UNKNOWN

echo "=== test 8: training metrics.json source ==="
B=$(mk_branch training)
cat > "$B/metrics.json" <<'JSON'
{"param_count": 12000000,
 "seeds": [0,1,2],
 "downstream_tasks": {
   "tissue_clf": {"metric": 0.92, "baseline_score": 0.78, "std": 0.01, "p_value": 0.001}
 }}
JSON
cat > "$B/RESULT.md" <<'EOF'
METRIC=0.92
EOF
# effect = 0.92 - 0.78 = 0.14 ≥ 0.10, no CI → STRONG provisional
assert_verdict "$B" STRONG

echo "=== test 9: aggregate ALL_NULL → pivot_recommended=true ==="
mkdir -p proj/.research-tree/branches/{r,c1,c2,c3}
for n in c1 c2 c3; do
  cat > "proj/.research-tree/branches/$n/audit_report.json" <<EOF
{"cohort_summary": {}, "blindspot_signal": {"fn_delta": 0.014, "ci_low": -0.007, "ci_hi": 0.035, "verdict": "no_signal"}}
EOF
  cat > "proj/.research-tree/branches/$n/RESULT.md" <<EOF
METRIC=0.014
EOF
done
cat > proj/.research-tree/tree.json <<'JSON'
{"nodes": {
  "r":  {"id":"r",  "parent_id": null, "title": "root junction", "status": "completed", "depth": 0},
  "c1": {"id":"c1", "parent_id": "r",  "title": "atlas A",       "status": "completed", "depth": 1},
  "c2": {"id":"c2", "parent_id": "r",  "title": "atlas B",       "status": "completed", "depth": 1},
  "c3": {"id":"c3", "parent_id": "r",  "title": "atlas C",       "status": "completed", "depth": 1}
}}
JSON
AGG=$(python3 "$SD" aggregate r --project-root proj)
PIVOT_REC=$(echo "$AGG" | python3 -c "import json,sys;print(json.load(sys.stdin)['pivot_recommended'])")
AGG_V=$(echo "$AGG" | python3 -c "import json,sys;print(json.load(sys.stdin)['aggregate_verdict'])")
[[ "$PIVOT_REC" == "True" ]] || { echo "FAIL: expected pivot_recommended=True, got $PIVOT_REC" >&2; exit 1; }
[[ "$AGG_V" == "ALL_NULL" ]] || { echo "FAIL: expected aggregate=ALL_NULL, got $AGG_V" >&2; exit 1; }
echo "  aggregate=ALL_NULL pivot_recommended=True OK"

echo "=== test 10: check-pivot writes AUTO_PIVOT_PROPOSAL.md and exits 10 ==="
set +e
python3 "$SD" check-pivot --project-root proj --write-proposal > /dev/null
EXIT=$?
set -e
[[ $EXIT -eq 10 ]] || { echo "FAIL: expected exit 10, got $EXIT" >&2; exit 1; }
[[ -f proj/.research-tree/AUTO_PIVOT_PROPOSAL.md ]] || { echo "FAIL: AUTO_PIVOT_PROPOSAL.md not written" >&2; exit 1; }
grep -q "ALL_NULL" proj/.research-tree/AUTO_PIVOT_PROPOSAL.md || { echo "FAIL: proposal missing ALL_NULL verdict" >&2; exit 1; }
echo "  proposal written + exit 10 OK"

echo "=== test 11: idempotent — second call does not overwrite existing proposal ==="
set +e
python3 "$SD" check-pivot --project-root proj --write-proposal 2> /tmp/sd_err > /dev/null
EXIT=$?
set -e
[[ $EXIT -eq 10 ]] || { echo "FAIL: expected exit 10 on repeat, got $EXIT" >&2; exit 1; }
grep -q "already exists" /tmp/sd_err || { echo "FAIL: expected 'already exists' warning" >&2; cat /tmp/sd_err >&2; exit 1; }
echo "  idempotent OK"

echo "=== test 12: aggregate with STRONG + NULL mix → MIXED_POSITIVE (no pivot) ==="
mkdir -p proj2/.research-tree/branches/{r,s,n}
cat > "proj2/.research-tree/branches/s/audit_report.json" <<'JSON'
{"blindspot_signal": {"fn_delta": 0.30, "ci_low": 0.25, "ci_hi": 0.35}}
JSON
cat > "proj2/.research-tree/branches/s/RESULT.md" <<EOF
METRIC=0.30
EOF
cat > "proj2/.research-tree/branches/n/audit_report.json" <<'JSON'
{"blindspot_signal": {"fn_delta": 0.014, "ci_low": -0.01, "ci_hi": 0.03}}
JSON
cat > "proj2/.research-tree/branches/n/RESULT.md" <<EOF
METRIC=0.014
EOF
cat > proj2/.research-tree/tree.json <<'JSON'
{"nodes": {
  "r": {"id":"r", "parent_id": null, "title": "root", "status": "completed", "depth": 0},
  "s": {"id":"s", "parent_id": "r",  "title": "strong", "status": "completed", "depth": 1},
  "n": {"id":"n", "parent_id": "r",  "title": "null",   "status": "completed", "depth": 1}
}}
JSON
AGG=$(python3 "$SD" aggregate r --project-root proj2)
AGG_V=$(echo "$AGG" | python3 -c "import json,sys;print(json.load(sys.stdin)['aggregate_verdict'])")
PIVOT_REC=$(echo "$AGG" | python3 -c "import json,sys;print(json.load(sys.stdin)['pivot_recommended'])")
[[ "$AGG_V" == "MIXED_POSITIVE" ]] || { echo "FAIL: expected MIXED_POSITIVE, got $AGG_V" >&2; exit 1; }
[[ "$PIVOT_REC" == "False" ]] || { echo "FAIL: expected pivot_recommended=False, got $PIVOT_REC" >&2; exit 1; }
echo "  MIXED_POSITIVE → no pivot OK"

echo ""
echo "=== ALL signal_detector TESTS PASSED ==="
