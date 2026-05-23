# Changelog

All notable changes to this project will be documented here.

## [0.1.7] — 2026-05-23

Two upgrades that close the autopilot's biggest remaining unattended-run
gaps: (1) autopilot can now actually **pull external data** for itself
(CELLxGENE Discover + GEO + figshare templates plus a discover helper),
and (2) it programmatically detects "this approach is dead" via
`signal_detector.py` and writes an auto-pivot proposal that re-frames the
question instead of retrying the dead protocol. Together these address
the sc-bias Stage 1 failure mode where a strong pilot signal led to
wholesale buy-in and a paper-framing rewrite when cross-atlas validation
came back null.

### Added
- `examples/data-acquisition/` — three copy-and-edit templates that
  produce the exact `DATA_MANIFEST.json` schema
  `charter_validator.py --task-type data-acquisition` requires:
  - `cellxgene_discover.py` — search / list-collection / inspect-dataset
    over CELLxGENE Discover via the curation API. Resolves the canonical
    download URL from `assets[].url` (the `<dataset_id>.h5ad` pattern
    is NOT reliable for re-versioned datasets).
  - `cellxgene_download.sh` — accepts either `SOURCE_URL` directly or
    `DATASET_ID + COLLECTION_ID` for auto-resolution; downloads via
    configurable proxy (defaults to `127.0.0.1:17891`, sc-bias convention);
    auto-counts `n_cells` from the resulting `.h5ad` via anndata (with
    h5py fallback when the anndata NumPy ABI is broken); writes
    `DATA_MANIFEST.json` + `RESULT.md` automatically.
  - `geo_figshare_download.sh` — generic single-URL puller for GEO ftp,
    figshare ndownloader, Zenodo records, GitHub releases. Supports
    `POST_DOWNLOAD_CMD` for unpacking archives + `EXTRACTED_LOCAL_PATH`
    so the manifest's `local_path` points to the validator-checkable
    artifact.
  - `README.md` with subagent recipes + proxy policy + protected-access
    escalation contract.
- `scripts/signal_detector.py` — classifies a completed branch as
  STRONG / WEAK / NULL / UNKNOWN. Prefers
  `audit_report.json.blindspot_signal` for `task_type=audit`,
  `metrics.json.downstream_tasks` for `task_type=training`, falls back to
  parsing `METRIC=`, `EFFECT_SIZE=`, `CI_LOW=`, `CI_HI=`, `P_VALUE=`
  from RESULT.md. Aggregates sibling branches at a junction into
  ALL_NULL / MOSTLY_NULL / MIXED_POSITIVE / ALL_STRONG / etc. and
  exits 10 + writes `.research-tree/AUTO_PIVOT_PROPOSAL.md` when
  auto-pivot fires. CI exclusion overrides p_value semantics so
  bootstrap-style "high P = good" metrics do not mis-trigger NULL.
- `tests/test_signal_detector.sh` — 12 cases covering Krishna STRONG /
  Li2022 NULL / per-FM WEAK / CI crossing zero / tiny-effect override /
  no-CI provisional / aggregate ALL_NULL → pivot proposal / idempotency
  / MIXED_POSITIVE → no pivot.
- `tests/test_data_acquisition.sh` — 8 cases covering discover CLI
  surface, end-to-end download against local HTTP server, manifest
  schema, validator PASS round-trip, validator FAIL on missing
  checksum, 17890-proxy warning.

### Changed
- `skills/research-tree/SKILL.md`:
  - Execute step's `task_type=data-acquisition` block now teaches the
    subagent to use the new templates, the proxy policy (17891 not
    17890), the nohup-then-return background pattern, and how to
    surface protected-access blockers via `DEAD.md`.
  - Expand step now requires the proposer to check local data
    existence before naming a case-needing-atlas; if the atlas is
    missing, the proposer auto-inserts a `task_type=data-acquisition`
    sibling and wires `depends_on` via the placeholder-id pattern.
  - Proposer JSON schema gains `placeholder_id` +
    `depends_on_placeholders` so siblings can declare dependencies on
    each other before any real node IDs exist; orchestrator resolves
    placeholders → real IDs in a two-pass add.
  - Autopilot gains step 7.5 (auto-pivot detection) that runs
    `signal_detector.py check-pivot --write-proposal`, expands
    dead-signal junctions with a re-framing prompt, and renames the
    handled proposal to `AUTO_PIVOT_PROPOSAL.handled.md`.
- `templates/RESEARCH_CHARTER.md` gains §"Data acquisition rules"
  (provenance, n_cells honesty, proxy policy, protected-access,
  no-silent-reprocessing) and §"Pivot trigger rules" (auto-pivot
  trigger table, signal_thresholds yaml, RESULT.md convention,
  pivot ≠ retry rule).

### Notes for users on v0.1.6
- Fully backward compatible. Tools added; existing schema unchanged.
- The `cellxgene_download.sh` template requires `COLLECTION_ID` in
  addition to `DATASET_ID` (or skip both and pass `SOURCE_URL`
  directly) because the curation API's dataset endpoint is
  collection-scoped — direct `/curation/v1/datasets/<id>` returns 404.
- Real-world dogfood on a 12.2 GiB CELLxGENE Discover dataset (Perez
  2022 SLE PBMC, 1.26M cells, collection 436154da-..., dataset
  218acb0f-...) succeeded against the live 17891 proxy.

## [0.1.6] — 2026-05-23

Task-type-aware nodes. The v0.1.3 validator was hard-coded for training new
foundation models (≥3 seed checkpoints, `param_count ≥ 10M`, ≥4 ablation
subdirs) — fine for that case, but it auto-FAILs any audit / analysis /
data-acquisition branch on contact, because those work modes physically
cannot produce checkpoints. v0.1.6 makes the validator schema route on the
node's `task_type` so post-hoc audit projects (e.g. evaluating a frozen
model on within-atlas vs cross-batch protocols) can finally pass through
the existing tree-exploration machinery without `--no-validate` hacks.

### Added
- Node schema fields: `task_type`, `depends_on`, `human_only`
  (auto-migrated on load — pre-v0.1.6 trees default to `task_type=training`
  / `depends_on=[]` / `human_only=false`, preserving v0.1.5 behavior).
  Root node gets `task_type=mixed`.
- `tree_state.py add` flags: `--task-type {training,audit,analysis,
  data-acquisition,framing-decision,mixed}`, `--depends-on <csv ids>`,
  `--human-only`.
- `tree_state.py deps <node_id>` — prints `{satisfied, unmet, depends_on}`
  JSON; exits 0 when satisfied, 1 when blocked. Lets shell scripts branch
  on dependency readiness.
- `charter_validator.py --task-type {training,audit,analysis,
  data-acquisition,framing-decision,mixed}` (default: read from
  `tree.json` node, fall back to `training`).
- Four new task-type-specific physical-artifact schemas in the validator:
  - **audit**: `audit_report.json` (cohort_summary + blindspot_signal),
    `donor_bootstrap.json` (n_iter ≥ 1000), `protocol_comparison.json`
    (within_atlas vs cross_batch vs over_estimation_ratio)
  - **analysis**: `analysis_output.json` + optional `figures/*.{png,pdf,svg}`
  - **data-acquisition**: `DATA_MANIFEST.json` with atlas_id / source_url
    / local_path / checksum / n_cells / downloaded_at; validator confirms
    the referenced local_path actually exists on disk
  - **framing-decision**: validator immediately FAILs with a pointer to
    set `human_only=true` and skip via `pick-next` (autopilot should
    never reach a framing-decision branch)
- `tests/test_task_type_aware.sh` — 20 cases covering field schema,
  enum validation, dependency rejection, pick-next skipping, deps
  command, and all five task-type validator paths.

### Changed
- `tree_state.py pick-next` now skips nodes with `human_only=true` and
  nodes whose `depends_on` lists any non-completed prerequisite.
  Existing scoring order (parent_score → shallowest depth) is preserved
  among the eligible set.
- `tree_state.py set` accepts `task_type=<enum>` (validates against the
  enum) and `depends_on=<csv>` (parses comma-separated ids). `human_only`
  was already a normal bool field.
- `charter_validator.py check_result_md` only enforces the strict-rule
  subset declared for the branch's task_type
  (`TASK_TYPE_STRICT_RULES`). Training keeps all 8 rules; audit drops
  2/3/5; analysis drops 1/2/3/5; data-acquisition drops 2/3/4/5;
  framing-decision short-circuits.
- `charter_validator.py` output JSON gains a `task_type` field for
  downstream tooling. Stderr summary line now reads
  `=== charter_validator [<task_type>]: <verdict> ===`.
- `templates/RESEARCH_CHARTER.md` adds two sections: §"Task type modes"
  (per-mode strict rule subset + required physical artifacts) and
  §"Dependency declaration".
- `skills/research-tree/SKILL.md` `expand` proposer schema now includes
  `task_type` + `depends_on` + `human_only` per candidate.
  `execute` subagent prompt now selects a task-type-specific artifact
  block (training / audit / analysis / data-acquisition) instead of
  hard-coding the training block.

### Migration notes
- **Old trees keep working**. `load_state()` backfills the new fields on
  read, so `tree.json` files created by v0.1.0-v0.1.5 are still
  consumable. No `init --force` required.
- **Old validator invocations keep working**. `charter_validator.py`
  without `--task-type` reads the node's task_type from `tree.json`
  (climbing up to find `.research-tree/`) and defaults to `training`
  when no tree state is found. Existing CI scripts that pass only
  `branch_dir` are unchanged in behavior.

## [0.1.5] — 2026-05-23

Smarter branching cadence: stop forcing 2-4 candidates at every node, let the
proposer skip expansion when there's nothing to fork on; replace the fixed-
interval `/loop` rhythm with `--continuous` chained steps that pause only
when blocked or when the session context counter says it's time to restart.

### Added
- `direct_executable` node field (default `false`). When the branch-proposer
  signals `skip_expansion: true` (no real fork at this depth), the orchestrator
  marks the node `direct_executable=true` instead of creating children. Next
  autopilot pick on that node dispatches `execute` directly, skipping a
  wasted expand round.
- `tree_state.py session-step <report|increment|reset>` — tracks autopilot
  steps within a single Claude Code session and reports `should_pause=true`
  when the count crosses `--threshold` (default 20). Session identity is
  determined by **ancestor PID chain intersection** (robust against bash
  `$(...)` transient subshells; the long-lived Claude Code main process
  appears in every chain).
- `autopilot --continuous` mode (chained): runs steps back-to-back until
  every live node is blocked on a background process, hits DONE/ROOT_FAILURE,
  hits the budget, or hits the session step threshold. Removes the
  fixed-30-min lag between quick chained steps. Combine with `/loop` for
  long-running training: `/loop 30m /research-tree autopilot --continuous --silent`.

### Changed
- `expand` branch-proposer prompt rewritten:
  - **depth 0**: still mandates 2-4 candidates spanning charter §2 families
    (research diversity for NBT/NMI submission is non-negotiable).
  - **depth ≥1**: accepts 1-4 candidates, OR `skip_expansion: true` when the
    node represents a canonical/standard step with no genuine design choice.
    "Run the standard evaluation" or "compute the required ARI metric" is now
    a valid no-fork. Strongly prefers skipping over fabricating fake-different
    candidates.
- Return schema upgraded from bare JSON array to `{skip_expansion, candidates|skip_reason}`
  object so the proposer can declare intent unambiguously.
- `autopilot` step 6 dispatch table now considers `direct_executable`:
  `pending + direct_executable=true → execute` instead of `expand`.
- New step 11.5 (session counter) and 11.6 (continuous loop) added to the
  autopilot script. Single-step mode (default) and `--silent` are unchanged
  in behavior; `--continuous` is opt-in.
- `SET_ALLOWED_KEYS` extended to include `direct_executable` so the proposer
  hook can update it via `set` without going through a privileged transition.

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
