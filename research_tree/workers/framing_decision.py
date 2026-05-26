"""FramingDecisionWorker — human-only nodes (paper framing, venue, narrative).

The orchestrator should NEVER pick a framing-decision node via pick-next
(graph.is_pickable returns False when artifacts.human_only=True). This
Worker exists so the registry is exhaustive — if the orchestrator does
reach this code path, it's a bug and we fail loudly.
"""
from __future__ import annotations

from .base import BaseWorker, ValidationResult, WorkerResult


class FramingDecisionWorker(BaseWorker):
    task_type = "framing-decision"

    def can_run(self, node, graph) -> tuple[bool, str]:
        return False, "framing-decision is human-only; autopilot must not execute it"

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        raise RuntimeError(
            f"BUG: orchestrator tried to spawn a subagent for framing-decision node "
            f"{node.id!r}. framing-decision is human-only; the human-gate sentinel "
            f"should have been raised instead."
        )

    def validate(self, node, branch_dir, *, require_codex_audit=False, nonce_file=None) -> ValidationResult:
        return ValidationResult(
            verdict="FAIL",
            failures=["framing-decision is human-only; autopilot must not validate it"],
        )

    def on_completion(self, node, graph, validation) -> WorkerResult:
        return WorkerResult(
            new_lifecycle="failed",
            artifacts_update={"death_reason": "framing-decision routed through autopilot in error"},
        )
