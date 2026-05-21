# research-tree-explorer

**Autonomous tree-shaped research exploration for Claude Code.**

A Claude Code skill that turns "I have an idea, explore it" into a multi-day autonomous run: the agent branches at every major decision point (architecture, experiment design, narrative angle), tries each branch in an isolated subagent, has a cross-model reviewer audit each junction, prunes dead branches with recorded reasons, and synthesizes a final report covering the whole tree — including the failures.

You hit `/research-tree init "<your idea>"`, walk away, and come back to `FINAL_REPORT.md`.

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
- Optional but recommended: `mcp__codex__codex` MCP server configured (for cross-model junction audits)
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
├── branches/
│   ├── 1/
│   │   ├── RESULT.md            # parseable: ends with METRIC=<float>
│   │   ├── fit_script.py        # whatever the branch's subagent wrote
│   │   └── <artifacts>
│   ├── 2/
│   │   └── DEAD.md              # reason this branch was abandoned
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

Three layers: a markdown skill (`SKILL.md`) that drives the main agent's behavior, a set of Python CLIs (`tree_state.py`, `synthesize_report.py`) that enforce state invariants, and an on-disk state directory (`.research-tree/`) that is the single source of truth. Heavy work happens in subagents — branch execution, candidate proposal, junction audit — so the main orchestrator's context never grows with tree size. `autopilot` is single-step by design: each invocation reads state from disk, dispatches one action, writes back, returns. Continuous operation is achieved by wrapping with `/loop`. Full details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Limitations and roadmap

| Status | Item |
|---|---|
| ✓ working | tree expansion, branch execution via subagent, junction audit via codex, dead-branch tracking, multi-depth deepening, cross-session resume |
| ⚠ caveat | best on goals with a quantifiable per-branch metric; pure-narrative work (writing a survey) doesn't tree well |
| ⚠ caveat | codex MCP must be configured for audits; without it, autopilot still runs but skips audit steps with a warning |
| 🚧 roadmap | cross-machine GPU dispatch (currently you manually rsync a winning branch to a beefier machine) |
| 🚧 roadmap | native integration with ARIS as a heavyweight `execute` option |
| 🚧 roadmap | web UI dashboard reading `tree.json` over SSH |
| 🚧 roadmap | `tree.json` schema versioning + migrations |

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

This tool exists because [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) by `@wanshuiyin` set the bar for what a Claude-Code-native research harness looks like, and [Sakana AI](https://sakana.ai)'s AI-Scientist-v2 showed that agentic tree search is a viable shape for autonomous research. Both repos are in `refs/` (gitignored) as references during development.
