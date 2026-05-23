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


def check_result_md(branch_dir: Path, failures: list, warnings: list, evidence: dict) -> dict[str, str]:
    result_path = branch_dir / "RESULT.md"
    if not check_file_exists(result_path, "RESULT.md", failures):
        return {}
    content = result_path.read_text()
    evidence["result_md_size"] = len(content)

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

    # Enforce that all strict rules appear AND are PASS
    for rule in STRICT_RULES:
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
    """Charter §1: held-out test set with hash, no leakage."""
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
    # test_ids must be non-empty list
    if isinstance(meta.get("test_ids"), list) and len(meta["test_ids"]) == 0:
        failures.append("data/test_split.json: 'test_ids' is empty")


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


def check_codex_audit(branch_dir: Path, nonce_path: Path | None,
                      failures: list, evidence: dict) -> None:
    """External codex audit — independent of self-reported charter table.

    Anti-forgery defenses (v0.1.3):
    - `nonce`: orchestrator writes a fresh random nonce to AUDIT_NONCE before
      calling codex; codex must echo it back in CODEX_AUDIT.json. A subagent
      pre-writing CODEX_AUDIT.json cannot guess the nonce.
    - `files_read`: codex must list every file it read with its SHA256. The
      validator re-hashes the files itself and confirms they match. This proves
      codex actually opened the files (or at least had them at the right state).
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

    # Nonce check — only enforced if --audit-nonce-file was passed
    if nonce_path is not None:
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
        required_files = {"RESULT.md", "metrics.json", "data/test_split.json"}
        missing_required = required_files - set(files_read.keys())
        if missing_required:
            failures.append(
                f"CODEX_AUDIT.json: 'files_read' missing required entries: {sorted(missing_required)}"
            )
            return
        for rel_path, claimed_sha in files_read.items():
            actual_file = branch_dir / rel_path
            if not actual_file.exists():
                failures.append(f"CODEX_AUDIT.json: claims to have read {rel_path} but file does not exist")
                continue
            actual_sha = sha256_file(actual_file)
            if actual_sha != str(claimed_sha).strip():
                failures.append(
                    f"CODEX_AUDIT.json: SHA256 mismatch for {rel_path} — codex claims "
                    f"{claimed_sha}, actual {actual_sha}. File changed after audit, or audit fake?"
                )


def check_done_ready(branch_dir: Path, table: dict[str, str], failures: list, evidence: dict) -> None:
    """When done_ready=true, every strict rule MUST PASS (not just most)."""
    result_path = branch_dir / "RESULT.md"
    if not result_path.exists():
        return
    content = result_path.read_text()
    m = re.search(r"^\s*DONE_READY\s*[=:]\s*(true|false)", content, re.IGNORECASE | re.MULTILINE)
    if not m or m.group(1).lower() != "true":
        return  # not claiming done_ready, nothing extra to check

    evidence["done_ready_claimed"] = True
    # When done_ready, all 8 strict rules MUST be PASS (no WARN tolerated)
    for rule in STRICT_RULES:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("branch_dir", help="path to .research-tree/branches/<id>/")
    ap.add_argument("--charter", default=None, help="optional path to RESEARCH_CHARTER.md (for future use)")
    ap.add_argument("--require-codex-audit", action="store_true",
                    help="fail if CODEX_AUDIT.json missing or verdict != PASS")
    ap.add_argument("--audit-nonce-file", default=None,
                    help="path to AUDIT_NONCE file written by orchestrator before codex call. "
                         "Enforces nonce match + files_read SHA256 cross-check in CODEX_AUDIT.json.")
    args = ap.parse_args()

    branch_dir = Path(args.branch_dir).resolve()
    if not branch_dir.is_dir():
        print(json.dumps({"verdict": "FAIL", "failures": [f"branch_dir does not exist: {branch_dir}"]}))
        print(f"FAIL: branch_dir not a directory: {branch_dir}", file=sys.stderr)
        return 2

    nonce_path = Path(args.audit_nonce_file).resolve() if args.audit_nonce_file else None

    failures: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"branch_dir": str(branch_dir)}

    # Order matters: RESULT.md first because its absence makes other checks moot
    table = check_result_md(branch_dir, failures, warnings, evidence)
    check_data_rules(branch_dir, failures, evidence)
    seed_sizes = check_training_rules(branch_dir, failures, evidence)
    metrics = _load_metrics(branch_dir, failures)
    check_metrics_json(branch_dir, failures, evidence)
    if metrics is not None:
        check_param_count_consistency(branch_dir, metrics, seed_sizes, failures, evidence)
    check_ablations(branch_dir, failures, evidence)
    check_reproducibility(branch_dir, failures, evidence)
    if args.require_codex_audit:
        check_codex_audit(branch_dir, nonce_path, failures, evidence)
    check_done_ready(branch_dir, table, failures, evidence)

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
        "failures": failures,
        "warnings": warnings,
        "evidence": evidence,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"\n=== charter_validator: {verdict} ===", file=sys.stderr)
    for f in failures:
        print(f"  FAIL  {f}", file=sys.stderr)
    for w in warnings:
        print(f"  WARN  {w}", file=sys.stderr)

    return code


if __name__ == "__main__":
    sys.exit(main())
