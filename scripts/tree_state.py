#!/usr/bin/env python3
"""
Research Tree state machine.

Stores the entire tree as a single JSON file at .research-tree/tree.json
in the project root. All commands are idempotent and safe to retry.

Subcommands:
  init <root_idea>              Create a new tree with root node.
  add <parent_id> <kind> <title>
                                Add a child node under parent. Returns new id.
  set <node_id> <key=value> ... Update node fields (status, score, death_reason, etc.)
  get <node_id>                 Print node as JSON.
  list [--status alive|dead|completed|expanded]
                                List nodes (optionally filtered by status).
  pick-next                     Pick the next alive leaf to expand (highest score, then shallowest).
  tree                          Print ASCII tree visualization.
  stats                         Print summary stats.
  audit-add <junction_id> <reviewer> <verdict>
                                Record a junction audit entry.
  budget-check                  Exit 1 if any global budget exceeded.

Node statuses:
  pending     — node created but not yet processed
  expanded    — children have been generated
  running     — currently executing
  completed   — finished successfully with a score
  dead        — abandoned (with death_reason)
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_DIR_NAME = ".research-tree"
STATE_FILE_NAME = "tree.json"
LOCK_FILE_NAME = "tree.lock"
SCHEMA_VERSION = "0.2"

VALID_STATUSES = {"pending", "expanded", "running", "completed", "dead"}

# v0.1.6 — task_type: each branch can declare what KIND of work it does, so
# the charter validator picks the right schema (e.g. an audit branch does
# not produce checkpoints, so checking for ≥3 seed checkpoints is nonsense).
# `training` is the default and preserves all v0.1.5 behavior.
VALID_TASK_TYPES = {
    "training",            # standard ML training run with checkpoints + multi-seed (v0.1.5 default)
    "audit",               # post-hoc evaluation/audit on frozen models, no new checkpoints
    "analysis",            # data analysis / statistics / figure generation, no model artifacts
    "data-acquisition",    # download + verify external dataset, produces raw data manifests
    "framing-decision",    # human-only paper framing / venue / narrative decision; autopilot skips
    "mixed",               # heterogeneous workload; defers to charter for per-branch validation
}
VALID_KINDS = {"root", "approach", "architecture", "experiment", "ablation", "narrative", "custom"}

# Fields the `set` command is allowed to write. Notably EXCLUDES `status` —
# status transitions go through dedicated commands (complete, die) that require
# proof / reason. This prevents a lazy subagent from doing `set <id> status=completed`
# without going through the validator chain.
SET_ALLOWED_KEYS = {
    "description", "score", "death_reason", "death_evidence",
    "done_ready", "completion_proof", "junction_audit_id", "branch_dir",
    "direct_executable",
    # v0.1.6 — task-type-aware nodes
    "task_type", "depends_on", "human_only",
}

# Session step counter — autopilot reports `should_pause: true` when this many
# steps have accumulated within a single Claude Code session, so the user can
# restart for a clean context. Default tuned to ~30-40% of typical context window.
DEFAULT_SESSION_STEP_THRESHOLD = 20


def state_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / STATE_FILE_NAME


def lock_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / LOCK_FILE_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    """Exclusive flock on .research-tree/tree.lock for the duration of the block.

    Prevents two concurrent autopilot processes (or accidental parallel CLI runs)
    from corrupting tree.json or producing duplicate IDs.
    """
    lp = lock_path(root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lp, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        fp.close()


def load_state(root: Path) -> dict[str, Any]:
    p = state_path(root)
    if not p.exists():
        sys.exit(f"ERROR: no tree found at {p}. Run `tree_state.py init <idea>` first.")
    with p.open() as f:
        state = json.load(f)
    # v0.1.6 — auto-migrate pre-v0.1.6 trees by backfilling task-type fields.
    # Trees created before v0.1.6 lack task_type/depends_on/human_only; treat
    # them as the conservative training default so v0.1.5 behavior is preserved.
    for n in state.get("nodes", {}).values():
        n.setdefault("task_type", "mixed" if n.get("kind") == "root" else "training")
        n.setdefault("depends_on", [])
        n.setdefault("human_only", False)
    return state


def save_state(root: Path, state: dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = now_iso()
    # Per-PID tmp file avoids concurrent writers stomping on each other before
    # rename (rename is atomic; tmp write is not). state_lock() is the real
    # safety; per-PID tmp is belt-and-suspenders.
    tmp = p.with_suffix(f".json.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def next_id(state: dict[str, Any], parent: str) -> str:
    if parent == "root":
        siblings = state["nodes"]["root"]["children"]
        return str(len(siblings) + 1)
    parent_node = state["nodes"][parent]
    return f"{parent}.{len(parent_node['children']) + 1}"


def cmd_init(args) -> None:
    root = Path(args.project_root).resolve()
    p = state_path(root)
    # Ensure dir exists so state_lock can create its lockfile
    p.parent.mkdir(parents=True, exist_ok=True)
    with state_lock(root):
        if p.exists() and not args.force:
            sys.exit(f"ERROR: tree already exists at {p}. Use --force to overwrite.")
        _do_init(args, root)


def _do_init(args, root: Path) -> None:
    state = {
        "schema_version": SCHEMA_VERSION,
        "project": root.name,
        "project_root": str(root),
        "root_idea": args.idea,
        "created_at": now_iso(),
        "last_updated": now_iso(),
        "current_focus": "root",
        "nodes": {
            "root": {
                "id": "root",
                "parent": None,
                "depth": 0,
                "kind": "root",
                "status": "pending",
                "title": args.idea[:80],
                "description": args.idea,
                "score": None,
                "death_reason": None,
                "death_evidence": None,
                "junction_audit_id": None,
                "branch_dir": None,
                "children": [],
                # v0.1.6 — task-type-aware fields (root inherits "mixed" by default;
                # individual child branches will declare their own task_type at add time)
                "task_type": "mixed",
                "depends_on": [],
                "human_only": False,
                "created_at": now_iso(),
            }
        },
        "audits": {},
        "global_constraints": {
            "max_depth": args.max_depth,
            "max_branches_per_junction": args.max_branches,
            "max_total_nodes": args.max_total_nodes,
            "max_gpu_hours_total": args.max_gpu_hours,
        },
        "stats": {
            "nodes_total": 1,
            "nodes_alive": 1,
            "nodes_dead": 0,
            "nodes_completed": 0,
            "gpu_hours_used": 0.0,
        },
    }
    save_state(root, state)

    state_dir = root / STATE_DIR_NAME
    (state_dir / "branches").mkdir(parents=True, exist_ok=True)
    (state_dir / "audits").mkdir(parents=True, exist_ok=True)
    (state_dir / "reflections").mkdir(parents=True, exist_ok=True)
    progress_log = state_dir / "progress.log"
    if not progress_log.exists():
        progress_log.write_text(f"{now_iso()}  step=0  action=init  node=root  alive=1  completed=0  dead=0\n")

    print(f"OK: tree initialized at {state_path(root)}")
    print(f"root idea: {args.idea}")


def cmd_add(args) -> None:
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)

        parent_id = args.parent
        if parent_id not in state["nodes"]:
            sys.exit(f"ERROR: parent node {parent_id!r} not found.")
        if args.kind not in VALID_KINDS:
            sys.exit(f"ERROR: invalid kind {args.kind!r}. Valid: {sorted(VALID_KINDS)}")

        # v0.1.6 — validate task_type and depends_on
        task_type = getattr(args, "task_type", None) or "training"
        if task_type not in VALID_TASK_TYPES:
            sys.exit(
                f"ERROR: invalid task_type {task_type!r}. "
                f"Valid: {sorted(VALID_TASK_TYPES)}"
            )
        depends_on_raw = getattr(args, "depends_on", None) or ""
        depends_on = [d.strip() for d in depends_on_raw.split(",") if d.strip()]
        for dep_id in depends_on:
            if dep_id not in state["nodes"]:
                sys.exit(f"ERROR: depends_on references unknown node {dep_id!r}.")
        human_only = bool(getattr(args, "human_only", False))

        parent_node = state["nodes"][parent_id]
        constraints = state["global_constraints"]

        if parent_node["depth"] + 1 > constraints["max_depth"]:
            sys.exit(f"ERROR: would exceed max_depth ({constraints['max_depth']}).")

        alive_children = [
            c for c in parent_node["children"]
            if state["nodes"][c]["status"] != "dead"
        ]
        if len(alive_children) >= constraints["max_branches_per_junction"]:
            sys.exit(
                f"ERROR: parent {parent_id} already has {len(alive_children)} alive "
                f"children (max_branches_per_junction = {constraints['max_branches_per_junction']}). "
                f"Prune one before adding another, or raise the budget."
            )
        if state["stats"]["nodes_total"] >= constraints["max_total_nodes"]:
            sys.exit(f"ERROR: would exceed max_total_nodes ({constraints['max_total_nodes']}).")

        new_id = next_id(state, parent_id)
        new_node = {
            "id": new_id,
            "parent": parent_id,
            "depth": parent_node["depth"] + 1,
            "kind": args.kind,
            "status": "pending",
            "title": args.title,
            "description": args.description or args.title,
            "score": None,
            "death_reason": None,
            "death_evidence": None,
            "completion_proof": None,
            "junction_audit_id": None,
            "branch_dir": f"{STATE_DIR_NAME}/branches/{new_id}",
            "children": [],
            "direct_executable": False,
            # v0.1.6 — task-type-aware fields
            "task_type": task_type,
            "depends_on": depends_on,
            "human_only": human_only,
            "created_at": now_iso(),
        }
        state["nodes"][new_id] = new_node
        parent_node["children"].append(new_id)
        if parent_node["status"] == "pending":
            parent_node["status"] = "expanded"
        state["stats"]["nodes_total"] += 1
        state["stats"]["nodes_alive"] += 1
        save_state(root, state)

        branch_dir = root / new_node["branch_dir"]
        branch_dir.mkdir(parents=True, exist_ok=True)

    print(new_id)


def parse_kv(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        if "=" not in it:
            sys.exit(f"ERROR: expected key=value, got {it!r}.")
        k, v = it.split("=", 1)
        # v0.1.6 — depends_on stores a list; accept comma-separated values from `set`
        if k == "depends_on":
            out[k] = [x.strip() for x in v.split(",") if x.strip()] if v else []
            continue
        # v0.1.6 — task_type must be one of the valid enum values
        if k == "task_type":
            if v not in VALID_TASK_TYPES:
                sys.exit(
                    f"ERROR: invalid task_type {v!r}. "
                    f"Valid: {sorted(VALID_TASK_TYPES)}"
                )
            out[k] = v
            continue
        if v.lower() == "true":
            out[k] = True
        elif v.lower() == "false":
            out[k] = False
        elif v.lower() in ("null", "none"):
            out[k] = None
        else:
            try:
                if "." in v:
                    out[k] = float(v)
                else:
                    out[k] = int(v)
            except ValueError:
                out[k] = v
    return out


def _apply_status_transition(state: dict, node: dict, new_status: str) -> None:
    prev_status = node["status"]
    if new_status == prev_status:
        return
    was_alive = prev_status in ("pending", "expanded", "running")
    now_alive = new_status in ("pending", "expanded", "running")
    node["status"] = new_status
    if was_alive and not now_alive:
        state["stats"]["nodes_alive"] -= 1
    elif not was_alive and now_alive:
        state["stats"]["nodes_alive"] += 1
    if new_status == "dead" and prev_status != "dead":
        state["stats"]["nodes_dead"] += 1
    if new_status == "completed" and prev_status != "completed":
        state["stats"]["nodes_completed"] += 1


def cmd_set(args) -> None:
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)
        if args.node_id not in state["nodes"]:
            sys.exit(f"ERROR: node {args.node_id!r} not found.")
        node = state["nodes"][args.node_id]
        updates = parse_kv(args.assignments)

        # SECURITY: `set` is for non-status mutations only. Status transitions to
        # `completed` or `dead` require dedicated commands that demand proof
        # (validator report / death reason). This prevents a lazy agent from
        # bypassing the validator chain with `set <id> status=completed score=0.99`.
        if "status" in updates:
            sys.exit(
                "ERROR: `set` cannot change status. Use:\n"
                "  - tree_state.py complete <node_id> --validator-report <path> --score <float>  (PASS only)\n"
                "  - tree_state.py die <node_id> --reason <text> [--evidence <path>]\n"
                "  - tree_state.py running <node_id>   (mark in-progress)\n"
                "  - tree_state.py reopen <node_id>    (admin: undo dead/completed back to pending)\n"
            )
        # Block other fields that should only be set by privileged transitions
        blocked = {k for k in updates if k not in SET_ALLOWED_KEYS}
        if blocked:
            sys.exit(
                f"ERROR: `set` cannot write fields {sorted(blocked)}. "
                f"Allowed via set: {sorted(SET_ALLOWED_KEYS)}. "
                f"Use the dedicated transition commands for status changes."
            )

        node.update(updates)
        node["updated_at"] = now_iso()
        save_state(root, state)
    print(json.dumps(node, indent=2, ensure_ascii=False))


def cmd_running(args) -> None:
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)
        if args.node_id not in state["nodes"]:
            sys.exit(f"ERROR: node {args.node_id!r} not found.")
        node = state["nodes"][args.node_id]
        if node["status"] not in ("pending", "expanded"):
            sys.exit(
                f"ERROR: can only mark pending/expanded nodes as running. "
                f"Current status: {node['status']}"
            )
        _apply_status_transition(state, node, "running")
        node["updated_at"] = now_iso()
        save_state(root, state)
    print(json.dumps(node, indent=2, ensure_ascii=False))


def cmd_complete(args) -> None:
    """Mark a node completed. Requires a PASSING validator report to prevent
    lazy subagents from bypassing the validator chain with raw `set status=completed`.
    The validator report's SHA256 is recorded as completion_proof for audit.
    """
    root = Path(args.project_root).resolve()
    report_path = Path(args.validator_report).resolve()
    if not report_path.exists():
        sys.exit(f"ERROR: validator report not found at {report_path}")
    try:
        report = json.loads(report_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: validator report is not valid JSON: {e}")
    verdict = report.get("verdict", "").upper()
    if verdict != "PASS":
        sys.exit(
            f"ERROR: validator verdict={verdict!r} — only PASS reports can mark a node completed. "
            f"For WARN or FAIL, use `die` with the reason."
        )
    proof_sha = sha256_file(report_path)

    with state_lock(root):
        state = load_state(root)
        if args.node_id not in state["nodes"]:
            sys.exit(f"ERROR: node {args.node_id!r} not found.")
        node = state["nodes"][args.node_id]
        _apply_status_transition(state, node, "completed")
        node["score"] = args.score
        node["completion_proof"] = {
            "validator_report": str(report_path),
            "validator_report_sha256": proof_sha,
            "validator_verdict": "PASS",
            "completed_at": now_iso(),
        }
        if args.done_ready:
            node["done_ready"] = True
        node["updated_at"] = now_iso()
        save_state(root, state)
    print(json.dumps(node, indent=2, ensure_ascii=False))


def cmd_die(args) -> None:
    """Mark a node dead with a reason. Used for validator failures, codex failures,
    blocker DEAD.md files, or manual pruning.
    """
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)
        if args.node_id not in state["nodes"]:
            sys.exit(f"ERROR: node {args.node_id!r} not found.")
        node = state["nodes"][args.node_id]
        _apply_status_transition(state, node, "dead")
        node["death_reason"] = args.reason
        if args.evidence:
            node["death_evidence"] = args.evidence
        node["updated_at"] = now_iso()
        save_state(root, state)
    print(json.dumps(node, indent=2, ensure_ascii=False))


def cmd_reopen(args) -> None:
    """Admin command: reset a dead or completed node back to pending. Used when
    the human wants to re-explore a previously-dead branch with different
    parameters. Clears score, death_reason, completion_proof.
    """
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)
        if args.node_id not in state["nodes"]:
            sys.exit(f"ERROR: node {args.node_id!r} not found.")
        node = state["nodes"][args.node_id]
        _apply_status_transition(state, node, "pending")
        node["score"] = None
        node["death_reason"] = None
        node["death_evidence"] = None
        node["completion_proof"] = None
        node["done_ready"] = False
        node["updated_at"] = now_iso()
        save_state(root, state)
    print(json.dumps(node, indent=2, ensure_ascii=False))


def cmd_get(args) -> None:
    root = Path(args.project_root).resolve()
    state = load_state(root)
    if args.node_id not in state["nodes"]:
        sys.exit(f"ERROR: node {args.node_id!r} not found.")
    print(json.dumps(state["nodes"][args.node_id], indent=2, ensure_ascii=False))


def cmd_list(args) -> None:
    root = Path(args.project_root).resolve()
    state = load_state(root)
    nodes = state["nodes"].values()
    if args.status:
        nodes = [n for n in nodes if n["status"] == args.status]
    for n in sorted(nodes, key=lambda x: (x["depth"], x["id"])):
        score = f"{n['score']:.2f}" if n["score"] is not None else "—"
        marker = {
            "pending": "·",
            "expanded": "▸",
            "running": "►",
            "completed": "✓",
            "dead": "✗",
        }.get(n["status"], "?")
        print(f"  {marker} [{n['id']:<6}] depth={n['depth']} score={score:>5} {n['kind']:<12} {n['title']}")


def _deps_satisfied(state: dict, node: dict) -> tuple[bool, list[str]]:
    """v0.1.6 — return (all_satisfied, list_of_unmet_dep_ids).

    A dependency is satisfied when the referenced node's status is
    `completed`. `dead` does NOT count: if a prerequisite died, the
    dependent branch is blocked (autopilot may later mark it dead too).
    """
    unmet = []
    for dep_id in node.get("depends_on", []) or []:
        dep_node = state["nodes"].get(dep_id)
        if dep_node is None:
            # broken reference — treat as unmet
            unmet.append(dep_id)
            continue
        if dep_node.get("status") != "completed":
            unmet.append(dep_id)
    return (len(unmet) == 0, unmet)


def cmd_pick_next(args) -> None:
    """Pick the next leaf to work on.

    Priority:
      1. Status == pending (never touched)
      2. v0.1.6 — skip nodes with `human_only=true` (autopilot must not touch them)
      3. v0.1.6 — skip nodes whose `depends_on` lists any non-completed node
      4. Highest parent score (deepen winners)
      5. Shallowest depth as tiebreak
    """
    root = Path(args.project_root).resolve()
    state = load_state(root)
    candidates = []
    for n in state["nodes"].values():
        if n["status"] != "pending":
            continue
        # v0.1.6 — autopilot must not pick human-only nodes (paper framing
        # decisions, venue choice, etc. belong to the user)
        if n.get("human_only", False):
            continue
        # v0.1.6 — skip nodes blocked by unmet dependencies
        ok, _ = _deps_satisfied(state, n)
        if not ok:
            continue
        parent_score = (
            state["nodes"][n["parent"]]["score"] if n["parent"] else 1.0
        )
        parent_score = parent_score if parent_score is not None else 0.5
        candidates.append((parent_score, -n["depth"], n["id"]))
    if not candidates:
        print("NONE")
        return
    candidates.sort(reverse=True)
    print(candidates[0][2])


def cmd_deps(args) -> None:
    """v0.1.6 — show dependency status for one node.

    stdout: JSON {node_id, depends_on, unmet, satisfied}
    Exit code: 0 if satisfied, 1 if unmet (so callers can branch on it).
    """
    root = Path(args.project_root).resolve()
    state = load_state(root)
    node = state["nodes"].get(args.node_id)
    if node is None:
        sys.exit(f"ERROR: node {args.node_id!r} not found.")
    ok, unmet = _deps_satisfied(state, node)
    report = {
        "node_id": args.node_id,
        "depends_on": node.get("depends_on", []) or [],
        "unmet": unmet,
        "satisfied": ok,
    }
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


def cmd_tree(args) -> None:
    root = Path(args.project_root).resolve()
    state = load_state(root)

    def render(node_id: str, prefix: str = "", is_last: bool = True) -> None:
        n = state["nodes"][node_id]
        marker = {
            "pending": "·",
            "expanded": "▸",
            "running": "►",
            "completed": "✓",
            "dead": "✗",
        }.get(n["status"], "?")
        score = f" [{n['score']:.2f}]" if n["score"] is not None else ""
        connector = "└── " if is_last else "├── "
        if node_id == "root":
            print(f"{marker} root: {n['title']}")
        else:
            print(f"{prefix}{connector}{marker} {n['id']}{score} {n['title']}")
        children = n["children"]
        new_prefix = prefix + ("    " if is_last else "│   ")
        if node_id == "root":
            new_prefix = ""
        for i, child in enumerate(children):
            render(child, new_prefix, i == len(children) - 1)

    render("root")


def cmd_stats(args) -> None:
    root = Path(args.project_root).resolve()
    state = load_state(root)
    s = state["stats"]
    print(f"project       : {state['project']}")
    print(f"root idea     : {state['root_idea'][:80]}")
    print(f"created       : {state['created_at']}")
    print(f"updated       : {state['last_updated']}")
    print(f"nodes total   : {s['nodes_total']}")
    print(f"  alive       : {s['nodes_alive']}")
    print(f"  dead        : {s['nodes_dead']}")
    print(f"  completed   : {s['nodes_completed']}")
    print(f"gpu hours     : {s['gpu_hours_used']:.1f} / {state['global_constraints']['max_gpu_hours_total']}")


def cmd_audit_add(args) -> None:
    root = Path(args.project_root).resolve()
    with state_lock(root):
        state = load_state(root)
        if args.junction not in state["nodes"]:
            sys.exit(f"ERROR: junction node {args.junction!r} not found.")
        audit_id = f"audit-{len(state['audits']) + 1:03d}"
        state["audits"][audit_id] = {
            "junction": args.junction,
            "reviewer": args.reviewer,
            "verdict": args.verdict,
            "timestamp": now_iso(),
            "trace_file": args.trace_file,
        }
        state["nodes"][args.junction]["junction_audit_id"] = audit_id
        save_state(root, state)
    print(audit_id)


def get_ancestor_pids() -> list[int]:
    """Walk up the process tree via /proc, returning the chain of ancestor PIDs
    from self up to (but not including) PID 1.

    Used by session-step to detect 'same Claude Code session'. Bash `$(...)`
    command substitution spawns a transient subshell, so getppid() varies between
    consecutive calls — but the long-lived Claude Code main process appears in
    every call's ancestor chain. Set intersection of ancestor lists between
    successive invocations is a reliable 'same-session' predicate.
    """
    pids: list[int] = []
    pid = os.getpid()
    seen: set[int] = {pid}
    while pid > 1:
        try:
            with open(f"/proc/{pid}/status") as f:
                ppid: int | None = None
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
            if ppid is None or ppid <= 1 or ppid in seen:
                break
            pids.append(ppid)
            seen.add(ppid)
            pid = ppid
        except (OSError, ValueError):
            break
    return pids


def cmd_session_step(args) -> None:
    """Track how many autopilot steps have run within the current Claude Code
    session. Detects 'same session' by ancestor-PID-chain intersection (robust
    against transient subshell PIDs from bash `$()` substitution). When count
    exceeds `--threshold`, reports `should_pause=true` so autopilot stops and
    asks the user to restart the session for a clean context window.

    Stored in `.research-tree/session_step.json`:
        {
          "ancestor_pids": [<pid>, ...],   # process tree up at first call
          "count": <int>,
          "started_at": <iso>,
          "last_step_at": <iso>
        }
    """
    root = Path(args.project_root).resolve()
    state_dir = root / STATE_DIR_NAME
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / "session_step.json"
    current_ancestors = get_ancestor_pids()

    prev: dict[str, Any] = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text())
        except json.JSONDecodeError:
            prev = {}

    prev_ancestors = prev.get("ancestor_pids", []) or []
    same_session = bool(set(current_ancestors) & set(prev_ancestors))

    if args.action == "report":
        count = prev.get("count", 0) if same_session else 0
        action_taken = "report"
    elif args.action == "increment":
        if same_session:
            count = prev.get("count", 0) + 1
            started = prev.get("started_at", now_iso())
            # Union ancestors so we keep accumulating known session pids
            merged_ancestors = sorted(set(prev_ancestors) | set(current_ancestors))
        else:
            count = 1
            started = now_iso()
            merged_ancestors = current_ancestors
        action_taken = "increment"
        payload = {
            "ancestor_pids": merged_ancestors,
            "count": count,
            "started_at": started,
            "last_step_at": now_iso(),
        }
        p.write_text(json.dumps(payload, indent=2))
    elif args.action == "reset":
        count = 0
        action_taken = "reset"
        if p.exists():
            p.unlink()
    else:
        sys.exit(f"ERROR: unknown action {args.action!r}")

    threshold = args.threshold
    should_pause = count >= threshold
    out = {
        "ancestor_pids_sampled": current_ancestors,
        "same_session_as_last_call": same_session,
        "count": count,
        "threshold": threshold,
        "should_pause": should_pause,
        "action": action_taken,
    }
    print(json.dumps(out, indent=2))
    return 1 if should_pause else 0


def cmd_budget_check(args) -> None:
    root = Path(args.project_root).resolve()
    state = load_state(root)
    s = state["stats"]
    c = state["global_constraints"]
    over = []
    if s["nodes_total"] >= c["max_total_nodes"]:
        over.append(f"nodes_total {s['nodes_total']} >= max_total_nodes {c['max_total_nodes']}")
    if s["gpu_hours_used"] >= c["max_gpu_hours_total"]:
        over.append(
            f"gpu_hours_used {s['gpu_hours_used']} >= max_gpu_hours_total {c['max_gpu_hours_total']}"
        )
    if over:
        for x in over:
            print(f"OVER: {x}")
        sys.exit(1)
    print("OK: all budgets under limit")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", default=os.getcwd(), help="project root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize a new tree")
    p_init.add_argument("idea", help="root research idea / direction")
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--max-depth", type=int, default=5)
    p_init.add_argument("--max-branches", type=int, default=4)
    p_init.add_argument("--max-total-nodes", type=int, default=30)
    p_init.add_argument("--max-gpu-hours", type=float, default=48.0)
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", help="add a child branch")
    p_add.add_argument("parent")
    p_add.add_argument("kind", help=f"one of: {sorted(VALID_KINDS)}")
    p_add.add_argument("title")
    p_add.add_argument("--description", default=None)
    # v0.1.6 — task-type-aware nodes
    p_add.add_argument(
        "--task-type",
        default="training",
        choices=sorted(VALID_TASK_TYPES),
        help="kind of work this branch performs (drives validator schema, default: training)",
    )
    p_add.add_argument(
        "--depends-on",
        default=None,
        help="comma-separated node_ids that must complete before pick-next selects this node",
    )
    p_add.add_argument(
        "--human-only",
        action="store_true",
        help="mark branch as human-only (autopilot pick-next skips; user must execute manually)",
    )
    p_add.set_defaults(func=cmd_add)

    p_set = sub.add_parser(
        "set",
        help="update non-status node fields (status changes use complete/die/running)",
    )
    p_set.add_argument("node_id")
    p_set.add_argument("assignments", nargs="+", help="key=value pairs")
    p_set.set_defaults(func=cmd_set)

    p_running = sub.add_parser("running", help="mark a node as running (in-progress)")
    p_running.add_argument("node_id")
    p_running.set_defaults(func=cmd_running)

    p_complete = sub.add_parser(
        "complete",
        help="mark a node completed (requires PASSING validator report)",
    )
    p_complete.add_argument("node_id")
    p_complete.add_argument(
        "--validator-report",
        required=True,
        help="path to charter_validator.py JSON output; must contain verdict=PASS",
    )
    p_complete.add_argument("--score", type=float, required=True)
    p_complete.add_argument(
        "--done-ready",
        action="store_true",
        help="set done_ready=true (DONE.md will be written on next synthesize)",
    )
    p_complete.set_defaults(func=cmd_complete)

    p_die = sub.add_parser("die", help="mark a node dead with a reason")
    p_die.add_argument("node_id")
    p_die.add_argument("--reason", required=True)
    p_die.add_argument("--evidence", default=None)
    p_die.set_defaults(func=cmd_die)

    p_reopen = sub.add_parser(
        "reopen",
        help="admin: reset a dead/completed node back to pending (clears score/death/proof)",
    )
    p_reopen.add_argument("node_id")
    p_reopen.set_defaults(func=cmd_reopen)

    p_get = sub.add_parser("get", help="print one node")
    p_get.add_argument("node_id")
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser("list", help="list nodes")
    p_list.add_argument("--status", choices=sorted(VALID_STATUSES))
    p_list.set_defaults(func=cmd_list)

    p_pick = sub.add_parser("pick-next", help="pick next leaf to expand/execute")
    p_pick.set_defaults(func=cmd_pick_next)

    # v0.1.6 — dependency inspection
    p_deps = sub.add_parser(
        "deps",
        help="show dependency status for one node (exit 0 if satisfied, 1 if not)",
    )
    p_deps.add_argument("node_id")
    p_deps.set_defaults(func=cmd_deps)

    p_tree = sub.add_parser("tree", help="ASCII tree visualization")
    p_tree.set_defaults(func=cmd_tree)

    p_stats = sub.add_parser("stats", help="summary stats")
    p_stats.set_defaults(func=cmd_stats)

    p_audit = sub.add_parser("audit-add", help="record a junction audit")
    p_audit.add_argument("junction")
    p_audit.add_argument("reviewer")
    p_audit.add_argument("verdict")
    p_audit.add_argument("--trace-file", default=None)
    p_audit.set_defaults(func=cmd_audit_add)

    p_budget = sub.add_parser("budget-check", help="exit 1 if any budget exceeded")
    p_budget.set_defaults(func=cmd_budget_check)

    p_session = sub.add_parser(
        "session-step",
        help="track autopilot step count per Claude Code session (PPID-based); "
             "reports should_pause=true after threshold steps",
    )
    p_session.add_argument(
        "action",
        choices=("report", "increment", "reset"),
        help="report = read without modifying; increment = +1 and save; reset = clear",
    )
    p_session.add_argument(
        "--threshold", type=int, default=DEFAULT_SESSION_STEP_THRESHOLD,
        help=f"steps in a session before should_pause=true (default {DEFAULT_SESSION_STEP_THRESHOLD})",
    )
    p_session.set_defaults(func=cmd_session_step)

    args = p.parse_args()
    rc = args.func(args)
    if isinstance(rc, int):
        sys.exit(rc)


if __name__ == "__main__":
    main()
