"""Thin CLI entry — research-tree command-line shell.

Design contract: docs/V1-ARCHITECTURE.md (section "cli.py").

Each command is a 5-30 line shell delegating to:
    graph.py             — data model
    migrator.py          — auto-trigger v0.5 → v1.0 on first run
    branching_decider.py — fork / candidate decisions
    node_merger.py       — detect / apply merges
    workers/             — task_type-specific dispatch
    scheduler.py         — event log + branch scan

The v0.5 scripts/tree_state.py (2233 lines) is replaced by a stub that
imports this module's main(). Most operations route through the same
graph_lock() helper so concurrent CLI invocations are safe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import branching_decider, migrator, node_merger
from .graph import (
    Edge,
    Graph,
    Node,
    STATE_DIR_NAME,
    VALID_EDGE_KINDS,
    VALID_KINDS,
    VALID_LIFECYCLES,
    VALID_TASK_TYPES,
    graph_lock,
    graph_path,
    has_graph,
    init_empty,
    now_iso,
)
from .scheduler import Scheduler


HUMAN_GATE_NAME = "AWAITING_HUMAN.md"


# ---------- migrate-on-read ----------


def _ensure_graph(root: Path) -> Graph:
    """Load graph.json, auto-migrating from v0.5 tree.json if needed."""
    if not has_graph(root):
        if migrator.has_v05_tree(root):
            # auto-migrate. Caller will be told via stderr; non-interactive.
            with graph_lock(root):
                g, report = migrator.migrate(root, dry_run=False)
                print(
                    f"[migrate] v0.5 tree.json → v1.0 graph.json: "
                    f"nodes={report.nodes_migrated}, edges={report.edges_created}",
                    file=sys.stderr,
                )
                if report.warnings:
                    print(f"[migrate] warnings: {report.warnings}", file=sys.stderr)
                return g
        sys.exit(
            f"ERROR: no tree found at {root}/.research-tree/. "
            f"Run `research-tree init '<idea>'` first."
        )
    return Graph.load(graph_path(root))


# ---------- commands: init / add / set / get / list / tree / stats ----------


def cmd_init(args) -> int:
    root = Path(args.project_root).resolve()
    if has_graph(root) and not args.force:
        sys.exit(f"ERROR: graph already exists at {graph_path(root)}. Use --force to overwrite.")
    g = init_empty(
        root,
        args.idea,
        max_depth=args.max_depth,
        max_branches=args.max_branches,
        max_total_nodes=args.max_total_nodes,
        max_gpu_hours=args.max_gpu_hours,
    )
    with graph_lock(root):
        g.save(graph_path(root))
    (root / STATE_DIR_NAME / "branches").mkdir(parents=True, exist_ok=True)
    (root / STATE_DIR_NAME / "audits").mkdir(parents=True, exist_ok=True)
    progress_log = root / STATE_DIR_NAME / "progress.log"
    if not progress_log.exists():
        progress_log.write_text(f"{now_iso()}  step=0  action=init  node=root\n")
    print(f"OK: graph initialized at {graph_path(root)}")
    print(f"root idea: {args.idea}")
    return 0


def cmd_add(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.parent not in g.nodes:
            sys.exit(f"ERROR: parent {args.parent!r} not found")
        if args.kind not in VALID_KINDS:
            sys.exit(f"ERROR: invalid kind {args.kind!r}; valid: {sorted(VALID_KINDS)}")
        task_type = args.task_type or "training"
        if task_type not in VALID_TASK_TYPES:
            sys.exit(f"ERROR: invalid task_type {task_type!r}; valid: {sorted(VALID_TASK_TYPES)}")
        new_id = g.next_id_under(args.parent)
        depth = g.depth_of(args.parent) + 1
        if depth > g.global_constraints["max_depth"]:
            sys.exit(f"ERROR: would exceed max_depth ({g.global_constraints['max_depth']})")

        artifacts: dict = {"branch_dir": f"{STATE_DIR_NAME}/branches/{new_id}"}
        if args.human_only:
            artifacts["human_only"] = True
        if args.spawned_by_agent:
            artifacts["spawned_by_agent"] = args.spawned_by_agent

        n = Node(
            id=new_id,
            kind=args.kind,
            task_type=task_type,
            title=args.title[:200],
            description=args.description or args.title,
            cost_budget_hours=args.budget_hours_min,
            info_value=args.info_value_score,
            artifacts=artifacts,
        )
        g.add_node(n)
        g.add_edge(Edge(src=args.parent, dst=new_id, kind="parent-of"))

        for dep in (args.depends_on or "").split(","):
            dep = dep.strip()
            if dep:
                if dep not in g.nodes:
                    sys.exit(f"ERROR: depends_on {dep!r} not in graph")
                g.add_edge(Edge(src=dep, dst=new_id, kind="hard-dep"))

        for soft in (args.depends_on_soft or "").split(","):
            soft = soft.strip()
            if soft:
                if soft not in g.nodes:
                    sys.exit(f"ERROR: depends_on_soft {soft!r} not in graph")
                g.add_edge(Edge(src=soft, dst=new_id, kind="soft-dep"))

        # mark parent as branched
        g.set_branched(args.parent, True)

        (root / n.artifacts["branch_dir"]).mkdir(parents=True, exist_ok=True)
        g.save(graph_path(root))
    print(new_id)
    return 0


def cmd_get(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    if args.node_id not in g.nodes:
        sys.exit(f"ERROR: node {args.node_id!r} not found")
    n = g.nodes[args.node_id]
    out = n.to_dict()
    # enrich with graph-derived fields the caller often wants
    out["parent"] = g.parent_of(args.node_id)
    out["children"] = g.children_of(args.node_id)
    out["hard_deps"] = g.hard_deps_of(args.node_id)
    out["soft_deps"] = g.soft_deps_of(args.node_id)
    out["depth"] = g.depth_of(args.node_id)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_set(args) -> int:
    """Restricted set — same allowlist as v0.5: description, cost_budget_hours,
    info_value_score, soft_dep changes via add/remove not via set."""
    root = Path(args.project_root).resolve()
    SET_ALLOWED = {"description", "cost_budget_hours", "info_value_score", "title"}
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        updates = {}
        for kv in args.kv:
            if "=" not in kv:
                sys.exit(f"ERROR: --kv must be key=value, got {kv!r}")
            k, v = kv.split("=", 1)
            if k not in SET_ALLOWED:
                sys.exit(f"ERROR: cannot set {k!r}; allowed: {sorted(SET_ALLOWED)}")
            if k in ("cost_budget_hours",):
                try:
                    v = float(v)
                except ValueError:
                    sys.exit(f"ERROR: {k} must be float")
            elif k in ("info_value_score",):
                try:
                    v = int(v)
                except ValueError:
                    sys.exit(f"ERROR: {k} must be int")
            updates[k] = v
        g.update_node(args.node_id, **updates)
        g.save(graph_path(root))
    print(json.dumps({"node": args.node_id, "updates": updates}, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    for nid, n in sorted(g.nodes.items()):
        if args.lifecycle and n.lifecycle != args.lifecycle:
            continue
        if args.task_type and n.task_type != args.task_type:
            continue
        marker = "⏸" if n.is_abandoned else {"created": "·", "running": "►",
                                              "done": "✓", "failed": "✗"}[n.lifecycle]
        branched = " ▸" if n.is_branched else ""
        print(f"{marker}{branched:2} {nid:10} [{n.task_type:18}] {n.title[:80]}")
    return 0


def cmd_tree(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)

    def render(nid: str, prefix: str = "", is_last: bool = True) -> None:
        n = g.nodes[nid]
        marker = ("⏸" if n.is_abandoned
                  else {"created": "·", "running": "►", "done": "✓", "failed": "✗"}[n.lifecycle])
        score = f" [{n.score:.2f}]" if n.score is not None else ""
        connector = "└── " if is_last else "├── "
        if nid == "root":
            print(f"{marker} root: {n.title}")
        else:
            print(f"{prefix}{connector}{marker} {nid}{score} {n.title[:80]}")
        kids = g.children_of(nid)
        new_prefix = prefix + ("    " if is_last else "│   ")
        if nid == "root":
            new_prefix = ""
        for i, c in enumerate(kids):
            render(c, new_prefix, i == len(kids) - 1)

    render("root")
    return 0


def cmd_stats(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    print(json.dumps({
        "project": g.project,
        "root_idea": g.root_idea[:120],
        "created_at": g.created_at,
        "updated_at": g.updated_at,
        "stats": g.stats,
        "constraints": g.global_constraints,
    }, indent=2, ensure_ascii=False))
    return 0


# ---------- commands: lifecycle (pick-next / running / complete / die / backtrack / resume-branch) ----------


def cmd_pick_next(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    candidates = []
    for nid, n in g.nodes.items():
        if not g.is_pickable(nid):
            continue
        depth = g.depth_of(nid)
        # parallel-group bonus: prefer if any peer is running
        peers = g.parallel_peers_of(nid)
        peer_running = any(g.nodes[p].lifecycle == "running" for p in peers if p in g.nodes)
        group_bonus = 1.0 if peer_running else 0.0
        # info_value bonus (1-5 → 0-0.5)
        iv = (n.info_value or 0) * 0.1
        # parent score
        parent_id = g.parent_of(nid)
        parent_score = g.nodes[parent_id].score if parent_id and g.nodes[parent_id].score is not None else 0.5
        composite = (group_bonus, iv, parent_score, -depth, nid)  # last is for determinism
        candidates.append((composite, nid))
    if not candidates:
        print("NONE")
        return 0
    candidates.sort(reverse=True)
    print(candidates[0][1])
    return 0


def cmd_running(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        n = g.nodes[args.node_id]
        if n.lifecycle != "created":
            sys.exit(f"ERROR: node lifecycle is {n.lifecycle}, cannot transition to running")
        g.set_lifecycle(args.node_id, "running")
        g.save(graph_path(root))
    print("OK")
    return 0


def cmd_complete(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        n = g.nodes[args.node_id]
        if n.lifecycle not in ("running", "created"):
            sys.exit(f"ERROR: node lifecycle is {n.lifecycle}, cannot complete")
        # delegate validation to the Worker if requested
        if args.with_validation:
            from .workers import get_worker
            w = get_worker(n.task_type)
            branch_dir = root / (n.artifacts.get("branch_dir") or f"{STATE_DIR_NAME}/branches/{args.node_id}")
            result = w.validate(n, branch_dir,
                                require_codex_audit=args.require_codex_audit,
                                nonce_file=Path(args.audit_nonce_file) if args.audit_nonce_file else None)
            if result.verdict == "FAIL":
                sys.exit(f"validation FAIL: {result.failures[:3]}")
            score = result.metric if result.metric is not None else args.score
        else:
            score = args.score
        g.set_lifecycle(args.node_id, "done")
        g.update_node(args.node_id, score=score)
        if args.completion_proof:
            new_artifacts = dict(n.artifacts)
            new_artifacts["completion_proof"] = args.completion_proof
            g.update_node(args.node_id, artifacts=new_artifacts)
        g.save(graph_path(root))
    print("OK")
    return 0


def cmd_die(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        n = g.nodes[args.node_id]
        if n.lifecycle == "failed":
            print("(already failed, no-op)")
            return 0
        g.set_lifecycle(args.node_id, "failed")
        new_artifacts = dict(n.artifacts)
        new_artifacts["death_reason"] = args.reason
        if args.evidence:
            new_artifacts["death_evidence"] = args.evidence
        g.update_node(args.node_id, artifacts=new_artifacts)
        g.save(graph_path(root))
    print("OK")
    return 0


def cmd_backtrack(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        g.set_abandoned(args.node_id, True)
        if args.reason:
            new_artifacts = dict(g.nodes[args.node_id].artifacts)
            new_artifacts["backtrack_reason"] = args.reason
            g.update_node(args.node_id, artifacts=new_artifacts)
        g.save(graph_path(root))
    print("OK")
    return 0


def cmd_resume_branch(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.node_id not in g.nodes:
            sys.exit(f"ERROR: node {args.node_id!r} not found")
        g.set_abandoned(args.node_id, False)
        g.save(graph_path(root))
    print("OK")
    return 0


# ---------- commands: BranchingDecider ----------


def cmd_decide_fork(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    if args.node_id not in g.nodes:
        sys.exit(f"ERROR: node {args.node_id!r} not found")
    ctx = branching_decider.DeciderContext(
        max_depth=g.global_constraints["max_depth"],
        max_branches=g.global_constraints["max_branches_per_junction"],
    )
    d = branching_decider.decide_to_fork(g.nodes[args.node_id], g, ctx)
    print(json.dumps(d.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_decide_candidate(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    cand = Node(
        id="__candidate__",
        kind=args.kind,
        task_type=args.task_type or "mixed",
        title=args.title,
        description=args.description or args.title,
    )
    ctx = branching_decider.DeciderContext(
        max_depth=g.global_constraints["max_depth"],
        max_branches=g.global_constraints["max_branches_per_junction"],
    )
    d = branching_decider.decide_to_accept_candidate(cand, args.parent, g, ctx)
    print(json.dumps(d.to_dict(), indent=2, ensure_ascii=False))
    return 0


# ---------- commands: NodeMerger ----------


def cmd_detect_merges(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    proposals = node_merger.detect_merge_opportunities(g, project_root=root)
    print(json.dumps([p.to_dict() for p in proposals], indent=2, ensure_ascii=False))
    return 0


def cmd_merge(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        proposals = node_merger.detect_merge_opportunities(g, project_root=root)
        target = next((p for p in proposals if p.proposal_id == args.proposal_id), None)
        if target is None:
            sys.exit(
                f"ERROR: no current proposal {args.proposal_id!r}. "
                f"Available: {[p.proposal_id for p in proposals]}"
            )
        new_id = node_merger.apply_merge(target, g)
        g.save(graph_path(root))
    print(new_id)
    return 0


# ---------- commands: migration / scheduler / audit / budget ----------


def cmd_migrate(args) -> int:
    root = Path(args.project_root).resolve()
    if has_graph(root) and not args.force:
        sys.exit(
            f"ERROR: graph.json already exists at {graph_path(root)}. "
            f"Use --force to re-run migration (deletes graph.json first)."
        )
    if not migrator.has_v05_tree(root):
        sys.exit(f"ERROR: no v0.5 tree.json at {root}/.research-tree/tree.json")
    with graph_lock(root):
        g, report = migrator.migrate(root, dry_run=args.dry_run)
    print(json.dumps({
        "nodes_migrated": report.nodes_migrated,
        "edges_created": report.edges_created,
        "parallel_groups_resolved": report.parallel_groups_resolved,
        "dropped_fields": report.dropped_fields,
        "warnings": report.warnings,
        "graph_json_path": report.graph_json_path,
        "stats": g.stats,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_audit_add(args) -> int:
    root = Path(args.project_root).resolve()
    with graph_lock(root):
        g = _ensure_graph(root)
        if args.junction not in g.nodes:
            sys.exit(f"ERROR: junction {args.junction!r} not found")
        audit_id = f"audit-{len(g.audits) + 1:03d}"
        g.audits[audit_id] = {
            "junction": args.junction,
            "reviewer": args.reviewer,
            "verdict": args.verdict,
            "timestamp": now_iso(),
            "trace_file": args.trace_file,
        }
        new_artifacts = dict(g.nodes[args.junction].artifacts)
        new_artifacts["junction_audit_id"] = audit_id
        g.update_node(args.junction, artifacts=new_artifacts)
        g.save(graph_path(root))
    print(audit_id)
    return 0


def cmd_budget_check(args) -> int:
    root = Path(args.project_root).resolve()
    g = _ensure_graph(root)
    gc = g.global_constraints
    over = []
    if g.stats.get("nodes_total", 0) > gc["max_total_nodes"]:
        over.append(f"total_nodes {g.stats['nodes_total']} > {gc['max_total_nodes']}")
    if g.stats.get("gpu_hours_used", 0) > gc["max_gpu_hours_total"]:
        over.append(f"gpu_hours {g.stats['gpu_hours_used']} > {gc['max_gpu_hours_total']}")
    if over:
        print(json.dumps({"verdict": "OVER", "details": over}, indent=2))
        return 1
    print(json.dumps({"verdict": "OK", "stats": g.stats}, indent=2))
    return 0


def cmd_emit_event(args) -> int:
    root = Path(args.project_root).resolve()
    s = Scheduler(root)
    payload = json.loads(args.payload) if args.payload else {}
    ev = s.emit(args.kind, payload)
    print(json.dumps(ev.to_dict(), ensure_ascii=False))
    return 0


def cmd_watch_events(args) -> int:
    root = Path(args.project_root).resolve()
    s = Scheduler(root)
    events = s.watch_once(advance_cursor=not args.peek)
    print(json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False))
    return 0


def cmd_scan_branches(args) -> int:
    root = Path(args.project_root).resolve()
    s = Scheduler(root)
    new_events = s.scan_branches()
    print(json.dumps([e.to_dict() for e in new_events], indent=2, ensure_ascii=False))
    return 0


# ---------- human-gate ----------


def cmd_human_gate(args) -> int:
    root = Path(args.project_root).resolve()
    gate = root / STATE_DIR_NAME / HUMAN_GATE_NAME
    gate.parent.mkdir(parents=True, exist_ok=True)
    if args.action == "check":
        if gate.exists():
            print(json.dumps({"gate": "up", "path": str(gate), "body": gate.read_text()[:500]}))
            return 2
        print(json.dumps({"gate": "down"}))
        return 0
    if args.action == "set":
        if gate.exists() and not args.overwrite:
            print(json.dumps({"gate": "up", "result": "already_up"}))
            return 0
        gate.write_text(
            f"# AWAITING HUMAN — autopilot paused\n\n"
            f"**Written:** {now_iso()}\n"
            f"**Reason:** {args.reason}\n\n"
            f"To resume: `research-tree resume` (clears this file + cursor).\n"
        )
        print(json.dumps({"gate": "up", "result": "set"}))
        return 0
    if args.action == "clear":
        try:
            gate.unlink()
            print(json.dumps({"gate": "down", "result": "cleared"}))
        except FileNotFoundError:
            print(json.dumps({"gate": "down", "result": "not_present"}))
        return 0
    sys.exit(f"ERROR: unknown human-gate action {args.action!r}")


# ---------- argparse plumbing ----------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="research-tree", description="v1.0 research-tree CLI")
    p.add_argument("--project-root", default=".", help="project root containing .research-tree/")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="create a new tree")
    p_init.add_argument("idea")
    p_init.add_argument("--max-depth", type=int, default=5)
    p_init.add_argument("--max-branches", type=int, default=4)
    p_init.add_argument("--max-total-nodes", type=int, default=30)
    p_init.add_argument("--max-gpu-hours", type=float, default=48.0)
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    # add
    p_add = sub.add_parser("add")
    p_add.add_argument("parent")
    p_add.add_argument("kind", choices=sorted(VALID_KINDS))
    p_add.add_argument("title")
    p_add.add_argument("--description")
    p_add.add_argument("--task-type", default="training", choices=sorted(VALID_TASK_TYPES))
    p_add.add_argument("--depends-on", help="comma-separated hard-dep node ids")
    p_add.add_argument("--depends-on-soft", help="comma-separated soft-dep node ids")
    p_add.add_argument("--human-only", action="store_true")
    p_add.add_argument("--spawned-by-agent", help="agent id (audit trail)")
    p_add.add_argument("--budget-hours-min", type=float)
    p_add.add_argument("--budget-hours-full", type=float)  # ignored in v1.0 (we use min as the budget)
    p_add.add_argument("--info-value-score", type=int, choices=[1, 2, 3, 4, 5])
    p_add.add_argument("--parallel-group", help="(ignored in v1.0 CLI; use add-edge instead)")
    p_add.set_defaults(func=cmd_add)

    # get
    p_get = sub.add_parser("get")
    p_get.add_argument("node_id")
    p_get.set_defaults(func=cmd_get)

    # set
    p_set = sub.add_parser("set")
    p_set.add_argument("node_id")
    p_set.add_argument("kv", nargs="+", help="key=value pairs")
    p_set.set_defaults(func=cmd_set)

    # list
    p_list = sub.add_parser("list")
    p_list.add_argument("--lifecycle", choices=sorted(VALID_LIFECYCLES))
    p_list.add_argument("--task-type", choices=sorted(VALID_TASK_TYPES))
    p_list.set_defaults(func=cmd_list)

    # tree
    p_tree = sub.add_parser("tree")
    p_tree.set_defaults(func=cmd_tree)

    # stats
    p_stats = sub.add_parser("stats")
    p_stats.set_defaults(func=cmd_stats)

    # pick-next
    p_pick = sub.add_parser("pick-next")
    p_pick.set_defaults(func=cmd_pick_next)

    # running
    p_run = sub.add_parser("running")
    p_run.add_argument("node_id")
    p_run.set_defaults(func=cmd_running)

    # complete
    p_complete = sub.add_parser("complete")
    p_complete.add_argument("node_id")
    p_complete.add_argument("--score", type=float)
    p_complete.add_argument("--with-validation", action="store_true",
                            help="run Worker.validate() and gate the transition")
    p_complete.add_argument("--require-codex-audit", action="store_true")
    p_complete.add_argument("--audit-nonce-file")
    p_complete.add_argument("--completion-proof")
    p_complete.set_defaults(func=cmd_complete)

    # die
    p_die = sub.add_parser("die")
    p_die.add_argument("node_id")
    p_die.add_argument("--reason", required=True)
    p_die.add_argument("--evidence")
    p_die.set_defaults(func=cmd_die)

    # backtrack / resume-branch
    p_back = sub.add_parser("backtrack")
    p_back.add_argument("node_id")
    p_back.add_argument("--reason")
    p_back.set_defaults(func=cmd_backtrack)
    p_resume = sub.add_parser("resume-branch")
    p_resume.add_argument("node_id")
    p_resume.set_defaults(func=cmd_resume_branch)

    # decide-fork / decide-candidate
    p_df = sub.add_parser("decide-fork", help="BranchingDecider API 1 — should this node fork?")
    p_df.add_argument("node_id")
    p_df.set_defaults(func=cmd_decide_fork)

    p_dc = sub.add_parser("decide-candidate", help="BranchingDecider API 2 — accept this candidate?")
    p_dc.add_argument("parent")
    p_dc.add_argument("title")
    p_dc.add_argument("--description")
    p_dc.add_argument("--kind", default="custom", choices=sorted(VALID_KINDS))
    p_dc.add_argument("--task-type", choices=sorted(VALID_TASK_TYPES))
    p_dc.set_defaults(func=cmd_decide_candidate)

    # detect-merges / merge
    p_dm = sub.add_parser("detect-merges")
    p_dm.set_defaults(func=cmd_detect_merges)
    p_m = sub.add_parser("merge")
    p_m.add_argument("proposal_id")
    p_m.set_defaults(func=cmd_merge)

    # migrate
    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.add_argument("--force", action="store_true")
    p_mig.set_defaults(func=cmd_migrate)

    # audit-add
    p_aa = sub.add_parser("audit-add")
    p_aa.add_argument("junction")
    p_aa.add_argument("reviewer")
    p_aa.add_argument("verdict")
    p_aa.add_argument("--trace-file")
    p_aa.set_defaults(func=cmd_audit_add)

    # budget-check
    p_bc = sub.add_parser("budget-check")
    p_bc.set_defaults(func=cmd_budget_check)

    # scheduler-related
    p_emit = sub.add_parser("emit-event")
    p_emit.add_argument("--kind", required=True)
    p_emit.add_argument("--payload", default="{}")
    p_emit.set_defaults(func=cmd_emit_event)

    p_we = sub.add_parser("watch-events")
    p_we.add_argument("--peek", action="store_true", help="don't advance cursor")
    p_we.set_defaults(func=cmd_watch_events)

    p_sb = sub.add_parser("scan-branches")
    p_sb.set_defaults(func=cmd_scan_branches)

    # human-gate
    p_hg = sub.add_parser("human-gate")
    p_hg.add_argument("action", choices=["check", "set", "clear"])
    p_hg.add_argument("--reason", default="")
    p_hg.add_argument("--overwrite", action="store_true")
    p_hg.set_defaults(func=cmd_human_gate)

    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
