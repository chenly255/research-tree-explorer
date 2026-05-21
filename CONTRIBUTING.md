# Contributing

Thanks for poking at this. The project is small and pragmatic — most contributions land in one of three places.

## What lives where

```
research-tree-explorer/
├── skills/research-tree/SKILL.md       ← the prompt that drives Claude
├── scripts/tree_state.py               ← state machine CLI
├── scripts/synthesize_report.py        ← FINAL_REPORT.md generator
├── scripts/install.sh                  ← symlinks skill into ~/.claude/skills/
├── tests/test_tree_state.sh            ← state-machine smoke tests
├── examples/toy_classification/        ← runnable end-to-end example
├── docs/                               ← architecture + applied recipes
└── refs/                               ← (gitignored) external repos for reference
```

Changes to the prompt (`SKILL.md`) are the highest-leverage contributions — they directly shape what Claude does. Changes to the Python CLIs are next — they enforce invariants the prompt can't. Changes to docs and examples help users without changing behavior.

## Quick start for hacking

```bash
git clone https://github.com/<your-fork>/research-tree-explorer.git
cd research-tree-explorer
bash scripts/install.sh              # symlinks ~/.claude/skills/research-tree
bash tests/test_tree_state.sh        # smoke tests should pass
```

To test prompt changes, edit `skills/research-tree/SKILL.md` and open a new Claude Code session — the symlink picks up your edits live.

## Running the example end to end

```bash
cd examples/toy_classification

# Regenerate the synthetic dataset if missing
test -f data.npz || python -c "
from sklearn.datasets import make_classification
import numpy as np
X, y = make_classification(n_samples=400, n_features=10, n_informative=5,
                           n_redundant=2, n_classes=3, random_state=42)
np.savez('data.npz', X=X, y=y)
"

# In Claude Code:
/research-tree init "Find the best classifier on data.npz (400 samples, 10 features, 3 classes). Maximize 5-fold cross-validated macro-F1. Budget: 30s per branch."
/research-tree autopilot     # one step at a time
# OR
/loop 10s /research-tree autopilot   # continuous

# After it converges:
cat .research-tree/FINAL_REPORT.md
```

The expected outcome is documented in `examples/toy_classification/RUN.md`.

## Tests

Two layers:

1. **`tests/test_tree_state.sh`** — fast smoke tests for the Python CLI. Run after every change to `tree_state.py` or `synthesize_report.py`. Should always be green.

2. **The toy example** — a real end-to-end run that spawns subagents. Use this to verify SKILL.md prompt changes haven't broken the dispatch logic. Slower (a few minutes) and consumes Claude API tokens; run it before sending a PR that touches the prompt.

There is no CI yet — the test corpus is small enough that contributors can run it locally.

## Code conventions

- **Python**: 3.8+, stdlib only for the state machine. No new heavy dependencies without a strong reason. Type hints encouraged but not required.
- **SKILL.md**: keep imperative voice ("Read X, then call Y"). When you add a new subcommand, follow the structure of existing ones: name, one-line purpose, numbered steps, then "Failure modes to refuse" if it has any.
- **Tests**: bash + plain commands. No test framework. A failed assertion `exit 1`s the script.
- **No emojis in code or commit messages unless the user explicitly wanted them.**

## What kinds of PRs are welcome

- Better branch-picking heuristics in `pick-next` (e.g., UCB-style exploration vs exploitation)
- Additional `kind`s of branching beyond the current 6
- Schema migrations for `tree.json` (with a `schema_version` bump and migration path)
- Integration shims with other research tools (ARIS, AI-Scientist, etc.)
- Better visualization of the tree (HTML viewer, mermaid export, etc.)
- More example projects (showing the tool on different research domains)
- Hardening: better error handling, retry logic, defensive guards in the state machine

## What kinds of PRs need a discussion first

Open an issue before sending these, since they touch load-bearing design:

- Anything that lets the orchestrator's main context grow with tree size (e.g., merging multiple autopilot steps into one invocation — the current single-step semantic is deliberate)
- Anything that asks the user to make technical decisions at runtime (the skill is opinionated about *not* doing this)
- Changing the tree.json schema in a non-additive way without a migration

## Reporting bugs

Open an issue with:
1. What you ran (`/research-tree <subcommand> ...`)
2. What you expected
3. What happened (paste `.research-tree/progress.log` + the relevant `RESULT.md` / `DEAD.md` / audit JSON)
4. Your Claude Code version and OS

The state on disk is usually enough to reproduce; if it's not, attach a tarball of `.research-tree/`.
