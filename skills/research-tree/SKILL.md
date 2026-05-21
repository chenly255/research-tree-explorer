---
name: research-tree
description: "Autonomous tree-shaped research exploration for Claude Code. Instead of running a research idea linearly, this skill branches at every major decision point (architecture, experiment design, narrative angle), runs each branch as an isolated subagent, audits junctions with a cross-model reviewer (codex), prunes dead branches with recorded reasons, and synthesizes a final report from the whole tree. Use when the user says '树状探索', 'research tree', 'tree exploration', 'autonomous research', 'idea落地全流程', or invokes /research-tree explicitly. Designed for long-running runs (hours to days). **autopilot is SINGLE-STEP** — each invocation does one orchestration action and returns; wrap with the /loop skill (e.g. `/loop 30m /research-tree autopilot`) for continuous runs so main context stays clean. State persists in .research-tree/tree.json + progress.log across sessions. The skill picks branches itself — never asks the user which technical fork to take."
argument-hint: "<subcommand> [args] — see SUBCOMMANDS section. Common: init '<idea>', autopilot, expand <node>, execute <node>, audit <node>, status, synthesize"
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, Skill, mcp__codex__codex, mcp__tavily__tavily_search
---

# Research Tree — Autonomous Tree-Shaped Research Exploration

You are running the `research-tree` skill. Your job is to drive a **tree-shaped research exploration**: at every junction, branch into 2-4 candidate approaches, run each one to a verifiable result, prune the losers (recording WHY), deepen the winners, and produce a final report covering the whole tree — including the dead branches as supplementary atlas.

**You do not ask the user which technical fork to take.** You pick yourself based on expected value, run a small pilot, audit with codex, and decide. The user only sees the final report.

## Locations and helpers

The skill ships with two Python helpers. Resolve them via this chain (works regardless of how the skill was installed):

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(dirname "$(realpath "$0" 2>/dev/null || echo .)")}"
RTE_REPO="${RESEARCH_TREE_REPO:-/data3/liying/research-tree-explorer}"
TREE_STATE="$RTE_REPO/scripts/tree_state.py"
SYNTHESIZE="$RTE_REPO/scripts/synthesize_report.py"
[ -f "$TREE_STATE" ] || TREE_STATE="$SKILL_DIR/../../scripts/tree_state.py"
[ -f "$SYNTHESIZE" ] || SYNTHESIZE="$SKILL_DIR/../../scripts/synthesize_report.py"
```

If neither path exists, abort with a clear error pointing to https://github.com/lily/research-tree-explorer.

State file: `.research-tree/tree.json` in the project root (where you were invoked from).
Branch workdirs: `.research-tree/branches/<node_id>/`.
Audits archive: `.research-tree/audits/`.

## Subcommands

The first token of `$ARGUMENTS` is the subcommand. Dispatch on it. If empty or `help`, print the usage block from this file and stop.

```
/research-tree init "<root idea>"           — create a new tree
/research-tree expand <node_id>             — generate N candidate children for one node
/research-tree execute <node_id>            — run one branch end-to-end in its own subagent
/research-tree audit <node_id>              — codex audits a junction's children, recommends prune/deepen
/research-tree prune <node_id> "<reason>"   — mark a branch dead with reason
/research-tree status                       — ASCII tree + stats
/research-tree synthesize                   — write FINAL_REPORT.md from current tree
/research-tree autopilot [max_loops=20]     — full loop: pick → expand-or-execute → audit → repeat until stop criterion
/research-tree resume                       — alias for autopilot, starts from current tree state
```

### init

Create `.research-tree/tree.json` with a single root node holding the user's research direction.

```bash
python3 "$TREE_STATE" init "$ROOT_IDEA" \
    --max-depth 5 \
    --max-branches 4 \
    --max-total-nodes 30 \
    --max-gpu-hours 48
```

If `RESEARCH_BRIEF.md` exists in the project root, mention it — the autopilot will consume it on expand.

After init, print one paragraph to the user: "Tree initialized with root idea '<X>'. Budgets: max depth 5, max 30 nodes total, max 48 GPU-hours. Run `/research-tree autopilot` to start exploring."

### expand

Generate 2-4 candidate child branches for a node. **This work goes into a subagent so your main context stays clean.**

1. Read the parent node and its existing children to ground the proposer:
   ```bash
   python3 "$TREE_STATE" get <node_id> > /tmp/rt_parent.json
   python3 "$TREE_STATE" list > /tmp/rt_siblings.txt
   ```
2. Decide what KIND of branching this junction calls for, based on parent's `depth`:
   - depth 0 (root): branch on **approach families** (set transformer vs perceiver vs gnn vs cnn)
   - depth 1: branch on **architecture details** within the approach
   - depth 2: branch on **experimental design** (datasets, scales, baselines)
   - depth 3: branch on **narrative angle** (which claim to lead with)
   - depth 4: branch on **ablations to lock in the story**
3. **Spawn an Agent (subagent_type=general-purpose) — the branch-proposer.** Hand it a self-contained prompt:
   - paste the parent node JSON (small)
   - paste the sibling list (small)
   - paste RESEARCH_BRIEF.md if it exists
   - state the kind of branching expected (from step 2)
   - tell it: "Propose 2-4 candidate sub-branches. Each must be mutually distinct from the others and from existing siblings (no two flavors of the same idea). Aim for diversity in expected outcome: one safe, one ambitious, one weird. You may use Tavily to scan recent literature if it helps you tell mutually distinct from redundant — but cap the search at 2 queries."
   - tell it: "Return ONLY a JSON array. No prose. Schema:
     ```json
     [{"kind": "approach|architecture|experiment|ablation|narrative|custom", "title": "<≤80 chars>", "description": "<2-4 sentences explaining the rationale and what 'success' would look like for this branch>"}]
     ```"
   - tell it: "Report cap: just the JSON. Anything else wastes the orchestrator's context."
4. Parse the returned JSON. For each candidate, run:
   ```bash
   python3 "$TREE_STATE" add <node_id> <kind> "<title>" --description "<description>"
   ```
5. (Optional, only if `mcp__codex__codex` is available) Spawn a codex fresh-thread red-team: pass the file paths `/tmp/rt_parent.json` + `/tmp/rt_siblings.txt` + the new children's ids, ask "any of these mutually redundant or obviously dominated?". Apply via `set status=dead death_reason=...` before continuing. **Never use codex-reply — fresh thread only.**
6. Log the action:
   ```bash
   echo "$(date -Iseconds)  expand <node_id>  added <count>  alive=$(python3 "$TREE_STATE" list --status pending | wc -l)" >> .research-tree/progress.log
   ```
7. Print the resulting subtree with `python3 "$TREE_STATE" tree` and stop. Return a one-sentence summary to the user.

### execute

Run one branch end-to-end in an **isolated subagent** so this main context isn't polluted by the branch's intermediate exploration.

1. Read the node: `python3 "$TREE_STATE" get <node_id>`
2. Read the node's parent and any sibling completed nodes for context.
3. Mark the node running: `python3 "$TREE_STATE" set <node_id> status=running`.
4. Create the branch workdir if not present (the `add` step already does this): `.research-tree/branches/<node_id>/`.
5. **Spawn an Agent** (subagent_type=general-purpose) with a self-contained prompt that:
   - States the branch's hypothesis (one paragraph)
   - States the budget (e.g., "2 hours wall time, 1 GPU max")
   - Says "work entirely inside `.research-tree/branches/<node_id>/`"
   - Says "write a `RESULT.md` at the end with: METRIC=<float>, KEY_FINDING=<paragraph>, COST=<gpu_hours>, ARTIFACTS=<list>"
   - Says "if you hit a blocker that makes the hypothesis untestable, write a `DEAD.md` with the blocker description instead — that is a valid outcome"
6. When the subagent returns:
   - If `RESULT.md` exists: parse it, then `python3 "$TREE_STATE" set <node_id> status=completed score=<METRIC>`.
   - If `DEAD.md` exists: `python3 "$TREE_STATE" set <node_id> status=dead death_reason="<from DEAD.md>" death_evidence=".research-tree/branches/<node_id>/DEAD.md"`.
   - If neither: `python3 "$TREE_STATE" set <node_id> status=dead death_reason="execution returned no verdict" death_evidence=<agent log path>`.
7. After the subagent returns, do NOT continue working on that branch in your own context — your job is tree-level orchestration only.

### audit

At a junction (a node that has children with mixed completed/dead status), invoke codex via `mcp__codex__codex` in a **fresh thread** to red-team the junction.

1. Read the junction node and all its children's RESULT.md / DEAD.md artifacts.
2. Prepare a prompt for codex along the lines of:
   ```
   I am running a tree-shaped research exploration. At junction "<junction title>",
   we tried N branches with these outcomes [list with file paths to RESULT.md / DEAD.md].
   Read the artifacts directly. Answer in JSON with fields:
     winner: <node_id or "none">
     deepen: [list of node_ids worth deepening further]
     prune: [list of node_ids to mark dead and skip]
     missing_branches: [list of approaches not tried that should be tried]
     overall_verdict: <one paragraph>
   Pass only file paths to the executor — they will not give you summaries.
   ```
3. Record the audit: `python3 "$TREE_STATE" audit-add <junction_id> codex "<one-line verdict>" --trace-file .research-tree/audits/<id>.json`.
4. Apply the verdict:
   - For each `prune`, `set status=dead death_reason="codex audit: <reason>"`.
   - For each `deepen`, queue an `expand` on that node.
   - For `missing_branches`, queue `add` calls to introduce them.

### prune

Manual prune. Just calls `set status=dead death_reason="<reason>"` on the node.

### status

```bash
python3 "$TREE_STATE" tree
echo
python3 "$TREE_STATE" stats
```

### synthesize

```bash
python3 "$SYNTHESIZE" --project-root "$(pwd)"
```

Read the resulting `.research-tree/FINAL_REPORT.md` and present its highlights to the user in one paragraph. The full report is on disk.

### autopilot / resume

**`autopilot` is a single-step command, not a long-running loop.** Each invocation does ONE unit of work and returns. To run continuously, the user wraps it with the external `/loop` skill, e.g. `/loop 30m /research-tree autopilot`. This keeps your main context fresh — each step is one orchestration turn, heavy work is in subagents, no in-prompt for-loops that bloat over time.

A single autopilot step does this:

```
1. Read progress: tail -1 .research-tree/progress.log (so you know what last step did)
2. Check for previously-detected root failure:
     if .research-tree/ROOT_FAILURE.md exists:
       Tell the user: every approach under root is dead. Show ROOT_FAILURE.md's
       content. Recommend: archive the tree (mv .research-tree .research-tree.failed-DATE)
       and re-run /idea-pipeline with the dead-branch reasons as input. STOP — do not
       auto-loop further. The /loop wrapper should also stop when this file is present.

3. Check budget:
     python3 "$TREE_STATE" budget-check
   If exit non-zero → run synthesize, report "budget exhausted", stop.

4. Pick the next leaf:
     next_id=$(python3 "$TREE_STATE" pick-next)
   If next_id == "NONE" → run synthesize, then read the new FINAL_REPORT.md's
   "Suggested next move" section and surface those options verbatim to the user
   (deepen winner / resolve alive / write paper via ARIS, OR pivot if all root dead).
   STOP.

5. Get its state:
     node_json=$(python3 "$TREE_STATE" get "$next_id")
   Parse "status" from JSON.

6. Dispatch ONE action based on status:
     - pending (never touched)        → invoke /research-tree expand "$next_id"
     - expanded (has children)        → invoke /research-tree execute on its first pending grandchild
                                       (this normally won't happen; pick-next prefers pending leaves)
     - any other unexpected state     → log and stop

   Each of these subcommands does its own subagent dispatch internally. autopilot
   does NOT run multiple subagents in one step. One step = one orchestrated action.

7. Every 3 invocations, check for junctions needing audit:
     loop_count=$(wc -l < .research-tree/progress.log)
     if loop_count % 3 == 0:
       for each junction with ≥1 completed AND ≥1 dead child AND no junction_audit_id yet:
         invoke /research-tree audit <junction_id>
   (Audit itself spawns codex fresh thread — no main context bloat.)

8. Every 5 invocations, force a self-audit reflection — write to .research-tree/reflections/<N>.md:
     "Am I picking the easiest branches because they're easy, or because they're the most
      informative? Are the dead branches dying for the right reasons or because the pilot
      was sloppy? Has the root idea drifted?"
   Answer honestly in 3 sentences. If sloppy, queue a re-run by setting affected nodes
   back to pending.

9. Run synthesize at the end of every step (cheap, idempotent):
     python3 "$SYNTHESIZE" --project-root "$(pwd)"
   This may write a ROOT_FAILURE.md if all root branches died. If it does, surface
   that to the user and STOP — recommend running /idea-pipeline with the dead reasons.

10. Append progress.log:
     echo "$(date -Iseconds)  step=$loop_count  action=<expand|execute|audit|reflect>  node=$next_id  alive=$alive_count  completed=$completed_count  dead=$dead_count" >> .research-tree/progress.log

11. Report ONE PARAGRAPH to the user: what you did this step, what the tree looks like now
   (`python3 "$TREE_STATE" tree | head -20`), what `/research-tree autopilot` will do next time.
   Then STOP. Do not loop in-prompt.
```

**Why single-step**: each autopilot invocation is a fresh orchestration turn. Subagents handle the heavy expand / execute / audit work in isolated contexts. The main context never accumulates more than one step's worth of state — it reads from disk (`tree.json`, `progress.log`) at the start, dispatches one action, writes back to disk, returns. This is the same pattern as GSD's `gsd-autonomous` and is the only way to truly run for days without context drift.

**Failure modes to refuse**:
- Do NOT pause to ask the user "which branch should I take next?" — your job is to decide.
- Do NOT run multiple steps in one invocation, even if it seems fast. The external `/loop` does that.
- Do NOT make the tree wider just to look busy. If two candidates are minor variants of the same idea, collapse them.
- Do NOT skip the audit step at the right cadence. The audit at junctions is the cheapest insurance against tree drift.
- Do NOT use `codex-reply` for audits — every audit is a fresh thread with file paths only.
- Do NOT do branch experiment work in your main context. Always spawn an Agent.

## Output to user

After every subcommand, print **one short paragraph** to the user with what changed in the tree, and what the next move is. Long output goes to `.research-tree/`. The user reads files, not your transcript.

Final synthesis to user when autopilot stops: 3 sentences. (1) What we set out to find. (2) Whether we found it (winner branch + score, or "no branch landed"). (3) Where the artifacts and dead-branch atlas live.

## Error handling

- If `.research-tree/tree.json` is missing for any subcommand other than `init`, tell the user "no tree found, run `/research-tree init '<idea>'` first" and stop.
- If a Python script returns non-zero, surface the stderr to the user — don't silently swallow.
- If a subagent crashes or times out, mark the branch dead with `death_reason="executor crashed: <reason>"` and continue. One bad branch doesn't kill the tree.
