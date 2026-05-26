"""AuditWorker — post-hoc evaluation of a frozen model.

No checkpoints, no multi-seed training. The artifact contract is about
statistical evidence on a held-out cohort.
"""
from __future__ import annotations

from .base import BaseWorker


class AuditWorker(BaseWorker):
    task_type = "audit"

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        return (
            self._common_prompt_header(node, graph, ctx)
            + "\n"
            + self._anti_laziness_block()
            + self._background_execution_block(node)
            + self._output_modes_block()
            + "PHYSICAL ARTIFACTS REQUIRED:\n\n"
            "    audit_report.json\n"
            "        Top-level keys:\n"
            "          cohort_summary   — dict with n_cohort_cells, n_control_cells,\n"
            "                              n_donor_cohort, n_donor_control\n"
            "          blindspot_signal — dict with fn_delta, ci_low, ci_hi, verdict\n\n"
            "    donor_bootstrap.json\n"
            "        Donor-level bootstrap with `n_iter` ≥ 1000 + per-donor leave-one-out\n"
            "        sensitivity.\n\n"
            "    protocol_comparison.json\n"
            "        Methodological core: `within_atlas_fn_delta`, `cross_batch_fn_delta`,\n"
            "        `over_estimation_ratio`. Identifies the protocol bias the audit is\n"
            "        designed to surface.\n\n"
            "    requirements.txt or environment.yml\n"
            "        Pinned dependencies.\n\n"
            "    NO checkpoint dirs, NO metrics.json:param_count, NO ablations/.\n"
            "    These are nonsense for an audit task and the validator will not require them.\n\n"
            "RESULT.md format:\n"
            "    METRIC=<float>  # over_estimation_ratio is the canonical headline metric\n"
            "    KEY_FINDING=<paragraph>\n"
            "    COST=<gpu_hours>\n"
            "    ARTIFACTS=<list>\n"
            "    DONE_READY=<true|false>\n"
            "    + the charter compliance table covering rules 0/1/4/7/8 only.\n"
            "      (Rules 2/3/5 are about model training and don't apply.)\n"
        )
