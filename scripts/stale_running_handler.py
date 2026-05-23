#!/usr/bin/env python3
"""
Stale running-node handler.

After a session restart (user closed IDE, /loop interval skipped, etc.) some
nodes may be stuck in `status=running` even though the actual training process
is dead. This script scans those nodes, decides what to do based on physical
evidence on disk, and emits a JSON action plan that autopilot consumes.

Decision tree per running node:

    EXECUTOR.json exists?
    ├─ no  → legacy orphan, written by old code path that didn't use nohup.
    │        Cannot recover — mark dead.
    └─ yes → read pid from EXECUTOR.json
             │
             ├─ pid still alive (os.kill(pid, 0) succeeds)
             │   → still running, leave alone, autopilot skips
             │
             └─ pid dead
                 │
                 ├─ RESULT.md exists → ready for validation chain (6a-6d)
                 ├─ DEAD.md exists   → ready to mark dead with DEAD.md reason
                 └─ neither          → abandoned, mark dead with
                                       "executor process exited without writing RESULT.md or DEAD.md"

Output (JSON to stdout):
    {
      "alive": [{"node_id": ..., "pid": ..., "branch_dir": ...}, ...],
      "ready_for_validation": [{"node_id": ..., "branch_dir": ...}, ...],
      "ready_for_death_from_file": [{"node_id": ..., "branch_dir": ..., "reason": ...}, ...],
      "abandoned": [{"node_id": ..., "branch_dir": ..., "reason": ...}, ...],
      "legacy_orphan": [{"node_id": ..., "branch_dir": ..., "reason": ...}, ...]
    }

autopilot consumes this and dispatches:
- alive               → pick-next will skip them
- ready_for_validation → run validation chain on that branch_dir
- ready_for_death_*   → tree_state.py die <id> --reason ... (PROGRAMMATIC, not LLM)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

STATE_DIR_NAME = ".research-tree"
STATE_FILE_NAME = "tree.json"


def pid_alive(pid: int) -> bool:
    """Return True if a process with this pid is still running.

    Uses kill(pid, 0) which sends no signal but errors if the process is gone
    or if we don't have permission. Permission errors are treated as alive
    (we'd rather false-positive 'alive' than false-positive 'dead' and lose
    a running training job to a premature death-marking).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user; conservatively assume alive
        return True


def first_line(path: Path) -> str:
    try:
        with path.open() as f:
            return f.readline().strip()
    except OSError:
        return ""


def classify_node(node: dict, root: Path) -> tuple[str, dict]:
    """Return (category, detail_dict) for one running node."""
    branch_dir_rel = node.get("branch_dir") or f"{STATE_DIR_NAME}/branches/{node['id']}"
    branch_dir = root / branch_dir_rel
    detail = {
        "node_id": node["id"],
        "branch_dir": str(branch_dir),
        "title": node.get("title", ""),
    }

    executor_path = branch_dir / "EXECUTOR.json"
    result_path = branch_dir / "RESULT.md"
    dead_path = branch_dir / "DEAD.md"

    if not executor_path.exists():
        detail["reason"] = (
            "no EXECUTOR.json — node marked running by code path that did not "
            "register a backgroundable PID. Cannot recover state across session restart."
        )
        return "legacy_orphan", detail

    try:
        executor = json.loads(executor_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        detail["reason"] = f"EXECUTOR.json unreadable: {e}"
        return "legacy_orphan", detail

    pid = executor.get("pid")
    if not isinstance(pid, int):
        detail["reason"] = f"EXECUTOR.json missing valid 'pid' field (got {pid!r})"
        return "legacy_orphan", detail

    detail["pid"] = pid
    detail["started_at"] = executor.get("started_at")
    detail["log_file"] = executor.get("log_file")

    if pid_alive(pid):
        return "alive", detail

    # Process is dead — what evidence did it leave?
    if dead_path.exists():
        detail["reason"] = first_line(dead_path) or "(empty DEAD.md)"
        return "ready_for_death_from_file", detail
    if result_path.exists():
        return "ready_for_validation", detail
    detail["reason"] = (
        f"executor pid {pid} exited without writing RESULT.md or DEAD.md "
        f"(check {detail.get('log_file', branch_dir / 'executor.log')} for trace)"
    )
    return "abandoned", detail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-root", default=os.getcwd())
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    state_path = root / STATE_DIR_NAME / STATE_FILE_NAME
    if not state_path.exists():
        sys.exit(f"ERROR: no tree.json at {state_path}")
    state = json.loads(state_path.read_text())

    buckets: dict[str, list] = {
        "alive": [],
        "ready_for_validation": [],
        "ready_for_death_from_file": [],
        "abandoned": [],
        "legacy_orphan": [],
    }

    for node in state["nodes"].values():
        if node["status"] != "running":
            continue
        category, detail = classify_node(node, root)
        buckets[category].append(detail)

    print(json.dumps(buckets, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
