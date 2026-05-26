#!/usr/bin/env python3
"""
Charter validator — programmatic anti-laziness enforcement.

This script is the SECOND line of defense after the in-prompt charter. The
prompt tells the subagent to obey the charter, but LLMs sometimes claim
compliance without doing the work (write "seeds: 3" while only training 1).
This validator does **physical** checks on the filesystem and parses
RESULT.md to catch lies.

Usage:
    charter_validator.py <branch_dir> [--charter <path>] [--require-codex-audit]
        [--require-done-ready]

Exit codes:
    0  PASS — all strict rules met, branch eligible for status=completed
    1  WARN — soft rules failed, branch alive but flagged
    2  FAIL — at least one strict rule failed, caller must set status=dead

stdout: JSON {verdict, failures: [...], warnings: [...], evidence: {...}}
stderr: human-readable summary

Design philosophy:
- Every check is a filesystem or content predicate, not "trust the model"
- A missing file is FAIL; a present-but-malformed file is FAIL; only
  present-and-well-formed is PASS
- Numerical checks (param count, seed count) read JSON, not prose
- The charter compliance table inside RESULT.md is parsed, any strict FAIL
  → overall FAIL regardless of what other prose claims
- If --require-codex-audit, CODEX_AUDIT.json must exist with verdict=PASS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

STRICT_RULES = {
    "0. Anti-laziness preamble",
    "1. Data rules",
    "2. Architecture rules",
    "3. Training rules",
    "4. Evaluation rules",
    "5. Ablation rules",
    "7. Reproducibility rules",
    "8. Compute honesty",
}
SOFT_RULES = {"6. Novelty rules"}

# v0.1.6 — task-type-aware rule subsets.
# Different kinds of work (training a new model vs auditing a frozen one vs
# pulling external data) need different acceptance criteria. The charter
# compliance table inside RESULT.md only needs the rules listed for THIS
# task type; other rules may be omitted entirely.
TASK_TYPE_STRICT_RULES: dict[str, set[str]] = {
    # `training` — all original strict rules apply (v0.1.5 default, unchanged)
    "training": STRICT_RULES,
    # `audit` — model not retrained, no checkpoints / param-count / ablations
    # exist physically; the 4 surviving strict rules are data integrity (which
    # cohort / control), evaluation (statistical tests on FN delta etc),
    # reproducibility (download / preprocessing scripts), compute honesty.
    "audit": {
        "0. Anti-laziness preamble",
        "1. Data rules",
        "4. Evaluation rules",
        "7. Reproducibility rules",
        "8. Compute honesty",
    },
    # `analysis` — statistics / figure generation / report. No training, no
    # held-out test, but anti-laziness still applies (figure must be backed
    # by data) and reproducibility / compute honesty stay.
    "analysis": {
        "0. Anti-laziness preamble",
        "4. Evaluation rules",
        "7. Reproducibility rules",
        "8. Compute honesty",
    },
    # `data-acquisition` — download and verify external dataset; physical
    # artifact is the data manifest with checksums. No evaluation possible
    # at this stage.
    "data-acquisition": {
        "0. Anti-laziness preamble",
        "1. Data rules",
        "7. Reproducibility rules",
    },
    # `framing-decision` — human-only; autopilot should never execute it.
    # Validator immediately rejects to prevent silent bypass.
    "framing-decision": set(),
    # `mixed` — falls back to full training rule set (conservative).
    "mixed": STRICT_RULES,
}

VALID_TASK_TYPES_VALIDATOR = set(TASK_TYPE_STRICT_RULES.keys())

# Minimum physical thresholds — these are hard-coded floors. The charter
# document may set stricter thresholds; this validator enforces the floor.
MIN_SEEDS = 3
MIN_PARAM_COUNT = 10_000_000        # 10M, matches charter §2 default
MIN_ABLATIONS = 4                    # charter §5: headline + scale + data + cross-batch
MIN_CHECKPOINT_BYTES = 1024          # empty `touch model.pt` = 0; real model ≥ many MB.
                                      # A 10M-param model in FP32 is ~40 MB. Floor at 1 KB
                                      # catches the obvious "touch model.pt to fake it" case;
                                      # the param_count cross-check (below) catches subtler fakes.
PARAM_BYTES_FLOOR = 1                # 1 byte/param — even INT8 quantized would be this. Floor
                                      # for sanity check that checkpoint files are big enough
                                      # to actually hold the claimed param count.


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file_exists(path: Path, label: str, failures: list) -> bool:
    if not path.exists():
        failures.append(f"{label}: missing file {path}")
        return False
    if path.stat().st_size == 0:
        failures.append(f"{label}: file is empty {path}")
        return False
    return True


def parse_charter_table(result_md: str) -> dict[str, str]:
    """Parse the '## Charter compliance' markdown table in RESULT.md.

    Returns dict mapping rule name -> verdict (PASS/WARN/FAIL/unknown).
    Returns empty dict if table not found.
    """
    # Find the "## Charter compliance" section, then the first markdown table
    sec_match = re.search(
        r"##\s+Charter compliance\b.*?(?=\n##\s|\Z)",
        result_md,
        re.DOTALL,
    )
    if not sec_match:
        return {}
    section = sec_match.group(0)

    out: dict[str, str] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # skip header / separator
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        rule = cells[0]
        verdict = cells[1].upper()
        # normalize verdict: take last token (in case it's "PASS / WARN / FAIL")
        m = re.search(r"\b(PASS|WARN|FAIL)\b", verdict)
        if not m:
            continue
        # skip the header row literally containing "Verdict"
        if "VERDICT" in verdict:
            continue
        out[rule] = m.group(1)
    return out


def find_rule_verdict(table: dict[str, str], rule_prefix: str) -> str | None:
    """Find a rule in the parsed table by leading number prefix.

    The user may write '0. Anti-laziness preamble' or '0. Anti-laziness'
    or other variants. Match on the leading number.
    """
    prefix = rule_prefix.split(".")[0] + "."
    for k, v in table.items():
        if k.lstrip().startswith(prefix):
            return v
    return None


def check_result_md(
    branch_dir: Path,
    failures: list,
    warnings: list,
    evidence: dict,
    task_type: str = "training",
) -> dict[str, str]:
    """Check RESULT.md presence + parse the charter compliance table.

    v0.1.6: only enforces the strict rules listed for ``task_type``. The
    older signature ``check_result_md(branch_dir, failures, warnings, evidence)``
    still works because the parameter is keyword-only with a default.
    """
    result_path = branch_dir / "RESULT.md"
    if not check_file_exists(result_path, "RESULT.md", failures):
        return {}
    content = result_path.read_text()
    evidence["result_md_size"] = len(content)
    evidence["task_type"] = task_type

    # Must contain the METRIC line
    metric_match = re.search(r"^\s*METRIC\s*[=:]\s*([\d.eE+-]+)", content, re.MULTILINE)
    if not metric_match:
        failures.append("RESULT.md: no 'METRIC=<float>' line found")
    else:
        evidence["metric"] = float(metric_match.group(1))

    table = parse_charter_table(content)
    evidence["charter_table"] = table
    if not table:
        failures.append("RESULT.md: '## Charter compliance' table missing or unparseable")
        return {}

    # v0.1.6 — only enforce the strict rules that apply to THIS task_type
    strict_for_task = TASK_TYPE_STRICT_RULES.get(task_type, STRICT_RULES)
    for rule in strict_for_task:
        v = find_rule_verdict(table, rule)
        if v is None:
            failures.append(f"charter table: rule '{rule}' not present in audit table")
        elif v == "FAIL":
            failures.append(f"charter table: strict rule '{rule}' = FAIL (self-reported)")
        elif v == "WARN":
            failures.append(
                f"charter table: strict rule '{rule}' = WARN — strict rules cannot WARN, must PASS or be marked FAIL"
            )

    for rule in SOFT_RULES:
        v = find_rule_verdict(table, rule)
        if v == "FAIL":
            warnings.append(f"charter table: soft rule '{rule}' = FAIL (downgraded to warning)")

    return table


def check_data_rules(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Charter §1: held-out test set with hash, no leakage.

    v0.3.1 (codex review P1-2): not enough to check the `hash` field exists;
    we must recompute it from `test_ids` and confirm. Otherwise a subagent can
    write `"hash": "fakebeef"` and it counts. Recompute rule:
        sha256(json.dumps(sorted(test_ids), separators=(',', ':')).encode())
    Reproducible, sort-stable, agnostic to JSON whitespace.
    """
    test_split = branch_dir / "data" / "test_split.json"
    if not check_file_exists(test_split, "data/test_split.json", failures):
        return
    try:
        meta = json.loads(test_split.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"data/test_split.json: invalid JSON ({e})")
        return
    evidence["test_split"] = {"keys": sorted(meta.keys())}
    # Required keys per charter spec
    for key in ("test_ids", "hash", "created_at"):
        if key not in meta:
            failures.append(f"data/test_split.json: missing required key '{key}'")
    test_ids = meta.get("test_ids")
    # test_ids must be non-empty list
    if isinstance(test_ids, list) and len(test_ids) == 0:
        failures.append("data/test_split.json: 'test_ids' is empty")
    # v0.3.1 — actually recompute hash from test_ids and cross-check
    claimed_hash = meta.get("hash")
    if isinstance(test_ids, list) and isinstance(claimed_hash, str) and claimed_hash:
        canonical = json.dumps(sorted(map(str, test_ids)), separators=(",", ":")).encode()
        computed_hash = hashlib.sha256(canonical).hexdigest()
        evidence["test_split"]["claimed_hash"] = claimed_hash
        evidence["test_split"]["computed_hash"] = computed_hash
        if claimed_hash != computed_hash:
            failures.append(
                f"data/test_split.json: hash mismatch — claimed {claimed_hash!r}, "
                f"sha256(sorted(test_ids))={computed_hash!r}. Field-existence check "
                f"is not enough; the hash must actually reproduce."
            )


def check_training_rules(branch_dir: Path, failures: list, evidence: dict) -> dict[str, int]:
    """Charter §3: multi-seed ≥ 3, convergence, HP sweep.

    Returns: dict mapping seed_dir_name -> total checkpoint bytes (for the
    param_count vs filesize cross-check in check_metrics_json).
    """
    seed_sizes: dict[str, int] = {}
    checkpoints_dir = branch_dir / "checkpoints"
    if not checkpoints_dir.exists():
        failures.append("checkpoints/: directory missing — cannot verify multi-seed training")
        return seed_sizes
    seed_dirs = sorted(d for d in checkpoints_dir.iterdir()
                       if d.is_dir() and d.name.startswith("seed_"))
    evidence["seed_dirs"] = [d.name for d in seed_dirs]
    if len(seed_dirs) < MIN_SEEDS:
        failures.append(
            f"checkpoints/: only {len(seed_dirs)} seed_* dirs found, charter requires ≥ {MIN_SEEDS}"
        )
    # Each seed dir must have a checkpoint file of non-trivial size
    for sd in seed_dirs:
        ckpt_files = list(sd.glob("*.pt")) + list(sd.glob("*.pth")) + list(sd.glob("*.safetensors")) + list(sd.glob("*.ckpt"))
        if not ckpt_files:
            failures.append(f"checkpoints/{sd.name}/: no checkpoint file (*.pt|*.pth|*.safetensors|*.ckpt)")
            continue
        total = sum(f.stat().st_size for f in ckpt_files)
        seed_sizes[sd.name] = total
        if total < MIN_CHECKPOINT_BYTES:
            failures.append(
                f"checkpoints/{sd.name}/: total checkpoint size = {total} bytes "
                f"(< {MIN_CHECKPOINT_BYTES} floor — empty / fabricated file?)"
            )
    return seed_sizes


def check_param_count_consistency(branch_dir: Path, metrics: dict, seed_sizes: dict[str, int],
                                   failures: list, evidence: dict) -> None:
    """Cross-check claimed param_count against actual checkpoint file sizes.

    Catches: subagent writes `param_count: 25000000` in metrics.json but the
    checkpoint files only total 50 KB (clearly cannot hold 25M params).
    """
    pc = metrics.get("param_count")
    if not isinstance(pc, (int, float)) or pc <= 0:
        return  # already caught by check_metrics_json
    if not seed_sizes:
        return
    min_required_bytes = int(pc * PARAM_BYTES_FLOOR)
    consistent = sum(1 for s in seed_sizes.values() if s >= min_required_bytes)
    evidence["param_consistency"] = {
        "claimed_param_count": pc,
        "min_required_bytes_per_seed": min_required_bytes,
        "seed_sizes_bytes": seed_sizes,
        "seeds_meeting_floor": consistent,
    }
    if consistent < MIN_SEEDS:
        failures.append(
            f"param_count cross-check: only {consistent}/{len(seed_sizes)} seed dirs have "
            f"checkpoints ≥ {min_required_bytes} bytes (claimed param_count = {pc}). "
            f"Checkpoint files cannot physically hold that many parameters — fabricated metrics?"
        )


def _load_metrics(branch_dir: Path, failures: list) -> dict | None:
    """Load metrics.json once for use by both check_metrics_json and
    check_param_count_consistency. Returns None if malformed (failures already logged)."""
    metrics_path = branch_dir / "metrics.json"
    if not metrics_path.exists() or metrics_path.stat().st_size == 0:
        return None
    try:
        return json.loads(metrics_path.read_text())
    except json.JSONDecodeError:
        return None


def check_metrics_json(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Cross-checks numerical claims in RESULT.md against a structured metrics.json."""
    metrics_path = branch_dir / "metrics.json"
    if not check_file_exists(metrics_path, "metrics.json", failures):
        return
    try:
        m = json.loads(metrics_path.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"metrics.json: invalid JSON ({e})")
        return
    evidence["metrics_keys"] = sorted(m.keys())

    # param_count must be ≥ floor (charter §2)
    pc = m.get("param_count")
    if pc is None:
        failures.append("metrics.json: missing 'param_count'")
    elif not isinstance(pc, (int, float)) or pc < MIN_PARAM_COUNT:
        failures.append(
            f"metrics.json: param_count={pc} < {MIN_PARAM_COUNT} floor "
            f"(charter §2 parameter floor)"
        )

    # seeds: at least the declared count, matches seed dirs
    seeds = m.get("seeds")
    if seeds is None:
        failures.append("metrics.json: missing 'seeds' (must be list of seed ids)")
    elif not isinstance(seeds, list) or len(seeds) < MIN_SEEDS:
        failures.append(
            f"metrics.json: declared seeds={seeds} has < {MIN_SEEDS} entries"
        )

    # Each downstream task must report a metric with std
    tasks = m.get("downstream_tasks")
    if tasks is None:
        failures.append("metrics.json: missing 'downstream_tasks' (must be dict)")
    elif not isinstance(tasks, dict) or len(tasks) == 0:
        failures.append("metrics.json: 'downstream_tasks' is empty")
    else:
        for task_name, task_data in tasks.items():
            if not isinstance(task_data, dict):
                failures.append(f"metrics.json: downstream_tasks['{task_name}'] must be a dict")
                continue
            for required in ("metric", "std", "baseline_score", "p_value"):
                if required not in task_data:
                    failures.append(
                        f"metrics.json: downstream_tasks['{task_name}'] missing '{required}'"
                    )

    # Compute honesty
    if "gpu_hours_used" not in m:
        failures.append("metrics.json: missing 'gpu_hours_used' (charter §8)")
    if "wall_clock_hours" not in m:
        failures.append("metrics.json: missing 'wall_clock_hours' (charter §8)")


def check_ablations(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Charter §5: at least 4 ablations."""
    ablations_dir = branch_dir / "ablations"
    if not ablations_dir.exists():
        failures.append("ablations/: directory missing")
        return
    abl = sorted(d for d in ablations_dir.iterdir() if d.is_dir())
    evidence["ablations"] = [d.name for d in abl]
    if len(abl) < MIN_ABLATIONS:
        failures.append(
            f"ablations/: only {len(abl)} subdirs, charter §5 requires ≥ {MIN_ABLATIONS} "
            f"(headline component + scale + data + cross-batch)"
        )
    # Each ablation must have a result file
    for ad in abl:
        if not any(ad.glob("*.json")) and not any(ad.glob("*.md")):
            failures.append(f"ablations/{ad.name}/: no result file (*.json or *.md)")


def check_reproducibility(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Charter §7: env locked, code committed, data versioned."""
    env_files = [branch_dir / "requirements.txt", branch_dir / "environment.yml"]
    if not any(p.exists() and p.stat().st_size > 0 for p in env_files):
        failures.append(
            "reproducibility: no requirements.txt or environment.yml in branch_dir"
        )


CODEX_AUDIT_REQUIRED_FILES_BY_TASK_TYPE: dict[str, set[str]] = {
    "training": {"RESULT.md", "metrics.json", "data/test_split.json"},
    "mixed": {"RESULT.md", "metrics.json", "data/test_split.json"},
    "audit": {"RESULT.md", "audit_report.json"},
    "analysis": {"RESULT.md", "analysis_output.json"},
    "data-acquisition": {"RESULT.md", "DATA_MANIFEST.json"},
}


def check_codex_audit(branch_dir: Path, nonce_path: Path | None,
                      failures: list, evidence: dict,
                      task_type: str = "training") -> None:
    """External codex audit — independent of self-reported charter table.

    Anti-forgery defenses (v0.1.3):
    - `nonce`: orchestrator writes a fresh random nonce to AUDIT_NONCE before
      calling codex; codex must echo it back in CODEX_AUDIT.json. A subagent
      pre-writing CODEX_AUDIT.json cannot guess the nonce.
    - `files_read`: codex must list every file it read with its SHA256. The
      validator re-hashes the files itself and confirms they match. This proves
      codex actually opened the files (or at least had them at the right state).

    v0.1.9 — task_type-aware required-files set. Earlier versions hardcoded the
    training file set, which forced data-acquisition / audit / analysis nodes
    to fail pass-2 even when their physical artifacts were correct.
    """
    codex_path = branch_dir / "CODEX_AUDIT.json"
    if not check_file_exists(codex_path, "CODEX_AUDIT.json", failures):
        return
    try:
        audit = json.loads(codex_path.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"CODEX_AUDIT.json: invalid JSON ({e})")
        return
    evidence["codex_audit"] = {k: audit.get(k) for k in ("verdict", "reasoning_summary", "nonce")}
    verdict = audit.get("verdict", "").upper()
    if verdict != "PASS":
        reasoning = audit.get("reasoning", audit.get("reasoning_summary", "(no reasoning given)"))
        failures.append(f"CODEX_AUDIT.json: external verdict={verdict or 'MISSING'}. Reason: {reasoning}")
        return  # no point checking nonce/files if it already failed

    # v0.3.1 (codex review P0-3): nonce + SHA256 cross-check is NOT optional. If
    # --require-codex-audit was passed but no --audit-nonce-file, fall back to
    # branch_dir/AUDIT_NONCE; if that also missing, FAIL. Skipping the cross-check
    # was a trust kernel hole — an agent could pre-write CODEX_AUDIT.json and
    # the orchestrator would accept it as proof of codex review without proof
    # that codex actually saw the files.
    if nonce_path is None:
        fallback_nonce = branch_dir / "AUDIT_NONCE"
        if fallback_nonce.exists():
            nonce_path = fallback_nonce
        else:
            failures.append(
                "CODEX_AUDIT.json: --require-codex-audit set but no nonce file "
                "(neither --audit-nonce-file nor branch_dir/AUDIT_NONCE). "
                "Refusing to accept self-reported codex verdict — see signiture 2 "
                "(nonce + SHA256 cross-check) in design_principles.md."
            )
            return

    if not nonce_path.exists():
        failures.append(f"CODEX_AUDIT.json: nonce file {nonce_path} missing (orchestrator did not seed challenge)")
        return
    expected_nonce = nonce_path.read_text().strip()
    if not expected_nonce:
        failures.append(f"CODEX_AUDIT.json: nonce file {nonce_path} is empty")
        return
    got_nonce = str(audit.get("nonce", "")).strip()
    if got_nonce != expected_nonce:
        failures.append(
            f"CODEX_AUDIT.json: nonce mismatch (got {got_nonce!r}, expected {expected_nonce!r}). "
            f"This suggests the file was pre-written before the orchestrator's codex call."
        )
        return

    # Files-read SHA256 cross-check
    files_read = audit.get("files_read")
    if not isinstance(files_read, dict) or not files_read:
        failures.append(
            "CODEX_AUDIT.json: 'files_read' must be a dict {relative_path: sha256} "
            "with at least RESULT.md, metrics.json, data/test_split.json"
        )
        return
    required_files = CODEX_AUDIT_REQUIRED_FILES_BY_TASK_TYPE.get(
        task_type, CODEX_AUDIT_REQUIRED_FILES_BY_TASK_TYPE["training"]
    )
    missing_required = required_files - set(files_read.keys())
    if missing_required:
        failures.append(
            f"CODEX_AUDIT.json: 'files_read' missing required entries for "
            f"task_type={task_type}: {sorted(missing_required)}"
        )
        return
    # v0.4.0 codex-final P1-新2: files_read keys are model-controlled. Without
    # path-safety filtering, the model could put "/dev/zero" (sha256_file blocks
    # forever) or "../../../etc/passwd" (read outside branch_dir). Restrict to
    # entries in required_files set + ban absolute / dot-dot paths + verify
    # the resolved path stays inside branch_dir + require regular file.
    import os as _os
    import stat as _stat
    branch_dir_resolved = branch_dir.resolve()
    for rel_path, claimed_sha in files_read.items():
        if rel_path not in required_files:
            failures.append(
                f"CODEX_AUDIT.json: files_read references {rel_path!r} which is "
                f"not in the required-files set for task_type={task_type}. Refusing."
            )
            continue
        rel_p = Path(rel_path)
        if rel_p.is_absolute() or ".." in rel_p.parts:
            failures.append(
                f"CODEX_AUDIT.json: files_read path {rel_path!r} unsafe "
                f"(absolute or contains '..')"
            )
            continue
        actual_file = branch_dir / rel_path
        try:
            actual_resolved = actual_file.resolve()
        except OSError as e:
            failures.append(f"CODEX_AUDIT.json: files_read path {rel_path}: {e}")
            continue
        # Resolved path must stay inside branch_dir (symlink escape guard)
        try:
            actual_resolved.relative_to(branch_dir_resolved)
        except ValueError:
            failures.append(
                f"CODEX_AUDIT.json: files_read path {rel_path} resolves to "
                f"{actual_resolved}, outside branch_dir {branch_dir_resolved}. "
                f"Symlink escape or path injection."
            )
            continue
        try:
            st = _os.lstat(str(actual_file))
        except OSError as e:
            failures.append(f"CODEX_AUDIT.json: lstat {rel_path}: {e}")
            continue
        if not _stat.S_ISREG(st.st_mode):
            failures.append(
                f"CODEX_AUDIT.json: files_read path {rel_path} is not a regular "
                f"file (mode={oct(st.st_mode)}). Refusing to sha256 it — could "
                f"be /dev/zero, a fifo, or a symlink to anything."
            )
            continue
        if st.st_size > 200 * 1024 * 1024:  # 200 MB sanity cap
            failures.append(
                f"CODEX_AUDIT.json: files_read path {rel_path} is "
                f"{st.st_size} bytes — refusing to sha256 (would tie up the "
                f"validator). Audit pipeline should not be feeding huge files."
            )
            continue
        actual_sha = sha256_file(actual_file)
        if actual_sha != str(claimed_sha).strip():
            failures.append(
                f"CODEX_AUDIT.json: SHA256 mismatch for {rel_path} — codex claims "
                f"{claimed_sha}, actual {actual_sha}. File changed after audit, or audit fake?"
            )

    # v0.4.0 — challenge-fragment cross-check (real anti-fabrication).
    # codex_audit_cli.py writes a SANITIZED AUDIT_CHALLENGES.json AFTER the
    # codex call: {challenge_id: {file, offset, length}}. Crucially, the
    # file does NOT contain expected_text — that would let an agent read
    # its own branch_dir, copy the answers, and fabricate a CODEX_AUDIT.json
    # without ever calling codex.
    #
    # Validator's job:
    #   1. Read sanitized challenges.
    #   2. Re-read disk at (file, offset, length) to derive expected_text fresh.
    #   3. Compare against CODEX_AUDIT.json.challenge_responses byte-for-byte.
    # The disk read at step 2 is the ground truth: it's what the model saw
    # inline at audit time (assuming files haven't been edited between audit
    # and validation — that case is caught by the SHA cross-check above).
    #
    # v0.4.0 codex-final P1-2: enforce coverage. Each required_file must have
    # exactly N_REQUIRED_CHALLENGES_PER_FILE challenges (default 3), each 64
    # bytes (unless the file is shorter than 64 bytes), all referencing
    # relative paths inside branch_dir (reject absolute / dot-dot paths).
    challenges_path = branch_dir / "AUDIT_CHALLENGES.json"
    if not challenges_path.exists():
        failures.append(
            "AUDIT_CHALLENGES.json: missing — codex_audit_cli (v0.4.0+) writes "
            "this after a successful codex call. Either the audit was run with "
            "a pre-v0.4.0 tool (regenerate the audit) or the file was deleted "
            "to bypass the challenge-fragment check."
        )
        return
    try:
        challenges = json.loads(challenges_path.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"AUDIT_CHALLENGES.json: invalid JSON ({e})")
        return
    if not isinstance(challenges, dict) or not challenges:
        failures.append("AUDIT_CHALLENGES.json: empty or not a dict")
        return
    responses = audit.get("challenge_responses")
    if not isinstance(responses, dict) or not responses:
        failures.append(
            "CODEX_AUDIT.json: 'challenge_responses' missing or not a dict. "
            "v0.4.0 requires the model to quote verbatim fragments at random "
            "byte offsets of each required file. Re-run the audit with the "
            "current codex_audit_cli.py."
        )
        return

    # P1-2 — count challenges per required file. Each must have exactly 3
    # (matches codex_audit_cli.py N_CHALLENGES_PER_FILE).
    N_REQUIRED_CHALLENGES_PER_FILE = 3
    CHALLENGE_FRAGMENT_BYTES = 64
    challenges_per_file: dict[str, int] = {f: 0 for f in required_files}

    # P1-1 prep: load file contents once for re-derivation
    file_contents_cache: dict[str, str] = {}
    for rel in required_files:
        p = branch_dir / rel
        if p.exists():
            try:
                file_contents_cache[rel] = p.read_text(errors="replace")
            except OSError:
                pass  # already complained above

    challenge_evidence: dict[str, str] = {}
    for cid, ch in challenges.items():
        rel = ch.get("file") if isinstance(ch, dict) else None
        offset = ch.get("offset") if isinstance(ch, dict) else None
        length = ch.get("length") if isinstance(ch, dict) else None
        if rel is None or offset is None or length is None:
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid!r} malformed "
                f"(needs file, offset, length — no expected_text since v0.4.0)"
            )
            continue
        # P1-2 — refuse absolute, dot-dot, or non-required-file challenges.
        # Path injection or out-of-scope reads must not pass.
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid} references unsafe "
                f"path {rel!r} (absolute or contains '..')"
            )
            continue
        if rel not in required_files:
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid} references {rel!r} "
                f"which is not in the required-files set for task_type={task_type}"
            )
            continue
        # P1-2 — enforce CHALLENGE_FRAGMENT_BYTES unless file is shorter
        disk_content = file_contents_cache.get(rel)
        if disk_content is None:
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid} references {rel} "
                f"which does not exist on disk now"
            )
            continue
        if length != CHALLENGE_FRAGMENT_BYTES and length != len(disk_content):
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid} length={length} "
                f"must be {CHALLENGE_FRAGMENT_BYTES} (or file length if smaller)"
            )
            continue
        if not isinstance(offset, int) or offset < 0 or offset + length > len(disk_content):
            failures.append(
                f"AUDIT_CHALLENGES.json: challenge {cid} offset={offset} + "
                f"length={length} out of bounds for {rel} (size={len(disk_content)})"
            )
            continue

        challenges_per_file[rel] = challenges_per_file.get(rel, 0) + 1

        # P1-1 — re-derive expected_text from disk (ground truth at validation
        # time); the model's quote must match. If files changed since audit
        # was generated, the SHA cross-check above already caught that.
        expected_text = disk_content[offset:offset + length]
        model_response = responses.get(cid)
        if not isinstance(model_response, str):
            failures.append(
                f"CODEX_AUDIT.json: challenge_responses[{cid!r}] missing or "
                f"not a string. Model failed to quote the fragment."
            )
            continue
        # Anti-fabrication tolerance: LLMs cannot reliably count to exact
        # char offset N in a 4KB file (token != char). If the model's quote
        # is a real substring of the disk content (length ≥ 32 chars to
        # avoid trivial matches), it demonstrably read the file — the
        # off-by-N is just counting noise. A fabricator cannot produce a
        # 32+ char substring of unread content.
        MIN_SUBSTRING_LEN = 32
        if model_response == expected_text:
            challenge_evidence[cid] = "match"
            continue
        if (
            model_response
            and len(model_response) >= MIN_SUBSTRING_LEN
            and model_response in disk_content
        ):
            challenge_evidence[cid] = "substring_match"
            continue
        failures.append(
            f"CODEX_AUDIT.json: challenge {cid} ({rel} chars "
            f"[{offset}..{offset + length})) — model quoted "
            f"{model_response[:80]!r}, disk has {expected_text[:80]!r}. "
            f"Mismatch means the model fabricated the audit without "
            f"reading the inlined file."
        )
        continue

    # P1-2 — enforce coverage: every required file must have N challenges.
    # If the file is shorter than 64 bytes, 1 challenge (whole file) suffices.
    for rel in required_files:
        n = challenges_per_file.get(rel, 0)
        content_len = len(file_contents_cache.get(rel, ""))
        required_n = 1 if content_len < CHALLENGE_FRAGMENT_BYTES else N_REQUIRED_CHALLENGES_PER_FILE
        if n != required_n:
            failures.append(
                f"AUDIT_CHALLENGES.json: required file {rel!r} has {n} "
                f"challenges, expected exactly {required_n}. "
                f"Coverage shortfall would let the agent answer only easy "
                f"challenges and skip hard files."
            )

    evidence["challenge_fragments"] = {
        "total": len(challenges),
        "matched": sum(1 for v in challenge_evidence.values() if v == "match"),
        "per_file": challenges_per_file,
    }


def check_done_ready(
    branch_dir: Path,
    table: dict[str, str],
    failures: list,
    evidence: dict,
    task_type: str = "training",
) -> None:
    """When done_ready=true, every strict rule MUST PASS (not just most).

    v0.1.6: only enforces the strict rules listed for ``task_type``.
    """
    result_path = branch_dir / "RESULT.md"
    if not result_path.exists():
        return
    content = result_path.read_text()
    m = re.search(r"^\s*DONE_READY\s*[=:]\s*(true|false)", content, re.IGNORECASE | re.MULTILINE)
    if not m or m.group(1).lower() != "true":
        return  # not claiming done_ready, nothing extra to check

    evidence["done_ready_claimed"] = True
    strict_for_task = TASK_TYPE_STRICT_RULES.get(task_type, STRICT_RULES)
    for rule in strict_for_task:
        v = find_rule_verdict(table, rule)
        if v != "PASS":
            failures.append(
                f"DONE_READY=true requires '{rule}' = PASS, got {v}"
            )
    # Kill-argument self-rejection memo must exist (charter §6)
    kill_arg = branch_dir / "KILL_ARGUMENT.md"
    if not kill_arg.exists():
        failures.append(
            "DONE_READY=true requires KILL_ARGUMENT.md (charter §6 /kill-argument audit)"
        )


# =============================================================================
# v0.1.6 — task-type-specific physical artifact checks
# =============================================================================

def check_audit_artifacts(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Audit-mode physical checks: an audit branch must produce its verdict
    JSON (cohort + control + FN delta + 95% CI), donor-level bootstrap, and
    a protocol comparison (within-atlas vs cross-batch). No checkpoints.
    """
    audit_report = branch_dir / "audit_report.json"
    if not check_file_exists(audit_report, "audit_report.json", failures):
        return
    try:
        report = json.loads(audit_report.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"audit_report.json: invalid JSON ({e})")
        return
    evidence["audit_report_keys"] = sorted(report.keys())
    # Required schema: must declare cohort & control sizes + at least one
    # signal metric (FN delta / FP delta / consensus score) + a 95% CI
    required = {
        "cohort_summary": dict,
        "blindspot_signal": dict,
    }
    for key, typ in required.items():
        if key not in report:
            failures.append(f"audit_report.json: missing required key '{key}'")
        elif not isinstance(report[key], typ):
            failures.append(
                f"audit_report.json: '{key}' must be {typ.__name__}, got {type(report[key]).__name__}"
            )
    # bootstrap with donor-level CI (charter §4 statistical rigor)
    boot_path = branch_dir / "donor_bootstrap.json"
    if not check_file_exists(boot_path, "donor_bootstrap.json", failures):
        return
    try:
        boot = json.loads(boot_path.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"donor_bootstrap.json: invalid JSON ({e})")
        return
    evidence["donor_bootstrap_keys"] = sorted(boot.keys())
    if "n_iter" not in boot:
        failures.append("donor_bootstrap.json: missing 'n_iter'")
    elif not isinstance(boot["n_iter"], int) or boot["n_iter"] < 1000:
        failures.append(
            f"donor_bootstrap.json: n_iter={boot.get('n_iter')} < 1000 (charter §4 statistical rigor)"
        )
    # protocol_comparison.json — within-atlas vs cross-batch is the
    # methodological core of an audit branch
    protocol = branch_dir / "protocol_comparison.json"
    if not check_file_exists(protocol, "protocol_comparison.json", failures):
        return
    try:
        proto = json.loads(protocol.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"protocol_comparison.json: invalid JSON ({e})")
        return
    evidence["protocol_comparison_keys"] = sorted(proto.keys())
    for key in ("within_atlas_fn_delta", "cross_batch_fn_delta", "over_estimation_ratio"):
        if key not in proto:
            failures.append(f"protocol_comparison.json: missing required key '{key}'")


def check_analysis_artifacts(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Analysis-mode physical checks: any structured output JSON plus at
    least one figure file (PNG / PDF / SVG)."""
    analysis = branch_dir / "analysis_output.json"
    if not check_file_exists(analysis, "analysis_output.json", failures):
        return
    try:
        out = json.loads(analysis.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"analysis_output.json: invalid JSON ({e})")
        return
    evidence["analysis_keys"] = sorted(out.keys())
    figures_dir = branch_dir / "figures"
    if figures_dir.exists():
        fig_files = (
            list(figures_dir.glob("*.png"))
            + list(figures_dir.glob("*.pdf"))
            + list(figures_dir.glob("*.svg"))
        )
        evidence["figure_count"] = len(fig_files)
        if not fig_files:
            failures.append("figures/: no figure file (*.png|*.pdf|*.svg)")
    else:
        # not strictly required — analysis can be statistics-only
        evidence["figure_count"] = 0


def check_data_acquisition_artifacts(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Data-acquisition mode: a manifest with checksum-verified files."""
    manifest = branch_dir / "DATA_MANIFEST.json"
    if not check_file_exists(manifest, "DATA_MANIFEST.json", failures):
        return
    try:
        m = json.loads(manifest.read_text())
    except json.JSONDecodeError as e:
        failures.append(f"DATA_MANIFEST.json: invalid JSON ({e})")
        return
    evidence["manifest_keys"] = sorted(m.keys())
    required = ("atlas_id", "source_url", "local_path", "checksum", "n_cells", "downloaded_at")
    for key in required:
        if key not in m:
            failures.append(f"DATA_MANIFEST.json: missing required key '{key}'")
    # Verify the downloaded file(s) actually exist on disk.
    # v0.1.9: accept both string (single file) and list-of-strings (multi-file pulls
    # like MSigDB Hallmark + Reactome). Earlier code crashed on list inputs.
    local_rel = m.get("local_path")
    claimed_checksum = m.get("checksum")
    if local_rel is not None:
        local_paths_raw = local_rel if isinstance(local_rel, list) else [local_rel]
        total_bytes = 0
        actual_shas: list[str] = []
        for lp in local_paths_raw:
            if not isinstance(lp, str):
                failures.append(
                    f"DATA_MANIFEST.json: 'local_path' entry not a string: {lp!r}"
                )
                continue
            local_path = branch_dir / lp if not Path(lp).is_absolute() else Path(lp)
            if not local_path.exists():
                failures.append(
                    f"DATA_MANIFEST.json: 'local_path' = {lp} but file does not exist on disk"
                )
            else:
                total_bytes += local_path.stat().st_size
                actual_shas.append(sha256_file(local_path))
        if total_bytes:
            evidence["local_file_bytes"] = total_bytes
        # v0.3.1 (codex review P1-2): actually recompute SHA256 and compare,
        # don't trust field existence. Single file: checksum == sha256(file).
        # Multi-file: checksum is dict {filename: sha256} OR a list[sha256] in
        # the same order as local_path.
        evidence["computed_sha256"] = actual_shas
        if isinstance(claimed_checksum, str) and len(actual_shas) == 1:
            evidence["claimed_checksum"] = claimed_checksum
            if claimed_checksum.lower() != actual_shas[0].lower():
                failures.append(
                    f"DATA_MANIFEST.json: checksum mismatch — claimed {claimed_checksum!r}, "
                    f"sha256({local_paths_raw[0]})={actual_shas[0]!r}. Field-existence "
                    f"check alone is not enough; sha256 must reproduce."
                )
        elif isinstance(claimed_checksum, dict) and len(actual_shas) > 0:
            evidence["claimed_checksum"] = claimed_checksum
            for lp, actual_sha in zip(local_paths_raw, actual_shas):
                want = claimed_checksum.get(lp) or claimed_checksum.get(Path(lp).name)
                if not want:
                    failures.append(
                        f"DATA_MANIFEST.json: checksum dict has no entry for {lp!r}"
                    )
                elif str(want).lower() != actual_sha.lower():
                    failures.append(
                        f"DATA_MANIFEST.json: checksum mismatch for {lp} — "
                        f"claimed {want!r}, actual sha256={actual_sha!r}."
                    )
        elif isinstance(claimed_checksum, list) and len(claimed_checksum) == len(actual_shas):
            for lp, want, actual_sha in zip(local_paths_raw, claimed_checksum, actual_shas):
                if str(want).lower() != actual_sha.lower():
                    failures.append(
                        f"DATA_MANIFEST.json: checksum mismatch for {lp} — "
                        f"claimed {want!r}, actual sha256={actual_sha!r}."
                    )


def check_framing_decision(branch_dir: Path, failures: list, evidence: dict) -> None:
    """Framing-decision branches are human-only. If autopilot ever calls
    the validator on one, that's an enforcement bug — fail loudly so the
    user notices."""
    failures.append(
        "task_type=framing-decision is human-only — autopilot should NEVER "
        "execute this branch. Mark the node `human_only=true` in tree.json "
        "and skip it via pick-next, or run /research-tree prune <id> with "
        "an explicit reason."
    )


def _resolve_task_type(args, branch_dir: Path) -> str:
    """v0.1.6 — figure out which task_type to validate against.

    Resolution order:
      1. Explicit ``--task-type`` CLI flag
      2. tree.json node's ``task_type`` field (via branch_dir → ancestor lookup)
      3. fallback to ``training`` (preserves v0.1.5 default behavior)
    """
    if getattr(args, "task_type", None):
        return args.task_type
    # Climb to find .research-tree/tree.json
    cur = branch_dir
    for _ in range(8):  # safety cap
        candidate = cur / ".research-tree" / "tree.json"
        if candidate.exists():
            try:
                state = json.loads(candidate.read_text())
                node_id = branch_dir.name
                node = state.get("nodes", {}).get(node_id)
                if node and node.get("task_type"):
                    return node["task_type"]
            except (json.JSONDecodeError, OSError):
                pass
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return "training"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("branch_dir", help="path to .research-tree/branches/<id>/")
    ap.add_argument("--charter", default=None, help="optional path to RESEARCH_CHARTER.md (for future use)")
    ap.add_argument("--require-codex-audit", action="store_true",
                    help="fail if CODEX_AUDIT.json missing or verdict != PASS")
    ap.add_argument("--audit-nonce-file", default=None,
                    help="path to AUDIT_NONCE file written by orchestrator before codex call. "
                         "Enforces nonce match + files_read SHA256 cross-check in CODEX_AUDIT.json.")
    # v0.1.6 — task-type-aware validation
    ap.add_argument(
        "--task-type",
        default=None,
        choices=sorted(VALID_TASK_TYPES_VALIDATOR),
        help="which task-type rule subset to enforce. If omitted, validator "
             "reads the node's task_type from .research-tree/tree.json; "
             "defaults to 'training' (v0.1.5 behavior) if neither is set.",
    )
    args = ap.parse_args()

    branch_dir = Path(args.branch_dir).resolve()
    if not branch_dir.is_dir():
        print(json.dumps({"verdict": "FAIL", "failures": [f"branch_dir does not exist: {branch_dir}"]}))
        print(f"FAIL: branch_dir not a directory: {branch_dir}", file=sys.stderr)
        return 2

    nonce_path = Path(args.audit_nonce_file).resolve() if args.audit_nonce_file else None
    task_type = _resolve_task_type(args, branch_dir)

    failures: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"branch_dir": str(branch_dir), "task_type": task_type}

    # framing-decision short-circuits — autopilot should never reach here
    if task_type == "framing-decision":
        check_framing_decision(branch_dir, failures, evidence)
    else:
        # RESULT.md + charter compliance table (task-type-aware rule subset)
        table = check_result_md(branch_dir, failures, warnings, evidence, task_type=task_type)

        # Dispatch task-type-specific physical artifact checks
        if task_type == "training" or task_type == "mixed":
            check_data_rules(branch_dir, failures, evidence)
            seed_sizes = check_training_rules(branch_dir, failures, evidence)
            metrics = _load_metrics(branch_dir, failures)
            check_metrics_json(branch_dir, failures, evidence)
            if metrics is not None:
                check_param_count_consistency(branch_dir, metrics, seed_sizes, failures, evidence)
            check_ablations(branch_dir, failures, evidence)
        elif task_type == "audit":
            check_audit_artifacts(branch_dir, failures, evidence)
        elif task_type == "analysis":
            check_analysis_artifacts(branch_dir, failures, evidence)
        elif task_type == "data-acquisition":
            check_data_acquisition_artifacts(branch_dir, failures, evidence)

        # Common to all non-framing task types
        check_reproducibility(branch_dir, failures, evidence)
        if args.require_codex_audit:
            check_codex_audit(branch_dir, nonce_path, failures, evidence, task_type=task_type)
        check_done_ready(branch_dir, table, failures, evidence, task_type=task_type)

    if failures:
        verdict = "FAIL"
        code = 2
    elif warnings:
        verdict = "WARN"
        code = 1
    else:
        verdict = "PASS"
        code = 0

    report = {
        "verdict": verdict,
        "task_type": task_type,
        "failures": failures,
        "warnings": warnings,
        "evidence": evidence,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"\n=== charter_validator [{task_type}]: {verdict} ===", file=sys.stderr)
    for f in failures:
        print(f"  FAIL  {f}", file=sys.stderr)
    for w in warnings:
        print(f"  WARN  {w}", file=sys.stderr)

    return code


if __name__ == "__main__":
    sys.exit(main())
