# Research Charter

> **This is the project's anti-laziness constitution.** Every research-tree subagent
> (proposer, executor, junction auditor) reads this before doing its job. Each branch's
> `RESULT.md` MUST include a charter compliance audit at the end. Branches that fail
> any FAIL-grade rule are marked dead with `death_reason="charter_violation: <rule>"`
> regardless of how good their metric looks.
>
> Override defaults by editing the fields below. Sections marked **(strict)** cannot
> be silently relaxed by subagents — they need explicit user authorization to weaken.
> Sections marked **(soft)** allow a WARN verdict instead of FAIL.

## Submission target

- venue: Nature Biotechnology
- venue_tier: top (Nature family / Science / Cell)
- min_quality_bar: must beat current SOTA on at least 3 of 5 downstream tasks by ≥ 5% relative

## 0. Anti-laziness preamble (strict)

The following shortcuts are FORBIDDEN as final results. They are allowed only as
explicit "pilot" runs that are clearly flagged and never count toward winner
selection or paper claims:

- Training on < 50% of available training data without an explicit `pilot=true` flag
- Using a parameter count < 1/10 of comparable SOTA models as the final winner
- Skipping evaluation on any downstream task listed in `evaluation` below
- Reporting metric without standard deviation across ≥ 3 random seeds
- "It works" claims without statistical significance against baseline
- Self-reported improvement without independent re-implementation of baseline

If a subagent detects pressure to take any of these shortcuts (e.g., "this would take
too long, let me use a subset"), the correct response is to WRITE A DEAD.md WITH
`reason="needs full-scale compute, cannot honestly complete in pilot budget"` and
stop. Misleading completion is worse than honest failure.

## 1. Data rules (strict)

- **Full-data mandate**: final runs use all available training data. Subsetting is
  allowed only with `pilot=true` flag; pilot results never claim the final number.
- **Held-out test set**: at least 20% of data, sampled once at project init, never
  touched until final evaluation. Hash and lock the split in `data/test_split.json`.
- **Cross-platform diversity**: if the project claims generalization, the test set
  must include at least 2 platforms / labs / batches not in the training set.
- **No leakage audit**: before declaring any winner, verify zero overlap between
  train and test on `cell_id` / `donor_id` / `slide_id` (whichever applies).

## 2. Architecture rules (strict)

- **Diversity at depth 0**: the root expansion MUST include at least 4 architectures
  spanning these families: linear/MLP baseline, attention-based, graph-based,
  ambitious-novel. Two flavors of the same family count as one.
- **Parameter floor**: any candidate competing for "winner" status must use
  ≥ 10M parameters (or justify explicitly why a smaller model is appropriate for
  this task) — prevents trivial mean-pool baselines from winning by default.
- **Strong baseline required**: at least one candidate must be the current best
  published method for this task, re-implemented faithfully. "We didn't compare to X"
  is grounds for FAIL.

## 3. Training rules (strict)

- **Convergence**: training must reach loss plateau or hit max_epochs naturally.
  Early-stopping due to "running out of patience" is FAIL.
- **Multi-seed**: ≥ 3 seeds for any number that goes into a comparison table.
  Single-seed numbers may appear in exploration but never in the final paper draft.
- **Hyperparam sweep**: each architecture candidate must do at least 8 trials
  (HP search) before being declared dead. "Tried once, didn't work" is FAIL.

## 4. Evaluation rules (strict)

Required downstream tasks (customize per project):

1. _<task 1 e.g., tissue zero-shot classification>_ — metric: macro-F1, baseline: <name>
2. _<task 2 e.g., cross-tech transfer>_ — metric: ARI, baseline: <name>
3. _<task 3>_ — metric: ..., baseline: ...
4. _<task 4>_ — metric: ..., baseline: ...
5. _<task 5>_ — metric: ..., baseline: ...

Each task must be reported with: metric ± std across seeds, statistical test against
baseline (p < 0.05), and a per-class / per-batch breakdown to detect cherry-picking.

## 5. Ablation rules (strict)

The winning method must report ablations on at least:

- Removing the headline novel component (does it still work? if yes, novelty claim is false)
- Scale ablation (small / medium / large parameter counts — does it scale?)
- Data ablation (10% / 50% / 100% training data — how data-efficient?)
- Cross-batch / cross-domain ablation if generalization is claimed

Missing ablations = the winner is NOT done, even if downstream numbers look good.

## 6. Novelty rules (soft, but strongly weighted)

- Each candidate at root depth 0 must have a one-sentence differentiation from prior
  work cited (paper title + venue + year). "Like X but bigger" is not differentiation.
- The winning branch must survive a `/kill-argument` style audit: write a 200-word
  rejection memo, then defend. If the rejection memo is convincing, escalate to a
  pivot decision (potentially back to /idea-pipeline).

## 7. Reproducibility rules (strict)

- All random seeds fixed and reported
- Environment locked: `requirements.txt` or `environment.yml` in each branch dir
- Code committed (atomic per branch) — no orphan results without code
- Data versioning: `data/<dataset>/VERSION.md` with date pulled + URL + checksum

## 8. Compute honesty rules (strict)

- Every RESULT.md MUST report actual wall-clock time and GPU-hours used
- If a branch requested compute extension beyond original budget, that's logged with reason
- "We couldn't finish in budget, so we cut corners" must be visible in DEAD.md or RESULT.md

## Charter compliance audit format (required in every RESULT.md)

Append this section to RESULT.md at the end. **v0.1.6**: only include the
rows that apply to your `task_type` (see §"Task type modes" below). The
validator only enforces the rule subset for your declared `task_type`.

Default (full table — required for `task_type=training` / `mixed`):

```
## Charter compliance

| Rule | Verdict | Evidence |
|---|---|---|
| 0. Anti-laziness preamble | PASS / WARN / FAIL | one line |
| 1. Data rules | PASS / WARN / FAIL | path to test_split.json + train/test overlap check |
| 2. Architecture rules | PASS / WARN / FAIL | param count, baseline comparison |
| 3. Training rules | PASS / WARN / FAIL | seeds, convergence plot ref, HP trials |
| 4. Evaluation rules | PASS / WARN / FAIL | all 5 tasks reported with std + sig test |
| 5. Ablation rules | PASS / WARN / FAIL | which ablations done |
| 6. Novelty rules | PASS / WARN | differentiation citation |
| 7. Reproducibility rules | PASS / WARN / FAIL | env, seed, code commit |
| 8. Compute honesty | PASS / WARN / FAIL | wall time + gpu hours actual vs budget |
```

Any FAIL on a (strict) rule → branch is dead, no exceptions.
WARN on a (soft) rule → branch alive but flagged in junction audit.

## Task type modes (v0.1.6)

Different kinds of work need different acceptance criteria. The
`charter_validator.py` reads each node's `task_type` field (set at `add`
time via `--task-type`) and enforces the relevant rule subset only.

### `training` (default — v0.1.5 behavior preserved)
- All 8 strict rules enforced
- Physical artifacts required: `data/test_split.json` (with hash), ≥3
  `checkpoints/seed_*/` dirs each with a real checkpoint file,
  `metrics.json` (param_count ≥10M, ≥3 seeds, per-task metric/std/p_value),
  ≥4 `ablations/` subdirs, `requirements.txt`
- This is the default when `task_type` is unspecified — old projects do
  not need to migrate

### `audit` (post-hoc evaluation on frozen models)
- Strict rules enforced: 0, 1, 4, 7, 8 (skipping 2 architecture, 3
  training, 5 ablation because no new model is trained)
- Physical artifacts required:
  - `audit_report.json` — with `cohort_summary` and `blindspot_signal`
    objects (cohort/control sizes, FN/FP delta, signal verdict)
  - `donor_bootstrap.json` — with `n_iter ≥ 1000` (donor-level 95% CI)
  - `protocol_comparison.json` — with `within_atlas_fn_delta`,
    `cross_batch_fn_delta`, `over_estimation_ratio` (this is the
    methodological core: did the audit protocol over-estimate signal?)
  - `requirements.txt`
- Use when: you are evaluating an already-trained model on new data,
  computing within-atlas vs cross-batch comparisons, running donor
  bootstrap on existing embeddings, etc.

### `analysis` (statistics / figure generation / report)
- Strict rules enforced: 0, 4, 7, 8 (skipping data / architecture /
  training / ablation — analysis consumes prior data and produces
  derived outputs)
- Physical artifacts required:
  - `analysis_output.json` — structured output of the analysis
    (statistics, computed metrics, decision recommendations)
  - `figures/` directory with ≥1 `*.png` / `*.pdf` / `*.svg` (optional
    if the analysis is statistics-only)
  - `requirements.txt`
- Use when: generating paper figures, post-hoc statistics across branches,
  producing comparison tables

### `data-acquisition` (download + verify external dataset)
- Strict rules enforced: 0, 1, 7 (skipping evaluation / training because
  no model interaction happens)
- Physical artifacts required:
  - `DATA_MANIFEST.json` — with `atlas_id`, `source_url`, `local_path`,
    `checksum`, `n_cells`, `downloaded_at`; the validator confirms the
    referenced `local_path` actually exists on disk
  - download / preprocessing script (any file is fine; recorded in
    `DATA_MANIFEST.json`'s `requirements.txt` reference)
- Use when: pulling an external atlas from CELLxGENE Census / GEO /
  figshare, verifying integrity, registering as available for
  downstream `audit` / `training` branches

### `framing-decision` (human-only narrative / venue choice)
- No strict rules; validator immediately FAILS if autopilot runs this
- Use when: a branch represents a paper-writing choice (which figure
  leads / which venue to target / wording of the headline). These are
  user decisions, not autopilot decisions.
- Always combine with `--human-only` so `pick-next` skips the node

## Dependency declaration (v0.1.6)

Use `--depends-on <id1>,<id2>,...` at `add` time when a branch can only
run after another branch has completed. Example: a `training` branch for
a repair head depends on the `audit` branch that identified the
per-FM blindspot it should repair.

`pick-next` skips nodes with unmet dependencies (any dep that is not
yet `status=completed` blocks selection). Once the prerequisite
completes, the dependent node becomes eligible automatically.

Use `python3 tree_state.py deps <node_id>` to inspect a node's
dependency status (returns JSON with `satisfied: true/false` and a list
of unmet dep ids).

## Done criteria

The project is "done" — autopilot transitions to ARIS /paper-writing automatically —
when all of these are true:

```yaml
done_criteria:
  winner_exists: true
  winner_charter_audit: all_strict_PASS
  winner_score_on_primary: ">= 0.85"
  winner_beats_sota_on: ">= 3 of 5 downstream tasks by >= 5% relative"
  winner_ablations_complete: true
  winner_seeds_count: ">= 3"
  reproducibility_audit: PASS
  kill_argument_survived: true
```

Until ALL of these are true, autopilot keeps deepening / opening new branches /
re-running missing ablations. **Hitting the GPU budget does NOT mean done — it means
"need more compute, escalate to user"**.

## Pivot criteria

Autopilot writes ROOT_FAILURE.md and recommends pivot back to /idea-pipeline when:

```yaml
pivot_criteria:
  all_root_branches_dead: true                # existing rule
  OR (consecutive_failed_steps > 20)          # safety stop
  OR (kill_argument_audit consistently fatal) # idea is dead even before experiments
```
