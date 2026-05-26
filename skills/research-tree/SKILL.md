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

**v0.1.6 — task-type-aware nodes**: not every research branch is a training
run. A branch can declare `task_type` at `add` time (training / audit /
analysis / data-acquisition / framing-decision / mixed), and the validator
enforces a task-specific rule subset (e.g. audit branches don't need
checkpoints; data-acquisition branches need a DATA_MANIFEST.json with
checksum-verified files instead). Nodes can also declare `depends_on` for
sequencing (pick-next skips nodes with unmet prerequisites) and `human_only`
for paper-writing / venue / narrative decisions that the user must resolve
(autopilot pick-next skips them entirely). This makes the skill safe to use
on audit-style projects (post-hoc model evaluation, protocol critique) where
the v0.1.5 training-only validator would force every branch dead.

## Locations and helpers

The skill ships with two Python helpers. Resolve them via this chain (works regardless of how the skill was installed):

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(dirname "$(realpath "$0" 2>/dev/null || echo .)")}"
RTE_REPO="${RESEARCH_TREE_REPO:-/data3/liying/research-tree-explorer}"
TREE_STATE="$RTE_REPO/scripts/tree_state.py"
SYNTHESIZE="$RTE_REPO/scripts/synthesize_report.py"
VALIDATOR="$RTE_REPO/scripts/charter_validator.py"
STALE_HANDLER="$RTE_REPO/scripts/stale_running_handler.py"
SIGNAL_DETECTOR="$RTE_REPO/scripts/signal_detector.py"
DATA_EXAMPLES="$RTE_REPO/examples/data-acquisition"
[ -f "$TREE_STATE" ] || TREE_STATE="$SKILL_DIR/../../scripts/tree_state.py"
[ -f "$SYNTHESIZE" ] || SYNTHESIZE="$SKILL_DIR/../../scripts/synthesize_report.py"
[ -f "$VALIDATOR" ] || VALIDATOR="$SKILL_DIR/../../scripts/charter_validator.py"
[ -f "$STALE_HANDLER" ] || STALE_HANDLER="$SKILL_DIR/../../scripts/stale_running_handler.py"
[ -f "$SIGNAL_DETECTOR" ] || SIGNAL_DETECTOR="$SKILL_DIR/../../scripts/signal_detector.py"
[ -d "$DATA_EXAMPLES" ] || DATA_EXAMPLES="$SKILL_DIR/../../examples/data-acquisition"
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
/research-tree step                         — v0.2.1 interactive: run ONE autopilot step + surface suggested next moves (deepen/sibling/audit/pivot/handoff)
/research-tree backtrack <node_id>          — v0.2.1 set node aside (NOT die); use when reviewer wants to try sibling first
/research-tree resume-branch <node_id>      — v0.2.1 un-abandon a node back to pending
/research-tree resume                       — clear human-gate + reset session step counter, then run one autopilot step
/research-tree human-gate                   — admin: check/set/clear the AWAITING_HUMAN.md fast-exit sentinel
```

**`/research-tree resume` semantics** (v0.1.8):
When the user invokes `resume`, do this BEFORE dispatching to autopilot logic:
```bash
python3 "$TREE_STATE" human-gate clear --project-root "$(pwd)"
python3 "$TREE_STATE" session-step reset --project-root "$(pwd)"
```
This is the only path that clears the gate. Resuming explicitly signals "human
has handled whatever was awaiting them; reopen exploration". Then fall through
to the normal autopilot step. If the user typed `/research-tree autopilot`
instead, the gate is NOT cleared — autopilot will fast-exit at Step 0 if a gate
is up. This distinction is intentional: `autopilot` is "do work if you can",
`resume` is "explicitly continue after a human pause".

**`/research-tree human-gate <action>`**: thin pass-through to
`python3 "$TREE_STATE" human-gate <check|set|clear> [--reason ... | --all | --force]`.
Useful for: manually raising the gate to pause a long-running /loop ("I'm going
out, don't keep working") or inspecting why autopilot keeps fast-exiting.

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

After init, print one paragraph to the user: "Tree initialized. Budgets: max depth 5, max 30 nodes total, max 48 GPU-hours. Charter at RESEARCH_CHARTER.md — **edit it now** if defaults don't fit (venue, downstream tasks, baselines). Two enforcement layers are active: (1) `charter_validator.py` checks physical files on every branch (test_split.json hash, ≥3 seed checkpoints, ablations, metrics.json fields), (2) every passing branch goes through a fresh codex thread for external audit. Fabricated RESULT.md without backing files = branch auto-marked dead. **v0.1.8 — token-saving fast-exit**: when autopilot needs a human decision (session step cap, DONE, ROOT_FAILURE, etc.) it raises `.research-tree/AWAITING_HUMAN.md` and every subsequent /loop tick short-circuits at Step 0 with zero main-context tokens spent. To resume after handling, run `/research-tree resume`. Run `/research-tree autopilot` to start, or `/loop 30m /research-tree autopilot --silent` for continuous unattended runs."

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
   - **v0.1.6 — task-type tagging**: the proposer MUST also declare a
     `task_type` for each candidate (one of: `training`, `audit`,
     `analysis`, `data-acquisition`, `framing-decision`, `mixed`).
     This drives the validator schema later. If a candidate represents a
     paper-writing / venue / narrative choice that only the user can
     resolve, set `task_type: "framing-decision"` AND `human_only: true`
     so autopilot skips it. If a candidate can only run after another
     node finishes (e.g. a repair head depends on the audit identifying
     blindspots), set `depends_on: ["<sibling_id>", ...]`.
   - **v0.1.7 — auto-propose data-acquisition siblings**. Before
     finalizing a candidate that NEEDS an external dataset (audit on
     atlas X, training on cohort Y), the proposer MUST check whether
     that dataset is locally present:
       1. The orchestrator gives the proposer two paths to grep:
          `data/atlases/` (sc-bias convention) and any other dataset
          root the project uses (read from RESEARCH_BRIEF.md or
          RESEARCH_CHARTER.md §Data sources). If neither is set, scan
          `data/`, `datasets/`, `atlases/` at the project root.
          **Prefer the machine-readable inventory** when present:
          `cat data/atlases/INDEX.json` (sc-bias convention) lists
          `{atlas_id, path, n_cells, disease, tissue, paper_doi,
          collection_id, dataset_id, manifest_path}` for every atlas
          already on disk. A matching `atlas_id` OR `(paper_doi,
          disease)` tuple means the data exists — do NOT propose a
          data-acquisition sibling. Inventory format documented in
          `examples/data-acquisition/README.md`.
       2. For each candidate-of-interest atlas the proposer names, if
          there is no matching subdirectory or .h5ad with the atlas's
          slug in the filename, the proposer MUST insert a
          `task_type=data-acquisition` SIBLING candidate that pulls
          that atlas first, and tag the original candidate with
          `depends_on: ["<the_new_data_acquisition_id>"]`. Use a
          placeholder dep id of the form `<atlas_slug>_DOWNLOAD` — the
          orchestrator will resolve real IDs after `add` calls return.
       3. Each data-acquisition candidate MUST include in its
          `description` the canonical source (e.g.
          "CELLxGENE Discover dataset <UUID>" or "GEO ftp
          <accession>") and the expected n_cells (from paper / lit
          scout). This lets the executor subagent pick the right
          template (`cellxgene_download.sh` vs
          `geo_figshare_download.sh`) without re-doing discovery.
       4. If the proposer cannot determine the source URL or UUID
          (e.g., paper cites GEO but no accession number visible),
          spawn `cellxgene_discover.py search --query "<keywords>"`
          *inside the proposer subagent* before deciding. If after
          discovery the data is in a protected-access tier (EGA /
          dbGaP / IRB), set the data-acquisition candidate's
          `human_only: true` and `description` MUST start with
          "PROTECTED ACCESS — Lily must <DAC | provision | manual
          download> before this dependency unblocks".
       5. The dependency wiring is what makes this safe: the
          downstream audit/training branch never runs until its
          data-acquisition sibling lands `status=completed`, and
          `pick-next` respects `depends_on`. This is the v0.1.7
          version of "stop forcing autopilot to attempt experiments
          on data that does not exist yet".
   - **v0.5.0 — first-principles cost gate**: tell the proposer:
     "Every node is a unit of (cost × information value). Before proposing a
     candidate, estimate: (a) `budget_hours_min` — minimum-viable wall hours
     that would let us answer the candidate's research question (PoC scope);
     (b) `budget_hours_full` — full-scale wall hours for the complete version;
     (c) `info_value_score` 1-5 — 1 cosmetic/supplementary, 3 informative,
     5 paper-headline-load-bearing. **Hard rule**: if `budget_hours_full /
     budget_hours_min > 3`, you MUST split the candidate into a PoC sibling
     (budget_hours_min scope) and a full sibling (depends_on_placeholders =
     [poc_placeholder]). The full version only runs after the PoC validates
     direction. **Default to the smallest version that answers the question.**
     Full-scale only justifies when PoC has already passed.
     Also: if multiple candidates can genuinely run concurrently with no
     resource conflict, set `parallel_group` to the same tag — pick-next
     will prefer dispatching grouped peers together."
   - tell it: "Return ONLY a JSON object (not a bare array), schema:
     ```json
     {
       "skip_expansion": false,
       "candidates": [
         {"placeholder_id": "<short slug, used by sibling depends_on>",
          "kind": "approach|architecture|experiment|ablation|narrative|custom|data",
          "task_type": "training|audit|analysis|data-acquisition|framing-decision|mixed",
          "human_only": false,
          "depends_on_placeholders": [],
          "depends_on_soft_placeholders": [],
          "parallel_group": null,
          "budget_hours_min": 2.0,
          "budget_hours_full": 4.0,
          "info_value_score": 3,
          "title": "<≤80 chars>",
          "description": "<2-4 sentences. For data-acquisition candidates: include source (CELLxGENE dataset UUID / GEO accession / figshare DOI) and expected n_cells.>"}
       ]
     }
     ```
     - `placeholder_id` lets siblings declare deps on each other before
       any real node IDs exist; orchestrator resolves on add (v0.1.7).
     - `depends_on_placeholders` = HARD deps (must complete before pickable).
     - `depends_on_soft_placeholders` (v0.5.0) = recommended-order deps
       (don't block pick-next; used for documentation + audit context).
     - `parallel_group` (v0.5.0) = string tag; peers with same tag are
       dispatched concurrently when ready. Leave null when N/A.
     - `budget_hours_min` / `budget_hours_full` / `info_value_score` (v0.5.0):
       mandatory cost-gate fields. See the "first-principles cost gate"
       paragraph above. If you cannot estimate them honestly, use null and
       explain in description — orchestrator will flag for human input.

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
   - Otherwise, walk `candidates[]` in TWO PASSES (v0.1.7) so that
     `depends_on_placeholders` can be resolved to real node ids.
     `tree_state.py add` already prints the new node id on stdout, so
     no extra flag is needed:
     ```bash
     # Pass 1 — add every candidate WITHOUT --depends-on. Capture the
     # real id printed by `add` and build a placeholder_id → real_id map.
     # v0.5.0 — also thread cost/value/parallel fields through to `add`.
     declare -A PLACE2ID
     for c in candidates[]; do
       EXTRA=""
       [ -n "<task_type>" ] && EXTRA="$EXTRA --task-type <task_type>"
       [ "<human_only>" = "true" ] && EXTRA="$EXTRA --human-only"
       [ -n "<budget_hours_min>" ] && EXTRA="$EXTRA --budget-hours-min <budget_hours_min>"
       [ -n "<budget_hours_full>" ] && EXTRA="$EXTRA --budget-hours-full <budget_hours_full>"
       [ -n "<info_value_score>" ] && EXTRA="$EXTRA --info-value-score <info_value_score>"
       [ -n "<parallel_group>" ] && EXTRA="$EXTRA --parallel-group <parallel_group>"
       NEW_ID=$(python3 "$TREE_STATE" add <parent_node_id> <kind> "<title>" \
                  --description "<description>" $EXTRA)
       PLACE2ID["<placeholder_id>"]="$NEW_ID"
     done

     # Pass 2 — for any candidate with non-empty depends_on_placeholders or
     # depends_on_soft_placeholders, translate placeholders to real ids and
     # patch the node via `set` (parse_kv handles comma-separated lists):
     for c in candidates[] where depends_on_placeholders != []; do
       DEPS_CSV=$(join , for p in depends_on_placeholders: PLACE2ID[$p])
       python3 "$TREE_STATE" set "${PLACE2ID[<placeholder_id>]}" depends_on="$DEPS_CSV"
     done
     for c in candidates[] where depends_on_soft_placeholders != []; do
       SOFT_CSV=$(join , for p in depends_on_soft_placeholders: PLACE2ID[$p])
       python3 "$TREE_STATE" set "${PLACE2ID[<placeholder_id>]}" depends_on_soft="$SOFT_CSV"
     done
     ```
     Pass-2 patching uses `set` because `add` validates `depends_on`
     against the existing tree at insertion time and would reject
     forward references to siblings added later in pass 1.
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
     3. Write `.research-tree/branches/<node_id>/EXECUTOR.json` IMMEDIATELY. v0.4.0
        added `pid_starttime` so `stale_running_handler.py` can distinguish PID
        reuse from a still-live process. On Linux, read field 22 of
        `/proc/$BGPID/stat`:
        ```bash
        STARTTIME=$(awk '{print $22}' /proc/$BGPID/stat 2>/dev/null || echo null)
        cat > EXECUTOR.json <<JSON
        {
          "pid": $BGPID,
          "pid_starttime": $STARTTIME,
          "started_at": "$(date -Iseconds)",
          "command": "bash train.sh",
          "log_file": ".research-tree/branches/<node_id>/executor.log",
          "expected_outputs": ["RESULT.md", "DEAD.md"],
          "timeout_hours": <reasonable_budget>
        }
        JSON
        ```
        On non-Linux (macOS), set `pid_starttime` to `null` — the handler falls
        back to plain `kill(pid, 0)` and a same-PID liveness signal.
     4. **Return to the orchestrator NOW.** Do not wait for the background process. Your only job at this point is to confirm the launch succeeded (PID exists, log file is being written to). The orchestrator will poll for completion in later autopilot steps via `stale_running_handler.py`.

     **Why background**: the user keeps Claude Code sessions open for hours, then closes the IDE. A foreground subagent dies with the session; a nohup-detached process survives, so the training continues across session restarts. When the session reopens, `stale_running_handler.py` detects the completed process via PID check + RESULT.md presence and routes it through the validation chain.

     **Pure-compute exceptions**: if your task takes < 60 seconds total (small unit test, file inspection, small classifier on toy data) you may run it foreground. In that case, do NOT write EXECUTOR.json — just produce RESULT.md or DEAD.md directly. The orchestrator distinguishes the two modes by EXECUTOR.json presence."
   - **Pastes RESEARCH_CHARTER.md in full and says: "Obey the charter. The final RESULT.md MUST end with the charter compliance audit table from §Charter compliance audit format. Any FAIL on a (strict) rule means you write DEAD.md instead of RESULT.md, with death_reason='charter_violation: <which rule>'. Honest failure beats fake success."**
   - **v0.2.0 — 4 output modes the agent can produce** (NEW: agent has authority to fork or pivot, not just succeed/fail). Tell the subagent verbatim:

     "You have FOUR ways to end your run, write exactly one of these files in the branch_dir:

     (a) **`RESULT.md`** — work completed successfully. Include METRIC + KEY_FINDING + ARTIFACTS + charter compliance table per the task_type schema below. This is the default outcome.

     (b) **`DEAD.md`** — work cannot finish. Hypothesis falsified, blocker hit, charter rule violated. Write one line `death_reason: <one-sentence>`, then a paragraph of context. **Honest failure beats fake success.**

     (c) **`SUBTREE_FORK.md`** (v0.2.0 NEW) — you discovered mid-flight that this step actually has 2-4 genuinely distinct sub-approaches worth competing. Don't pick one yourself, hand control back to the orchestrator who will run each. Format:
     ```
     # Why fork: <one line>
     ```json
     {
       \"candidates\": [
         {\"placeholder_id\": \"a\", \"kind\": \"ablation\", \"task_type\": \"training\",
          \"title\": \"<≤80 chars>\", \"description\": \"<2-4 sentences>\",
          \"human_only\": false, \"depends_on_placeholders\": []},
         ...
       ]
     }
     ```
     Use this when: the original node description compressed a real branching decision (e.g. 'try GSVA vs AUCell vs ssGSEA' bundled as one node, but each is its own training run worth competing). Don't use this to escape work — codex audit catches that. Maximum 4 candidates.

     (d) **`SUBTREE_PIVOT.md`** (v0.2.0 NEW) — you discovered the entire hypothesis of this branch is wrong and a different framing is needed. NOT a fork (parallel siblings) — a redirect. Format:
     ```
     reason: <one-line: what makes the original hypothesis dead>
     suggest_new_parent_node_kind: <kind, e.g. narrative / experiment>
     suggest_new_node_title: <≤80 chars proposing the new direction>
     evidence: <2-3 sentences why this is a pivot not a fork>
     ```
     Use this VERY sparingly — only when continuing makes no sense. Orchestrator will raise human-gate so Lily decides whether to follow the pivot. If you're tempted to write this, double-check it's not actually (c).

     **Decision rule** for which mode: complete work → (a). Hit a blocker → (b). Mid-flight discovered real internal forks → (c). Mid-flight discovered the whole branch is misframed → (d). Default to (a). Use (c)/(d) only when you genuinely cannot make a single principled forward choice."

   - **v0.2.0 — retry context**. If this node's `last_failure_context` field is non-null, paste it at the top of the agent prompt verbatim under header "PREVIOUS ATTEMPT FAILED — avoid repeating these specific mistakes:". Get the field by running `python3 "$TREE_STATE" get <node_id> | python3 -c "import json,sys; print(json.load(sys.stdin).get('last_failure_context') or '')"`. The agent knows what NOT to do.

   - **v0.3.0 — sub-step checkpointing (long-running branches only)**. For branches that take > 30 minutes (any training, multi-step audit, multi-file download), the agent SHOULD use phase_log.jsonl checkpointing so a crash mid-execution is resumable not catastrophic. Tell the agent verbatim:

     "If your work is long enough to potentially crash mid-flight (training, multi-file download, multi-seed sweep), break it into named phases. At the start of each phase, mark it:
       ```bash
       python3 $RTE_REPO/scripts/phase_checkpoint.py mark .research-tree/branches/<node_id> --phase setup --action start
       # ... do setup work ...
       python3 $RTE_REPO/scripts/phase_checkpoint.py mark .research-tree/branches/<node_id> --phase setup --action complete --checkpoint-file data/test_split.json
       ```
     At the **start of each phase**, ALSO check if it's already complete (idempotent resume):
       ```bash
       if python3 $RTE_REPO/scripts/phase_checkpoint.py is-complete .research-tree/branches/<node_id> --phase setup; then
         echo \"phase setup already done in prior attempt, skipping\"
       else
         # do the actual setup work, then mark complete
       fi
       ```
     Suggested phase names (use these unless you have reason to change): `setup` (load data, build model), `train_seed_0` / `train_seed_1` / `train_seed_2`, `eval`, `ablations`, `finalize` (write RESULT.md / metrics.json). The orchestrator's stale-running detector will, on crash, automatically re-spawn you with last_failure_context set to which phases were done and which to resume from. **You do NOT need to manually persist intermediate state — the phase log is the resume contract**. Just be honest about phase boundaries (don't mark 'complete' if it wasn't)."

   - **v0.3.0 — recursive sub-decisions (fractal agent)**. The subagent has access to the `Agent` tool (its tools include `Agent` because subagent_type=general-purpose is "Tools: *"). Tell it:

     "For sub-decisions YOU need to own internally (not tree-level forks — those go via SUBTREE_FORK.md), you can spawn your own sub-subagent via the Agent tool. Use this when a single research step has an internal exploration that doesn't deserve to be a permanent tree node, but is too complex to think through in your own context. Example: 'before I commit to GSVA, let me spawn a quick sub-agent to test all three (GSVA / AUCell / ssGSEA) on a 1000-cell pilot and report Spearman correlations'. The sub-subagent returns a result; you absorb it and continue your branch's main work.

     Two paths to fork the tree are distinct:
       - **In-context sub-agent via Agent tool**: ephemeral, for sub-decisions you own. Result is a return value you read. Does NOT create a tree node. Use when you're CURRENTLY working and just need a quick parallel exploration.
       - **SUBTREE_FORK.md**: persistent, becomes new tree nodes. Use when the sub-decisions deserve full validation / codex audit / their own RESULT.md each.
     The distinction is: 'do I need a quick assist, or is this really 3 competing experiments?' If 3 competing experiments → SUBTREE_FORK. If 'just help me decide on one thing' → in-context sub-agent."

   - **v0.1.6 — task_type-aware artifact requirements**. Read the node's `task_type` field (from `python3 "$TREE_STATE" get <node_id>` → `.task_type`) and tell the subagent the artifact set that matches its task_type. The orchestrator MUST select ONE of the following blocks based on the node's `task_type`:

     **For `task_type=training` (default, v0.1.5 behavior)**:
     - Says "the BACKGROUND PROCESS (not the subagent itself) writes `RESULT.md` at completion, with: METRIC=<float>, KEY_FINDING=<paragraph>, COST=<gpu_hours>, ARTIFACTS=<list>, DONE_READY=<true|false>, then the charter compliance audit table"
     - **Says: "You also MUST produce these PHYSICAL artifacts inside the branch_dir, because a programmatic validator will check for them after you return — text claims alone are not enough:**
       - `data/test_split.json` — JSON with keys `test_ids`, `hash`, `created_at`
       - `checkpoints/seed_0/`, `seed_1/`, `seed_2/` — each containing at least one `.pt`/`.pth`/`.safetensors`/`.ckpt` file
       - `metrics.json` — must include `param_count` (int ≥ 10M), `seeds` (list of ≥3), `downstream_tasks` (dict where each task has `metric`, `std`, `baseline_score`, `p_value`), `gpu_hours_used`, `wall_clock_hours`
       - `ablations/` — at least 4 subdirs (headline component, scale, data efficiency, cross-batch), each with a result file
       - `requirements.txt` or `environment.yml`
       - If you set `DONE_READY=true`: also `KILL_ARGUMENT.md` with a self-rejection memo + defense

     **For `task_type=audit` (post-hoc evaluation on a frozen model)**:
     - "RESULT.md must include `METRIC=<float>` (over_estimation_ratio is the headline metric), `KEY_FINDING`, `COST`, `ARTIFACTS`, `DONE_READY`, plus the charter compliance table covering rules 0/1/4/7/8 only (skip 2/3/5 — no new training)."
     - "You MUST produce these PHYSICAL artifacts in the branch_dir:
       - `audit_report.json` — contains `cohort_summary` (n_cohort_cells, n_control_cells, n_donor_cohort, n_donor_control) AND `blindspot_signal` (fn_delta, ci_low, ci_hi, verdict)
       - `donor_bootstrap.json` — donor-level bootstrap with `n_iter` ≥ 1000, plus per-donor leave-one-out sensitivity
       - `protocol_comparison.json` — `within_atlas_fn_delta`, `cross_batch_fn_delta`, `over_estimation_ratio` (this is the methodological core of v0.1.6 audit framing)
       - `requirements.txt` or `environment.yml`"
     - "NO checkpoint dirs, NO metrics.json with param_count, NO ablations/ — these are nonsense for an audit task."

     **For `task_type=analysis` (statistics / figures / report)**:
     - "RESULT.md must include `METRIC=<float>`, key claims, charter table covering rules 0/4/7/8 only."
     - "PHYSICAL artifacts: `analysis_output.json` (structured statistics output) + optionally `figures/*.png|*.pdf|*.svg` + `requirements.txt`. NO checkpoints, NO test_split, NO metrics.json with param_count."

     **For `task_type=data-acquisition` (download + verify external data)**:
     - "RESULT.md must include `METRIC=<float>` (set to n_cells downloaded), `KEY_FINDING` (one line: which atlas, how many cells, where it lives), `ARTIFACTS`. Charter table covers rules 0/1/7."
     - "PHYSICAL artifacts: `DATA_MANIFEST.json` with required keys (`atlas_id`, `source_url`, `local_path`, `checksum`, `n_cells`, `downloaded_at`) where the referenced `local_path` MUST exist on disk after the download finishes. Plus `requirements.txt` or the download script."
     - "NO model artifacts; data-acquisition is a pure infrastructure step."
     - "**Use the ready-made templates** in `$RTE_REPO/examples/data-acquisition/` — they already produce the exact DATA_MANIFEST.json schema the validator expects and emit a working RESULT.md. Three scripts:
       - `cellxgene_discover.py` — only needed if you know the paper / disease / tissue but not the CELLxGENE dataset UUID. Runs three subcommands: `search --query '<keywords>'`, `list-collection --collection-id <uuid>`, `inspect-dataset --dataset-id <uuid>`. All return JSON. No auth; goes through env proxy if set.
       - `cellxgene_download.sh` — copy into branch dir, edit the 4 env defaults (DATASET_ID / ATLAS_ID / ATLAS_LABEL / PAPER_DOI), then run with `nohup`. Auto-counts n_cells from the .h5ad, writes RESULT.md + DATA_MANIFEST.json.
       - `geo_figshare_download.sh` — for non-CELLxGENE sources (GEO ftp, figshare ndownloader, Zenodo, GitHub releases). Same nohup pattern. Auto-counts cells for .h5ad; for other formats (.rds / .tar.gz / .mtx.gz) the subagent must pass N_CELLS=<int> from the paper or supplementary."
     - "**Proxy policy** (sc-bias project hard rule, mirrored in any project that sets PROXY): downloads go through `http://127.0.0.1:17891`. NEVER use port 17890 (that is Claude Code's own metered upstream — one accidental 15 GB pull burned a quota). The templates default to 17891 and log a loud WARN if 17890 is detected. For projects without 17891, override with `PROXY='' bash cellxgene_download.sh` for direct connect, or `PROXY=http://your-host:port ...` for a custom proxy."
     - "**Background mandate** (same as the training/audit task types — see step 5 'CRITICAL — background execution mandate' above). A multi-GB download takes hours, easily survives a session close. Concretely:
       ```bash
       cd .research-tree/branches/<node_id>/
       cp $RTE_REPO/examples/data-acquisition/cellxgene_download.sh .
       $EDITOR cellxgene_download.sh   # set DATASET_ID etc.
       nohup bash cellxgene_download.sh > executor.log 2>&1 &
       BGPID=$!
       cat > EXECUTOR.json <<JSON
       {\"pid\": $BGPID, \"started_at\": \"$(date -Iseconds)\", \"command\": \"bash cellxgene_download.sh\", \"log_file\": \"executor.log\", \"expected_outputs\": [\"RESULT.md\", \"DATA_MANIFEST.json\"], \"timeout_hours\": 12}
       JSON
       ```
       Then return to the orchestrator. `stale_running_handler.py` will pick up completion on the next autopilot cycle."
     - "**Protected-access escalation**: if the dataset is EGA / dbGaP / IRB-restricted / cloud-storage-with-credentials, DO NOT brute-force download. Write `DEAD.md` with `death_reason=\"needs_human: protected-access data (<source>), requires <DAC application | account provisioning | $-cost>\"`. That surfaces to the human as a STUCK trigger, which is the contract for hand-off. Lily will provision credentials or download manually and restart the branch."
     - "**No silent format conversion**. If the source provides .rds and the project expects .h5ad, that conversion is a SEPARATE branch (`task_type=analysis` with sceasy or anndata2ri). Data-acquisition's only job is: pull bytes off the network, verify they are what the upstream metadata claimed, and register them. Conversion is downstream."

     **For `task_type=framing-decision`**: the orchestrator should NEVER reach this step. If it does, log the bug and write DEAD.md with `death_reason='framing-decision is human-only; autopilot must not execute it (skip in pick-next by setting human_only=true)'`.

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
   - **6a. Quick triage** (v0.2.0 — 4 output modes recognized):
     - **`SUBTREE_FORK.md` exists** (v0.2.0 — agent decided mid-flight that this step has 2-4 real sub-forks worth competing): apply the fork and STOP — the new pending children will be picked next autopilot tick.
       ```bash
       python3 "$TREE_STATE" --project-root "$(pwd)" apply-subtree-fork <node_id>
       ```
       The command parses `SUBTREE_FORK.md`, creates the candidate children, marks parent status=`expanded` (v0.4.0 — was `forked` in v0.2-v0.3.1; the agent-self-fork lineage now lives on each child's `spawned_by_agent` field, no separate parent status). Done — skip to step 7. **Do not also try to validate RESULT.md if both exist — fork takes precedence (a forking branch hasn't finished its own work).**
     - **`SUBTREE_PIVOT.md` exists** (v0.2.0 — agent discovered the whole hypothesis is wrong; not fork but redirect): die the node + raise human-gate so Lily can decide whether to follow the pivot suggestion.
       ```bash
       python3 "$TREE_STATE" die <node_id> --reason "agent_pivot: $(grep -m1 reason .research-tree/branches/<node_id>/SUBTREE_PIVOT.md | sed 's/^reason:\s*//')" --evidence ".research-tree/branches/<node_id>/SUBTREE_PIVOT.md"
       cp .research-tree/branches/<node_id>/SUBTREE_PIVOT.md .research-tree/PIVOT_PROPOSAL.md
       python3 "$TREE_STATE" --project-root "$(pwd)" human-gate set --reason "AGENT_PIVOT from <node_id>: read .research-tree/PIVOT_PROPOSAL.md and decide whether to follow the suggested new direction. Run /research-tree resume after deciding."
       ```
       Done — skip to step 7. autopilot stops.
     - **`DEAD.md` exists**: `python3 "$TREE_STATE" die <node_id> --reason "<first line of DEAD.md>" --evidence ".research-tree/branches/<node_id>/DEAD.md"`. Done — skip to step 7.
     - **`RESULT.md` missing AND EXECUTOR.json exists AND process is dead**: `python3 "$TREE_STATE" die <node_id> --reason "executor process exited without writing RESULT.md or DEAD.md — see EXECUTOR.json log_file"`. Done — skip to step 7.
     - **`RESULT.md` missing (no executor either)**: `python3 "$TREE_STATE" die <node_id> --reason "execution returned no RESULT.md and no DEAD.md"`. Done — skip to step 7.
   - **6b. Programmatic charter validation pass 1** (always runs, never skipped):
     ```bash
     python3 "$VALIDATOR" ".research-tree/branches/<node_id>" > .research-tree/branches/<node_id>/VALIDATION.json 2> .research-tree/branches/<node_id>/VALIDATION.stderr
     VALIDATOR_EXIT=$?
     ```
     If `VALIDATOR_EXIT != 0` (FAIL or WARN): read VALIDATION.json `failures[]`/`warnings[]`, take the first item. v0.2.0: try AIDE-style repair retry first (max 2 attempts), THEN die. This gives the agent a chance to learn from the failure and try again — but only twice per node, so the tree doesn't loop forever:
     ```bash
     FAIL_LINE=$(python3 -c "import json; v=json.load(open('.research-tree/branches/<node_id>/VALIDATION.json')); f=(v.get('failures') or v.get('warnings') or ['?'])[0]; print(f)")
     python3 "$TREE_STATE" --project-root "$(pwd)" repair-retry <node_id> --failure-context "validator pass-1: $FAIL_LINE"
     RETRY_EXIT=$?
     if [ "$RETRY_EXIT" -eq 0 ]; then
       echo "  → repair retry granted; node back to pending, next autopilot tick will re-execute with last_failure_context"
       # Skip the validator chain for this tick; node is pending again.
       # autopilot's continuous loop will pick it up on the next iteration.
     else
       python3 "$TREE_STATE" die <node_id> --reason "validator (final after $RETRY_EXIT retries): $FAIL_LINE" --evidence ".research-tree/branches/<node_id>/VALIDATION.json"
     fi
     ```
     Done — skip to step 7. **The agent gets 2 retries with last_failure_context to learn from; after that the branch dies. Do NOT manually try a 3rd retry — the budget is the budget.**
   - **6b.5. Validator-repair pre-pass** (v0.1.9 — cosmetic auto-fix BEFORE pass 1 final verdict). If pass 1 only failed on the cosmetic categories (charter-table rule headers using "§N" instead of "N. Canonical", DATA_MANIFEST file named with a suffix like `DATA_MANIFEST_foo.json` instead of `DATA_MANIFEST.json`, missing `requirements.txt`), run:
        ```bash
        python3 "$RTE_REPO/scripts/validator_repair.py" ".research-tree/branches/<node_id>" --task-type <task_type>
        ```
        It returns exit 0 if everything is cosmetic and got fixed, exit 2 if there are real failures it cannot repair. Re-run validator pass 1 once after a repair-0 exit. NEVER repair the underlying numeric / scientific claims — only filename / table-header / missing-env-manifest. If pass 1 STILL fails after repair, then die per 6b. The repair is a single deterministic fix, not a "try again" of the subagent.
   - **6c. External codex audit** (v0.1.9: CLI-FIRST, MCP fallback. Always runs when 6b returned PASS).
     1. Generate a fresh challenge nonce and write it to disk. The orchestrator owns the nonce file; the subagent has never seen it:
        ```bash
        openssl rand -hex 32 > ".research-tree/branches/<node_id>/AUDIT_NONCE"
        NONCE=$(cat ".research-tree/branches/<node_id>/AUDIT_NONCE")
        ```
     2. **PREFERRED PATH — codex CLI via `codex_audit_cli.py`** (works without MCP registration; uses the project's OPENAI_API_KEY from `~/.codex/auth.json` + GPT-5.5 at `api.biom.autos`):
        ```bash
        env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY \
        python3 "$RTE_REPO/scripts/codex_audit_cli.py" \
            --branch-dir ".research-tree/branches/<node_id>" \
            --charter "RESEARCH_CHARTER.md" \
            --nonce-file ".research-tree/branches/<node_id>/AUDIT_NONCE" \
            --task-type "<task_type>" \
            --out ".research-tree/branches/<node_id>/CODEX_AUDIT.json"
        AUDIT_EXIT=$?
        ```
        If `AUDIT_EXIT == 0`, the CLI already wrote both `CODEX_AUDIT.json` (model's response: nonce echo + per-file SHA256 + **challenge-fragment quotes** new in v0.4.0) AND `AUDIT_CHALLENGES.json` (orchestrator's random (file, offset, length) windows). Proceed to step 6d. The challenge-fragment scheme is v0.4.0's real anti-fabrication mechanism — the model must quote disk bytes at random offsets that the orchestrator picked before the call. Echoing the prompt is no longer sufficient.
     3. **FALLBACK PATH — `mcp__codex__codex`** (only if the CLI exit != 0 AND the tool is registered). Invoke in a FRESH thread (never codex-reply). The prompt MUST embed the nonce, demand SHA256 echo, AND include challenge fragments (random byte offsets the model must quote verbatim). If the fallback path can't include challenges, write `AUDIT_CHALLENGES.json` first using `python3 -c "import random; ..."` and embed the challenge IDs/offsets in the codex prompt:
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
       python3 "$TREE_STATE" complete <node_id> --score "$METRIC" \
           --audit-nonce-file ".research-tree/branches/<node_id>/AUDIT_NONCE" \
           --require-codex-audit \
           $DONE_READY_FLAG
       ```
       v0.3.1+: `complete` no longer accepts an external validator-report
       path (that was a trust-kernel bypass). It re-runs `charter_validator`
       as a subprocess on `branch_dir`, passing the nonce file so codex audit
       + SHA cross-check + challenge-fragment cross-check all enforce. Trusted
       source is fresh validator stdout, not anything the agent wrote. The
       SHA256 of the validator's report (written to `branch_dir/VALIDATION.json`
       by `complete`) is recorded as `completion_proof` on the node.

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

### step (v0.2.1 — interactive co-pilot, NOT silent/continuous)

Single autopilot step + explicit "what next?" suggestions for Lily. Differs from `autopilot` in two ways: (1) always chatty (never silent), (2) ends with a structured suggestion list so she can pick the next move instead of letting silent autopilot decide.

```bash
# 1. Run one autopilot step (use the autopilot section logic, NOT --silent NOT --continuous)
# All the validation chain / cascade-reap / agent-fork-handling still applies.
# At the END of that single step, do NOT just stop — also:
python3 "$TREE_STATE" --project-root "$(pwd)" suggest-next > /tmp/rt_suggest_$$.json
```

After the step completes (success or branch died), present to Lily:
- ASCII tree (first 15 lines of `tree_state.py tree`)
- One-paragraph "what changed this step" (which node ran, what verdict)
- Top 3-5 suggestions from `suggest-next` JSON, formatted as a clickable list:
  ```
  Next moves (you pick):
    1. deepen 1.2 — head-to-head with completed 1.1 (sibling)
    2. expand 1.4 — completed 1.1 unlocked dependents (deepen)
    3. backtrack 1.3 — set aside, try sibling first
  Reply with the option number (or `step` for autopilot's choice).
  ```

STOP and wait for Lily. **No /loop wrap on `step`** — it's explicitly the "one step, talk to me" mode.

### backtrack <node_id> (v0.2.1)

Lily reviewed a branch result, doesn't want to kill it but wants to park it:
```bash
python3 "$TREE_STATE" --project-root "$(pwd)" backtrack "$NODE_ID" --reason "Lily wants to try sibling first"
```
Output the new tree (head 15 lines). One-line confirmation: "Node X parked (status=abandoned). Sibling siblings or other paths can proceed."

Differs from `prune`: `prune` calls `die` (counted in dead atlas, downstream cascades may reap). `backtrack` is reversible, no death record, no cascade.

### resume-branch <node_id> (v0.2.1)

Counterpart to backtrack. Un-park an abandoned node:
```bash
python3 "$TREE_STATE" --project-root "$(pwd)" resume-branch "$NODE_ID"
```
Output one-line: "Node X back to pending; next pick-next may select it."

### autopilot / resume

**`autopilot` is a single-step command, not a long-running loop.** Each invocation does ONE unit of work and returns. To run continuously, the user wraps it with the external `/loop` skill, e.g. `/loop 30m /research-tree autopilot`. This keeps your main context fresh — each step is one orchestration turn, heavy work is in subagents, no in-prompt for-loops that bloat over time.

**Modes**:
- default: chatty single-step — every step returns a one-paragraph summary to the user
- `autopilot --silent`: silent single-step — no per-step summary. Surfaces ONLY
  on key events:
  (a) DONE (charter done_criteria satisfied — autopilot STOPS and hands off to
      human for paper-writing; nothing auto-invoked),
  (b) ROOT_FAILURE (all root branches dead, pivot to /idea-pipeline),
  (c) STUCK / session-cap (no new completed node in 10 steps OR budget exhausted).
  **v0.1.8: each of these events ALSO raises the human-gate sentinel** — so
  subsequent /loop ticks while the user hasn't responded cost ZERO main-context
  tokens (Step 0 short-circuits before any orchestration runs). For long runs
  (`/loop 30m autopilot --silent`) the user sees the milestone exactly once.
- `autopilot --continuous [--silent]` (v0.1.5): chained — keep doing steps as long
  as there is non-blocking work (pending leaves OR ready_for_validation nodes
  from finished background processes). Stop when:
  - all live nodes are `running` (waiting on background nohup) → can't do anything until they finish
  - DONE / ROOT_FAILURE detected → terminal
  - session step counter hits threshold (default 10, lowered from 20 in v0.1.8) → raise human-gate, ask user to restart session for clean context
  - budget exhausted
  - same step counter ceiling acts as the STUCK guard
  Use `--continuous` when you want autopilot to chew through quick chained work
  (expand → audit → light analysis) without `/loop`'s 30-min sleep penalty.
  Combine with `/loop 30m /research-tree autopilot --continuous --silent` for
  best of both: continuous when there's quick work, /loop wakes up to handle
  long-running training results when they land.

A single autopilot step does this:

```
0. **Human-gate fast-exit** (v0.1.8 — the most important token-saving change).
   Run this BEFORE anything else, including the stale-running sweep. The point
   is to make `/loop 30m autopilot --silent` cost ~zero main-context tokens
   per tick while the tree is waiting on a human decision.

   **v0.5.0 — auto-clear-then-check**: before reading the gate, give any
   orchestrator-raised ALL_RUNNING gate a chance to self-clear. The gate
   carries a snapshot of `running_node_ids` at raise time; if any of those
   has transitioned out of `running` (completed / dead / abandoned), the
   work environment has changed and the gate is stale. `auto-clear-if-stale`
   is a no-op on human-raised gates (no auto_marker) and on DONE/ROOT_FAILURE.
   ```bash
   python3 "$TREE_STATE" human-gate auto-clear-if-stale --project-root "$(pwd)" > /tmp/rte_autoclr_$$.json
   python3 "$TREE_STATE" human-gate check --project-root "$(pwd)" > /tmp/rte_gate_$$.json
   GATE_EXIT=$?
   ```
   If `GATE_EXIT == 2`, the gate is up (one of: `.research-tree/AWAITING_HUMAN.md`,
   `DONE.md`, or `ROOT_FAILURE.md`). A previous autopilot step already surfaced
   the relevant info to the user. Stop NOW without running steps 1-11.
   - **If `--silent` is in `$ARGUMENTS`** (or `SILENT=1` in env): print NOTHING.
     Just exit. The Claude Code turn that wrapped this skill invocation
     surfaces zero text to the user; main context stays untouched.
   - **Otherwise**: print exactly ONE LINE — no markdown headers, no
     paragraph, no tree dump:
     ```
     [awaiting human — see .research-tree/AWAITING_HUMAN.md; run /research-tree resume to clear]
     ```
     Then exit. Even in default (chatty) mode, do not elaborate. The user
     already saw the original reason when the gate was first raised, and any
     reminder adds context-window cost on every loop tick.
   ```bash
   rm -f /tmp/rte_gate_$$.json
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
     for each node in `ready_for_resume`:
         # v0.3.0 — phase_log.jsonl shows partial progress; the crash is
         # resumable not abandoned. Use repair-retry with the phase context
         # so the new agent reads phase_log.jsonl and skips completed phases.
         python3 "$TREE_STATE" --project-root "$(pwd)" repair-retry <node_id> \
             --failure-context "executor crashed mid-execution at phase=<resumable_from_phase>; phase_log.jsonl shows <completed_phases> already done — skip those, resume from <resumable_from_phase>. If retry budget allows, the new agent will continue from the partial state."
         # If repair-retry returns exit 2 (budget exhausted), die the node:
         python3 "$TREE_STATE" die <node_id> --reason "executor crash with phase_log partial, repair budget exhausted"
         # STOP this autopilot step; next tick picks up the now-pending node.
     for each node in `alive`:
         # process still running, leave alone; pick-next won't pick it.
         log "node <id> still running pid=<pid> since <started_at>"

1.7. **Cascade-reap zombies** (v0.1.9 — prevents whole-subtree zombie-lock).
     A single cosmetic failure on a parent used to leave dependents stuck in
     `pending` forever, because `_deps_satisfied` only accepts `completed`,
     not `dead`. cascade-reap walks the tree and converts those zombie-pending
     dependents to `dead` with reason `parent_dep_died:<id>`:
     ```bash
     python3 "$TREE_STATE" --project-root "$(pwd)" cascade-reap > /tmp/rte_reap_$$.json
     ```
     If the JSON `count > 0`, log it to progress.log so the user can see what
     got swept. Do NOT die over this — cascade-reap is housekeeping, not failure.

2. Check for previously-detected terminal states (FIRST TIME ONLY — repeats
   are caught by Step 0's human-gate fast-exit so we don't re-surface them
   every /loop tick):
     if .research-tree/ROOT_FAILURE.md exists:
       Tell the user: every approach under root is dead. Show ROOT_FAILURE.md.
       Recommend pivot. Then raise the human-gate so subsequent /loop ticks
       cost zero tokens:
         python3 "$TREE_STATE" human-gate set --reason "ROOT_FAILURE: all approaches under root died — pivot via /idea-pipeline"
       STOP.
     if .research-tree/DONE.md exists:
       Tell the user: done_criteria satisfied, autopilot has stopped, branch
       passed programmatic validator AND external codex audit. Show DONE.md
       (which contains the human-review checklist). Do NOT auto-invoke any
       writing tool. Then raise the human-gate:
         python3 "$TREE_STATE" human-gate set --reason "DONE: charter done_criteria satisfied — human review then write paper"
       STOP and wait for the human.

3. Check budget:
     python3 "$TREE_STATE" budget-check
   If exit non-zero → run synthesize, report "budget exhausted, escalating to user",
   stop. Even in silent mode, surface this.

4. Pick the next leaf:
     next_id=$(python3 "$TREE_STATE" pick-next)
   If next_id == "NONE":
   - **v0.5.0 — try auto-raise first** before deciding what to surface:
     ```bash
     python3 "$TREE_STATE" human-gate auto-raise > /tmp/rte_autoraise_$$.json
     ```
     `auto-raise` writes an ALL_RUNNING gate **iff** there are running
     background nodes AND no actionable pending work. On the next /loop
     tick, Step 0's `auto-clear-if-stale` will unstick it the moment any
     running node finishes. This collapses "tree waiting on nohup" from
     "30 ticks burning a few KB each" to a single token-free fast-exit
     until something changes.
   - If `auto-raise` raised the gate (silent mode): exit silently. Default
     mode: print one line `[ALL_RUNNING — N background job(s) in flight,
     auto-gate raised; next tick self-clears on state change]`.
   - If `auto-raise` DID NOT raise (no running jobs at all → terminal):
     run synthesize, then read FINAL_REPORT.md's "Suggested next move"
     section and surface those options to the user (deepen winner /
     resolve alive / write paper via ARIS, OR pivot if all root dead).
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

7.5. **Auto-pivot detection** (v0.1.7 — signal_detector):
     SIGNAL_DETECTOR="$RTE_REPO/scripts/signal_detector.py"
     [ -f "$SIGNAL_DETECTOR" ] || SIGNAL_DETECTOR="$SKILL_DIR/../../scripts/signal_detector.py"
     python3 "$SIGNAL_DETECTOR" check-pivot --project-root "$(pwd)" --write-proposal > /tmp/rte_pivot_$$.json
     PIVOT_EXIT=$?
     if [ $PIVOT_EXIT -eq 10 ]; then
       # signal_detector found ≥1 junction where all/most completed siblings
       # came back NULL. It wrote .research-tree/AUTO_PIVOT_PROPOSAL.md
       # listing the dead-signal junctions. Read the proposal:
       PROPOSAL=".research-tree/AUTO_PIVOT_PROPOSAL.md"
       cat "$PROPOSAL"
       # For each dead-signal junction, spawn ONE expand cycle on that
       # junction with a re-framing prompt (proposer gets the
       # AUTO_PIVOT_PROPOSAL section as context and is told: "the existing
       # sibling approaches all came back null on the metric this junction
       # was meant to measure. Propose 2-4 RE-FRAMING candidates — same
       # root question, different angle of attack. Use placeholder
       # depends_on if a new approach needs a precursor data-acquisition
       # node. Mark anything that changes paper headline / venue / claim
       # wording as `task_type=framing-decision` + `human_only=true` so it
       # surfaces to the human instead of being auto-executed.")
       #
       # IMPORTANT: do NOT re-spawn the dead siblings or attempt to "fix"
       # the protocol that produced NULLs. The whole point of auto-pivot
       # is to recognize "this APPROACH is dead at this question" and
       # branch into a different framing.
       for jid in $(python3 -c "import json,sys; d=json.load(open('/tmp/rte_pivot_$$.json')); print(' '.join(j['parent_id'] for j in d['pivot_junctions']))"); do
         # Re-expand junction with re-framing intent. The expand subagent's
         # parent-context input includes the AUTO_PIVOT_PROPOSAL.md, so
         # the proposer knows it must offer pivot candidates not retries.
         /research-tree expand "$jid"
       done
       # After expanding, rename the proposal so next check-pivot cycle
       # does not re-trigger on the same junctions:
       mv "$PROPOSAL" ".research-tree/AUTO_PIVOT_PROPOSAL.handled.md"
       echo "$(date -Iseconds)  auto_pivot expanded junctions: $jid" >> .research-tree/progress.log
     fi
     rm -f /tmp/rte_pivot_$$.json

     **In silent mode**: do NOT surface the pivot proposal to the user
     unless one of the proposed re-framing candidates was tagged
     `human_only=true` (those are the paper-headline / venue / claim
     wording decisions only the human can make — see CLAUDE.md red
     lines). When such a candidate appears, surface it via a STUCK
     event (the silent-mode contract surfaces STUCK / DONE /
     ROOT_FAILURE only).

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
   - **--silent mode**: do NOT report per-step. Just write to progress.log and
     exit. Surface ONLY if: DONE.md was written this step, ROOT_FAILURE.md was
     written, budget exhausted, OR no new completed node in 10 consecutive
     steps (STUCK). Each of those events ALSO raises the human-gate (Step 0),
     so the message is delivered exactly once and subsequent /loop ticks are
     free.

11.5. **Session counter check** (v0.1.5; v0.1.8 raises human-gate automatically;
     v0.1.9 silent mode raises threshold to 80 via env var):
     ```bash
     # v0.1.9 — pass RESEARCH_TREE_SILENT=1 when invoking from `autopilot --silent`
     # so the threshold default flips from 10 → 80 (≈ 40 hours unattended capacity).
     if [[ "$AUTOPILOT_MODE" == *silent* ]]; then export RESEARCH_TREE_SILENT=1; fi
     # v0.4.0 — same-session detection is via $RESEARCH_TREE_SESSION_ID, not the
     # old PPid chain. autopilot is supposed to set this once per Claude Code
     # session; if it's missing, set it here so the first tick of a fresh
     # session gets a stable id. Subsequent ticks (same session) inherit it via
     # /loop's process env; ticks from a new Claude Code session (e.g. after
     # IDE restart) get a fresh uuid and the counter resets cleanly.
     if [ -z "${RESEARCH_TREE_SESSION_ID:-}" ]; then
       export RESEARCH_TREE_SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex)")
     fi
     python3 "$TREE_STATE" session-step increment > /tmp/rte_session_$$.json
     SESSION_EXIT=$?
     ```
     If `SESSION_EXIT != 0` (i.e., `should_pause: true`), the session has accumulated
     enough steps that the main Claude Code context is getting heavy. **v0.1.8: the
     session-step command itself raises the human-gate sentinel on first threshold
     hit** — so subsequent /loop ticks short-circuit at Step 0 (zero tokens). Here at
     Step 11.5 the autopilot only needs to surface the message ONCE on the
     triggering tick:
     - default mode: print the verbatim block below.
     - `--silent` mode: print nothing (just exit). The gate is up; the user will
       see the awaiting-human state next time they look at the project, and
       can opt to inspect `.research-tree/AWAITING_HUMAN.md` then. This is the
       v0.1.8 contract: silent runs cost zero main-context tokens while paused.

     Default-mode message:
     ```
     Session context approaching capacity (10+ autopilot steps in this session).
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
