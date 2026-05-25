#!/usr/bin/env bash
# Smoke test for charter_validator.py.
# Builds a "perfect" mock branch_dir, then tests individual failure modes
# by mutating one thing at a time.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="$REPO_ROOT/scripts/charter_validator.py"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

# Helper: build a fully-charter-compliant fake branch_dir at $1.
# Caller may then mutate it to test specific failure modes.
build_perfect_branch() {
    local DIR="$1"
    mkdir -p "$DIR/data" "$DIR/checkpoints/seed_0" "$DIR/checkpoints/seed_1" \
        "$DIR/checkpoints/seed_2" "$DIR/ablations/headline_component" \
        "$DIR/ablations/scale" "$DIR/ablations/data_efficiency" \
        "$DIR/ablations/cross_batch"

    # Held-out test set
    # v0.3.1: validator now recomputes hash from sorted(test_ids); fixture must
    # provide the real hash. sha256(json.dumps(sorted(ids), separators=(',', ':')))
    local SPLIT_HASH
    SPLIT_HASH=$(python3 -c 'import hashlib, json; print(hashlib.sha256(json.dumps(sorted(["cell_001","cell_002","cell_003"]), separators=(",", ":")).encode()).hexdigest())')
    cat > "$DIR/data/test_split.json" <<EOF
{
  "test_ids": ["cell_001", "cell_002", "cell_003"],
  "hash": "$SPLIT_HASH",
  "created_at": "2026-05-23T10:00:00Z",
  "fraction": 0.2
}
EOF

    # Checkpoints — each "weight file" must be ≥ param_count bytes for the
    # param-consistency cross-check. We claim 25M params, so write 25M bytes of zeros.
    # head -c 25M is much faster than dd and works on all unixes.
    for seed in 0 1 2; do
        head -c 25M /dev/zero > "$DIR/checkpoints/seed_$seed/model.pt"
    done

    # Metrics
    cat > "$DIR/metrics.json" <<EOF
{
  "param_count": 25000000,
  "seeds": [0, 1, 2],
  "downstream_tasks": {
    "task1": {"metric": 0.85, "std": 0.02, "baseline_score": 0.78, "p_value": 0.003},
    "task2": {"metric": 0.79, "std": 0.03, "baseline_score": 0.72, "p_value": 0.01}
  },
  "gpu_hours_used": 18.4,
  "wall_clock_hours": 6.2
}
EOF

    # Ablation result files
    for d in "$DIR"/ablations/*/; do
        echo '{"effect_size": 0.05}' > "$d/result.json"
    done

    # Reproducibility
    cat > "$DIR/requirements.txt" <<EOF
torch==2.1.0
numpy==1.26.0
EOF

    # RESULT.md with full charter compliance table
    cat > "$DIR/RESULT.md" <<'EOF'
METRIC=0.85
KEY_FINDING=The group-attention layer beats the per-cell baseline by 7% absolute on task1.
COST=18.4 GPU-hours
ARTIFACTS=checkpoints/, ablations/, data/test_split.json
DONE_READY=false

## Summary

Group-attention pooling outperforms baseline on both downstream tasks.

## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS | full-data run, no shortcuts |
| 1. Data rules | PASS | data/test_split.json with hash, no leakage |
| 2. Architecture rules | PASS | 25M params, baseline included |
| 3. Training rules | PASS | 3 seeds, converged, 12 HP trials |
| 4. Evaluation rules | PASS | 2 tasks reported with std + p-values |
| 5. Ablation rules | PASS | 4 ablations done |
| 6. Novelty rules | PASS | cited Geneformer 2023 and scGPT 2024 |
| 7. Reproducibility rules | PASS | requirements.txt locked |
| 8. Compute honesty | PASS | 18.4 GPU-hours actual / 24h budget |
EOF
}

# Helper: assert validator exit code matches expected
expect_exit() {
    local DESC="$1"
    local EXPECTED="$2"
    shift 2
    local OUT
    set +e
    OUT=$(python3 "$VALIDATOR" "$@" 2>/dev/null)
    local CODE=$?
    set -e
    if [ "$CODE" != "$EXPECTED" ]; then
        echo "FAIL: $DESC — expected exit $EXPECTED, got $CODE"
        echo "  output:"
        echo "$OUT" | sed 's/^/    /'
        exit 1
    fi
    echo "  PASS: $DESC (exit $CODE)"
}

echo "=== test 1: perfect branch passes ==="
B="$TMP/perfect"
build_perfect_branch "$B"
expect_exit "perfect branch returns PASS" 0 "$B"

echo "=== test 2: missing RESULT.md fails ==="
B="$TMP/no_result"
build_perfect_branch "$B"
rm "$B/RESULT.md"
expect_exit "missing RESULT.md fails" 2 "$B"

echo "=== test 3: missing charter table fails ==="
B="$TMP/no_table"
build_perfect_branch "$B"
cat > "$B/RESULT.md" <<EOF
METRIC=0.85
KEY_FINDING=stuff
COST=0
ARTIFACTS=none
DONE_READY=false
EOF
expect_exit "missing charter table fails" 2 "$B"

echo "=== test 4: charter strict rule FAIL → overall FAIL ==="
B="$TMP/strict_fail"
build_perfect_branch "$B"
sed -i 's/| 3\. Training rules | PASS/| 3. Training rules | FAIL/' "$B/RESULT.md"
expect_exit "strict FAIL in table fails overall" 2 "$B"

echo "=== test 5: charter strict rule WARN → overall FAIL ==="
B="$TMP/strict_warn"
build_perfect_branch "$B"
sed -i 's/| 3\. Training rules | PASS/| 3. Training rules | WARN/' "$B/RESULT.md"
expect_exit "strict WARN in table fails overall (strict cannot WARN)" 2 "$B"

echo "=== test 6: missing test_split.json fails ==="
B="$TMP/no_split"
build_perfect_branch "$B"
rm "$B/data/test_split.json"
expect_exit "missing test_split fails" 2 "$B"

echo "=== test 7: test_split.json missing 'hash' fails ==="
B="$TMP/split_no_hash"
build_perfect_branch "$B"
cat > "$B/data/test_split.json" <<EOF
{"test_ids": ["a", "b"], "created_at": "2026-05-23"}
EOF
expect_exit "test_split missing hash fails" 2 "$B"

echo "=== test 8: only 2 seeds fails ==="
B="$TMP/two_seeds"
build_perfect_branch "$B"
rm -rf "$B/checkpoints/seed_2"
expect_exit "2 seed dirs fails (need ≥3)" 2 "$B"

echo "=== test 9: seed dir without checkpoint file fails ==="
B="$TMP/empty_seed"
build_perfect_branch "$B"
rm "$B/checkpoints/seed_2/model.pt"
expect_exit "empty seed dir fails" 2 "$B"

echo "=== test 10: param_count below floor fails ==="
B="$TMP/small_model"
build_perfect_branch "$B"
sed -i 's/"param_count": 25000000/"param_count": 500000/' "$B/metrics.json"
expect_exit "param_count < 10M fails" 2 "$B"

echo "=== test 11: missing downstream_tasks p_value fails ==="
B="$TMP/no_pvalue"
build_perfect_branch "$B"
python3 -c "
import json
p = '$B/metrics.json'
m = json.load(open(p))
del m['downstream_tasks']['task1']['p_value']
json.dump(m, open(p, 'w'))
"
expect_exit "missing p_value in downstream task fails" 2 "$B"

echo "=== test 12: only 3 ablations (need 4) fails ==="
B="$TMP/few_ablations"
build_perfect_branch "$B"
rm -rf "$B/ablations/cross_batch"
expect_exit "<4 ablations fails" 2 "$B"

echo "=== test 13: missing env file fails ==="
B="$TMP/no_env"
build_perfect_branch "$B"
rm "$B/requirements.txt"
expect_exit "missing env file fails" 2 "$B"

echo "=== test 14: --require-codex-audit + missing audit fails ==="
B="$TMP/no_audit"
build_perfect_branch "$B"
expect_exit "missing CODEX_AUDIT.json fails when required" 2 "$B" --require-codex-audit

echo "=== test 15: --require-codex-audit + verdict=FAIL fails ==="
B="$TMP/audit_fail"
build_perfect_branch "$B"
cat > "$B/CODEX_AUDIT.json" <<EOF
{"verdict": "FAIL", "reasoning": "The model architecture is identical to scGPT but rebranded"}
EOF
expect_exit "codex verdict=FAIL fails when required" 2 "$B" --require-codex-audit

echo "=== test 16: --require-codex-audit needs nonce path (v0.3.1) ==="
# v0.3.1 (codex review P0-3): --require-codex-audit without nonce file used to
# silently skip the SHA cross-check. Now it FAILs — caller must pass
# --audit-nonce-file OR put AUDIT_NONCE inside the branch.
B="$TMP/audit_no_nonce"
build_perfect_branch "$B"
cat > "$B/CODEX_AUDIT.json" <<EOF
{"verdict": "PASS", "reasoning_summary": "Honest, well-instrumented branch."}
EOF
expect_exit "codex audit without nonce now FAILs" 2 "$B" --require-codex-audit

echo "=== test 16b: --require-codex-audit + nonce + sha cross-check passes ==="
B="$TMP/audit_pass"
build_perfect_branch "$B"
NONCE16="real-nonce-test16b"
echo "$NONCE16" > "$B/AUDIT_NONCE"
RESULT_SHA=$(sha256sum "$B/RESULT.md" | awk '{print $1}')
METRICS_SHA=$(sha256sum "$B/metrics.json" | awk '{print $1}')
SPLIT_SHA=$(sha256sum "$B/data/test_split.json" | awk '{print $1}')
cat > "$B/CODEX_AUDIT.json" <<EOF
{
  "nonce": "$NONCE16",
  "verdict": "PASS",
  "reasoning_summary": "Honest, well-instrumented branch.",
  "files_read": {
    "RESULT.md": "$RESULT_SHA",
    "metrics.json": "$METRICS_SHA",
    "data/test_split.json": "$SPLIT_SHA"
  }
}
EOF
expect_exit "codex verdict=PASS with nonce + sha cross-check passes" 0 "$B" --require-codex-audit --audit-nonce-file "$B/AUDIT_NONCE"

echo "=== test 17: DONE_READY=true + missing KILL_ARGUMENT.md fails ==="
B="$TMP/done_no_kill"
build_perfect_branch "$B"
sed -i 's/^DONE_READY=false$/DONE_READY=true/' "$B/RESULT.md"
expect_exit "DONE_READY=true requires KILL_ARGUMENT.md" 2 "$B"

echo "=== test 18: DONE_READY=true with KILL_ARGUMENT.md + all PASS passes ==="
B="$TMP/done_ok"
build_perfect_branch "$B"
sed -i 's/^DONE_READY=false$/DONE_READY=true/' "$B/RESULT.md"
cat > "$B/KILL_ARGUMENT.md" <<EOF
# Self-rejection memo

Reviewer: this paper is just X with bigger data...

# Defense

Actually we beat X by 7% on task1 and ...
EOF
expect_exit "DONE_READY=true with KILL_ARGUMENT.md + all PASS = PASS" 0 "$B"

echo "=== test 19: soft rule FAIL → WARN (not FAIL) ==="
B="$TMP/soft_fail"
build_perfect_branch "$B"
sed -i 's/| 6\. Novelty rules | PASS/| 6. Novelty rules | FAIL/' "$B/RESULT.md"
expect_exit "soft rule FAIL is WARN not FAIL" 1 "$B"

echo "=== test 20: missing rule in table fails ==="
B="$TMP/missing_rule"
build_perfect_branch "$B"
sed -i '/| 5\. Ablation rules /d' "$B/RESULT.md"
expect_exit "missing strict rule entry fails" 2 "$B"

echo "=== test 21: empty checkpoint file (touch model.pt) fails ==="
B="$TMP/empty_ckpt"
build_perfect_branch "$B"
# Wipe one checkpoint to size 0 — the "touch model.pt" cheat
> "$B/checkpoints/seed_1/model.pt"
expect_exit "empty checkpoint file fails (size < 1024)" 2 "$B"

echo "=== test 22: param_count contradicts checkpoint size ==="
B="$TMP/param_lie"
build_perfect_branch "$B"
# Claim 1 billion params but the actual files are only 25 MB each
python3 -c "
import json
p = '$B/metrics.json'
m = json.load(open(p))
m['param_count'] = 1_000_000_000
json.dump(m, open(p, 'w'))
"
# RESULT.md table still says PASS for arch rule — validator should catch the size lie
expect_exit "param_count vs checkpoint size mismatch fails" 2 "$B"

echo "=== test 23: nonce mismatch in CODEX_AUDIT.json fails ==="
B="$TMP/nonce_mismatch"
build_perfect_branch "$B"
echo "real-nonce-1234567890abcdef" > "$B/AUDIT_NONCE"
# Codex audit echoes the WRONG nonce — typical pre-fabricated audit case
RESULT_SHA=$(sha256sum "$B/RESULT.md" | awk '{print $1}')
METRICS_SHA=$(sha256sum "$B/metrics.json" | awk '{print $1}')
SPLIT_SHA=$(sha256sum "$B/data/test_split.json" | awk '{print $1}')
cat > "$B/CODEX_AUDIT.json" <<EOF
{
  "nonce": "wrong-nonce-attacker-guessed",
  "verdict": "PASS",
  "reasoning_summary": "looks fine",
  "files_read": {
    "RESULT.md": "$RESULT_SHA",
    "metrics.json": "$METRICS_SHA",
    "data/test_split.json": "$SPLIT_SHA"
  }
}
EOF
expect_exit "nonce mismatch fails" 2 "$B" --require-codex-audit --audit-nonce-file "$B/AUDIT_NONCE"

echo "=== test 24: sha256 mismatch in CODEX_AUDIT.json fails ==="
B="$TMP/sha_mismatch"
build_perfect_branch "$B"
NONCE="legit-nonce-abc123"
echo "$NONCE" > "$B/AUDIT_NONCE"
# Codex echoes correct nonce but claims wrong SHA — file changed after audit, or audit fake
cat > "$B/CODEX_AUDIT.json" <<EOF
{
  "nonce": "$NONCE",
  "verdict": "PASS",
  "files_read": {
    "RESULT.md": "0000000000000000000000000000000000000000000000000000000000000000",
    "metrics.json": "0000000000000000000000000000000000000000000000000000000000000000",
    "data/test_split.json": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
EOF
expect_exit "sha256 mismatch fails" 2 "$B" --require-codex-audit --audit-nonce-file "$B/AUDIT_NONCE"

echo "=== test 25: missing files_read in CODEX_AUDIT.json fails ==="
B="$TMP/no_files_read"
build_perfect_branch "$B"
NONCE="nonce-26"
echo "$NONCE" > "$B/AUDIT_NONCE"
cat > "$B/CODEX_AUDIT.json" <<EOF
{"nonce": "$NONCE", "verdict": "PASS", "reasoning_summary": "fine"}
EOF
expect_exit "missing files_read fails when nonce mode enabled" 2 "$B" --require-codex-audit --audit-nonce-file "$B/AUDIT_NONCE"

echo "=== test 26: correct nonce + correct sha passes ==="
B="$TMP/audit_legit"
build_perfect_branch "$B"
NONCE="real-nonce-deadbeef"
echo "$NONCE" > "$B/AUDIT_NONCE"
RESULT_SHA=$(sha256sum "$B/RESULT.md" | awk '{print $1}')
METRICS_SHA=$(sha256sum "$B/metrics.json" | awk '{print $1}')
SPLIT_SHA=$(sha256sum "$B/data/test_split.json" | awk '{print $1}')
cat > "$B/CODEX_AUDIT.json" <<EOF
{
  "nonce": "$NONCE",
  "verdict": "PASS",
  "reasoning_summary": "actually inspected files",
  "files_read": {
    "RESULT.md": "$RESULT_SHA",
    "metrics.json": "$METRICS_SHA",
    "data/test_split.json": "$SPLIT_SHA"
  }
}
EOF
expect_exit "legitimate nonce + sha passes" 0 "$B" --require-codex-audit --audit-nonce-file "$B/AUDIT_NONCE"

echo
echo "PASS — all charter_validator.py smoke tests green."
