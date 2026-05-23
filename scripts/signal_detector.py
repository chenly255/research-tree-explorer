#!/usr/bin/env python3
"""signal_detector — classify completed branch results as STRONG / WEAK / NULL.

Solves the "POC Krishna +0.338 → Li 2022 +0.014, 24x dilution" failure mode
that ate sc-bias Stage 1: a strong pilot signal led to wholesale buy-in,
then cross-atlas reality forced a paper-framing rewrite. The autopilot
should detect "all sibling branches are NULL" *programmatically* and
propose re-framing candidates without waiting for the human to notice.

Inputs:
    A branch directory (.research-tree/branches/<node_id>/) that already
    contains RESULT.md and optionally task-type-specific artifacts:
      - audit_report.json (task_type=audit) — preferred source: reads
        blindspot_signal.{fn_delta, ci_low, ci_hi, verdict}
      - metrics.json (task_type=training) — reads
        downstream_tasks[*].p_value and metric vs baseline
      - RESULT.md (fallback) — parses METRIC=<float>, CI_LOW=, CI_HI=,
        P_VALUE=, EFFECT_SIZE=, BASELINE=

Classification (default thresholds, override via charter cfg):
    STRONG : CI excludes zero AND |effect| >= strong_min_effect
             AND p_value < 0.05 (or absent)
    WEAK   : CI excludes zero BUT |effect| < strong_min_effect
             (i.e., statistically positive but small)
    NULL   : CI crosses zero, OR p_value >= null_p_threshold,
             OR |effect| < null_max_effect
    UNKNOWN: not enough info to classify (no CI, no p-value, no effect)

Aggregate (siblings mode):
    For a set of sibling branches at the same junction, classify the
    junction:
      ALL_STRONG     : every sibling STRONG
      MIXED_POSITIVE : at least one STRONG, rest WEAK/STRONG
      ALL_WEAK       : every sibling WEAK
      ALL_NULL       : every sibling NULL → AUTO-PIVOT TRIGGER
      MOSTLY_NULL    : >=2/3 NULL → AUTO-PIVOT TRIGGER (warning)
      INSUFFICIENT   : not enough completed siblings to judge

Usage:
    # classify one branch
    signal_detector.py classify .research-tree/branches/<node_id>/

    # aggregate siblings (pass parent node id; reads tree.json)
    signal_detector.py aggregate <parent_node_id> --project-root <project>

    # check whether autopilot should fire auto-pivot
    signal_detector.py check-pivot --project-root <project>
    # exit 0 = no pivot needed; exit 10 = pivot recommended (writes
    # AUTO_PIVOT_PROPOSAL.md candidate file at project root)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default thresholds. A project can override via charter.signal_thresholds
# in tree.json's root metadata (future extension; falling back to defaults).
DEFAULTS = {
    "strong_min_effect": 0.10,   # |effect| >= 0.10 with CI excluding 0 = STRONG
    "null_max_effect": 0.05,     # |effect| <  0.05 = NULL regardless of CI
    "null_p_threshold": 0.5,     # p >= 0.5 → NULL (failing to reject null)
    "min_siblings_for_aggregate": 2,  # need at least 2 sibling completed branches
    "auto_pivot_min_null_fraction": 0.67,  # ≥2/3 NULL siblings → pivot
}


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _parse_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().strip("[]()")
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _scan_result_md(branch_dir: Path) -> dict[str, Any]:
    """Pull METRIC / CI_LOW / CI_HI / P_VALUE / EFFECT_SIZE / BASELINE from RESULT.md.

    Conventions (extension of the existing RESULT.md format):
        METRIC=<float>           # required (already in v0.1.5)
        EFFECT_SIZE=<float>      # optional; defaults to METRIC - BASELINE if both present
        BASELINE=<float>         # optional
        CI_LOW=<float>           # optional
        CI_HI=<float>            # optional
        P_VALUE=<float>          # optional
        CI=[<low>, <hi>]         # alternative compact form
    """
    md = branch_dir / "RESULT.md"
    if not md.exists():
        return {}
    txt = md.read_text(errors="replace")
    out: dict[str, Any] = {}
    for key in ("METRIC", "EFFECT_SIZE", "BASELINE", "CI_LOW", "CI_HI", "P_VALUE"):
        m = re.search(rf"^{key}\s*[=:]\s*(.+)$", txt, re.MULTILINE)
        if m:
            v = _parse_float(m.group(1).split()[0])
            if v is not None:
                out[key.lower()] = v
    # compact CI=[lo, hi] form
    if "ci_low" not in out:
        m = re.search(r"^CI\s*[=:]\s*\[?\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\]?", txt, re.MULTILINE)
        if m:
            out["ci_low"] = float(m.group(1))
            out["ci_hi"] = float(m.group(2))
    return out


def _audit_report_signal(branch_dir: Path) -> dict[str, Any] | None:
    rep = _read_json(branch_dir / "audit_report.json")
    if not rep or "blindspot_signal" not in rep:
        return None
    bs = rep["blindspot_signal"]
    return {
        "source": "audit_report.json",
        "effect_size": bs.get("fn_delta"),
        "ci_low": bs.get("ci_low"),
        "ci_hi": bs.get("ci_hi"),
        "p_value": bs.get("p_value"),
        "verdict_recorded": bs.get("verdict"),
    }


def _training_metrics_signal(branch_dir: Path) -> dict[str, Any] | None:
    m = _read_json(branch_dir / "metrics.json")
    if not m:
        return None
    tasks = m.get("downstream_tasks") or {}
    if not isinstance(tasks, dict) or not tasks:
        return None
    # Use the first task as headline; later versions can pick by charter.
    name, t = next(iter(tasks.items()))
    metric = t.get("metric")
    baseline = t.get("baseline_score")
    effect = None
    if isinstance(metric, (int, float)) and isinstance(baseline, (int, float)):
        effect = float(metric) - float(baseline)
    return {
        "source": f"metrics.json:downstream_tasks[{name}]",
        "effect_size": effect,
        "p_value": t.get("p_value"),
        "metric": metric,
        "baseline": baseline,
        "std": t.get("std"),
    }


def classify_one(branch_dir: Path, cfg: dict | None = None) -> dict[str, Any]:
    """Return {verdict, evidence, source, raw}."""
    cfg = {**DEFAULTS, **(cfg or {})}
    raw: dict[str, Any] = {}

    sig = _audit_report_signal(branch_dir) or _training_metrics_signal(branch_dir)
    if sig is None:
        # fallback: RESULT.md scan
        scanned = _scan_result_md(branch_dir)
        if not scanned:
            return {
                "branch_dir": str(branch_dir),
                "verdict": "UNKNOWN",
                "reason": "no audit_report.json, no metrics.json, no parseable RESULT.md",
                "raw": {},
            }
        raw = scanned
        effect = scanned.get("effect_size")
        if effect is None and "metric" in scanned and "baseline" in scanned:
            effect = scanned["metric"] - scanned["baseline"]
        if effect is None:
            effect = scanned.get("metric")
        ci_low = scanned.get("ci_low")
        ci_hi = scanned.get("ci_hi")
        p_value = scanned.get("p_value")
        source = "RESULT.md"
    else:
        raw = sig
        effect = sig.get("effect_size")
        ci_low = sig.get("ci_low")
        ci_hi = sig.get("ci_hi")
        p_value = sig.get("p_value")
        source = sig["source"]

    if effect is None:
        return {
            "branch_dir": str(branch_dir),
            "verdict": "UNKNOWN",
            "reason": f"no effect-size / METRIC parseable from {source}",
            "raw": raw,
        }

    abs_effect = abs(effect)
    has_ci = ci_low is not None and ci_hi is not None
    ci_crosses_zero = has_ci and (ci_low <= 0 <= ci_hi)
    # p_value here is interpreted as "probability of failing to reject null"
    # (standard frequentist), so high p = NULL signal. Bootstrap-style
    # reproducibility metrics (e.g., sc-bias's "P=1.000 = 100% reproducible
    # across donors") should NOT be reported in this field — projects using
    # such metrics should set p_value=None and rely on CI exclusion alone.
    # When CI is present and informative, CI overrides p_value semantics
    # (CI exclusion is a stronger statement than a single p_value number).

    # Tier 1: |effect| too small → NULL regardless of significance
    if abs_effect < cfg["null_max_effect"]:
        verdict = "NULL"
        reason = (
            f"|effect|={abs_effect:.4f} < null_max_effect={cfg['null_max_effect']} "
            "— signal too small to chase"
        )
    # Tier 2: CI present and informative → use CI exclusion
    elif has_ci:
        if ci_crosses_zero:
            verdict = "NULL"
            reason = (
                f"CI [{ci_low}, {ci_hi}] crosses zero; |effect|={abs_effect:.4f}"
            )
        elif abs_effect >= cfg["strong_min_effect"]:
            verdict = "STRONG"
            reason = (
                f"|effect|={abs_effect:.4f} ≥ strong_min_effect={cfg['strong_min_effect']}, "
                f"CI [{ci_low}, {ci_hi}] excludes zero"
            )
        else:
            verdict = "WEAK"
            reason = (
                f"|effect|={abs_effect:.4f} < strong_min_effect={cfg['strong_min_effect']} "
                f"but CI [{ci_low}, {ci_hi}] excludes zero"
            )
    # Tier 3: no CI → fall back to p_value (only when explicitly provided)
    elif p_value is not None:
        if p_value >= cfg["null_p_threshold"]:
            verdict = "NULL"
            reason = (
                f"no CI available; p_value={p_value} ≥ null_p_threshold={cfg['null_p_threshold']} "
                "(treating as failure-to-reject-null)"
            )
        elif p_value < 0.05 and abs_effect >= cfg["strong_min_effect"]:
            verdict = "STRONG"
            reason = f"no CI; p_value={p_value} < 0.05 and |effect|={abs_effect:.4f} ≥ strong_min_effect"
        else:
            verdict = "WEAK"
            reason = f"no CI; p_value={p_value}, |effect|={abs_effect:.4f}"
    # Tier 4: no CI, no p_value → trust effect size alone (cautiously)
    else:
        if abs_effect >= cfg["strong_min_effect"]:
            verdict = "STRONG"
            reason = (
                f"|effect|={abs_effect:.4f} ≥ strong_min_effect={cfg['strong_min_effect']}; "
                "WARNING no CI / p_value — verdict provisional"
            )
        else:
            verdict = "WEAK"
            reason = f"|effect|={abs_effect:.4f}; WARNING no CI / p_value — verdict provisional"

    return {
        "branch_dir": str(branch_dir),
        "verdict": verdict,
        "reason": reason,
        "effect": effect,
        "ci_low": ci_low,
        "ci_hi": ci_hi,
        "p_value": p_value,
        "source": source,
        "raw": raw,
    }


def aggregate(parent_id: str, project_root: Path, cfg: dict | None = None) -> dict[str, Any]:
    """Look up parent's children, classify each completed child, summarize."""
    cfg = {**DEFAULTS, **(cfg or {})}
    tree_path = project_root / ".research-tree" / "tree.json"
    if not tree_path.exists():
        return {"error": f"tree.json not found at {tree_path}"}
    state = json.loads(tree_path.read_text())
    nodes = state.get("nodes", {})
    parent = nodes.get(parent_id)
    if parent is None:
        return {"error": f"parent {parent_id} not in tree"}

    child_ids = [nid for nid, n in nodes.items() if n.get("parent_id") == parent_id]
    classifications: list[dict[str, Any]] = []
    for cid in child_ids:
        child = nodes[cid]
        if child.get("status") != "completed":
            classifications.append({
                "node_id": cid, "title": child.get("title"),
                "status": child.get("status"), "verdict": "N/A",
            })
            continue
        bdir = project_root / ".research-tree" / "branches" / cid
        c = classify_one(bdir, cfg)
        c["node_id"] = cid
        c["title"] = child.get("title")
        classifications.append(c)

    # Aggregate
    completed = [c for c in classifications if c.get("verdict") in {"STRONG", "WEAK", "NULL", "UNKNOWN"}]
    n = len(completed)
    null_count = sum(1 for c in completed if c["verdict"] == "NULL")
    strong_count = sum(1 for c in completed if c["verdict"] == "STRONG")
    weak_count = sum(1 for c in completed if c["verdict"] == "WEAK")

    if n < cfg["min_siblings_for_aggregate"]:
        agg = "INSUFFICIENT"
        pivot_recommended = False
    elif null_count == n:
        agg = "ALL_NULL"
        pivot_recommended = True
    elif null_count / n >= cfg["auto_pivot_min_null_fraction"]:
        agg = "MOSTLY_NULL"
        pivot_recommended = True
    elif strong_count == n:
        agg = "ALL_STRONG"
        pivot_recommended = False
    elif weak_count == n:
        agg = "ALL_WEAK"
        pivot_recommended = False
    elif strong_count >= 1:
        agg = "MIXED_POSITIVE"
        pivot_recommended = False
    else:
        agg = "MIXED"
        pivot_recommended = False

    return {
        "parent_id": parent_id,
        "parent_title": parent.get("title"),
        "n_children": len(child_ids),
        "n_completed": n,
        "n_strong": strong_count,
        "n_weak": weak_count,
        "n_null": null_count,
        "aggregate_verdict": agg,
        "pivot_recommended": pivot_recommended,
        "classifications": classifications,
    }


def check_pivot(project_root: Path, cfg: dict | None = None) -> dict[str, Any]:
    """Scan every junction with ≥2 completed children. Return aggregates +
    list of junctions where auto-pivot is recommended."""
    cfg = {**DEFAULTS, **(cfg or {})}
    tree_path = project_root / ".research-tree" / "tree.json"
    if not tree_path.exists():
        return {"error": f"tree.json not found at {tree_path}"}
    state = json.loads(tree_path.read_text())
    nodes = state.get("nodes", {})

    junctions: list[str] = []
    for nid, n in nodes.items():
        child_ids = [cid for cid, c in nodes.items() if c.get("parent_id") == nid]
        if len(child_ids) >= cfg["min_siblings_for_aggregate"]:
            junctions.append(nid)

    aggregates: list[dict[str, Any]] = []
    pivot_junctions: list[dict[str, Any]] = []
    for jid in junctions:
        a = aggregate(jid, project_root, cfg)
        if "error" in a:
            continue
        aggregates.append(a)
        if a.get("pivot_recommended"):
            pivot_junctions.append(a)

    return {
        "project_root": str(project_root),
        "n_junctions_scanned": len(junctions),
        "n_pivot_recommended": len(pivot_junctions),
        "aggregates": aggregates,
        "pivot_junctions": pivot_junctions,
    }


def write_pivot_proposal(check_result: dict, project_root: Path) -> Path:
    """When auto-pivot fires, write AUTO_PIVOT_PROPOSAL.md at the project
    root listing the dead-signal junctions and prompting the autopilot to
    spawn re-framing branches at the next expand step.

    The proposal is a *signal* file; the actual re-framing branches are
    created by autopilot reading this file and running an expand on a
    pivot-decision parent node (or by surfacing to the human via DONE.md /
    surface-to-human if the proposed candidates include any human_only
    framing changes)."""
    out = project_root / ".research-tree" / "AUTO_PIVOT_PROPOSAL.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# AUTO_PIVOT_PROPOSAL",
        "",
        f"written_at: {now}",
        f"trigger: signal_detector found {check_result['n_pivot_recommended']} junction(s) "
        "with all/most NULL sibling branches",
        "",
        "## Why this fires",
        "",
        "A research junction expanded into ≥2 sibling branches that all came back",
        "NULL or majority-NULL after the validation chain. That is the same failure",
        "mode that broke sc-bias Stage 1 (Krishna POC strong → cross-atlas null → human",
        "had to re-frame paper). Autopilot should now treat this junction as a dead",
        "approach and either:",
        "",
        "  (a) re-frame the question (the sibling branches were all asking the WRONG",
        "      question — pivot to a different framing of the same root idea), OR",
        "  (b) escalate to the human if the re-frame implies changing paper headline /",
        "      venue / claim wording (those are `framing-decision` + `human_only=true`",
        "      and the human is the only authority).",
        "",
        "## Dead-signal junctions",
        "",
    ]
    for a in check_result["pivot_junctions"]:
        lines.append(f"### junction `{a['parent_id']}` — {a['parent_title']}")
        lines.append("")
        lines.append(f"- aggregate: **{a['aggregate_verdict']}**")
        lines.append(
            f"- {a['n_null']}/{a['n_completed']} completed children classified NULL "
            f"({a['n_strong']} STRONG, {a['n_weak']} WEAK)"
        )
        lines.append("- children:")
        for c in a["classifications"]:
            v = c.get("verdict", "N/A")
            t = c.get("title", "<no title>")
            e = c.get("effect")
            ci_low = c.get("ci_low")
            ci_hi = c.get("ci_hi")
            ci_str = f", CI [{ci_low}, {ci_hi}]" if ci_low is not None else ""
            lines.append(f"    - `{c.get('node_id')}` [{v}] — {t} — effect={e}{ci_str}")
        lines.append("")
        lines.append("- next autopilot move: in the next `expand` cycle, propose 2-4 RE-FRAMING")
        lines.append(f"  candidates as new siblings under junction `{a['parent_id']}`. Each candidate")
        lines.append("  must answer: \"given that <existing approach> is null on these N replicates,")
        lines.append("  what RE-FRAMING of the question would make signal show up — without lowering")
        lines.append("  the evidence bar?\" Candidates that change paper headline / venue must be")
        lines.append("  `task_type=framing-decision` + `human_only=true` (autopilot does NOT execute).")
        lines.append("")

    lines.append("## Acknowledgement protocol")
    lines.append("")
    lines.append("After autopilot reads this file and creates the re-framing siblings (or surfaces")
    lines.append("the human-only ones), it should rename this file to `AUTO_PIVOT_PROPOSAL.handled.md`")
    lines.append("so the next check-pivot cycle does not re-trigger on the same junctions.")
    out.write_text("\n".join(lines))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_c = sub.add_parser("classify", help="classify one branch directory")
    sp_c.add_argument("branch_dir")

    sp_a = sub.add_parser("aggregate", help="aggregate sibling verdicts at a junction")
    sp_a.add_argument("parent_id")
    sp_a.add_argument("--project-root", default=".")

    sp_p = sub.add_parser("check-pivot", help="scan whole tree for auto-pivot triggers")
    sp_p.add_argument("--project-root", default=".")
    sp_p.add_argument("--write-proposal", action="store_true",
                      help="if any junction triggers pivot, write AUTO_PIVOT_PROPOSAL.md and exit 10")

    args = ap.parse_args()

    if args.cmd == "classify":
        out = classify_one(Path(args.branch_dir).resolve())
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "aggregate":
        out = aggregate(args.parent_id, Path(args.project_root).resolve())
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "check-pivot":
        root = Path(args.project_root).resolve()
        out = check_pivot(root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        if args.write_proposal and out.get("n_pivot_recommended", 0) > 0:
            # don't write if AUTO_PIVOT_PROPOSAL.md already exists and is unhandled
            existing = root / ".research-tree" / "AUTO_PIVOT_PROPOSAL.md"
            if existing.exists():
                print(f"# AUTO_PIVOT_PROPOSAL.md already exists at {existing}, not overwriting",
                      file=sys.stderr)
            else:
                p = write_pivot_proposal(out, root)
                print(f"# wrote {p}", file=sys.stderr)
            return 10
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
