#!/usr/bin/env python3
"""LangGraph-inspired sub-step checkpointing for research-tree branches.

A long-running branch (e.g. an 8-hour training) is broken into phases. Each
phase has a deterministic name; the branch executor writes to phase_log.jsonl
when each phase starts and completes. On crash + restart, stale_running_handler
reads phase_log.jsonl and figures out which phase to resume from — instead of
restarting the whole branch from scratch.

This is OPT-IN. Branches that don't use phase_log.jsonl behave like v0.2.x
(restart-from-scratch on crash). Branches that adopt it gain mid-execution
crash recovery.

## Phase log format

`.research-tree/branches/<id>/phase_log.jsonl` — one JSON object per line:

    {"phase": "setup", "started_at": "2026-05-25T12:00:00Z", "completed_at": "2026-05-25T12:05:00Z", "checkpoint_files": ["data/test_split.json"]}
    {"phase": "train_seed_0", "started_at": "...", "completed_at": "...", "checkpoint_files": ["checkpoints/seed_0/model.pt"]}
    {"phase": "train_seed_1", "started_at": "...", "completed_at": null}   # crashed before completion

A phase is considered "completed" iff its line has non-null `completed_at`.
Resume = first phase WITHOUT a completed_at, or the next phase that comes
after the last completed phase (whichever exists).

## Convention for branch scripts

The training shell script reads phase_log.jsonl on entry and skips
already-completed phases. Subagent execute prompts must instruct scripts
to honor this. See examples/training_with_checkpoint.sh for the canonical
shape.

## CLI

    python3 phase_checkpoint.py status <branch_dir>    # show phase log + next-resumable
    python3 phase_checkpoint.py mark <branch_dir> --phase NAME --action start|complete --checkpoint-file FILE
    python3 phase_checkpoint.py next-phase <branch_dir>  # output the next phase to run (or '' if none pending)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_phase_log(branch_dir: Path) -> list[dict]:
    p = branch_dir / "phase_log.jsonl"
    if not p.exists():
        return []
    entries = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"WARN: phase_log.jsonl line malformed: {e}", file=sys.stderr)
    return entries


def append_phase_entry(branch_dir: Path, entry: dict) -> None:
    p = branch_dir / "phase_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def mark_phase_start(branch_dir: Path, phase: str) -> None:
    """Append start record. If the phase already has a completed record, skip
    (idempotent — a script restart should not re-mark a completed phase)."""
    entries = read_phase_log(branch_dir)
    for e in entries:
        if e.get("phase") == phase and e.get("completed_at"):
            print(f"phase {phase} already completed; skipping start mark", file=sys.stderr)
            return
        if e.get("phase") == phase and e.get("started_at") and not e.get("completed_at"):
            # already started but not completed — likely crash recovery, leave alone
            return
    append_phase_entry(branch_dir, {
        "phase": phase,
        "started_at": now_iso(),
        "completed_at": None,
        "checkpoint_files": [],
    })


def mark_phase_complete(branch_dir: Path, phase: str, checkpoint_files: list[str] | None = None) -> None:
    """Rewrite the most recent start record for `phase` with completed_at = now."""
    entries = read_phase_log(branch_dir)
    # find last started-but-not-completed entry for this phase
    found = False
    for entry in reversed(entries):
        if entry.get("phase") == phase and entry.get("started_at") and not entry.get("completed_at"):
            entry["completed_at"] = now_iso()
            entry["checkpoint_files"] = checkpoint_files or entry.get("checkpoint_files", [])
            found = True
            break
    if not found:
        # no prior start; append a self-contained complete entry (degenerate but harmless)
        entries.append({
            "phase": phase,
            "started_at": now_iso(),
            "completed_at": now_iso(),
            "checkpoint_files": checkpoint_files or [],
        })
    # rewrite full log
    p = branch_dir / "phase_log.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")


def is_phase_complete(branch_dir: Path, phase: str) -> bool:
    for e in read_phase_log(branch_dir):
        if e.get("phase") == phase and e.get("completed_at"):
            return True
    return False


def cmd_status(args) -> int:
    branch_dir = args.branch_dir.resolve()
    entries = read_phase_log(branch_dir)
    completed = [e for e in entries if e.get("completed_at")]
    incomplete = [e for e in entries if not e.get("completed_at")]
    last_completed = completed[-1]["phase"] if completed else None
    next_resumable = incomplete[0]["phase"] if incomplete else None
    print(json.dumps({
        "branch_dir": str(branch_dir),
        "total_phases": len(entries),
        "completed_count": len(completed),
        "incomplete_count": len(incomplete),
        "last_completed_phase": last_completed,
        "next_resumable_phase": next_resumable,
        "phases": [
            {
                "phase": e.get("phase"),
                "completed": bool(e.get("completed_at")),
                "checkpoint_files": e.get("checkpoint_files", []),
            }
            for e in entries
        ],
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_mark(args) -> int:
    branch_dir = args.branch_dir.resolve()
    if args.action == "start":
        mark_phase_start(branch_dir, args.phase)
    elif args.action == "complete":
        mark_phase_complete(branch_dir, args.phase, args.checkpoint_file)
    return 0


def cmd_next_phase(args) -> int:
    """Output the next phase that should be run, or empty string if none.
    Useful in shell scripts: `NEXT=$(phase_checkpoint.py next-phase $DIR)`"""
    branch_dir = args.branch_dir.resolve()
    entries = read_phase_log(branch_dir)
    for e in entries:
        if not e.get("completed_at"):
            print(e.get("phase"))
            return 0
    print("")  # all done
    return 0


def cmd_is_complete(args) -> int:
    """Exit 0 if the phase is completed, exit 1 if not.
    Useful in shell scripts: `phase_checkpoint.py is-complete $DIR --phase X && echo skip`"""
    branch_dir = args.branch_dir.resolve()
    if is_phase_complete(branch_dir, args.phase):
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="show phase log + next-resumable")
    p_status.add_argument("branch_dir", type=Path)
    p_status.set_defaults(func=cmd_status)

    p_mark = sub.add_parser("mark", help="record a phase start or completion")
    p_mark.add_argument("branch_dir", type=Path)
    p_mark.add_argument("--phase", required=True)
    p_mark.add_argument("--action", required=True, choices=("start", "complete"))
    p_mark.add_argument("--checkpoint-file", nargs="*", default=None,
                        help="files produced this phase (relative to branch_dir)")
    p_mark.set_defaults(func=cmd_mark)

    p_next = sub.add_parser("next-phase", help="output next un-completed phase name")
    p_next.add_argument("branch_dir", type=Path)
    p_next.set_defaults(func=cmd_next_phase)

    p_ic = sub.add_parser("is-complete", help="exit 0 if --phase is complete, exit 1 otherwise")
    p_ic.add_argument("branch_dir", type=Path)
    p_ic.add_argument("--phase", required=True)
    p_ic.set_defaults(func=cmd_is_complete)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
