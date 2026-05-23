# Changelog

All notable changes to this project will be documented here.

## [0.1.4] — 2026-05-23

Cross-session-restart survival. Previous versions ran branch experiments in
the foreground inside a Claude Code subagent — closing the IDE killed the
training process and left tree.json with an orphan `status=running` node
that nothing knew how to recover. v0.1.4 makes long-running work survive
session restarts.

### Added
- `scripts/stale_running_handler.py` — programmatic scan of all
  `status=running` nodes. For each node, reads `EXECUTOR.json`, checks
  whether the PID is still alive via `os.kill(pid, 0)`, and classifies
  into one of 5 buckets:
  - `alive` — process still running, leave alone
  - `ready_for_validation` — PID dead AND RESULT.md present, run validation chain
  - `ready_for_death_from_file` — PID dead AND DEAD.md present, mark dead
  - `abandoned` — PID dead AND no output files, mark dead with executor log pointer
  - `legacy_orphan` — no EXECUTOR.json at all (pre-v0.1.4 code path), mark dead
- `tests/test_stale_running_handler.sh` — 8 test cases covering all 5
  classification buckets, invalid PID, malformed EXECUTOR.json, and
  ignoring non-running nodes.
- SKILL.md `autopilot` step 1.5: runs the stale handler at the start of
  every cycle, dispatches each bucket programmatically (`die` for
  abandoned/legacy/death-from-file, validation chain for
  ready_for_validation, log for alive). This recovery work takes
  priority over picking the next leaf.

### Changed
- **`execute` subagent must launch long work with `nohup`** (v0.1.4
  mandate). The subagent prompt now requires: any task expected to
  exceed 60 seconds (training, downloads, HP sweeps) MUST be detached
  with `nohup bash train.sh > executor.log 2>&1 &`, and the PID/log path
  written to `EXECUTOR.json` IMMEDIATELY. The subagent then returns to
  the orchestrator without waiting. The training process survives the
  Claude Code session; `stale_running_handler.py` picks it up on a
  later autopilot cycle when the PID is dead and RESULT.md is present.
- `execute` step 6 now has a new `6.0` background-detection gate: if
  EXECUTOR.json exists, PID is alive, and no RESULT.md/DEAD.md yet, the
  autopilot step ends immediately leaving the node in `running` state.
  No premature validation, no premature death.
- Pure-compute tasks under 60 seconds may still run foreground (no
  EXECUTOR.json needed). The orchestrator distinguishes the two modes
  by EXECUTOR.json presence.

## [0.1.3] — 2026-05-23

Hardline anti-laziness enforcement, hardened against codex's own adversarial
review. The charter is no longer just a prompt: a programmatic validator +
a fresh-thread external codex auditor gate every branch before it can be
marked `completed`. Auto-handoff to ARIS removed — DONE now stops at human
review by design. Every gate is structural (filesystem checks, status-machine
locks, cryptographic nonces) rather than prompt-level, so a lazy subagent
cannot bypass enforcement by misreading SKILL.md.

### Added
- `scripts/charter_validator.py` — programmatic charter checker, independent
  of any LLM. Reads the branch_dir filesystem and verifies:
  - `data/test_split.json` exists with required keys (`test_ids`, `hash`, `created_at`)
  - `checkpoints/seed_*/` ≥ 3 directories, each with a real checkpoint file
  - `metrics.json` includes `param_count` ≥ 10M, `seeds` list ≥ 3, every
    downstream task has `metric`/`std`/`baseline_score`/`p_value`, plus
    `gpu_hours_used` and `wall_clock_hours`
  - `ablations/` has ≥ 4 subdirs each with a result file
  - `requirements.txt` or `environment.yml` exists
  - RESULT.md contains the charter compliance table, every strict rule
    parses as PASS (WARN or FAIL on a strict rule = overall FAIL)
  - With `--require-codex-audit`: `CODEX_AUDIT.json` exists with verdict=PASS
  - If `DONE_READY=true`: `KILL_ARGUMENT.md` must exist AND every strict
    rule must be PASS (no WARN tolerated)
  Exit codes: 0=PASS, 1=WARN, 2=FAIL.
- `tests/test_charter_validator.sh` — 20 cases covering perfect branch,
  every individual cheat (missing test_split, 2 seeds, small param count,
  missing p_value, <4 ablations, missing env file, fake charter table,
  missing codex audit, DONE_READY without kill-argument, soft-rule FAIL).
- SKILL.md `execute` now has explicit steps 6a–6d: triage → programmatic
  validator → fresh-thread codex audit → final validator with codex result.
  Each gate failure → `set status=dead` with a specific `death_reason`.
  The orchestrator NEVER re-spawns the subagent to "try again" on a
  validator failure — programmatic FAIL is final.

### Changed
- **DONE no longer auto-invokes ARIS `/paper-writing`.** When all
  enforcement layers pass, autopilot writes DONE.md and STOPS. DONE.md
  now contains an explicit human-review checklist (walk artifacts, read
  CODEX_AUDIT.json, compare to dead-branch atlas). The human writes the
  paper themselves after manual review of the model/algorithm.
- `synthesize_report.py` DONE.md template rewritten for human hand-off
  tone instead of "auto-handoff to ARIS".
- SKILL.md `execute` subagent prompt now bluntly tells the agent: a
  validator will check the physical files after you return, fabricated
  RESULT.md without backing files = dead branch. Also enumerates the
  required physical artifacts (test_split.json, checkpoints/seed_*,
  metrics.json fields, ablations/, requirements.txt, KILL_ARGUMENT.md
  if DONE_READY).
- SKILL.md `init` paragraph to user now mentions both enforcement layers
  explicitly so the user understands what's protecting against laziness.
- `--silent` mode behavior updated: DONE event now surfaces "autopilot
  stopped, awaiting human review" instead of "auto-handoff to ARIS".

### Removed
- Auto-invocation of ARIS `/paper-writing` from autopilot step 12.
- All references to writing the paper from inside autopilot — paper
  writing is now strictly human-driven, per user policy.
- `set <id> status=completed` / `set <id> status=dead` — `set` cannot
  change status anymore. Use `complete --validator-report ... --score ...`,
  `die --reason ...`, `running`, or `reopen`. This closes the bypass
  where a subagent could skip the validator chain by directly setting
  `status=completed`.

### Hardened (after codex adversarial review found these holes)
- **Status state machine locked**: tree_state.py introduces `complete`,
  `die`, `running`, `reopen` commands. `complete` requires a JSON
  validator report file path with `verdict=PASS`; otherwise it refuses.
  The report's SHA256 is recorded as `completion_proof` on the node, so
  later forgery of the report is detectable.
- **synthesize_report.py DONE detection** now requires `completion_proof`
  in addition to `done_ready=true`, so an attacker who somehow flipped the
  status flag still cannot trigger DONE.md without a valid proof.
- **Checkpoint physical-size floor**: `charter_validator.py` requires each
  seed's total checkpoint size ≥ 1024 bytes (catches `touch model.pt`)
  AND ≥ 1 byte per claimed parameter (catches "claimed 25M params but
  the .pt file is 50 KB" — the file cannot physically hold that many
  parameters).
- **Codex audit anti-forgery**: orchestrator writes a fresh random
  `AUDIT_NONCE` to disk before calling codex; codex must echo the nonce
  back in `CODEX_AUDIT.json`. Additionally, codex must list every file
  it read with its SHA256; the validator re-hashes those files and
  rejects the audit if hashes don't match. This blocks a subagent from
  pre-writing `CODEX_AUDIT.json` before the orchestrator calls codex.
- **Fail-CLOSED when codex unavailable**: if the codex MCP server doesn't
  respond, the branch is marked dead with
  `death_reason="external_audit_unavailable"`. Never fail-open. Codex
  MCP is now a HARD dependency for v0.1.3 (documented in README install
  requirements).
- **Validator WARN promotes to dead**: previously WARN kept the branch
  alive-but-flagged. Now WARN also triggers `die` with the warning as
  the death reason. Strict-by-default; the user can `reopen` if they
  disagree.
- **State machine concurrency**: `tree_state.py` wraps every state-mutating
  command in `flock(.research-tree/tree.lock)`, preventing duplicate IDs
  and lost writes when two autopilot processes accidentally run in
  parallel (e.g., `/loop` invoked twice). Tmp file naming switched to
  per-PID + UUID suffix as belt-and-suspenders.

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
