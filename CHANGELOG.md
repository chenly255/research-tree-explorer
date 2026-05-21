# Changelog

All notable changes to this project will be documented here.

## [0.1.0] — 2026-05-21

Initial public release.

### Added
- `/research-tree` skill with subcommands: `init`, `expand`, `execute`, `audit`, `prune`, `status`, `synthesize`, `autopilot`, `resume`
- `scripts/tree_state.py` — state machine CLI for the tree (JSON-backed, atomic writes)
- `scripts/synthesize_report.py` — FINAL_REPORT.md generator covering winners, dead-branch atlas, junction audits, suggested next move
- `scripts/install.sh` — symlinks skill into `~/.claude/skills/research-tree` and exports `RESEARCH_TREE_REPO` env
- Branch-proposer subagent pattern: `/research-tree expand` spawns an isolated agent to generate candidates so the main orchestrator's context stays clean
- Single-step `autopilot` semantics: each invocation does one orchestration action and returns; continuous runs are produced by wrapping with the external `/loop` skill
- Junction audit via `mcp__codex__codex` in fresh threads (never `codex-reply`)
- `.research-tree/progress.log` — append-only orchestration log that survives context compaction and session restart
- `.research-tree/reflections/` — periodic self-audit notes (every 5 autopilot steps)
- `max_branches_per_junction` caps alive children only; dead branches free their slot
- `examples/toy_classification/` — end-to-end smoke test with 4 classifier branches + 1 ablation + 1 junction audit
- `tests/test_tree_state.sh` — state-machine unit/smoke tests
- `docs/ARCHITECTURE.md` — design rationale, three-layer model, subagent boundaries, state schema
- `CONTRIBUTING.md` — how to extend or fix the project
