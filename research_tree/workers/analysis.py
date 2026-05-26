"""AnalysisWorker — statistics, figures, report generation. No training,
no held-out test, no checkpoints."""
from __future__ import annotations

from .base import BaseWorker


class AnalysisWorker(BaseWorker):
    task_type = "analysis"

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        return (
            self._common_prompt_header(node, graph, ctx)
            + "\n"
            + self._anti_laziness_block()
            + self._background_execution_block(node)
            + self._output_modes_block()
            + "PHYSICAL ARTIFACTS REQUIRED:\n\n"
            "    analysis_output.json\n"
            "        Structured statistics output. Schema depends on the analysis; at minimum\n"
            "        a dict with named results keyed by their semantic role.\n\n"
            "    figures/  (optional but encouraged)\n"
            "        PNG / PDF / SVG files referenced in RESULT.md by file path.\n\n"
            "    requirements.txt or environment.yml\n\n"
            "    NO checkpoint dirs, NO test_split.json, NO metrics.json:param_count.\n\n"
            "RESULT.md format:\n"
            "    METRIC=<float>  # the primary scalar this analysis produces\n"
            "    KEY_FINDING=<paragraph>\n"
            "    COST=<gpu_hours>\n"
            "    ARTIFACTS=<list>\n"
            "    + the charter compliance table covering rules 0/4/7/8 only.\n"
        )
