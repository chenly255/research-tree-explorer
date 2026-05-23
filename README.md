# research-tree-explorer

**Autonomous tree-shaped research exploration for Claude Code.**

A Claude Code skill that turns "I have an idea, explore it" into a multi-day autonomous run: the agent branches at every major decision point (architecture, experiment design, narrative angle), tries each branch in an isolated subagent, has a cross-model reviewer audit each junction, prunes dead branches with recorded reasons, and synthesizes a final report covering the whole tree — including the failures.

You hit `/research-tree init "<your idea>"`, walk away, and come back to `FINAL_REPORT.md`.

**v0.1.3 — hardline anti-laziness**: the project's research charter is enforced by a programmatic validator (checks filesystem: test-split hash, ≥3 seed checkpoints, ablation dirs, metrics.json fields) and a fresh-thread external codex auditor — *not* just by prompt. Branches that fabricate RESULT.md text without backing files get marked dead automatically. When all gates pass, autopilot writes `DONE.md` and STOPS for human review (no auto-paper-writing).

**v0.1.4 — survives session restart**: long-running training is launched with `nohup` and registered in `EXECUTOR.json` (PID + log path). Closing the IDE no longer kills the work. When you reopen and run `/research-tree resume`, the new `stale_running_handler.py` scans every `status=running` node, checks PID liveness, and routes finished work through the validation chain — so cross-session recovery is automatic.

**v0.1.5 — smart branching cadence**: the proposer no longer forces 2-4 fake-different candidates at every node — at depth ≥1 it can return `skip_expansion: true` for canonical steps (no design choice → execute directly). `autopilot --continuous` chains steps back-to-back without `/loop`'s 30-min sleep until every live node is blocked on background work or the session step counter (default 20) hits its threshold, at which point you're told to restart Claude Code for a clean context. Best of both: `/loop 30m /research-tree autopilot --continuous --silent` chews through quick chained work between `/loop` ticks but never accumulates more than ~20 steps of context per session.

---

## What this is, in one diagram

```
                    "Build a model that learns groups of cells as the basic unit"
                                              │
                                              ▼
                                    ┌─ /research-tree init ─┐
                                    │                       │
                                    ▼                       ▼
                                root: idea statement
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
        approach: set        approach: perceiver   approach: GNN
        transformer          IO cross-attention    on cell graph
                │                   │                   │
                │  pilot              │ pilot               │ pilot
                ▼                   ▼                   ▼
         RESULT.md            RESULT.md            DEAD.md
         score 0.71           score 0.78           "no signal,
                              ◀── junction audit ──    too sparse"
                              winner: perceiver
                                    │
                ┌───────────────────┼───────────────────┐
                ▼                                       ▼
        ablation: scale up                   ablation: add equivariance
                │                                       │
                ▼                                       ▼
         RESULT.md                              RESULT.md
         score 0.82 ★                           score 0.79

                                              ▼
                                    /research-tree synthesize
                                              │
                                              ▼
                                    .research-tree/FINAL_REPORT.md
                            (winner + dead-branch atlas + next move)
```

The tree shape, the branch decisions, and the prune/deepen calls are made by the agent — not by you. You provide the root idea and read the final report.

## Why this exists (and how it relates to other tools)

[Sakana AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) (`agentic tree search`, peer-reviewed paper at ICLR workshop, then Nature) is the gold-standard implementation of this idea — but it's a standalone Python framework that calls model APIs directly, not Claude Code native.

[ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) is Claude Code native and excellent, but it's a **linear pipeline** with adversarial review at each stage — no explicit branching or backtracking. Great when you already know which idea to pursue.

`research-tree-explorer` is the missing piece for users who want:
- the **deep tree exploration** of AI-Scientist
- inside the **Claude Code skill ecosystem** so it composes with everything else (Tavily, codex MCP, your own skills)
- with **explicit cross-session state** so a single run can span days

It's complementary, not competitive. The roadmap includes letting a branch's `execute` step invoke ARIS's `/research-pipeline` as a heavyweight option when you want the full ARIS treatment on a winning leaf.

## When to use it

Good fit:
- A research direction with a **quantifiable per-branch signal** — a metric, a pass/fail experiment, an artifact you can score. ML, computational biology, simulation, optimization.
- A problem space where **multiple distinct approaches are plausible** and you want to triage cheaply before committing GPU/time to one.
- A multi-day timeline where you'd otherwise check in every couple of hours.

Bad fit:
- Pure literature surveys with no experiments → use a survey-writing tool.
- Single-approach implementation work where there are no real forks → use plain Claude Code or [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep).
- Anything that needs sub-second iteration → the orchestration overhead per step is meaningful.

## Install

Requires:
- Claude Code (CLI or IDE extension), authenticated
- Python 3.8+ on the path
- **REQUIRED (v0.1.3): `mcp__codex__codex` MCP server configured.** The external codex audit is now a hard dependency — autopilot fail-CLOSES (marks the branch dead with `death_reason="external_audit_unavailable"`) if codex doesn't respond. This is intentional: the user explicitly requested that branches NEVER reach `completed` status without an independent cross-model audit. If you don't want this, fork and re-enable fail-open in `SKILL.md` step 6c.3.
- `openssl` on PATH (for `openssl rand -hex 32` to generate audit nonces).
- Optional: `mcp__tavily__tavily_search` MCP server (for literature scans during expansion)

```bash
git clone https://github.com/chenly255/research-tree-explorer.git
cd research-tree-explorer
bash scripts/install.sh
```

The installer:
1. Symlinks `skills/research-tree/` into `~/.claude/skills/research-tree`
2. Adds `export RESEARCH_TREE_REPO=...` to your shell rc so helpers are findable

Open a new Claude Code session — `/research-tree` should appear in the skill list.

## How to use it

### The minimum viable run

```bash
cd /path/to/your/project
```

In Claude Code:

```
/research-tree init "Your research direction in one or two sentences. Be concrete enough that an outside reader could explain what 'success' would look like."
/research-tree autopilot
```

`autopilot` does **one step** — it picks the next leaf, dispatches the appropriate subagent, writes to disk, returns a one-paragraph summary. To run continuously:

```
/loop 30m /research-tree autopilot
```

This invokes one autopilot step every 30 minutes for as long as the loop runs. State persists across loop iterations and across session restarts — `.research-tree/tree.json` is the durable truth.

### Optional but recommended: write a RESEARCH_BRIEF.md first

If `<project>/RESEARCH_BRIEF.md` exists when you run `init`, every subagent gets it as shared context. Worth investing 10 minutes here to get sharper branches.

```markdown
# Research Brief

## Goal
<one paragraph: what you want to build, what venue / outcome you're aiming for>

## Constraints
- Compute: <e.g., 4 × A800 here, H100 cluster reachable via rsync>
- Data: <pointers to where it lives, what's preprocessed>
- Time: <e.g., 1 month for a Nature Methods submission>
- Avoid: <approaches you've ruled out and why — saves the tree from re-discovering>

## Known priors
1. <observation that should bias initial branching>
2. <constraint that subsequent ablations should respect>

## Evaluation
- <downstream task 1> — baseline number from <citation>
- <downstream task 2> — baseline number from <citation>
```

### Checking on progress

```
/research-tree status
```

Prints an ASCII tree plus stats. Or, for a richer view:

```
cat .research-tree/FINAL_REPORT.md     # most recent synthesis
tail -20 .research-tree/progress.log   # last 20 orchestration steps
```

Or open `.research-tree/tree.json` in any JSON viewer.

### Manual control

You're not locked out — you can steer the tree at any time:

```
/research-tree prune <node_id> "this branch is going nowhere because <reason>"
/research-tree expand <node_id>            # force a re-expansion at a node
/research-tree execute <node_id>           # force a re-run of one branch
/research-tree audit <junction_id>         # force an audit
/research-tree synthesize                  # regenerate the final report
```

If you change your mind about a dead branch, `python3 scripts/tree_state.py set <id> status=pending` puts it back in queue.

## What it produces

After a run, `<project>/.research-tree/` contains:

```
.research-tree/
├── tree.json                    # single source of truth — the whole tree
├── progress.log                 # one line per orchestration step
├── FINAL_REPORT.md              # human-readable synthesis (regenerated each `synthesize`)
├── DONE.md                      # written when winner passes all enforcement layers — read this and decide whether to write the paper yourself
├── branches/
│   ├── 1/
│   │   ├── RESULT.md            # parseable: METRIC=<float>, ends with charter compliance table
│   │   ├── data/test_split.json # held-out test set with hash (required, validator-checked)
│   │   ├── checkpoints/seed_*/  # ≥3 dirs, each with .pt/.pth/.safetensors (validator-checked)
│   │   ├── metrics.json         # param_count ≥10M, seeds ≥3, per-task metric/std/p_value (validator-checked)
│   │   ├── ablations/*/         # ≥4 subdirs each with result.json (validator-checked)
│   │   ├── requirements.txt     # env lock (validator-checked)
│   │   ├── KILL_ARGUMENT.md     # required when claiming DONE_READY=true
│   │   ├── VALIDATION.json      # charter_validator output (PASS/WARN/FAIL + evidence)
│   │   ├── CODEX_AUDIT.json     # fresh-thread external auditor verdict
│   │   └── <other artifacts>
│   ├── 2/
│   │   └── DEAD.md              # reason this branch was abandoned (validator fail / codex fail / charter fail / honest blocker)
│   └── ...
├── audits/
│   └── audit-001.json           # codex verdict on junction "root"
└── reflections/
    └── 005.md                   # self-audit at step 5
```

Every branch — alive, completed, dead — keeps its artifacts. The dead branches are the supplementary atlas of any eventual paper.

## Default budgets

Set at `init` time, capped to prevent runaway runs:

| Budget | Default | Override |
|---|---|---|
| `max_depth` | 5 | `--max-depth N` |
| `max_branches_per_junction` (alive only — dead branches free their slot) | 4 | `--max-branches N` |
| `max_total_nodes` | 30 | `--max-total-nodes N` |
| `max_gpu_hours_total` | 48.0 | `--max-gpu-hours N` |

These are advisory — the state machine refuses to add nodes that would exceed them, but the actual GPU usage is tracked by your branches reporting their costs in `RESULT.md`.

## Architecture in 100 words

Three layers: a markdown skill (`SKILL.md`) that drives the main agent's behavior, a set of Python CLIs (`tree_state.py`, `synthesize_report.py`, `charter_validator.py`) that enforce state invariants and anti-laziness rules, and an on-disk state directory (`.research-tree/`) that is the single source of truth. Heavy work happens in subagents — branch execution, candidate proposal, junction audit — so the main orchestrator's context never grows with tree size. `autopilot` is single-step by design: each invocation reads state from disk, dispatches one action, writes back, returns. Continuous operation is achieved by wrapping with `/loop`. Full details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Anti-laziness: two enforcement layers + structural hardening (v0.1.3)

LLMs are good at *claiming* compliance and bad at *delivering* it under pressure. This tool refuses to trust the subagent's word. Every gate is **structural** (filesystem checks, status-machine locks, cryptographic nonces) — not just "the prompt told Claude to be careful".

1. **Programmatic validator** (`scripts/charter_validator.py`) — runs after every branch. Checks the **filesystem**:
   - `data/test_split.json` with required keys (test_ids, hash, created_at)
   - ≥3 `checkpoints/seed_*/` dirs, each with a real checkpoint file ≥ 1024 bytes
   - **Cross-check**: total checkpoint bytes per seed ≥ claimed `param_count` × 1 byte (catches "claimed 25M params but file is 50 KB" lies)
   - `metrics.json` with `param_count` ≥ 10M, `seeds` list ≥ 3, per-task `metric`/`std`/`baseline_score`/`p_value`, `gpu_hours_used`, `wall_clock_hours`
   - ≥4 `ablations/` subdirs, each with a non-empty result file
   - `requirements.txt` or `environment.yml`
   - RESULT.md charter compliance table parsed — any strict rule that is not PASS is a FAIL (WARN on strict = FAIL too)
   - If `DONE_READY=true`: additionally requires `KILL_ARGUMENT.md` and all strict rules at PASS

2. **External codex audit with anti-forgery**:
   - Orchestrator writes a fresh random nonce to `AUDIT_NONCE` before calling codex (subagent never sees this nonce)
   - Codex (fresh thread, never codex-reply) must echo the nonce back in `CODEX_AUDIT.json`
   - Codex must list every file it read with its SHA256; validator re-hashes the actual files and rejects the audit if hashes don't match
   - Validator runs second pass with `--require-codex-audit --audit-nonce-file ...`
   - This blocks the "subagent pre-writes `CODEX_AUDIT.json` with `verdict: PASS`" bypass

3. **Status state machine locked**:
   - `tree_state.py set` refuses to write `status=` at all. Status transitions go through dedicated commands: `complete --validator-report X --score Y` (refuses unless validator report verdict=PASS), `die --reason X`, `running`, `reopen`
   - `complete` records the SHA256 of the validator report as `completion_proof` on the node; later forgery of the report is detectable
   - `synthesize_report.py` requires `completion_proof` (not just `status=completed`) to trigger DONE.md, so flipping `done_ready` in tree.json alone is not enough

4. **Fail-CLOSED when codex unavailable**: if the codex MCP doesn't respond, the branch is `die`d with `death_reason="external_audit_unavailable"`. Never fail-open. Codex MCP is a HARD dependency for v0.1.3.

5. **Concurrency safe**: every state-mutating command takes an exclusive `flock` on `.research-tree/tree.lock`. Two parallel autopilots can't produce duplicate IDs or lost writes.

Either enforcement layer's failure → branch is `die`d with a specific `death_reason`, no LLM negotiation. The orchestrator **never** re-spawns the subagent to "fix" a validator failure — by design that defeats the purpose.

When all gates pass on a branch that self-attests `DONE_READY=true`, autopilot writes `DONE.md` (with a human-review checklist) and STOPS. The user reviews artifacts manually and decides whether to write the paper — autopilot does NOT auto-invoke any writing tool.

## Limitations and roadmap

| Status | Item |
|---|---|
| ✓ working | tree expansion, branch execution via subagent, junction audit via codex, dead-branch tracking, multi-depth deepening, cross-session resume |
| ⚠ caveat | best on goals with a quantifiable per-branch metric; pure-narrative work (writing a survey) doesn't tree well |
| ⚠ caveat | codex MCP is a HARD dependency in v0.1.3 — without it, every branch fail-closes to `dead` with `death_reason="external_audit_unavailable"` |
| 🚧 roadmap | cross-machine GPU dispatch (currently you manually rsync a winning branch to a beefier machine) |
| 🚧 roadmap | native integration with ARIS as a heavyweight `execute` option |
| 🚧 roadmap | web UI dashboard reading `tree.json` over SSH |
| 🚧 roadmap | `tree.json` schema versioning + migrations |

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

This tool exists because [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) by `@wanshuiyin` set the bar for what a Claude-Code-native research harness looks like, and [Sakana AI](https://sakana.ai)'s AI-Scientist-v2 showed that agentic tree search is a viable shape for autonomous research. Both repos are in `refs/` (gitignored) as references during development.
