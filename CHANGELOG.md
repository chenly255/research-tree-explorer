# Changelog

All notable changes to this project will be documented here.

## [0.1.2] — 2026-05-21

Real autonomous operation: anti-laziness charter, silent mode, automatic
DONE detection with handoff to ARIS for paper writing.

### Added
- `templates/RESEARCH_CHARTER.md` — the **anti-laziness constitution** every
  branch's subagent reads. Defines strict rules on data (full-data mandate,
  held-out test set, no leakage), architecture (diversity at depth 0, parameter
  floor, strong baseline required), training (multi-seed, convergence,
  hyperparam sweep), evaluation (all downstream tasks, statistical significance),
  ablations (headline component, scale, data, cross-batch), novelty (citation
  required, /kill-argument audit), reproducibility, compute honesty. Branches
  that FAIL any strict rule are marked dead with `death_reason="charter_violation"`
  regardless of how good their metric looks.
- Charter is auto-installed at `init` time if missing — user is told to edit
  defaults (venue, downstream tasks, done_criteria) before running autopilot.
- `RESULT.md` must end with a charter compliance audit table (PASS/WARN/FAIL per
  rule). The orchestrator parses it and demotes the branch to dead on any
  strict FAIL.
- `done_ready=true` field on nodes. When a branch's subagent self-attests
  charter compliance + threshold met, it sets `DONE_READY: true` in RESULT.md
  and the orchestrator records it. Synthesize detects this and writes
  `.research-tree/DONE.md`.
- `autopilot` auto-invokes ARIS `/paper-writing` on the winner when DONE.md
  appears, plus dead-branch atlas as supplementary material.
- `--silent` flag on autopilot: no per-step summaries; surfaces only on DONE,
  ROOT_FAILURE, or STUCK (20 consecutive steps without a new completed node).
- Global CLAUDE.md §9 routing: phrases like "通宵跑 / 不要打扰 / 放着别管 /
  直到投稿" auto-wrap with `/loop 30m ... --silent`. Physical limitation of
  Claude Code's no-true-background-daemon model is documented; `task-monitor`
  email notifications recommended for hands-off operation.

### Changed
- `synthesize_report.py` checks for DONE before the existing root-failure /
  three-way-handoff cases — DONE takes priority.
- `init` step now copies the default RESEARCH_CHARTER.md template if missing.
- `expand` subagent prompt mandates obeying charter-required diversity at each
  depth.
- `execute` subagent prompt pastes the full charter and forces RESULT.md to
  contain the compliance audit table.

## [0.1.1] — 2026-05-21

Tighter integration with sibling skills (idea-pipeline upstream, ARIS downstream)
without expanding scope. The skill now acts as a clean middle layer in the
fractal research workflow.

### Added
- Three-way handoff at tree convergence in `FINAL_REPORT.md` Suggested next move:
  (a) deepen the winner further, (b) resolve remaining alive branches first,
  (c) hand off to ARIS `/paper-writing` with winner + dead-branch atlas
- Automatic root-failure detection: when all direct children of root are dead,
  the synthesizer writes `.research-tree/ROOT_FAILURE.md` and the `FINAL_REPORT.md`
  Suggested next move switches to PIVOT mode (archive tree, re-run idea-pipeline)
- `autopilot` now runs `synthesize` at the end of every step (idempotent, cheap)
  so root-failure and 3-way handoff trigger immediately, not just when the user
  manually runs synthesize
- `autopilot` checks for `ROOT_FAILURE.md` at the start of each step and stops
  with a clear recommendation if it exists

### Changed
- Renumbered the autopilot step sequence (1-11) for clarity
- Better SKILL.md prose around how the tool fits with sibling skills

## [0.1.0] — 2026-05-21

Initial public release. See git log for full feature list.
