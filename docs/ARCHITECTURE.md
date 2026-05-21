# Architecture

This doc explains *why* `research-tree-explorer` is shaped the way it is, so contributors can change it without breaking the load-bearing parts.

## Design constraints

The tool was built to satisfy these constraints, in order:

1. **The main agent's context must not grow with tree size.** A research tree can span days and hundreds of branches; the main orchestration context cannot accumulate every branch's intermediate output. → All heavy work (branch execution, candidate proposal, junction audit) goes into isolated subagents or cross-model fresh threads. The main agent only reads small JSON state and dispatches one action per turn.

2. **State must survive context compaction and full session restart.** → All state lives on disk in `.research-tree/`. The JSON is the single source of truth. A new session reads `tree.json` + `progress.log` and picks up exactly where the last one left off.

3. **Dead branches are deliverables, not failures.** → The state machine records `death_reason` and `death_evidence` for every pruned branch. The final report assembles a "dead-branch atlas" — supplementary material for the eventual paper.

4. **The skill never stops to ask the user "which fork should I take?"** → All decisions are made by either the main agent (orchestration), a branch-proposer subagent (candidates), a cross-model reviewer (audit), or are gated by explicit budgets. The user is only consulted for goal-level changes, not technical forks.

5. **Single-step autopilot.** A long in-prompt for-loop accumulates context with every iteration. Instead, `autopilot` does exactly one orchestration step per invocation, then returns. Continuous runs are produced by wrapping with an external scheduler like the `/loop` skill.

## Three-layer architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 3 — User-facing skill (markdown)                                │
│ skills/research-tree/SKILL.md                                          │
│   - Loaded into the main agent's context at /research-tree invocation │
│   - Describes subcommand dispatch, subagent spawn rules, budgets      │
│   - Anti-laziness PUA reminders + failure modes to refuse             │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  reads, writes, dispatches subagents
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 2 — Orchestration helpers (Python CLI)                          │
│ scripts/tree_state.py     — state machine: init/add/set/get/list/    │
│                              pick-next/tree/stats/audit-add/budget-check │
│ scripts/synthesize_report.py — FINAL_REPORT.md generator              │
│ scripts/install.sh        — symlinks skill into ~/.claude/skills/    │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  writes, reads
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 1 — On-disk state (single source of truth)                      │
│ .research-tree/tree.json         — all nodes + budgets + audits       │
│ .research-tree/progress.log      — one line per orchestration step    │
│ .research-tree/branches/<id>/    — per-branch isolated workspace      │
│ .research-tree/audits/<id>.json  — junction audit traces              │
│ .research-tree/reflections/      — periodic self-audit notes          │
│ .research-tree/FINAL_REPORT.md   — generated synthesis                │
└──────────────────────────────────────────────────────────────────────┘
```

Each layer is replaceable without touching the others:

- Layer 1 is just files — you could mount it on a shared filesystem to coordinate across machines.
- Layer 2 is plain Python CLIs — you could call them from another orchestrator (a notebook, a CI job).
- Layer 3 is a markdown skill — you could substitute another agent harness (codex, gemini) that follows the same dispatch contract.

## Subagent boundaries

The skill spawns three kinds of subagents, each with a different isolation contract:

| Trigger | Subagent type | What goes in | What comes back | Why isolated |
|---|---|---|---|---|
| `expand <id>` | `general-purpose` (branch-proposer) | parent node JSON, siblings list, RESEARCH_BRIEF.md, kind hint | JSON array of `{kind, title, description}` | Candidate generation may scan literature; we don't want search results in the main context |
| `execute <id>` | `general-purpose` (branch-runner) | branch description, budget, RESULT.md schema | a `RESULT.md` or `DEAD.md` on disk + a 1-2 sentence summary | Branch experiments can produce gigabytes of intermediate output; the orchestrator never sees it |
| `audit <id>` | `mcp__codex__codex` fresh thread (NEVER codex-reply) | paths to children's `RESULT.md` / `DEAD.md` files only | structured JSON verdict | Cross-model independence — different model family, fresh context, no narrative accumulation |

## State schema

`.research-tree/tree.json`:

```json
{
  "schema_version": "0.1",
  "project": "<dir name>",
  "root_idea": "<one-line idea statement>",
  "created_at": "<ISO-8601>",
  "last_updated": "<ISO-8601>",
  "current_focus": "<node id>",
  "nodes": {
    "<id>": {
      "id": "<id>",
      "parent": "<id or null>",
      "depth": <int>,
      "kind": "root | approach | architecture | experiment | ablation | narrative | custom",
      "status": "pending | expanded | running | completed | dead",
      "title": "<≤80 chars>",
      "description": "<2-4 sentences>",
      "score": <float or null>,
      "death_reason": "<string or null>",
      "death_evidence": "<file path or null>",
      "junction_audit_id": "<audit id or null>",
      "branch_dir": "<relative path or null>",
      "children": ["<id>", ...],
      "created_at": "<ISO-8601>",
      "updated_at": "<ISO-8601>"
    }
  },
  "audits": {
    "<audit id>": {
      "junction": "<node id>",
      "reviewer": "<name>",
      "verdict": "<one-line>",
      "timestamp": "<ISO-8601>",
      "trace_file": "<path to detail JSON>"
    }
  },
  "global_constraints": {
    "max_depth": 5,
    "max_branches_per_junction": 4,
    "max_total_nodes": 30,
    "max_gpu_hours_total": 48.0
  },
  "stats": {
    "nodes_total": <int>,
    "nodes_alive": <int>,
    "nodes_dead": <int>,
    "nodes_completed": <int>,
    "gpu_hours_used": <float>
  }
}
```

Node id convention: root is `"root"`. Direct children of root are `"1"`, `"2"`, ... Grandchildren append a dotted suffix: `"1.1"`, `"1.2"`, etc. This makes the parent-child relationship readable from the id alone.

`max_branches_per_junction` caps *alive* children only — dead branches free their slot so a junction audit can introduce a new candidate when a previous one failed.

## ID picking algorithm (pick-next)

`scripts/tree_state.py pick-next` chooses the next leaf to process:

1. Filter to nodes with `status == "pending"`
2. Score each by `(parent_score, -depth, node_id)` — descending parent score (deepen winners first), then prefer shallower (don't tunnel into one branch before exploring siblings), then lex order for determinism
3. Return the highest-scoring id, or `"NONE"` if no pending nodes remain

This is a deliberate simple heuristic — not MCTS, not UCB. Scientific rewards are too qualitative for a strict formula; the orchestrator's judgment at junction audits is the real control signal. Pick-next just enforces basic discipline (deepen winners before opening more siblings).

## What's intentionally not in scope

- **Cross-machine GPU dispatch.** Each branch's `execute` step runs wherever the subagent's bash runs. If you need a branch on a different machine, the skill writes a `NEEDS_FULL_SCALE: true` flag into RESULT.md and the final report tells you which branch to rsync.
- **Real-time dashboard.** Read `tree.json` with `jq` or open it in any JSON viewer. A dashboard belongs in a separate tool that watches the file.
- **Inter-branch communication.** Branches are fully isolated by design. If two branches need to share weights or intermediate results, they need to be the same branch with shared state — not two branches.
- **Automatic ARIS integration.** ARIS (`wanshuiyin/Auto-claude-code-research-in-sleep`) is a sibling tool with overlapping concerns. Calling ARIS's `/research-pipeline` from inside a branch's `execute` step is on the roadmap but not wired by default.

## How a single autopilot step flows

```
User invokes: /research-tree autopilot

Main agent:
  ├─ tail -1 .research-tree/progress.log              # know what last step did
  ├─ python3 tree_state.py budget-check               # any budget over?
  ├─ python3 tree_state.py pick-next  → node_id      # what's next?
  ├─ python3 tree_state.py get $node_id → JSON        # what kind of action?
  │
  ├─ if status == "pending":
  │     dispatch to → /research-tree expand $node_id
  │       └─ Agent(general-purpose, "branch-proposer")
  │         └─ thinks, optionally searches literature, returns JSON
  │       └─ tree_state.py add ... (parent applies each candidate)
  │
  ├─ if status == "expanded" and grandchild pending:
  │     dispatch to → /research-tree execute $grandchild_id
  │       └─ Agent(general-purpose, "branch-runner")
  │         └─ writes RESULT.md or DEAD.md in branches/<id>/
  │       └─ tree_state.py set <id> status=... score=...
  │
  ├─ every 3rd step, scan for ready junctions:
  │     for each junction with mixed-status children and no audit yet:
  │       dispatch to → /research-tree audit $junction
  │         └─ mcp__codex__codex (fresh thread, file paths only)
  │         └─ tree_state.py audit-add ...
  │
  ├─ every 5th step, write reflection note to reflections/
  ├─ echo "<timestamp>  step=N  action=...  node=..." >> progress.log
  └─ return ONE PARAGRAPH summary to user

User (or /loop) invokes /research-tree autopilot again →
  fresh main-agent turn, repeats with one more step's state on disk.
```

The key invariant: every step starts and ends on disk. The main agent never holds tree state in its head; it always reads from `tree.json` at step start and writes back at step end. This is what lets it survive context compaction, full session restart, and multi-day runs.
