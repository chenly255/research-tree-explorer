"""TrainingWorker — standard ML training runs.

Artifact requirements (enforced by charter_validator.py):
    - data/test_split.json with test_ids + reproducible sha256 hash
    - checkpoints/seed_0..seed_N/ (≥ 3 seeds), each with a .pt/.pth/.safetensors/.ckpt
    - metrics.json with param_count (≥ 10M), seeds, downstream_tasks {metric, std, baseline_score, p_value}
    - ablations/ (≥ 4 subdirs: headline component / scale / data efficiency / cross-batch)
    - requirements.txt or environment.yml
    - if DONE_READY=true: KILL_ARGUMENT.md
"""
from __future__ import annotations

from .base import BaseWorker


class TrainingWorker(BaseWorker):
    task_type = "training"

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        return (
            self._common_prompt_header(node, graph, ctx)
            + "\n"
            + self._anti_laziness_block()
            + self._background_execution_block(node)
            + self._output_modes_block()
            + "PHYSICAL ARTIFACTS REQUIRED (the background process — not you — writes these):\n\n"
            "    data/test_split.json\n"
            "        JSON with keys `test_ids` (non-empty list), `hash`, `created_at`.\n"
            "        The hash field MUST equal sha256(json.dumps(sorted(test_ids),\n"
            "            separators=(',', ':')).encode()) — validator recomputes.\n\n"
            "    checkpoints/seed_0/, seed_1/, seed_2/  (and optionally more)\n"
            "        Each directory contains at least one *.pt / *.pth / *.safetensors / *.ckpt file\n"
            "        of non-trivial size (≥ 1 KB). The file size will be cross-checked\n"
            "        against metrics.json:param_count — 1 byte/param floor.\n\n"
            "    metrics.json\n"
            "        Top-level keys (all required):\n"
            "          param_count      — int ≥ 10_000_000\n"
            "          seeds            — list, len ≥ 3\n"
            "          downstream_tasks — dict, each value has metric / std / baseline_score / p_value\n"
            "          gpu_hours_used   — float\n"
            "          wall_clock_hours — float\n\n"
            "    ablations/\n"
            "        At least 4 subdirectories, each with a result file. Required ablation\n"
            "        kinds per charter §5: headline component, scale, data efficiency, cross-batch.\n\n"
            "    requirements.txt or environment.yml\n"
            "        Pinned dependencies. Charter §7 (reproducibility).\n\n"
            "    KILL_ARGUMENT.md (only if you write DONE_READY=true in RESULT.md)\n"
            "        Self-rejection memo. State the strongest counter-argument to your own\n"
            "        result, then defend why it doesn't kill the finding.\n\n"
            "RESULT.md format:\n"
            "    METRIC=<float>\n"
            "    KEY_FINDING=<paragraph>\n"
            "    COST=<gpu_hours>\n"
            "    ARTIFACTS=<list>\n"
            "    DONE_READY=<true|false>\n"
            "    + the charter compliance table covering all 9 rules (0/1/2/3/4/5/6/7/8).\n\n"
            "If `cost_budget_hours` from the orchestrator is below what a clean run needs,\n"
            "write DEAD.md with death_reason='needs full-scale compute, cannot honestly\n"
            "complete in pilot budget' rather than a shortcut version.\n"
        )
