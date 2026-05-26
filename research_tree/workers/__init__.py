"""Worker registry — one Worker class per task_type.

Design contract: docs/V1-ARCHITECTURE.md (section "workers/").

The v0.5 architecture had task_type-specific artifact rules embedded as
markdown blocks inside SKILL.md (~ 200 lines per task type). Every change
to those rules required editing SKILL.md, which is loaded into the main
agent's context for every invocation — so the rules cost tokens even
when the active task only used one task type.

The v1.0 architecture: each Worker class encapsulates everything a
task type needs:
    - artifact requirements (passed into the subagent prompt as a string)
    - validation logic (a thin wrapper around scripts/charter_validator.py
      check_* functions; v1.1 may migrate the bodies in-tree)
    - completion handling (what edges / next actions to emit)

The orchestrator does ONE dispatch:
    worker = get_worker(node.task_type)
    prompt = worker.spawn_subagent_prompt(node, graph, ctx)
    ...subagent runs, writes to branch_dir...
    result = worker.validate(node, branch_dir)
    worker.on_completion(node, graph, result)

There is no per-action special-casing in the orchestrator. Adding a 6th
task_type means writing one Worker class; no edits to SKILL.md or the
CLI dispatcher.
"""
from __future__ import annotations

from .base import Worker, WorkerResult, ValidationResult
from .training import TrainingWorker
from .audit import AuditWorker
from .analysis import AnalysisWorker
from .data_acquisition import DataAcquisitionWorker
from .framing_decision import FramingDecisionWorker


_REGISTRY: dict[str, Worker] = {
    "training": TrainingWorker(),
    "audit": AuditWorker(),
    "analysis": AnalysisWorker(),
    "data-acquisition": DataAcquisitionWorker(),
    "framing-decision": FramingDecisionWorker(),
    # `mixed` defaults to the most demanding (training) schema, which works
    # for branches that genuinely need everything; truly heterogeneous work
    # should be split into single-task children.
    "mixed": TrainingWorker(),
}


def get_worker(task_type: str) -> Worker:
    if task_type not in _REGISTRY:
        raise KeyError(
            f"no Worker registered for task_type {task_type!r}. "
            f"Known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[task_type]


def register_worker(task_type: str, worker: Worker) -> None:
    """Allow downstream projects to add their own Worker classes."""
    _REGISTRY[task_type] = worker


def known_task_types() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "Worker",
    "WorkerResult",
    "ValidationResult",
    "get_worker",
    "register_worker",
    "known_task_types",
    "TrainingWorker",
    "AuditWorker",
    "AnalysisWorker",
    "DataAcquisitionWorker",
    "FramingDecisionWorker",
]
