"""v0.5 tree.json → v1.0 graph.json migrator.

Design contract: docs/V1-ARCHITECTURE.md (section "migrator.py").

This runs **automatically** at the top of every CLI invocation if graph.json
is missing but tree.json exists. One-way conversion — v1.0 never writes back
to tree.json. The old file is preserved on disk (read-only snapshot) for one
milestone in case Lily wants to compare.

Mapping (v0.5 → v1.0):
- node.parent           → parent-of edge from parent into node
- node.children         → derived from edges (not migrated explicitly)
- node.depends_on       → hard-dep edge from each dep into node
- node.depends_on_soft  → soft-dep edge from each dep into node
- node.parallel_group   → parallel-with edges across group members (m:n bidir)
- node.status (6 enum)  → (lifecycle, is_branched, is_abandoned) triple
- node.branch_dir       → artifacts.branch_dir
- node.death_reason     → artifacts.death_reason
- node.death_evidence   → artifacts.death_evidence
- node.completion_proof → artifacts.completion_proof
- node.junction_audit_id → artifacts.junction_audit_id
- node.spawned_by_agent → artifacts.spawned_by_agent
- node.repair_attempts  → artifacts.repair_attempts
- node.last_failure_context → artifacts.last_failure_context
- node.direct_executable → dropped (now derived: not is_branched AND no pending children)
- node.budget_hours_min / full → cost_budget_hours (uses min if set, else full)
- node.info_value_score  → info_value
- node.human_only       → artifacts.human_only (kept for is_pickable check)
- node.task_type        → preserved as-is
- top-level audits / global_constraints → preserved
- SCHEMA_VERSION        → dropped (no more versioning)

Verification: caller can pass `dry_run=True` to get the resulting graph in
memory without writing graph.json, useful for sc-bias E2E validation.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .graph import (
    Edge,
    Graph,
    Node,
    STATE_DIR_NAME,
    graph_path,
    now_iso,
)


# v0.5 status → (lifecycle, is_branched_default, is_abandoned)
# is_branched is overridden per-node based on whether children exist.
STATUS_TO_LIFECYCLE = {
    "pending":   "created",
    "expanded":  "created",   # node's own work may still be pending; children just exist
    "running":   "running",
    "completed": "done",
    "dead":      "failed",
    "abandoned": "created",   # paired with is_abandoned=True
    # v0.2-era "forked" (v0.4 collapsed into expanded; defensive)
    "forked":    "created",
}


@dataclass
class MigrationReport:
    nodes_migrated: int = 0
    edges_created: int = 0
    parallel_groups_resolved: int = 0
    dropped_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    graph_json_path: str | None = None


def _v05_tree_json_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / "tree.json"


def has_v05_tree(root: Path) -> bool:
    return _v05_tree_json_path(root).exists()


def migrate(root: Path, *, dry_run: bool = False) -> tuple[Graph, MigrationReport]:
    """Read v0.5 tree.json under root, return (graph, report).

    If `dry_run=False`, also persist graph.json. Caller wraps in graph_lock().
    The v0.5 tree.json is NOT deleted — it stays as a read-only snapshot.
    """
    tree_json = _v05_tree_json_path(root)
    if not tree_json.exists():
        raise FileNotFoundError(f"no v0.5 tree.json at {tree_json}")

    with tree_json.open() as f:
        old = json.load(f)

    report = MigrationReport()

    g = Graph(
        project=old.get("project", root.name),
        project_root=old.get("project_root", str(root)),
        root_idea=old.get("root_idea", ""),
        audits=old.get("audits", {}),
        global_constraints=old.get("global_constraints"),
        created_at=old.get("created_at"),
    )

    old_nodes = old.get("nodes", {})

    # ---------- pass 1: nodes ----------
    for old_id, old_node in old_nodes.items():
        new_node = _convert_node(old_id, old_node, report)
        g.nodes[new_node.id] = new_node
        report.nodes_migrated += 1

    # ---------- pass 2: parent-of edges ----------
    for old_id, old_node in old_nodes.items():
        parent = old_node.get("parent")
        if parent and parent in old_nodes:
            _add_edge_safe(g, Edge(src=parent, dst=old_id, kind="parent-of"), report)
        elif parent is None and old_id != "root":
            report.warnings.append(f"node {old_id!r} has no parent and is not root")

    # ---------- pass 3: hard-dep edges ----------
    for old_id, old_node in old_nodes.items():
        for dep_id in old_node.get("depends_on", []) or []:
            if dep_id in old_nodes:
                _add_edge_safe(g, Edge(src=dep_id, dst=old_id, kind="hard-dep"), report)
            else:
                report.warnings.append(f"node {old_id!r} depends_on unknown node {dep_id!r}")

    # ---------- pass 4: soft-dep edges ----------
    for old_id, old_node in old_nodes.items():
        for soft_id in old_node.get("depends_on_soft", []) or []:
            if soft_id in old_nodes:
                _add_edge_safe(g, Edge(src=soft_id, dst=old_id, kind="soft-dep"), report)

    # ---------- pass 5: parallel-with edges (group → m:n bidir) ----------
    by_group: dict[str, list[str]] = {}
    for old_id, old_node in old_nodes.items():
        pg = old_node.get("parallel_group")
        if pg:
            by_group.setdefault(pg, []).append(old_id)
    for group_name, members in by_group.items():
        if len(members) < 2:
            continue
        report.parallel_groups_resolved += 1
        for i, a in enumerate(members):
            for b in members[i+1:]:
                _add_edge_safe(
                    g,
                    Edge(src=a, dst=b, kind="parallel-with",
                         metadata={"group": group_name}),
                    report,
                )

    # ---------- pass 6: is_branched flag (derived) ----------
    for nid, n in g.nodes.items():
        kids = g.children_of(nid)
        if kids:
            n.is_branched = True

    # bypassed add_node above (direct dict assignment for speed); recompute stats now
    g._recompute_stats()

    # ---------- persistence ----------
    if not dry_run:
        out_path = graph_path(root)
        g.save(out_path)
        report.graph_json_path = str(out_path)

    return g, report


def _convert_node(old_id: str, old_node: dict, report: MigrationReport) -> Node:
    old_status = old_node.get("status", "pending")
    lifecycle = STATUS_TO_LIFECYCLE.get(old_status, "created")
    is_abandoned = (old_status == "abandoned")

    # task_type: v0.5 defaulted to "training" for non-root, "mixed" for root
    task_type = old_node.get("task_type") or ("mixed" if old_node.get("kind") == "root" else "training")

    # cost_budget_hours: prefer min (proposer's PoC scope), fallback full
    cost = old_node.get("budget_hours_min")
    if cost is None:
        cost = old_node.get("budget_hours_full")

    artifacts = {
        "branch_dir": old_node.get("branch_dir"),
        "death_reason": old_node.get("death_reason"),
        "death_evidence": old_node.get("death_evidence"),
        "completion_proof": old_node.get("completion_proof"),
        "junction_audit_id": old_node.get("junction_audit_id"),
        "spawned_by_agent": old_node.get("spawned_by_agent"),
        "repair_attempts": old_node.get("repair_attempts", 0),
        "last_failure_context": old_node.get("last_failure_context"),
        "human_only": old_node.get("human_only", False),
    }
    # strip None values to keep the dict compact
    artifacts = {k: v for k, v in artifacts.items() if v not in (None, False, 0, "")}

    # track dropped v0.5 fields
    for dead_field in ("direct_executable", "schema_version", "agent_capable",
                       "subtree_origin", "max_repair_attempts"):
        if dead_field in old_node:
            if dead_field not in report.dropped_fields:
                report.dropped_fields.append(dead_field)

    n = Node(
        id=old_id,
        kind=old_node.get("kind", "custom"),
        task_type=task_type,
        title=old_node.get("title", "")[:200],
        description=old_node.get("description", ""),
        lifecycle=lifecycle,
        is_branched=False,  # will be set in pass 6
        is_abandoned=is_abandoned,
        cost_budget_hours=cost,
        info_value=old_node.get("info_value_score"),
        score=old_node.get("score"),
        artifacts=artifacts,
        created_at=old_node.get("created_at", now_iso()),
        updated_at=old_node.get("created_at", now_iso()),
    )
    return n


def _add_edge_safe(g: Graph, edge: Edge, report: MigrationReport) -> None:
    try:
        before = len(g.edges)
        g.add_edge(edge)
        if len(g.edges) > before:
            report.edges_created += 1
    except ValueError as e:
        report.warnings.append(f"edge {edge.kind} {edge.src}→{edge.dst}: {e}")


def main() -> int:
    """CLI: python -m research_tree.migrator <project_root> [--dry-run]"""
    import argparse
    p = argparse.ArgumentParser(description="v0.5 tree.json → v1.0 graph.json migrator")
    p.add_argument("project_root", help="absolute path to the project root containing .research-tree/")
    p.add_argument("--dry-run", action="store_true",
                   help="don't write graph.json, just print the report")
    args = p.parse_args()

    root = Path(args.project_root).resolve()
    if not has_v05_tree(root):
        print(f"ERROR: no v0.5 tree.json at {root}/.research-tree/tree.json", file=sys.stderr)
        return 2

    g, report = migrate(root, dry_run=args.dry_run)
    print(json.dumps({
        "nodes_migrated": report.nodes_migrated,
        "edges_created": report.edges_created,
        "parallel_groups_resolved": report.parallel_groups_resolved,
        "dropped_fields": report.dropped_fields,
        "warnings": report.warnings,
        "graph_json_path": report.graph_json_path,
        "by_lifecycle": g.stats.get("by_lifecycle"),
        "nodes_total": g.stats.get("nodes_total"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
