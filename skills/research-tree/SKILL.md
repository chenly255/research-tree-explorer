---
name: research-tree
description: "Autonomous tree-shaped research exploration for Claude Code. Instead of running a research idea linearly, this skill branches at every major decision point (architecture, experiment design, narrative angle), runs each branch as an isolated subagent, audits junctions with a cross-model reviewer (codex), prunes dead branches with recorded reasons, and synthesizes a final report from the whole tree. Use when the user says '树状探索', 'research tree', 'tree exploration', 'autonomous research', 'idea落地全流程', or invokes /research-tree explicitly. Designed for long-running runs (hours to days). **autopilot is SINGLE-STEP** — each invocation does one orchestration action and returns; wrap with the /loop skill (e.g. `/loop 30m /research-tree autopilot`) for continuous runs so main context stays clean. State persists in .research-tree/tree.json + progress.log across sessions. The skill picks branches itself — never asks the user which technical fork to take."
argument-hint: "<subcommand> [args] — see SUBCOMMANDS section. Common: init '<idea>', autopilot, expand <node>, execute <node>, audit <node>, status, synthesize"
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, Skill, mcp__codex__codex, mcp__tavily__tavily_search
---

# Research Tree — Autonomous Tree-Shaped Research Exploration

You are running the `research-tree` skill. Your job is to drive a **tree-shaped research exploration**: at every junction, branch into 2-4 candidate approaches, run each one to a verifiable result, prune the losers (recording WHY), deepen the winners, and produce a final report covering the whole tree — including the dead branches as supplementary atlas.

**You do not ask the user which technical fork to take.** You pick yourself based on expected value, run a small pilot, audit with codex, and decide. The user only sees the final report.

**Hardline enforcement** (v0.1.3): the charter is NOT just a prompt — it is enforced by `scripts/charter_validator.py`, a separate program that runs after every branch and checks for the **physical files** (test_split.json with hash, ≥3 seed checkpoint dirs, metrics.json with param_count ≥10M and downstream task p-values, ≥4 ablation subdirs, requirements.txt). On top of that, every passing branch goes through a **fresh codex thread** for external adversarial audit before being marked `completed`. A subagent that fabricates RESULT.md text without the backing files gets marked dead by the validator, period. **Never skip steps 6b–6d in `execute`. Never re-spawn the subagent to "try again" after a validator FAIL — that defeats the purpose.**

## Locations and helpers

The skill ships with two Python helpers. Resolve them via this chain (works regardless of how the skill was installed):

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(dirname "$(realpath "$0" 2>/dev/null || echo .)")}"
RTE_REPO="${RESEARCH_TREE_REPO:-/data3/liying/research-tree-explorer}"
TREE_STATE="$RTE_REPO/scripts/tree_state.py"
SYNTHESIZE="$RTE_REPO/scripts/synthesize_report.py"
VALIDATOR="$RTE_REPO/scripts/charter_validator.py"
STALE_HANDLER="$RTE_REPO/scripts/stale_running_handler.py"
[ -f "$TREE_STATE" ] || TREE_STATE="$SKILL_DIR/../../scripts/tree_state.py"
[ -f "$SYNTHESIZE" ] || SYNTHESIZE="$SKILL_DIR/../../scripts/synthesize_report.py"
[ -f "$VALIDATOR" ] || VALIDATOR="$SKILL_DIR/../../scripts/charter_validator.py"
[ -f "$STALE_HANDLER" ] || STALE_HANDLER="$SKILL_DIR/../../scripts/stale_running_handler.py"
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

**Charter and brief setup**:
1. If `<project>/RESEARCH_BRIEF.md` exists, the brief will be consumed by every subagent. If missing, alert the user (the run will be less informed but won't fail).
2. If `<project>/RESEARCH_CHARTER.md` exists, the **anti-laziness charter** governs the whole run — every branch's RESULT.md must include a charter compliance section. **If missing, copy the template**:
   ```bash
   cp "$RTE_REPO/templates/RESEARCH_CHARTER.md" RESEARCH_CHARTER.md
   echo "WARN: copied default charter. EDIT IT before running autopilot." >&2
   ```
   Then tell the user: "I copied the default research charter to RESEARCH_CHARTER.md. **Read it and edit the venue/data/architecture/done-criteria fields to match your project before running autopilot.** Subagents will obey whatever's in there — bad charter = bad behavior."

After init, print one paragraph to the user: "Tree initialized. Budgets: max depth 5, max 30 nodes total, max 48 GPU-hours. Charter at RESEARCH_CHARTER.md — **edit it now** if defaults don't fit (venue, downstream tasks, baselines). Two enforcement layers are active: (1) `charter_validator.py` checks physical files on every branch (test_split.json hash, ≥3 seed checkpoints, ablations, metrics.json fields), (2) every passing branch goes through a fresh codex thread for external audit. Fabricated RESULT.md without backing files = branch auto-marked dead. Run `/research-tree autopilot` to start, or `/loop 30m /research-tree autopilot --silent` for continuous unattended runs."

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
   - paste the parent node JSON (small) — **note the `depth` field, it changes the rules below**
   - paste the sibling list (small)
   - paste RESEARCH_BRIEF.md if it exists
   - **paste RESEARCH_CHARTER.md (mandatory if file exists) — proposer must propose candidates that COULD satisfy the charter; e.g., if charter §2 demands diversity at depth 0 across 4 architecture families, the proposer must include all 4 even if it thinks 3 would suffice**
   - state the kind of branching expected (from step 2)
   - tell it the **branching rules by depth** (v0.1.5):
     - **depth 0 (root)**: MUST propose 2-4 candidates per charter §2 diversity rule. Cannot skip. Cannot return 1.
     - **depth ≥1**: Propose **1-4 candidates, OR signal that this node should be executed directly without sub-branching**. Choose based on honest research judgment:
       - 2-4 candidates: there are genuinely distinct ways to do this step that should compete
       - 1 candidate: there's an obvious next step, but it's worth keeping the option open to extend later
       - **skip expansion**: this is the canonical/standard way to do this — branching is just busywork. Examples: "evaluate the trained model on the held-out test set" (no fork, you just do it), "compute the standard ARI metric" (no fork, this IS the method), "run the next obvious ablation that the charter requires"
     - Decide as a human researcher would. Strongly prefer skipping expansion over forcing 3 fake-different "candidates" that are all the same thing.
   - tell it: "Return ONLY a JSON object (not a bare array), schema:
     ```json
     {
       "skip_expansion": false,
       "candidates": [
         {"kind": "approach|architecture|experiment|ablation|narrative|custom",
          "title": "<≤80 chars>",
          "description": "<2-4 sentences>"}
       ]
     }
     ```
     OR, when no branching is justified (depth ≥1 only):
     ```json
     {
       "skip_expansion": true,
       "skip_reason": "<one sentence on why no fork is needed — e.g., 'this is the canonical evaluation step, no design choice exists'>"
     }
     ```
     "
   - tell it: "Report cap: just the JSON. Anything else wastes the orchestrator's context."
4. Parse the returned JSON.
   - If `skip_expansion: true` AND parent depth ≥ 1: **do NOT create sub-nodes**. Instead, mark the current node as direct-executable:
     ```bash
     python3 "$TREE_STATE" set <node_id> direct_executable=true
     echo "$(date -Iseconds)  skip_expansion node=<node_id> reason=\"<skip_reason>\"" >> .research-tree/progress.log
     ```
     The next autopilot pick on this node will dispatch `execute` directly, not `expand` again. Skip to step 6.
   - If `skip_expansion: true` BUT parent depth == 0: ignore the skip (charter forbids it at root). Tell the user via stderr "WARN: proposer requested skip_expansion at depth 0 — overriding per charter §2". Treat as if proposer returned 0 candidates and re-prompt or error.
   - Otherwise, for each candidate in `candidates[]`:
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
3. Mark the node running: `python3 "$TREE_STATE" running <node_id>`.
4. Create the branch workdir if not present (the `add` step already does this): `.research-tree/branches/<node_id>/`. Then **scrub any stale audit artifacts that an earlier run may have left** so they cannot fool the validator:
   ```bash
   rm -f ".research-tree/branches/<node_id>/CODEX_AUDIT.json" ".research-tree/branches/<node_id>/AUDIT_NONCE" ".research-tree/branches/<node_id>/VALIDATION.json"
   ```
5. **Spawn an Agent** (subagent_type=general-purpose) with a self-contained prompt that:
   - States the branch's hypothesis (one paragraph)
   - States the budget (e.g., "2 hours wall time, 1 GPU max")
   - Says "work entirely inside `.research-tree/branches/<node_id>/`"
   - **CRITICAL — background execution mandate** (v0.1.4): "Do NOT block on long-running work. Any task that takes longer than 60 seconds (training, downloads, hyperparameter sweeps) MUST be launched with `nohup` so it survives session termination. Concretely:
     1. Prepare the training/evaluation script (e.g., `train.sh` invoking your Python entry point) inside `.research-tree/branches/<node_id>/`.
     2. Launch it as a detached background process:
        ```bash
        cd .research-tree/branches/<node_id>/
        nohup bash train.sh > executor.log 2>&1 &
        BGPID=$!
        ```
     3. Write `.research-tree/branches/<node_id>/EXECUTOR.json` IMMEDIATELY:
        ```json
        {
          \"pid\": <BGPID>,
          \"started_at\": \"<iso8601>\",
          \"command\": \"bash train.sh\",
          \"log_file\": \".research-tree/branches/<node_id>/executor.log\",
          \"expected_outputs\": [\"RESULT.md\", \"DEAD.md\"],
          \"timeout_hours\": <reasonable_budget>
        }
        ```
     4. **Return to the orchestrator NOW.** Do not wait for the background process. Your only job at this point is to confirm the launch succeeded (PID exists, log file is being written to). The orchestrator will poll for completion in later autopilot steps via `stale_running_handler.py`.

     **Why background**: the user keeps Claude Code sessions open for hours, then closes the IDE. A foreground subagent dies with the session; a nohup-detached process survives, so the training continues across session restarts. When the session reopens, `stale_running_handler.py` detects the completed process via PID check + RESULT.md presence and routes it through the validation chain.

     **Pure-compute exceptions**: if your task takes < 60 seconds total (small unit test, file inspection, small classifier on toy data) you may run it foreground. In that case, do NOT write EXECUTOR.json — just produce RESULT.md or DEAD.md directly. The orchestrator distinguishes the two modes by EXECUTOR.json presence."
   - **Pastes RESEARCH_CHARTER.md in full and says: "Obey the charter. The final RESULT.md MUST end with the charter compliance audit table from §Charter compliance audit format. Any FAIL on a (strict) rule means you write DEAD.md instead of RESULT.md, with death_reason='charter_violation: <which rule>'. Honest failure beats fake success."**
   - Says "the BACKGROUND PROCESS (not the subagent itself) writes `RESULT.md` at completion, with: METRIC=<float>, KEY_FINDING=<paragraph>, COST=<gpu_hours>, ARTIFACTS=<list>, DONE_READY=<true|false>, then the charter compliance audit table"
   - **Says: "You also MUST produce these PHYSICAL artifacts inside the branch_dir, because a programmatic validator will check for them after you return — text claims alone are not enough:**
     - `data/test_split.json` — JSON with keys `test_ids`, `hash`, `created_at`
     - `checkpoints/seed_0/`, `seed_1/`, `seed_2/` — each containing at least one `.pt`/`.pth`/`.safetensors`/`.ckpt` file
     - `metrics.json` — must include `param_count` (int ≥ 10M), `seeds` (list of ≥3), `downstream_tasks` (dict where each task has `metric`, `std`, `baseline_score`, `p_value`), `gpu_hours_used`, `wall_clock_hours`
     - `ablations/` — at least 4 subdirs (headline component, scale, data efficiency, cross-batch), each with a result file
     - `requirements.txt` or `environment.yml`
     - If you set `DONE_READY=true`: also `KILL_ARGUMENT.md` with a self-rejection memo + defense
   - **Bluntly says: "Do NOT fabricate these files with fake content. A separate validator AND an external codex auditor will run after you, and both will catch lies. Faking files = branch marked dead with `death_reason='validator: <detail>'`."**
   - Says "if you hit a blocker that makes the hypothesis untestable, write a `DEAD.md` with the blocker description instead — that is a valid outcome"
   - **Anti-laziness reminder**: "Do NOT take shortcuts because the deadline is tight or the data is awkward. If the charter mandates full-data training and you can only finish in budget with a subset, write DEAD.md with reason 'needs full-scale compute, cannot honestly complete in pilot budget'. The user values honest failure over fake success. The dead-branch atlas is part of the deliverable."

6. When the subagent returns, **first decide whether the work is actually finished**, then run the validation chain only if so:
   - **6.0. Background detection** (v0.1.4): check whether the subagent launched a long-running background process:
     ```bash
     EXEC_JSON=".research-tree/branches/<node_id>/EXECUTOR.json"
     if [ -f "$EXEC_JSON" ] && [ ! -f ".research-tree/branches/<node_id>/RESULT.md" ] && [ ! -f ".research-tree/branches/<node_id>/DEAD.md" ]; then
       PID=$(python3 -c "import json; print(json.load(open('$EXEC_JSON'))['pid'])")
       if kill -0 "$PID" 2>/dev/null; then
         echo "  → background process pid=$PID still running, leaving node in 'running' state. autopilot will poll next cycle."
         exit 0  # end this autopilot step, leave status=running
       fi
     fi
     ```
     If the EXECUTOR.json says a process is alive and neither RESULT.md nor DEAD.md exists yet, **end the autopilot step here** with the node still in `running` state. A later autopilot cycle (driven by `/loop`) will detect completion via `stale_running_handler.py` and resume from step 6a.
   - Otherwise (no EXECUTOR.json, or process is dead, or output files already exist), proceed with the validation chain. Every status transition uses the dedicated `complete` / `die` commands; `set` cannot change status anymore (the state machine refuses it):
   - **6a. Quick triage**:
     - If `DEAD.md` exists: `python3 "$TREE_STATE" die <node_id> --reason "<first line of DEAD.md>" --evidence ".research-tree/branches/<node_id>/DEAD.md"`. Done — skip to step 7.
     - If `RESULT.md` is missing AND EXECUTOR.json exists AND process is dead: `python3 "$TREE_STATE" die <node_id> --reason "executor process exited without writing RESULT.md or DEAD.md — see EXECUTOR.json log_file"`. Done — skip to step 7.
     - If `RESULT.md` is missing (no executor either): `python3 "$TREE_STATE" die <node_id> --reason "execution returned no RESULT.md and no DEAD.md"`. Done — skip to step 7.
   - **6b. Programmatic charter validation pass 1** (always runs, never skipped):
     ```bash
     python3 "$VALIDATOR" ".research-tree/branches/<node_id>" > .research-tree/branches/<node_id>/VALIDATION.json 2> .research-tree/branches/<node_id>/VALIDATION.stderr
     VALIDATOR_EXIT=$?
     ```
     If `VALIDATOR_EXIT != 0` (FAIL or WARN): read VALIDATION.json `failures[]`/`warnings[]`, take the first item, then `python3 "$TREE_STATE" die <node_id> --reason "validator: <first failure-or-warning>" --evidence ".research-tree/branches/<node_id>/VALIDATION.json"`. Done — skip to step 7. **Do not argue with the validator. Do not re-spawn the subagent to "try again". Both FAIL and WARN kill the branch — strict-by-default is the design.**
   - **6c. External codex audit** (always runs when 6b returned PASS, fail-CLOSED if codex unavailable):
     1. Generate a fresh challenge nonce and write it to disk. The orchestrator owns the nonce file; the subagent has never seen it:
        ```bash
        openssl rand -hex 32 > ".research-tree/branches/<node_id>/AUDIT_NONCE"
        NONCE=$(cat ".research-tree/branches/<node_id>/AUDIT_NONCE")
        ```
     2. Invoke `mcp__codex__codex` in a FRESH thread (never codex-reply). The prompt MUST embed the nonce and demand that codex list every file it reads with its SHA256, so the validator can verify the audit was not pre-fabricated:
        ```
        You are an external adversarial auditor for a research branch. Your job is
        to read the artifacts on disk and decide whether the branch genuinely meets
        the project's research charter, or whether it appears to take shortcuts.

        AUDIT NONCE (echo this back verbatim in your JSON, field `nonce`): <NONCE>

        Charter: <abs path to project>/RESEARCH_CHARTER.md
        Branch directory: <abs path to .research-tree/branches/<node_id>/>
        Files you MUST read and SHA256 (use `sha256sum` or equivalent):
          - RESULT.md                  (claims + charter compliance table)
          - metrics.json               (numerical evidence)
          - data/test_split.json       (held-out test hash)
          - checkpoints/seed_*/*.{pt,pth,safetensors,ckpt}  (sample at least one)
          - ablations/*/result.json    (sample at least one)
          - VALIDATION.json            (programmatic validator's pass-1 findings)

        Look for:
        - fabricated numbers (metrics suspiciously round, std=0, identical across seeds)
        - missing ablations dressed up as present (result.json is just `{}`)
        - baselines not actually compared to SOTA
        - param_count in metrics.json contradicts checkpoint file sizes
        - downstream task results inconsistent across seeds
        - KILL_ARGUMENT.md that doesn't actually defend (if DONE_READY=true)

        Return ONLY a JSON object, no prose around it:
        {
          "nonce": "<the AUDIT NONCE above, verbatim>",
          "verdict": "PASS" | "FAIL",
          "reasoning_summary": "<one sentence>",
          "reasoning": "<detailed 3-5 sentence justification>",
          "specific_concerns": ["<concern 1>", "<concern 2>", ...],
          "files_read": {
            "RESULT.md": "<sha256>",
            "metrics.json": "<sha256>",
            "data/test_split.json": "<sha256>",
            ...
          }
        }
        ```
        Write codex's response (extracting just the JSON object if codex wraps it in prose) to `.research-tree/branches/<node_id>/CODEX_AUDIT.json`.
     3. **If the codex MCP server is unavailable, the call errors, or returns no JSON within timeout**: fail-CLOSED. Run `python3 "$TREE_STATE" die <node_id> --reason "external_audit_unavailable: codex MCP did not respond" --evidence ".research-tree/branches/<node_id>/"`. Never silently skip the audit. v0.1.3 treats codex MCP as a HARD dependency.
   - **6d. Programmatic charter validation pass 2** (validates the codex audit, including nonce + SHA256 cross-checks):
     ```bash
     python3 "$VALIDATOR" ".research-tree/branches/<node_id>" --require-codex-audit --audit-nonce-file ".research-tree/branches/<node_id>/AUDIT_NONCE" > .research-tree/branches/<node_id>/VALIDATION.json 2> .research-tree/branches/<node_id>/VALIDATION.stderr
     VALIDATOR_EXIT=$?
     METRIC=$(grep -E '^METRIC[=:]' ".research-tree/branches/<node_id>/RESULT.md" | head -1 | sed -E 's/.*[=:]\s*//')
     ```
     - `VALIDATOR_EXIT != 0`: `die <node_id> --reason "codex audit / validation pass 2: <first failure>" --evidence ".../VALIDATION.json"`.
     - `VALIDATOR_EXIT == 0`: mark completed via the proof-carrying command:
       ```bash
       DONE_READY_FLAG=""
       if grep -qE '^DONE_READY[=:]\s*true' ".research-tree/branches/<node_id>/RESULT.md"; then
         DONE_READY_FLAG="--done-ready"
       fi
       python3 "$TREE_STATE" complete <node_id> --validator-report ".research-tree/branches/<node_id>/VALIDATION.json" --score "$METRIC" $DONE_READY_FLAG
       ```
       `complete` re-reads the validator report, refuses to proceed if verdict ≠ PASS, and records the SHA256 of the report as completion_proof on the node. This means even if a subagent later swaps the validator report on disk, the proof becomes verifiably stale.

7. After step 6, do NOT continue working on that branch in your own context. Do NOT re-spawn the subagent to "fix" validator failures. A FAIL or WARN is a dead branch — that's the design. The orchestrator moves on.

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

**Modes**:
- default: chatty single-step — every step returns a one-paragraph summary to the user
- `autopilot --silent`: silent single-step — no per-step summary; only surfaces to the user on
  three events: (a) DONE (charter done_criteria satisfied — autopilot STOPS and
  hands off to human for paper-writing; nothing auto-invoked), (b) ROOT_FAILURE
  (all root branches dead, pivot to /idea-pipeline), (c) STUCK (no new completed
  node in 20 steps OR budget exhausted). For long runs (`/loop 30m autopilot --silent`)
  the user only sees these key milestones.
- `autopilot --continuous [--silent]` (v0.1.5): chained — keep doing steps as long
  as there is non-blocking work (pending leaves OR ready_for_validation nodes
  from finished background processes). Stop when:
  - all live nodes are `running` (waiting on background nohup) → can't do anything until they finish
  - DONE / ROOT_FAILURE detected → terminal
  - session step counter hits threshold (default 20) → ask user to restart session for clean context
  - budget exhausted
  - same step counter ceiling acts as the STUCK guard
  Use `--continuous` when you want autopilot to chew through quick chained work
  (expand → audit → light analysis) without `/loop`'s 30-min sleep penalty.
  Combine with `/loop 30m /research-tree autopilot --continuous --silent` for
  best of both: continuous when there's quick work, /loop wakes up to handle
  long-running training results when they land.

A single autopilot step does this:

```
1. Read progress: tail -1 .research-tree/progress.log (so you know what last step did)

1.5. **Stale-running sweep** (v0.1.4 — handles cross-session-restart recovery):
     python3 "$STALE_HANDLER" --project-root "$(pwd)" > /tmp/rte_stale_$$.json
     Read the JSON output and dispatch programmatically (NOT via Claude):

     for each node in `abandoned`:
         python3 "$TREE_STATE" die <node_id> --reason "<reason from handler>"
     for each node in `legacy_orphan`:
         python3 "$TREE_STATE" die <node_id> --reason "<reason from handler>"
     for each node in `ready_for_death_from_file`:
         python3 "$TREE_STATE" die <node_id> --reason "<reason from handler>" --evidence "<branch_dir>/DEAD.md"
     for each node in `ready_for_validation`:
         # Background process finished and left a RESULT.md. Run the
         # validation chain (execute step 6b-6d) on this branch and stop
         # this autopilot step. Don't pick a new leaf until the catch-up
         # is done — finishing in-flight work is higher priority than
         # starting new work.
         dispatch validation chain for <node_id>; STOP this autopilot step here.
     for each node in `alive`:
         # process still running, leave alone; pick-next won't pick it.
         log "node <id> still running pid=<pid> since <started_at>"

2. Check for previously-detected terminal states:
     if .research-tree/ROOT_FAILURE.md exists:
       Tell the user (always, even in silent): every approach under root is dead.
       Show ROOT_FAILURE.md. Recommend pivot. STOP.
     if .research-tree/DONE.md exists:
       Tell the user (always, even in silent): done_criteria satisfied, autopilot
       has stopped, branch passed programmatic validator AND external codex audit.
       Show DONE.md (which contains the human-review checklist). Do NOT auto-invoke
       any writing tool. STOP and wait for the human.

3. Check budget:
     python3 "$TREE_STATE" budget-check
   If exit non-zero → run synthesize, report "budget exhausted, escalating to user",
   stop. Even in silent mode, surface this.

4. Pick the next leaf:
     next_id=$(python3 "$TREE_STATE" pick-next)
   If next_id == "NONE" → run synthesize, then read the new FINAL_REPORT.md's
   "Suggested next move" section and surface those options verbatim to the user
   (deepen winner / resolve alive / write paper via ARIS, OR pivot if all root dead).
   STOP.

5. Get its state:
     node_json=$(python3 "$TREE_STATE" get "$next_id")
   Parse "status" from JSON.

6. Dispatch ONE action based on status AND direct_executable flag (v0.1.5):
     - pending + direct_executable=true  → invoke /research-tree execute "$next_id"  (skip expand, this node is canonical)
     - pending + direct_executable=false → invoke /research-tree expand "$next_id"   (try to fork)
     - expanded (has children)           → invoke /research-tree execute on its first pending grandchild
                                          (this normally won't happen; pick-next prefers pending leaves)
     - any other unexpected state        → log and stop

   Each of these subcommands does its own subagent dispatch internally. In default
   (single-step) mode autopilot does NOT run multiple subagents in one step.
   See `--continuous` mode below for the chained variant.

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

11. **Reporting** (mode-dependent):
   - **default mode**: report ONE PARAGRAPH to the user: what you did this step,
     what the tree looks like now (`python3 "$TREE_STATE" tree | head -20`), what
     `/research-tree autopilot` will do next time.
   - **--silent mode**: do NOT report per-step. Just write to progress.log and stop.
     Surface ONLY if: DONE.md was written this step, ROOT_FAILURE.md was written,
     budget exhausted, OR no new completed node in 20 consecutive steps (STUCK).

11.5. **Session counter check** (v0.1.5 — context safety):
     ```bash
     python3 "$TREE_STATE" session-step increment --threshold 20 > /tmp/rte_session_$$.json
     SESSION_EXIT=$?
     ```
     If `SESSION_EXIT != 0` (i.e., `should_pause: true`), the session has accumulated
     enough steps that the main Claude Code context is getting heavy. Stop and tell
     the user verbatim (always, even in --silent mode):
     ```
     Session context approaching capacity (20+ autopilot steps in this session).
     Please restart Claude Code to clear context, then run /research-tree resume
     to continue. State is durable in .research-tree/tree.json — no progress lost.
     ```
     If `--continuous` mode is set, this is the only graceful exit for a productive run
     (other exits are terminal: DONE / ROOT_FAILURE / budget / STUCK).

11.6. **Continuous loop** (only when `--continuous` flag is set):
     After step 11.5, decide whether to immediately do another step:
     ```
     NEXT_ID=$(python3 "$TREE_STATE" pick-next)
     # Also re-run stale handler to pick up any background processes that just finished
     STALE_OUTPUT=$(python3 "$STALE_HANDLER" --project-root "$(pwd)")
     READY_FOR_VAL=$(echo "$STALE_OUTPUT" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['ready_for_validation']))")
     ```
     Continue (loop back to step 1) if ANY of:
     - `NEXT_ID != "NONE"` (there's a pending leaf to work on)
     - `READY_FOR_VAL > 0` (a background process just finished, run validation)
     - AND not should_pause, not DONE.md, not ROOT_FAILURE.md, not budget exhausted

     Stop if ALL live nodes are `running` (everything is waiting on background work).
     This is the "blocked, can't make progress without waiting" state — `/loop` will
     wake autopilot back up later when background work has had time to finish.

     In single-step mode (without --continuous), just stop after step 11.5 regardless.

12. **Hand-off on DONE — manual review, no auto-writing** (v0.1.3):
   If synthesize wrote .research-tree/DONE.md this step:
     - Surface DONE.md to the user verbatim (it already contains the human-review
       checklist: read RESULT.md, walk branch_dir, eyeball CODEX_AUDIT.json,
       compare against dead-branch atlas, then decide whether to write the paper).
     - Do NOT invoke any paper-writing tool. The user explicitly wants to write
       the paper themselves after manual review of the model + algorithm.
     - STOP. Autopilot will not resume until the human deletes DONE.md and
       reopens exploration (`python3 .../tree_state.py set <winner_id> done_ready=false`).
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
