"""Event-driven-ish scheduler.

Design contract: docs/V1-ARCHITECTURE.md (section "scheduler.py").

The v0.5 design polled a sentinel file (AWAITING_HUMAN.md) every /loop tick.
Each poll cost ~5-10 tokens of main context even when there was no work
to do — over an overnight run, that adds up.

The v1.0 design separates state from polling. The scheduler maintains a
small events.log file under .research-tree/. Branches and background
processes append events; the main agent reads events.log delta since
the last tick. If there's no delta, the tick is essentially free.

This is "events as a log" — not true inotify, but it gets most of the
benefit without the system dependency. v1.1 may add inotify for
sub-second wake-up on local runs.

Public API:

    Scheduler(root).watch_once() -> list[Event]
        Read events.log delta. Returns events added since last call.
        Stateless across processes: the cursor is stored in
        .research-tree/scheduler_cursor.txt.

    Scheduler(root).emit(kind, payload) -> Event
        Append an event to events.log.

    Scheduler(root).scan_branches() -> list[Event]
        Walk .research-tree/branches/*/. Emit synthetic events for newly
        appeared RESULT.md / DEAD.md / SUBTREE_FORK.md / SUBTREE_PIVOT.md
        files and for background processes whose PID has died. Idempotent:
        re-running on the same state yields no new events.

The dispatch table — events → actions — lives in cli.py's autopilot step,
not here. scheduler.py is purely the event source.
"""
from __future__ import annotations

import errno
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .graph import STATE_DIR_NAME, now_iso


EVENTS_LOG_NAME = "events.log"
CURSOR_FILE_NAME = "scheduler_cursor.txt"
BRANCH_INDEX_NAME = "scheduler_branch_index.json"


VALID_EVENT_KINDS = {
    "background_process_exit",
    "result_md_written",
    "dead_md_written",
    "subtree_fork_written",
    "subtree_pivot_written",
    "audit_complete",
    "node_lifecycle_changed",
    "merge_proposed",
    "branching_decided",
    "validation_complete",
    "human_decision_required",
    "human_decision_resolved",
}


@dataclass
class Event:
    t: str
    kind: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"t": self.t, "kind": self.kind, "payload": self.payload}

    @classmethod
    def from_line(cls, line: str) -> "Event | None":
        line = line.strip()
        if not line:
            return None
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return None
        return cls(t=d.get("t", ""), kind=d.get("kind", "unknown"), payload=d.get("payload", {}))

    def to_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False) + "\n"


class Scheduler:
    """Event log + branch scanner. Stateless across processes (cursor on disk)."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.events_log = self.root / STATE_DIR_NAME / EVENTS_LOG_NAME
        self.cursor_file = self.root / STATE_DIR_NAME / CURSOR_FILE_NAME
        self.branch_index = self.root / STATE_DIR_NAME / BRANCH_INDEX_NAME
        self.events_log.parent.mkdir(parents=True, exist_ok=True)
        if not self.events_log.exists():
            self.events_log.touch()

    # ---------- emit ----------

    def emit(self, kind: str, payload: dict | None = None) -> Event:
        if kind not in VALID_EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}; known: {sorted(VALID_EVENT_KINDS)}")
        ev = Event(t=now_iso(), kind=kind, payload=payload or {})
        # append O_APPEND ensures atomic concurrent writes on POSIX
        with self.events_log.open("a") as f:
            f.write(ev.to_line())
        return ev

    # ---------- watch (cursor-based delta) ----------

    def _read_cursor(self) -> int:
        if not self.cursor_file.exists():
            return 0
        try:
            return int(self.cursor_file.read_text().strip() or "0")
        except (OSError, ValueError):
            return 0

    def _write_cursor(self, offset: int) -> None:
        self.cursor_file.write_text(str(offset))

    def watch_once(self, *, advance_cursor: bool = True) -> list[Event]:
        """Read events.log delta since last call. If `advance_cursor=False`,
        the cursor is not advanced (useful for peeking without consuming)."""
        cur = self._read_cursor()
        try:
            with self.events_log.open("rb") as f:
                f.seek(cur)
                raw = f.read()
                new_offset = f.tell()
        except FileNotFoundError:
            return []

        events: list[Event] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            ev = Event.from_line(line)
            if ev is not None:
                events.append(ev)

        if advance_cursor and new_offset != cur:
            self._write_cursor(new_offset)
        return events

    # ---------- branch scan (synthesize events from filesystem state) ----------

    def scan_branches(self) -> list[Event]:
        """Walk .research-tree/branches/<id>/ and emit synthetic events for
        files that appeared since the last scan. Also emit
        `background_process_exit` events for EXECUTOR.json PIDs that are no
        longer alive.

        Returns the list of new events emitted. Idempotent: state is
        persisted in scheduler_branch_index.json.
        """
        branches_root = self.root / STATE_DIR_NAME / "branches"
        if not branches_root.exists():
            return []

        # load prior index
        try:
            with self.branch_index.open() as f:
                prior_state: dict[str, dict] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            prior_state = {}

        new_state: dict[str, dict] = {}
        new_events: list[Event] = []

        for branch_dir in branches_root.iterdir():
            if not branch_dir.is_dir():
                continue
            node_id = branch_dir.name
            prior = prior_state.get(node_id, {})
            current = self._snapshot_branch(branch_dir)
            new_state[node_id] = current

            # detect newly-appeared output files
            for marker, kind in (
                ("RESULT.md", "result_md_written"),
                ("DEAD.md", "dead_md_written"),
                ("SUBTREE_FORK.md", "subtree_fork_written"),
                ("SUBTREE_PIVOT.md", "subtree_pivot_written"),
                ("CODEX_AUDIT.json", "audit_complete"),
            ):
                if current.get(marker) and not prior.get(marker):
                    ev = self.emit(kind, {"node": node_id, "branch_dir": str(branch_dir)})
                    new_events.append(ev)

            # detect background process exit
            exec_info = current.get("EXECUTOR")
            prior_exec = prior.get("EXECUTOR")
            if exec_info and prior_exec and prior_exec.get("alive") and not exec_info.get("alive"):
                ev = self.emit("background_process_exit", {
                    "node": node_id,
                    "pid": exec_info.get("pid"),
                    "branch_dir": str(branch_dir),
                })
                new_events.append(ev)

        # persist updated index atomically
        tmp = self.branch_index.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(new_state, f, indent=2, sort_keys=True)
        os.replace(tmp, self.branch_index)

        return new_events

    def _snapshot_branch(self, branch_dir: Path) -> dict:
        """Capture the current observable state of one branch dir."""
        snap: dict = {}
        for marker in ("RESULT.md", "DEAD.md", "SUBTREE_FORK.md",
                       "SUBTREE_PIVOT.md", "CODEX_AUDIT.json"):
            snap[marker] = (branch_dir / marker).exists()

        executor_json = branch_dir / "EXECUTOR.json"
        if executor_json.exists():
            try:
                with executor_json.open() as f:
                    e = json.load(f)
                pid = e.get("pid")
                alive = _pid_alive(pid) if isinstance(pid, int) else False
                snap["EXECUTOR"] = {"pid": pid, "alive": alive}
            except (OSError, json.JSONDecodeError):
                snap["EXECUTOR"] = {"alive": False}

        return snap


def _pid_alive(pid: int) -> bool:
    """Return True if `pid` corresponds to a live process. Linux-friendly;
    on other platforms uses the universal `kill(pid, 0)` check."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        if e.errno == errno.EPERM:
            # process exists but we lack permission; alive
            return True
        return False
    return True


# ---------- CLI ----------


def main() -> int:
    """python -m research_tree.scheduler <project_root> <subcommand>"""
    import argparse
    import sys

    p = argparse.ArgumentParser(description="research-tree scheduler / event log")
    p.add_argument("project_root")
    sub = p.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("watch", help="print events.log delta since last call")
    p_emit = sub.add_parser("emit", help="emit one event")
    p_emit.add_argument("--kind", required=True, choices=sorted(VALID_EVENT_KINDS))
    p_emit.add_argument("--payload", default="{}", help="JSON payload")
    sub.add_parser("scan", help="scan branches/, emit synthesized events for new output files")
    sub.add_parser("tail", help="print all events (does not advance cursor)")

    args = p.parse_args()
    root = Path(args.project_root).resolve()
    s = Scheduler(root)

    if args.subcommand == "watch":
        events = s.watch_once()
        print(json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False))
        return 0
    if args.subcommand == "emit":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as e:
            print(f"ERROR: payload is not valid JSON: {e}", file=sys.stderr)
            return 2
        ev = s.emit(args.kind, payload)
        print(json.dumps(ev.to_dict()))
        return 0
    if args.subcommand == "scan":
        events = s.scan_branches()
        print(json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False))
        return 0
    if args.subcommand == "tail":
        events = s.watch_once(advance_cursor=False)
        for e in events:
            print(json.dumps(e.to_dict(), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
