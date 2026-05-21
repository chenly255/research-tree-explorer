---
name: research-tree
description: "Autonomous tree-shaped research exploration for Claude Code. Instead of running a research idea linearly, this skill branches at every major decision point (architecture, experiment design, narrative angle), runs each branch as an isolated subagent, audits junctions with a cross-model reviewer (codex), prunes dead branches with recorded reasons, and synthesizes a final report from the whole tree. Use when the user says '树状探索', 'research tree', 'tree exploration', 'autonomous research', 'idea落地全流程', or invokes /research-tree explicitly. Designed for long-running runs (hours to days) with cross-session resume via .research-tree/tree.json. The skill picks branches itself — never asks the user which technical fork to take."
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

Generate 2-4 candidate child branches for a node. This is where the tree branches.

1. Read the parent node: `python3 "$TREE_STATE" get <node_id>`
2. Read `RESEARCH_BRIEF.md` (if present) and the parent node's `description`.
3. **Choose what kind of branching this junction calls for**, based on parent's `depth` and `kind`:
   - depth 0 (root): branch on **approach families** (e.g., set transformer vs perceiver vs pointnet vs mean-pool)
   - depth 1: branch on **architecture details** within the approach
   - depth 2: branch on **experimental design** (datasets, scales, baselines)
   - depth 3: branch on **narrative angle** (which claim to lead with)
   - depth 4: branch on **ablations to lock in the story**
4. **Generate the candidates yourself** — think hard, use Tavily to scan recent literature if helpful, but do not stop to ask the user. Aim for candidates that are **mutually distinct** (not three flavors of the same idea) and **diverse in expected outcome** (one safe, one ambitious, one weird).
5. For each candidate, run:
   ```bash
   python3 "$TREE_STATE" add <node_id> <kind> "<title>" --description "<2-4 sentence rationale>"
   ```
6. Optionally invoke `mcp__codex__codex` with a fresh thread to red-team the candidate list before locking it in (do NOT use codex-reply — fresh thread only). Pass codex only file paths to read, never your own interpretation. If codex flags a candidate as "redundant" or "obviously dominated", remove it via `set status=dead death_reason=...` before moving on.
7. Print the resulting subtree with `python3 "$TREE_STATE" tree`.

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

The main loop. This is where the skill earns its keep — the user runs `autopilot` once and the tree grows until it converges or hits a budget.

```
loop_count = 0
while loop_count < max_loops:
    loop_count += 1

    # 1. Check global budget. If over, stop.
    if `python3 "$TREE_STATE" budget-check` exits non-zero:
        break with reason="budget_exhausted"

    # 2. Pick the next leaf to work on.
    next_id = `python3 "$TREE_STATE" pick-next`
    if next_id == "NONE":
        break with reason="no_alive_leaves"

    # 3. Branch on the leaf's status:
    node = `python3 "$TREE_STATE" get <next_id>`
    if node.status == "pending":
        # not yet expanded — expand it
        invoke /research-tree expand <next_id>
    elif node.status == "expanded":
        # has children — pick one of its children's pending leaves
        # (this case is naturally handled by pick-next next iter)
        continue
    elif node.status in ("completed", "dead"):
        # shouldn't be picked, but defensively skip
        continue

    # 4. After every K loops (default K=3), audit all junctions with mixed-status children.
    if loop_count % 3 == 0:
        for each junction with at least one completed and one dead child:
            invoke /research-tree audit <junction_id>

    # 5. After each loop, write a one-line progress marker to .research-tree/progress.log
    #    with timestamp + loop_count + tree stats. This survives context compaction.

# Done. Synthesize.
invoke /research-tree synthesize
print 3-sentence summary to user.
```

**Pacing and self-checks** (PUA against laziness):

- After every 5 loops, force a self-audit: "Am I picking the easiest branches because they're easy, or because they're the most informative? Are the dead branches dying for the right reasons or because the pilot was sloppy?" If sloppy, re-run those branches.
- After every 10 loops, force a global reflection: "Has the root idea drifted? Is the tree still answering the original question?" Update root node's description if the framing has evolved.
- Never silently lower the bar. If a branch keeps failing, recording WHY is the deliverable — do not declare success on a half-done branch.

**Failure modes to refuse**:

- Do NOT pause to ask the user "which branch should I take next?" — your job is to decide.
- Do NOT make the tree wider just to look busy. If two candidates are minor variants of the same idea, collapse them.
- Do NOT skip the audit step. The audit at junctions is the cheapest insurance against tree drift.
- Do NOT use `codex-reply` for audits — every audit is a fresh thread with file paths only.

## Output to user

After every subcommand, print **one short paragraph** to the user with what changed in the tree, and what the next move is. Long output goes to `.research-tree/`. The user reads files, not your transcript.

Final synthesis to user when autopilot stops: 3 sentences. (1) What we set out to find. (2) Whether we found it (winner branch + score, or "no branch landed"). (3) Where the artifacts and dead-branch atlas live.

## Error handling

- If `.research-tree/tree.json` is missing for any subcommand other than `init`, tell the user "no tree found, run `/research-tree init '<idea>'` first" and stop.
- If a Python script returns non-zero, surface the stderr to the user — don't silently swallow.
- If a subagent crashes or times out, mark the branch dead with `death_reason="executor crashed: <reason>"` and continue. One bad branch doesn't kill the tree.
