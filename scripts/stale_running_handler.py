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


def read_pid_starttime(pid: int) -> int | None:
    """Read /proc/<pid>/stat field 22 (starttime in clock ticks since boot).

    Returns None if /proc unavailable or pid gone. Used to disambiguate PID
    reuse — when a long-running training process dies and the OS rolls the
    PID counter, a totally unrelated process can land on the same PID number.
    Comparing starttime catches that.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
        # field 1 = pid, field 2 = (comm) in parens (may contain spaces),
        # field 3+ space-separated. starttime is field 22 (1-indexed).
        rparen = data.rfind(b")")
        if rparen < 0:
            return None
        tail = data[rparen + 1:].split()
        # field 3 is tail[0], so field 22 is tail[19]
        if len(tail) < 20:
            return None
        return int(tail[19])
    except (OSError, ValueError):
        return None


def pid_alive(pid: int, expected_starttime: int | None = None) -> bool:
    """Return True if a process with this pid is still running, AND if
    expected_starttime is given, the process's start time still matches.

    v0.3.1 (codex review P1-4): bare kill(pid, 0) treats zombies as alive and
    cannot distinguish PID reuse. When EXECUTOR.json was written we now record
    /proc/<pid>/stat field 22 (starttime); on every check we re-read and
    compare. Mismatch = PID was reused, the original process is gone.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user; conservatively assume alive
        return True
    if expected_starttime is None:
        return True
    actual_starttime = read_pid_starttime(pid)
    if actual_starttime is None:
        # /proc unreadable — fall back to "alive" rather than killing a real process
        return True
    return actual_starttime == expected_starttime


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
    expected_starttime = executor.get("pid_starttime")  # v0.3.1 — Linux clock ticks
    if isinstance(expected_starttime, int):
        detail["pid_starttime_expected"] = expected_starttime

    if pid_alive(pid, expected_starttime=expected_starttime if isinstance(expected_starttime, int) else None):
        return "alive", detail
    if isinstance(expected_starttime, int) and read_pid_starttime(pid) is not None:
        # PID exists but starttime differs — definitely PID reuse, not our process
        detail["pid_reuse_detected"] = True

    # Process is dead — what evidence did it leave?
    if dead_path.exists():
        detail["reason"] = first_line(dead_path) or "(empty DEAD.md)"
        return "ready_for_death_from_file", detail
    if result_path.exists():
        return "ready_for_validation", detail

    # v0.3.0 — before declaring abandoned, check phase_log.jsonl. If the branch
    # was using sub-step checkpointing AND at least one phase completed, the
    # crash is RESUMABLE not abandoned. Orchestrator can repair-retry the
    # branch and the new agent reads phase_log.jsonl to skip done phases.
    phase_log = branch_dir / "phase_log.jsonl"
    if phase_log.exists():
        try:
            lines = [
                json.loads(line) for line in phase_log.read_text().splitlines()
                if line.strip()
            ]
            completed_phases = [e["phase"] for e in lines if e.get("completed_at")]
            incomplete_phases = [e["phase"] for e in lines if not e.get("completed_at")]
            if completed_phases:
                detail["reason"] = (
                    f"executor pid {pid} crashed mid-execution; phase_log shows "
                    f"{len(completed_phases)} phase(s) complete: {completed_phases}, "
                    f"crashed during {incomplete_phases[:1]}. Resumable via phase_checkpoint."
                )
                detail["resumable_from_phase"] = incomplete_phases[0] if incomplete_phases else None
                detail["completed_phases"] = completed_phases
                return "ready_for_resume", detail
        except (json.JSONDecodeError, KeyError, OSError) as e:
            # malformed phase log — fall through to abandoned
            detail["phase_log_parse_error"] = str(e)

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
        "ready_for_resume": [],  # v0.3.0 — phase_log shows partial progress, can resume
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
