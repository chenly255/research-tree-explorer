#!/usr/bin/env python3
"""
Synthesize a FINAL_REPORT.md from the tree state.

Reads .research-tree/tree.json and writes .research-tree/FINAL_REPORT.md
covering: what we explored, what worked, what died and why, and a
suggested next move.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

STATE_DIR_NAME = ".research-tree"
STATE_FILE_NAME = "tree.json"
REPORT_FILE_NAME = "FINAL_REPORT.md"


def load_state(root: Path) -> dict:
    p = root / STATE_DIR_NAME / STATE_FILE_NAME
    if not p.exists():
        sys.exit(f"ERROR: no tree found at {p}")
    with p.open() as f:
        return json.load(f)


def render_tree(state: dict) -> list[str]:
    lines: list[str] = []

    def walk(node_id: str, prefix: str, is_last: bool) -> None:
        n = state["nodes"][node_id]
        marker = {"completed": "✓", "dead": "✗", "running": "►", "expanded": "▸", "pending": "·"}.get(
            n["status"], "?"
        )
        score = f" [{n['score']:.2f}]" if n["score"] is not None else ""
        connector = "└── " if is_last else "├── "
        if node_id == "root":
            lines.append(f"{marker} root: {n['title']}")
        else:
            lines.append(f"{prefix}{connector}{marker} {n['id']}{score} {n['title']}")
        children = n["children"]
        new_prefix = prefix + ("    " if is_last else "│   ")
        if node_id == "root":
            new_prefix = ""
        for i, c in enumerate(children):
            walk(c, new_prefix, i == len(children) - 1)

    walk("root", "", True)
    return lines


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", default=os.getcwd())
    args = p.parse_args()

    root = Path(args.project_root).resolve()
    state = load_state(root)

    nodes = state["nodes"]
    completed = sorted(
        [n for n in nodes.values() if n["status"] == "completed"],
        key=lambda x: x["score"] if x["score"] is not None else -1,
        reverse=True,
    )
    dead = [n for n in nodes.values() if n["status"] == "dead"]
    alive = [
        n for n in nodes.values()
        if n["status"] in ("pending", "expanded", "running") and n["id"] != "root"
    ]

    lines: list[str] = []
    lines.append(f"# Research Tree Final Report")
    lines.append("")
    lines.append(f"**Project**: {state['project']}")
    lines.append(f"**Root idea**: {state['root_idea']}")
    lines.append(f"**Started**: {state['created_at']}")
    lines.append(f"**Updated**: {state['last_updated']}")
    lines.append("")

    lines.append("## Tree shape")
    lines.append("")
    lines.append("```")
    lines.extend(render_tree(state))
    lines.append("```")
    lines.append("")

    s = state["stats"]
    lines.append("## Numbers")
    lines.append("")
    lines.append(f"- Nodes explored : {s['nodes_total']}")
    lines.append(f"- Completed      : {s['nodes_completed']}")
    lines.append(f"- Dead branches  : {s['nodes_dead']}")
    lines.append(f"- Still alive    : {s['nodes_alive']}")
    lines.append(f"- GPU hours used : {s['gpu_hours_used']:.1f}")
    lines.append("")

    if completed:
        lines.append("## What worked")
        lines.append("")
        winner = completed[0]
        lines.append(f"**Winner — `{winner['id']}` (score {winner['score']:.2f}): {winner['title']}**")
        lines.append("")
        lines.append(winner.get("description", "") or "")
        lines.append("")
        if winner.get("branch_dir"):
            lines.append(f"Artifacts: `{winner['branch_dir']}/`")
            lines.append("")
        if len(completed) > 1:
            lines.append("Other completed branches (lower-ranked):")
            lines.append("")
            for n in completed[1:]:
                lines.append(f"- `{n['id']}` (score {n['score']:.2f}): {n['title']}")
            lines.append("")
    else:
        lines.append("## What worked")
        lines.append("")
        lines.append("_No branch reached a completed state._")
        lines.append("")

    if dead:
        lines.append("## What died and why (dead-branch atlas)")
        lines.append("")
        for n in sorted(dead, key=lambda x: x["id"]):
            reason = n.get("death_reason") or "(no reason recorded)"
            score = f" (score {n['score']:.2f})" if n.get("score") is not None else ""
            lines.append(f"- **`{n['id']}` — {n['title']}**{score}")
            lines.append(f"  - reason: {reason}")
            if n.get("death_evidence"):
                lines.append(f"  - evidence: `{n['death_evidence']}`")
        lines.append("")

    if alive:
        lines.append("## Still alive (not pursued, why?)")
        lines.append("")
        for n in alive:
            lines.append(f"- `{n['id']}` ({n['status']}): {n['title']}")
        lines.append("")
        lines.append("These branches were not pursued — either budget ran out, or another branch reached the goal first.")
        lines.append("")

    if state.get("audits"):
        lines.append("## Junction audits")
        lines.append("")
        for aid, a in sorted(state["audits"].items()):
            lines.append(f"- **{aid}** at junction `{a['junction']}` by {a['reviewer']} ({a['timestamp']})")
            lines.append(f"  - verdict: {a['verdict']}")
            if a.get("trace_file"):
                lines.append(f"  - trace: `{a['trace_file']}`")
        lines.append("")

    # Root-failure detection: all direct children of root are dead.
    root_node = nodes["root"]
    root_children = [nodes[c] for c in root_node["children"]]
    all_root_dead = bool(root_children) and all(c["status"] == "dead" for c in root_children)

    # DONE detection: requires (a) status=completed, (b) done_ready=true,
    # (c) completion_proof recorded (proves status was set via the validator
    # chain, not a raw `set status=completed` bypass).
    done_winner = next(
        (n for n in completed
         if n.get("done_ready") is True and n.get("completion_proof") is not None),
        None,
    )
    if done_winner is not None:
        done_path = root / STATE_DIR_NAME / "DONE.md"
        done_lines = [
            "# Project DONE — awaiting human review",
            "",
            f"Winner: **`{done_winner['id']}` — {done_winner['title']}**",
            f"Score: {done_winner['score']:.4f} on primary metric",
            f"Branch dir: `{done_winner.get('branch_dir', 'N/A')}/`",
            "",
            "## Why autopilot stopped here",
            "",
            f"The branch's subagent self-attested `done_ready=true` AND the programmatic "
            f"charter validator confirmed all strict rules PASS AND the external codex "
            f"audit returned verdict=PASS. See:",
            "",
            f"- `{done_winner.get('branch_dir', 'branches/<id>')}/RESULT.md` — full result + charter table",
            f"- `{done_winner.get('branch_dir', 'branches/<id>')}/CODEX_AUDIT.json` — external auditor verdict",
            f"- `{done_winner.get('branch_dir', 'branches/<id>')}/KILL_ARGUMENT.md` — self-rejection memo + defense",
            "",
            "## Hand-off to human (you)",
            "",
            "Autopilot does NOT auto-write the paper. Recommended manual review:",
            "",
            "1. Read RESULT.md end-to-end — sanity-check the numbers and the audit table.",
            "2. Walk the branch_dir — open metrics.json, eyeball checkpoints/ and ablations/.",
            "3. Look at CODEX_AUDIT.json — what did the external reviewer flag, even if PASS?",
            "4. Compare against the dead-branch atlas in FINAL_REPORT.md — does the winner",
            "   genuinely dominate the losers, or did it just survive longest?",
            "5. If satisfied, you (the human) decide whether to write the paper, run more",
            "   ablations, or pivot. Autopilot will not act further until you re-enable it.",
            "",
            "## Re-open exploration",
            "",
            "```",
            "rm .research-tree/DONE.md",
            f"python3 .../tree_state.py set {done_winner['id']} done_ready=false",
            "```",
        ]
        done_path.write_text("\n".join(done_lines) + "\n")
        print(f"OK: also wrote DONE.md to {done_path}")

    lines.append("## Suggested next move")
    lines.append("")

    if done_winner is not None:
        lines.append(
            f"**DONE — HUMAN REVIEW REQUIRED.** Winner `{done_winner['id']}` "
            f"({done_winner['title']}) passed self-attestation, programmatic charter "
            f"validation, and external codex audit. Autopilot has stopped. "
            f"Artifacts at `{done_winner.get('branch_dir', 'N/A')}/`. "
            f"See `.research-tree/DONE.md` for the human-review checklist. "
            f"Autopilot will NOT auto-write a paper — that decision is yours."
        )
        lines.append("")
        lines.append("To reopen exploration:")
        lines.append("")
        lines.append("```")
        lines.append("rm .research-tree/DONE.md")
        lines.append(f"python3 .../tree_state.py set {done_winner['id']} done_ready=false")
        lines.append("```")
        lines.append("")
    elif all_root_dead:
        # Pivot path — write ROOT_FAILURE.md too so the autopilot loop can detect it.
        lines.append(
            "**PIVOT** — every approach under root is dead. The current idea is unlikely to "
            "work as framed. Suggested action:"
        )
        lines.append("")
        lines.append(
            "1. Read the dead-branch atlas above; the failure modes are the next idea's anti-targets.")
        lines.append(
            "2. Archive this tree: `mv .research-tree .research-tree.failed-$(date +%Y%m%d)`")
        lines.append(
            "3. Re-run `/idea-pipeline` with the dead-branch reasons as input; it will refine "
            "the idea to dodge what we just learned.")
        lines.append(
            "4. The refreshed `RESEARCH_BRIEF.md` will then feed a new `/research-tree init`.")
        lines.append("")
        root_failure_path = root / STATE_DIR_NAME / "ROOT_FAILURE.md"
        root_failure_path.write_text(
            f"# Root-level failure detected\n\n"
            f"All {len(root_children)} direct children of root are dead. "
            f"This idea is not making forward progress.\n\n"
            f"See FINAL_REPORT.md \"What died\" section for dead-branch atlas.\n\n"
            f"Recommended action: re-run /idea-pipeline with these reasons as input.\n"
        )
        print(f"OK: also wrote ROOT_FAILURE.md to {root_failure_path}")
    elif completed:
        winner = completed[0]
        winner_dir = winner.get("branch_dir", "N/A")
        threshold_strong = winner["score"] is not None and winner["score"] >= 0.80
        lines.append(
            f"Winner so far: **`{winner['id']}` — {winner['title']}** (score "
            f"{winner['score']:.2f}). Three live options, pick by current goal:"
        )
        lines.append("")
        lines.append(
            f"**(a) Deepen the winner** — open ablations / scale-up / baseline sub-branches "
            f"under `{winner['id']}`. Use `/research-tree expand {winner['id']}` or just "
            f"`/research-tree autopilot`. Pick this if you're not yet sure the winner is "
            f"publishable-strong."
        )
        if alive:
            lines.append("")
            lines.append(
                f"**(b) Resolve remaining alive branches first** — {len(alive)} branches are "
                f"still pending. Running them gives a fairer junction picture and may unseat "
                f"the current winner."
            )
        lines.append("")
        paper_strength = "looks strong" if threshold_strong else "may not be strong enough yet"
        lines.append(
            f"**(c) Hand off to human for paper writing** — the winner {paper_strength}. "
            f"Autopilot will not auto-write the paper. To finish, the human should:"
        )
        lines.append("")
        lines.append(f"- Walk the winner artifacts at `{winner_dir}/`")
        lines.append("- Read RESULT.md charter compliance table + CODEX_AUDIT.json")
        lines.append("- Write the paper manually (or invoke their preferred writing tool)")
    elif alive:
        lines.append(
            f"All branches either pending or in progress ({len(alive)} alive). Resume by "
            f"running `/research-tree autopilot` to keep extending the tree. If you've been "
            f"running for a while without new `completed` nodes, consider auditing the "
            f"oldest running branch — it may be stuck."
        )
    else:
        lines.append(
            "Tree is empty or in an unexpected state. If this is a fresh init, run "
            "`/research-tree autopilot` to expand root."
        )
    lines.append("")

    out = root / STATE_DIR_NAME / REPORT_FILE_NAME
    out.write_text("\n".join(lines))
    print(f"OK: report written to {out}")


if __name__ == "__main__":
    main()
