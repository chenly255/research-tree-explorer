"""Core data model — Node / Edge / Graph.

Design contract: docs/V1-ARCHITECTURE.md (sections "Layer 1" + "graph.py").

Key invariants:
- Nodes hold no relationship state. All relationships are edges.
- Status is three orthogonal axes (lifecycle / is_branched / is_abandoned),
  not one enum.
- Schema is permanently additive — new fields default to None/empty so old
  readers stay forward-compatible. NEVER bump a SCHEMA_VERSION; never change
  an existing field's semantics.
- Persistence is exactly graph.json. Migration from v0.5 tree.json lives in
  migrator.py and is triggered automatically by cli.py before any read.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal


STATE_DIR_NAME = ".research-tree"
GRAPH_FILE_NAME = "graph.json"
LOCK_FILE_NAME = "graph.lock"


VALID_KINDS = {
    "root",
    "approach",
    "architecture",
    "experiment",
    "ablation",
    "narrative",
    "synthesis",   # v1.0 — node_merger output
    "custom",
}

VALID_TASK_TYPES = {
    "training",
    "audit",
    "analysis",
    "data-acquisition",
    "framing-decision",
    "mixed",
}

VALID_LIFECYCLES = {"created", "running", "done", "failed"}

VALID_EDGE_KINDS = {
    "parent-of",        # structural parent → child
    "hard-dep",         # src must complete before dst can run
    "soft-dep",         # recommended order, doesn't block
    "merges-into",      # src result feeds into dst synthesis node
    "derived-from",     # dst is a refinement / continuation of src
    "parallel-with",    # batched dispatch hint (symmetric in spirit)
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- data classes ----------


@dataclass
class Node:
    id: str
    kind: str
    task_type: str
    title: str
    description: str

    # three orthogonal status axes (replaces v0.5 status enum)
    lifecycle: Literal["created", "running", "done", "failed"] = "created"
    is_branched: bool = False
    is_abandoned: bool = False

    cost_budget_hours: float | None = None
    info_value: int | None = None        # 1-5
    score: float | None = None

    # everything else (death_reason, branch_dir, codex audit pointer, etc.)
    # is namespaced under artifacts to keep the top-level small.
    artifacts: dict = field(default_factory=dict)

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        # Permissively skip unknown keys so v1.x readers tolerate new fields.
        known = {
            "id", "kind", "task_type", "title", "description",
            "lifecycle", "is_branched", "is_abandoned",
            "cost_budget_hours", "info_value", "score",
            "artifacts", "created_at", "updated_at",
        }
        filtered = {k: v for k, v in d.items() if k in known}
        # defensive default for artifacts dict
        filtered.setdefault("artifacts", {})
        return cls(**filtered)


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    created_at: str = field(default_factory=now_iso)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        known = {"src", "dst", "kind", "created_at", "metadata"}
        filtered = {k: v for k, v in d.items() if k in known}
        filtered.setdefault("metadata", {})
        return cls(**filtered)

    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.kind)


# ---------- locking ----------


@contextlib.contextmanager
def graph_lock(root: Path) -> Iterator[None]:
    """Exclusive flock on graph.lock for the duration of the block.

    Mirrors v0.5 tree_state.state_lock — same O_NOFOLLOW hardening, same
    semantics. Prevents two concurrent CLI invocations from corrupting
    graph.json or producing duplicate IDs.
    """
    lp = root / STATE_DIR_NAME / LOCK_FILE_NAME
    lp.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(
            str(lp),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as e:
        sys.exit(
            f"ERROR: cannot open lockfile {lp}: {e}. "
            f"If {lp} is a symlink, remove it — lockfile must not follow symlinks."
        )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------- graph ----------


class Graph:
    """In-memory representation of the research DAG.

    All relationship queries (parent / children / depth / depends_on / merges)
    are computed from `edges`. Nodes never carry relationship state.

    Persistence: `graph.json` at <project_root>/.research-tree/graph.json.
    """

    def __init__(
        self,
        *,
        project: str,
        project_root: str,
        root_idea: str,
        nodes: dict[str, Node] | None = None,
        edges: list[Edge] | None = None,
        audits: dict | None = None,
        global_constraints: dict | None = None,
        stats: dict | None = None,
        created_at: str | None = None,
    ):
        self.project = project
        self.project_root = project_root
        self.root_idea = root_idea
        self.nodes: dict[str, Node] = nodes or {}
        self.edges: list[Edge] = edges or []
        self.audits: dict = audits or {}
        self.global_constraints: dict = global_constraints or {
            "max_depth": 5,
            "max_branches_per_junction": 4,
            "max_total_nodes": 30,
            "max_gpu_hours_total": 48.0,
        }
        self.stats: dict = stats or {}
        self.created_at = created_at or now_iso()
        self.updated_at = now_iso()

        # edge dedup index (key tuple → edge) for O(1) duplicate detection
        self._edge_index: dict[tuple[str, str, str], Edge] = {}
        for e in self.edges:
            self._edge_index[e.key()] = e

        self._recompute_stats()

    # ---------- relationship queries ----------

    def parent_of(self, node_id: str) -> str | None:
        for e in self.edges:
            if e.kind == "parent-of" and e.dst == node_id:
                return e.src
        return None

    def children_of(self, node_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.kind == "parent-of" and e.src == node_id]

    def depth_of(self, node_id: str) -> int:
        """Walk parent-of chain back to root. root has depth 0."""
        depth = 0
        seen = set()
        current = node_id
        while current and current != "root":
            if current in seen:
                # cycle detection — should never happen but degrade gracefully
                return depth
            seen.add(current)
            parent = self.parent_of(current)
            if parent is None:
                break
            current = parent
            depth += 1
        return depth

    def hard_deps_of(self, node_id: str) -> list[str]:
        return [e.src for e in self.edges if e.kind == "hard-dep" and e.dst == node_id]

    def soft_deps_of(self, node_id: str) -> list[str]:
        return [e.src for e in self.edges if e.kind == "soft-dep" and e.dst == node_id]

    def parallel_peers_of(self, node_id: str) -> list[str]:
        peers = set()
        for e in self.edges:
            if e.kind == "parallel-with":
                if e.src == node_id:
                    peers.add(e.dst)
                elif e.dst == node_id:
                    peers.add(e.src)
        return sorted(peers)

    def merge_sources_of(self, node_id: str) -> list[str]:
        """If node_id is a synthesis target, return the source nodes it merged from."""
        return [e.src for e in self.edges if e.kind == "merges-into" and e.dst == node_id]

    def merge_targets_of(self, node_id: str) -> list[str]:
        """If node_id was merged into one or more synthesis nodes, return those targets."""
        return [e.dst for e in self.edges if e.kind == "merges-into" and e.src == node_id]

    # ---------- lifecycle / abandonment helpers ----------

    def is_alive(self, node_id: str) -> bool:
        n = self.nodes.get(node_id)
        if n is None:
            return False
        return n.lifecycle != "failed" and not n.is_abandoned

    def is_pickable(self, node_id: str) -> bool:
        """A node is pickable iff:
        - lifecycle == created (work not yet started)
        - not abandoned
        - not is_branched (a branched node's work is already distributed to
          children; the parent itself is a container, not a work unit)
        - not human_only
        - all hard-dep sources are in lifecycle=done

        Note: root is never pickable because root.is_branched=True the
        moment a first child is added.
        """
        n = self.nodes.get(node_id)
        if n is None:
            return False
        if n.lifecycle != "created":
            return False
        if n.is_abandoned:
            return False
        if n.is_branched:
            return False
        if n.artifacts.get("human_only"):
            return False
        for dep_id in self.hard_deps_of(node_id):
            dep = self.nodes.get(dep_id)
            if dep is None or dep.lifecycle != "done":
                return False
        return True

    def has_pending_children(self, node_id: str) -> bool:
        return any(
            self.nodes[c].lifecycle == "created" and not self.nodes[c].is_abandoned
            for c in self.children_of(node_id)
            if c in self.nodes
        )

    # ---------- mutation ----------

    def add_node(self, node: Node) -> None:
        if node.id in self.nodes:
            raise ValueError(f"node {node.id!r} already exists")
        if node.kind not in VALID_KINDS:
            raise ValueError(f"invalid kind {node.kind!r}; valid: {sorted(VALID_KINDS)}")
        if node.task_type not in VALID_TASK_TYPES:
            raise ValueError(f"invalid task_type {node.task_type!r}; valid: {sorted(VALID_TASK_TYPES)}")
        if node.lifecycle not in VALID_LIFECYCLES:
            raise ValueError(f"invalid lifecycle {node.lifecycle!r}; valid: {sorted(VALID_LIFECYCLES)}")
        self.nodes[node.id] = node
        self.updated_at = now_iso()
        self._recompute_stats()

    def add_edge(self, edge: Edge) -> None:
        if edge.kind not in VALID_EDGE_KINDS:
            raise ValueError(f"invalid edge kind {edge.kind!r}; valid: {sorted(VALID_EDGE_KINDS)}")
        if edge.src not in self.nodes and edge.src != "root":
            raise ValueError(f"edge.src {edge.src!r} not in graph")
        if edge.dst not in self.nodes:
            raise ValueError(f"edge.dst {edge.dst!r} not in graph")
        k = edge.key()
        if k in self._edge_index:
            return  # idempotent — same edge twice is a no-op
        self.edges.append(edge)
        self._edge_index[k] = edge
        self.updated_at = now_iso()

    def remove_edge(self, src: str, dst: str, kind: str) -> bool:
        k = (src, dst, kind)
        if k not in self._edge_index:
            return False
        e = self._edge_index.pop(k)
        self.edges.remove(e)
        self.updated_at = now_iso()
        return True

    def update_node(self, node_id: str, **changes) -> None:
        """Update a node's mutable fields. Lifecycle transitions go through
        dedicated helpers below; this is for description / score / artifacts /
        cost_budget_hours / info_value / etc."""
        n = self.nodes[node_id]
        for key, value in changes.items():
            if not hasattr(n, key):
                raise ValueError(f"unknown node field {key!r}")
            if key == "lifecycle":
                raise ValueError("use set_lifecycle() to transition lifecycle")
            setattr(n, key, value)
        n.updated_at = now_iso()
        self.updated_at = now_iso()
        self._recompute_stats()

    def set_lifecycle(self, node_id: str, new_lifecycle: str) -> None:
        if new_lifecycle not in VALID_LIFECYCLES:
            raise ValueError(f"invalid lifecycle {new_lifecycle!r}")
        n = self.nodes[node_id]
        n.lifecycle = new_lifecycle
        n.updated_at = now_iso()
        self.updated_at = now_iso()
        self._recompute_stats()

    def set_branched(self, node_id: str, value: bool = True) -> None:
        self.nodes[node_id].is_branched = value
        self.nodes[node_id].updated_at = now_iso()
        self.updated_at = now_iso()

    def set_abandoned(self, node_id: str, value: bool) -> None:
        self.nodes[node_id].is_abandoned = value
        self.nodes[node_id].updated_at = now_iso()
        self.updated_at = now_iso()
        self._recompute_stats()

    # ---------- id allocation ----------

    def next_id_under(self, parent_id: str) -> str:
        """Allocate the next child id under parent. Matches v0.5 convention:
        root children are 1, 2, 3, ...; deeper children append .N suffixes.

        Identity must not depend on whether the parent-of edge has been added
        yet — a caller might allocate the id, add the node, then add the edge
        in three separate steps. So we enumerate all node ids that look like
        children of `parent_id` and take max + 1.
        """
        import re
        if parent_id == "root":
            # direct children of root have ids "1", "2", ... (no dot)
            pattern = re.compile(r"^(\d+)$")
        else:
            pattern = re.compile(rf"^{re.escape(parent_id)}\.(\d+)$")
        max_n = 0
        for existing_id in self.nodes:
            m = pattern.match(existing_id)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except ValueError:
                    continue
        next_n = max_n + 1
        return str(next_n) if parent_id == "root" else f"{parent_id}.{next_n}"

    # ---------- stats ----------

    def _recompute_stats(self) -> None:
        nodes = list(self.nodes.values())
        self.stats = {
            "nodes_total": len(nodes),
            "by_lifecycle": {
                lc: sum(1 for n in nodes if n.lifecycle == lc)
                for lc in VALID_LIFECYCLES
            },
            "by_abandoned": sum(1 for n in nodes if n.is_abandoned),
            "by_branched": sum(1 for n in nodes if n.is_branched),
            "gpu_hours_used": self.stats.get("gpu_hours_used", 0.0) if isinstance(self.stats, dict) else 0.0,
        }

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "project_root": self.project_root,
            "root_idea": self.root_idea,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "audits": self.audits,
            "global_constraints": self.global_constraints,
            "stats": self.stats,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=False)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Graph":
        with path.open() as f:
            d = json.load(f)
        nodes = {nid: Node.from_dict(nd) for nid, nd in d.get("nodes", {}).items()}
        edges = [Edge.from_dict(ed) for ed in d.get("edges", [])]
        return cls(
            project=d.get("project", "unknown"),
            project_root=d.get("project_root", ""),
            root_idea=d.get("root_idea", ""),
            nodes=nodes,
            edges=edges,
            audits=d.get("audits", {}),
            global_constraints=d.get("global_constraints"),
            stats=d.get("stats"),
            created_at=d.get("created_at"),
        )


# ---------- helpers for callers ----------


def graph_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / GRAPH_FILE_NAME


def has_graph(root: Path) -> bool:
    return graph_path(root).exists()


def init_empty(root: Path, root_idea: str, **constraints) -> Graph:
    """Create a fresh graph with a root node."""
    g = Graph(
        project=root.name,
        project_root=str(root),
        root_idea=root_idea,
        global_constraints={
            "max_depth": constraints.get("max_depth", 5),
            "max_branches_per_junction": constraints.get("max_branches", 4),
            "max_total_nodes": constraints.get("max_total_nodes", 30),
            "max_gpu_hours_total": constraints.get("max_gpu_hours", 48.0),
        },
    )
    root_node = Node(
        id="root",
        kind="root",
        task_type="mixed",
        title=root_idea[:200],
        description=root_idea,
        lifecycle="created",
        is_branched=False,
        is_abandoned=False,
    )
    g.add_node(root_node)
    return g
