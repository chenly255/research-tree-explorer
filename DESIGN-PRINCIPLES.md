# Design Principles — Read before optimizing

**Written 2026-05-26 after a Linus-style critique from Lily.**

Before adding any field, command, or SKILL.md section to this tool, read this
document. The point is to stop the "add another field" / "add another option"
spiral that has been the dominant change mode through v0.1 → v0.5.

## The trap to avoid

When you see a problem, the fast fix is to add a field, a flag, or a new
optional parameter. Each individual patch looks reasonable. After 20 patches,
SKILL.md is 985 lines, tree_state.py is 1982 lines, the schema has
budget_hours_min, budget_hours_full, info_value_score, depends_on_soft,
parallel_group, repair_attempts, last_failure_context, spawned_by_agent,
direct_executable, completion_proof, junction_audit_id — and a new user
cannot tell which fields they actually need to set.

**The fast fix makes future fixes harder.** Every new field is a new
migration path, a new corner case in pick-next, a new line in the proposer
prompt. The complexity is multiplicative, not additive.

## Six structural problems that compound

Spotted by Lily after watching the tool design a 30-50h benchmark for a
decision (which-pathway-activity-algorithm) that the field literature had
already answered. The cost-budget patch I added on top did not fix the
underlying issue — the underlying issue is **the tool has no first-class
notion of "is this work worth its cost"**, and bolting on a `budget_hours_*`
field only adds an estimate that downstream code mostly ignores.

The six structural problems, ordered by how much pain they cause:

1. **"Tree" is the wrong data structure**.
   Research exploration is a DAG, not a tree. Real research nodes:
   - reference multiple upstream nodes (merge inputs)
   - are referenced by multiple downstream nodes (fan-out)
   - have cross-cutting "soft" relationships with non-parent/non-child nodes

   The current schema forces all of this through parent-child + a growing
   set of side fields (`depends_on`, `depends_on_soft`, `parallel_group`,
   `merge_sources`). Adding more side fields cannot fix this — the
   underlying object model is wrong.

   **Right fix**: Edges become first-class objects with explicit type
   (`hard-dep`, `soft-dep`, `merges-into`, `derived-from`, `parallel-with`).
   Nodes hold no relationship state. pick-next walks the edge graph.

2. **Node status is not orthogonal**.
   The single `status` field conflates lifecycle, branching, and user
   intent. `pending / expanded / running / completed / dead / abandoned`
   are six values pretending to be exclusive, but a parent can be
   "expanded" AND "running" simultaneously (autopilot already runs the
   parent's own work after expanding it). v0.2 added `forked`, v0.4
   collapsed it back into `expanded` — that reversal was the symptom.

   **Right fix**: three orthogonal axes.
   ```
   lifecycle:    created | running | done | failed
   is_branched:  bool         (have children been added?)
   is_abandoned: bool         (user set aside, reversible)
   ```
   Every state the tool currently represents is one combination of these
   three. The state machine becomes a product type, not an enum.

3. **The five "atomic actions" (expand / execute / audit / merge / prune)
   are not atomic**.
   They are the same operation — `(node, context) -> next_actions` — in
   five different special-cased flavors. Each has its own subagent prompt,
   its own validation chain, its own dispatch logic. Adding a sixth action
   means touching at least four files.

   **Right fix**: one `Worker` interface. Each node `kind` (or each
   `task_type`) registers a worker class. Autopilot's dispatch is one
   function: `worker.run(node, context) -> WorkerResult`. The
   per-task-type artifact requirements move out of SKILL.md into the
   worker class itself.

4. **The gate is poll-based, when it should be event-driven**.
   Autopilot polls a sentinel file every cron tick. `auto-raise` +
   `auto-clear-if-stale` (added 2026-05-26) try to make polling smarter,
   but they are still polling. The root issue: autopilot does not know
   when state changes — it has to re-scan everything every 30 minutes
   to find out.

   **Right fix**: events. A background process exits → emit
   `node-finished` event. A new node is added → emit `node-created`.
   Scheduler subscribes to events and wakes up only when there is real
   work. No gate, no fast-exit, no token-bleed-by-1KB-per-tick.

5. **Two-layer subagent pattern is wasteful**.
   Every expand call spawns a "proposer" subagent. Every execute call
   spawns an "executor" subagent. Their system prompts are 60% identical
   — the charter, the brief, the task_type artifact rules. Two 50KB
   prompts per branch is a structural cost.

   **Right fix**: one Agent role with a `mode` parameter. The
   common context (charter + brief + tree state) gets sent once;
   the mode-specific delta is small.

6. **SCHEMA_VERSION existing at all is failure-by-design**.
   A schema that needs versioning is a schema that broke compatibility.
   A well-designed schema only adds fields; never changes semantics; uses
   absence/null as the "old behavior" indicator. v0.4's `forked` →
   `expanded` reversal proves the schema is changing semantics, not just
   adding optional surface.

   **Right fix**: tag changes as additive (new optional field) or
   replacement (a *new* schema, side-by-side with the old, with the old
   marked deprecated and kept readable for one major version). Never
   in-place edit semantics of an existing field.

## What "optimizing" should mean from now on

When you hit a research-tree problem and want to change something:

1. **Read this file. Look at the six structural problems list.**
2. Ask: which of the six does my proposed change touch?
3. If your fix adds a new field, a new optional flag, or a new edge case
   to an existing dispatch — STOP. That is a patch. Patches are debt.
4. If your fix changes the underlying object model (edges as objects,
   status as 3-axis orthogonal, workers as interface, events instead of
   polling) — proceed. That is structural.
5. If your fix is genuinely orthogonal (a real new capability, not a
   workaround for a missing abstraction) — proceed but document why
   here so future-you can tell the difference.

## The big rewrite (v1.0) outline

When sc-bias finishes the current run and Lily releases the freeze on
this tool, the rewrite should land roughly this shape. Not implementing
it now (it would disrupt sc-bias), but recording the target:

### Data model

```python
class Node:
    id: str                          # stable, immutable
    title: str
    description: str
    kind: str                        # registered via WorkerRegistry
    task_type: str                   # selects artifact schema
    lifecycle: Literal["created", "running", "done", "failed"]
    is_branched: bool
    is_abandoned: bool
    cost_budget_hours: float | None
    info_value: int | None           # 1-5
    artifacts: dict                  # task-type-specific physical files
    created_at: datetime
    updated_at: datetime

class Edge:
    src: str                         # node id
    dst: str                         # node id
    kind: Literal["hard-dep", "soft-dep", "merges-into",
                  "derived-from", "parallel-with"]
    created_at: datetime

class Tree:                          # rename to Graph
    nodes: dict[str, Node]
    edges: list[Edge]
    workers: WorkerRegistry          # kind -> Worker subclass
```

### Worker interface

```python
class Worker(Protocol):
    """One worker per node kind. Autopilot doesn't special-case actions."""

    def can_run(self, node: Node, graph: Graph) -> bool:
        """All hard-dep edges into `node` point to lifecycle=done nodes."""

    def run(self, node: Node, graph: Graph, ctx: Context) -> WorkerResult:
        """Return: next_node_ids to create, edges to add, node lifecycle
        change. Worker decides if it spawns a subagent, runs a tool, or
        does pure computation. Autopilot doesn't know."""
```

### Event-driven scheduler

```python
# Replace the cron+gate poll with a watch loop.
# Inotify on .research-tree/ + signal handlers on background process
# exit. Events drive next_actions.
class Scheduler:
    def on_event(self, event: Event) -> list[Action]:
        ...
```

### What goes away

- SCHEMA_VERSION (replaced by always-additive Node/Edge fields)
- HUMAN_GATE_FILE_NAME + auto-raise + auto-clear-if-stale (replaced by
  events; if no events, scheduler sleeps cheaply)
- separate cmd_expand / cmd_execute / cmd_audit / cmd_merge (replaced
  by one cmd_dispatch_worker)
- task_type-specific blocks in SKILL.md (moved into Worker subclasses;
  SKILL.md becomes ~ 300 lines about workflow, not artifact rules)
- depends_on, depends_on_soft, parallel_group as node fields (replaced
  by edges)
- direct_executable flag (replaced by "worker has nothing to fork")

### Migration path

A v1.0 rewrite that keeps v0.5 trees readable for one cycle. The
migrator reads the old `tree.json`, builds the new `graph.json` and
`edges.json`. The old `tree.json` stays on disk, marked
`deprecated`, until the next milestone. No silent in-place semantic
flips like v0.4's forked→expanded.

## How to use this document

If you (future Claude session, or anyone) are about to add a field
to tree_state.py or a section to SKILL.md, **first paste this file
into your context** and answer:

- Which of the six structural problems am I touching?
- Is this a structural fix or a patch?
- If a patch: am I committing to "we'll fix the structure in v1.0"
  rather than "this is fine"?

If the honest answer is "patch", that's OK *as long as it is the last
patch before v1.0*. Stop adding patches that compound the debt.
