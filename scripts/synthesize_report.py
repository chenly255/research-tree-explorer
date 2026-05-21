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

    lines.append("## Suggested next move")
    lines.append("")
    if completed:
        winner = completed[0]
        lines.append(
            f"Deepen `{winner['id']}` further (e.g., ablations, scaling-up, baselines). "
            f"Its current artifacts at `{winner.get('branch_dir', 'N/A')}/` are the strongest "
            f"foundation for the paper draft."
        )
    elif alive:
        lines.append(
            f"All branches either dead or in progress. Resume by running "
            f"`/research-tree autopilot` to keep extending the tree."
        )
    else:
        lines.append(
            "Every branch died. Consider re-rooting with a refined research direction — "
            "consult the dead-branch atlas above to avoid repeating mistakes."
        )
    lines.append("")

    out = root / STATE_DIR_NAME / REPORT_FILE_NAME
    out.write_text("\n".join(lines))
    print(f"OK: report written to {out}")


if __name__ == "__main__":
    main()
