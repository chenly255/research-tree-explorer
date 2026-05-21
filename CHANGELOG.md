# Changelog

All notable changes to this project will be documented here.

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
