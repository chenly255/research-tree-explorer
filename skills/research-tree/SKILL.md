---
name: research-tree
description: "Autonomous DAG-shaped research exploration for Claude Code. v1.0 architecture: nodes form a graph (not a tree); branching decisions go through structured gates (BranchingDecider) so the orchestrator never invokes a proposer that would produce no-meaning forks; sibling completions are scanned for complementarity and surfaced as MergeProposals; per-task-type artifact requirements live in Worker classes, not in this SKILL.md. Use when the user says '树状探索', 'research tree', 'tree exploration', 'autonomous research', or invokes /research-tree explicitly. Designed for long-running runs (hours to days). **autopilot is SINGLE-STEP** — each invocation does one orchestration action and returns; wrap with the /loop skill for continuous runs. State persists in .research-tree/graph.json + events.log + progress.log across sessions. The skill picks branches itself — never asks the user which technical fork to take."
argument-hint: "<subcommand> [args] — common: init '<idea>', autopilot, status, decide-fork <node>, decide-candidate <parent> <title>, detect-merges, merge <proposal_id>"
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, Skill, mcp__codex__codex, mcp__tavily__tavily_search
---

# Research Tree v1.0

You are running the `research-tree` skill. Your job is to drive an **autonomous DAG-shaped research exploration**: at every junction, structured gates decide whether to branch, the proposer's candidates are each filtered against existing siblings, branches run as isolated subagents, completions get audited by a fresh-thread codex reviewer, and sibling completions get scanned for complementarity → merge proposals.

**Architectural commitments** (will not be regressed without a v2 design doc):

1. **Edges are first-class.** parent / depends / merges-into / parallel-with are all `Edge` objects in `graph.json`. Nodes do not carry relationship state.
2. **Status is three orthogonal axes.** `lifecycle ∈ {created, running, done, failed}` + `is_branched: bool` + `is_abandoned: bool`. No more 6-value status enum overloaded with branching state.
3. **One Worker class per task_type.** Artifact requirements and validation logic live in `research_tree/workers/<task_type>.py`, not in SKILL.md. Adding a 6th task_type is one Worker class.
4. **Structured branching decisions.** `BranchingDecider` answers `decide_to_fork(parent)` BEFORE the proposer runs, and `decide_to_accept_candidate(cand, parent)` for EACH candidate the proposer returns. The proposer no longer self-judges duplication.
5. **Node merging is a first-class operation.** `NodeMerger.detect_merge_opportunities()` scans completed siblings, returns MergeProposals; `merge <proposal_id>` creates a synthesis node + `merges-into` edges.
6. **Physical-evidence trust kernel preserved.** `charter_validator.py` still runs as a subprocess from `Worker.validate()`. AUDIT_NONCE + challenge-fragment cross-check still gate codex audit. Anti-fabrication contract is unchanged from v0.4.

## Locations and helpers

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(dirname "$(realpath "$0" 2>/dev/null || echo .)")}"
RTE_REPO="${RESEARCH_TREE_REPO:-/data3/liying/research-tree-explorer}"

TREE_STATE="$RTE_REPO/scripts/tree_state.py"     # thin shim → research_tree.cli
DATA_EXAMPLES="$RTE_REPO/examples/data-acquisition"

[ -f "$TREE_STATE" ] || TREE_STATE="$SKILL_DIR/../../scripts/tree_state.py"
[ -d "$DATA_EXAMPLES" ] || DATA_EXAMPLES="$SKILL_DIR/../../examples/data-acquisition"
```

If `$TREE_STATE` does not exist, abort with pointer to https://github.com/lily/research-tree-explorer.

State files under `<project_root>/.research-tree/`:
- `graph.json` — v1.0 main file (nodes + edges)
- `tree.json` — v0.5 snapshot, kept read-only after migration
- `events.log` — append-only event stream (scheduler reads + branches emit)
- `progress.log` — one human-readable line per orchestration step
- `branches/<node_id>/` — per-branch workdir
- `audits/<id>.json` — junction audit traces
- `AWAITING_HUMAN.md` — sentinel for framing-decision / pivot / DONE / ROOT_FAILURE

## Subcommands

```
init "<root idea>"             create a new graph
add <parent> <kind> <title>    add a child node (returns new id)
get <node_id>                  print node JSON
set <node_id> key=value [...]  update allowed fields (description / cost_budget_hours / info_value_score / title)
list [--lifecycle X --task-type Y]   list nodes (filtered)
tree                           ASCII tree visualization
stats                          summary stats

pick-next                      pick the next pickable leaf (lifecycle=created + deps satisfied + not branched / abandoned / human_only)
running <node_id>              transition created → running
complete <node_id> [--with-validation]    transition → done; --with-validation runs Worker.validate()
die <node_id> --reason "..."   transition → failed
backtrack <node_id>            mark abandoned (reversible)
resume-branch <node_id>        un-abandon

decide-fork <node_id>          BranchingDecider API 1 — should this parent fork?
decide-candidate <parent> <title>   BranchingDecider API 2 — accept this candidate?
detect-merges                  scan completed siblings for complementary pairs
merge <proposal_id>            apply a merge proposal

migrate [--dry-run --force]    explicit v0.5 → v1.0 migrator (auto-triggered otherwise)
audit-add <junction> <reviewer> <verdict>   record a junction audit
budget-check                   exit 1 if budgets exceeded
emit-event --kind X --payload '{...}'       emit one event into events.log
watch-events [--peek]          drain events since last cursor
scan-branches                  walk branches/, emit synthesized events for new RESULT.md / DEAD.md / PID-death
human-gate <check|set|clear>   sentinel control
```

`autopilot` itself is documented below — it is a single-step orchestration command, NOT a subcommand of the CLI.

## Charter setup

If `<project_root>/RESEARCH_BRIEF.md` exists, every subagent receives it. If absent, the run continues but is less informed.

If `<project_root>/RESEARCH_CHARTER.md` exists, the anti-laziness charter governs the whole run. If absent on `init`, copy the default:

```bash
cp "$RTE_REPO/templates/RESEARCH_CHARTER.md" RESEARCH_CHARTER.md
echo "WARN: copied default charter. EDIT IT before running autopilot." >&2
```

Then tell the user: "I copied the default research charter to RESEARCH_CHARTER.md. **Read it and edit the venue / data / architecture / done-criteria fields before running autopilot.** Subagents obey whatever's in there."

## Autopilot — single step

Each invocation does ONE orchestration unit and returns. To run continuously, wrap with `/loop 30m /research-tree autopilot`. Modes:

- default: chatty single-step
- `autopilot --silent`: silent single-step, surfaces ONLY on DONE / ROOT_FAILURE / STUCK / session-cap
- `autopilot --continuous`: chained single-steps until no more pickable work (still single Claude session)

A single autopilot step does this:

```
0. Human-gate fast-exit
   python3 $TREE_STATE --project-root $(pwd) human-gate check
   exit_code 2 means gate is up. In --silent: exit silently. Otherwise print
   one line "[awaiting human — run /research-tree resume to clear]" and exit.

1. Drain events.log + scan branches
   python3 $TREE_STATE --project-root $(pwd) scan-branches
   python3 $TREE_STATE --project-root $(pwd) watch-events
   For each event, dispatch:
     - background_process_exit → mark validation-ready for that node
     - result_md_written       → validation chain on that node
     - dead_md_written         → die <node>
     - subtree_fork_written    → apply SUBTREE_FORK.md (filter through decide-candidate per child)
     - subtree_pivot_written   → human-gate set + STOP this tick

2. Budget check
   python3 $TREE_STATE --project-root $(pwd) budget-check
   If OVER → human-gate set --reason "budget exceeded" + STOP.

3. Pick next pickable leaf
   NEXT=$(python3 $TREE_STATE --project-root $(pwd) pick-next)
   If NEXT == NONE → STOP (synthesize report, optionally human-gate set).

4. BranchingDecider — decide_to_fork
   FORK_DECISION=$(python3 $TREE_STATE --project-root $(pwd) decide-fork "$NEXT")
   If kind == DIRECT_EXECUTE → step 6.
   If kind == FORK → step 5.

5. Spawn proposer subagent
   The proposer receives:
     - the parent node JSON
     - RESEARCH_BRIEF.md / RESEARCH_CHARTER.md
     - the BranchingDecision constraints (min_candidates, max_candidates,
       must_diversify_axis, min_info_value)
     - sibling list
   It returns a JSON candidates array. For EACH candidate:
     CAND_DECISION=$(python3 $TREE_STATE --project-root $(pwd) decide-candidate
                       "$NEXT" "<candidate title>" --description "<cand description>"
                       --kind "<kind>" --task-type "<task_type>")
     If kind == ADD          → python3 $TREE_STATE add ... (capture new id)
     If kind == MERGE_WITH   → emit-event merge_proposed with sources=[merge_target, new_idea]
                               (do NOT add the candidate; the merge will happen later)
     If kind == REJECT       → log to progress.log, drop candidate
   After all candidates processed, STOP this autopilot tick. Next tick will pick a child.

6. Execute (DIRECT_EXECUTE or post-FORK leaf)
   running <NEXT>
   worker = python3 -c "from research_tree.workers import get_worker; ..." (this happens
   inside the executor subagent we're about to spawn — the Worker generates its own prompt)

   Spawn an Agent (subagent_type=general-purpose) with the prompt produced by:
     python3 -c "
       import sys; sys.path.insert(0, '$RTE_REPO')
       from research_tree.cli import _ensure_graph
       from research_tree.workers import get_worker
       from pathlib import Path
       g = _ensure_graph(Path('$(pwd)'))
       n = g.nodes['$NEXT']
       w = get_worker(n.task_type)
       print(w.spawn_subagent_prompt(n, g, None))
     "

   The executor subagent launches its long-running work with nohup (per the
   prompt's BACKGROUND EXECUTION block), writes EXECUTOR.json, and returns.

   This autopilot step ends here. Next tick: step 1 will detect RESULT.md /
   DEAD.md via scan-branches and trigger the validation chain.

7. Validation chain (triggered by event background_process_exit + RESULT.md exists)
   - 6b. Worker.validate(node, branch_dir)
   - 6c. codex audit (NONCE + challenge-fragments)
     env -u http_proxy ... python3 "$RTE_REPO/scripts/codex_audit_cli.py"
       --branch-dir ".research-tree/branches/<id>" --charter RESEARCH_CHARTER.md
       --nonce-file ".research-tree/branches/<id>/AUDIT_NONCE" --task-type <task_type>
       --out ".research-tree/branches/<id>/CODEX_AUDIT.json"
   - 6d. Worker.validate again with --require-codex-audit
   - If PASS: complete <id> --score <metric>
   - If FAIL: die <id> --reason "<first failure>"

   Repair retry budget: at most 2 retries per node (the v0.5 mechanism is
   unchanged). After budget exhausted, die.

8. Periodic — every 5 ticks
   python3 $TREE_STATE --project-root $(pwd) detect-merges
   For each proposal, append a line to progress.log:
     merge_proposed <proposal_id> sources=[...] axes=[...] confidence=N
   Do NOT auto-apply. Wait for user / /research-tree merge <proposal_id>.

9. Append progress.log + return.
   In --silent mode: only surface on terminal events (DONE / ROOT_FAILURE /
   STUCK / budget exhaust / session-cap).
   In default mode: one-paragraph summary + tree head -15 lines.
```

**Why single-step**: each tick is a fresh orchestration turn. Subagents handle heavy work in isolated context. The main context never holds more than one tick's state — it reads from disk (graph.json + events.log), dispatches one action, writes back, returns.

## Branch execution — single Agent role per node

The v0.5 design had two subagent types (proposer + executor). v1.0 uses ONE Agent role, mode-dispatched by the orchestrator:

```python
mode = "propose" if FORK_DECISION.kind == "FORK" else "execute"
prompt = (
    common_header
    + (proposer_block if mode == "propose" else worker.spawn_subagent_prompt(...))
)
```

The common header (charter + brief + budget) is the same; only the task-specific block differs. This keeps per-spawn token cost minimal — the orchestrator builds the right prompt for the right mode.

## Output modes (executor subagent)

Verbatim from `Worker.spawn_subagent_prompt`:

(a) **`RESULT.md`** — work completed. METRIC + KEY_FINDING + ARTIFACTS + charter table.
(b) **`DEAD.md`** — honest failure. `death_reason:` + paragraph.
(c) **`SUBTREE_FORK.md`** — mid-flight discovery of 2-4 sub-approaches worth competing. Orchestrator filters each through decide-candidate before creating children.
(d) **`SUBTREE_PIVOT.md`** — entire branch hypothesis is wrong. Triggers human-gate.

## Error handling

- `graph.json` missing AND no `tree.json` → tell the user to run `init` first.
- `graph.json` missing AND `tree.json` exists → CLI auto-migrates on first read. Migration is logged to stderr.
- Python subprocess returns non-zero → surface its stderr to the user, don't swallow.
- Subagent crash or timeout → die <node> with reason "executor crashed: <detail>". One bad branch doesn't kill the run.
- codex audit unreachable → fail-CLOSED (die the node with reason "external_audit_unavailable"). The v0.4 contract is preserved.

## Failure modes to refuse

- Do NOT pause to ask "which branch should I take next?" — that's BranchingDecider's job.
- Do NOT run multiple ticks in one invocation outside `--continuous`. The external `/loop` handles continuity.
- Do NOT make the tree wider just to look busy. `decide-fork` + `decide-candidate` are the discipline; respect them.
- Do NOT skip the codex audit at the right cadence. It is the trust kernel.
- Do NOT use `codex-reply` for audits — every audit is a fresh thread with file paths only.
- Do NOT do branch experiment work in your main context. Always spawn an Agent.

## Output to user

After every subcommand, ONE short paragraph: what changed + what's next. Long output goes to `.research-tree/`. The user reads files, not your transcript.

Final synthesis when autopilot stops: 3 sentences. (1) What we set out to find. (2) Whether we found it (winner + score, or "no branch landed"). (3) Where artifacts + dead-branch atlas live.

## See also

- `docs/V1-ARCHITECTURE.md` — the full design contract this SKILL.md implements
- `docs/ARCHITECTURE.md` — v0.5 reference (kept for historical context; do not edit)
- `DESIGN-PRINCIPLES.md` — the six structural problems v1.0 fixed
- `CHANGELOG.md` — version history
