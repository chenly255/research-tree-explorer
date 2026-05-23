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

Append this section to RESULT.md at the end:

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
