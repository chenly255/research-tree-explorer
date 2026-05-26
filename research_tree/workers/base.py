"""Worker base interface + shared helpers.

Every concrete Worker subclass provides exactly four methods:

    can_run(node, graph) -> tuple[bool, str]
        Check whether the node can be picked right now. Default: all
        hard-dep edges into the node point to lifecycle=done. Override
        only if the task type has additional preconditions.

    spawn_subagent_prompt(node, graph, ctx) -> str
        Build the full prompt for the executor subagent. The prompt
        includes: hypothesis, budget, output modes (RESULT/DEAD/FORK/PIVOT),
        and the task-type-specific PHYSICAL artifact requirements. The
        v0.5 SKILL.md embedded these artifact requirements as markdown
        blocks; v1.0 moves them into this method.

    validate(node, branch_dir) -> ValidationResult
        Run the physical artifact check after the subagent returns.
        Thin wrapper around scripts/charter_validator.py for now.

    on_completion(node, graph, validation) -> WorkerResult
        Decide what to do next given validation outcome: set lifecycle,
        emit edges, propose next actions. Default implementation handles
        the common case (PASS → lifecycle=done; FAIL → lifecycle=failed).
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol


# ---------- result dataclasses ----------


@dataclass
class ValidationResult:
    verdict: Literal["PASS", "WARN", "FAIL"]
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    metric: float | None = None


@dataclass
class WorkerResult:
    new_lifecycle: Literal["running", "done", "failed", "created"]
    score: float | None = None
    artifacts_update: dict = field(default_factory=dict)
    new_edges: list = field(default_factory=list)  # list of (src, dst, kind) tuples
    pivot_proposal: dict | None = None
    next_actions: list[str] = field(default_factory=list)


# ---------- locating the legacy validator ----------


def _resolve_legacy_validator() -> Path:
    """Find scripts/charter_validator.py. Worker subclasses use this as a
    subprocess for now; v1.1 will migrate the bodies in-tree.
    """
    here = Path(__file__).resolve()
    # research_tree/workers/base.py -> research_tree/workers -> research_tree -> repo
    repo_root = here.parent.parent.parent
    candidate = repo_root / "scripts" / "charter_validator.py"
    if not candidate.exists():
        raise FileNotFoundError(
            f"legacy charter_validator.py not found at {candidate}. "
            f"v1.0 Worker classes still delegate to the v0.5 validator script."
        )
    return candidate


def _resolve_legacy_codex_audit_cli() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    return repo_root / "scripts" / "codex_audit_cli.py"


# ---------- Worker protocol ----------


class Worker(Protocol):
    task_type: str

    def can_run(self, node, graph) -> tuple[bool, str]: ...

    def spawn_subagent_prompt(self, node, graph, ctx) -> str: ...

    def validate(self, node, branch_dir: Path, *,
                 require_codex_audit: bool = False,
                 nonce_file: Path | None = None) -> ValidationResult: ...

    def on_completion(self, node, graph, validation: ValidationResult) -> WorkerResult: ...


# ---------- BaseWorker (default behavior) ----------


class BaseWorker:
    """Default Worker implementation. Concrete subclasses override the
    task-specific bits (artifact requirements in prompt; what to require
    in validate)."""

    task_type: str = "mixed"

    # --- default can_run: hard-deps satisfied ---

    def can_run(self, node, graph) -> tuple[bool, str]:
        if node.lifecycle != "created":
            return False, f"lifecycle is {node.lifecycle}, must be 'created'"
        if node.is_abandoned:
            return False, "node is abandoned"
        if node.artifacts.get("human_only"):
            return False, "node is human_only"
        for dep_id in graph.hard_deps_of(node.id):
            dep = graph.nodes.get(dep_id)
            if dep is None:
                return False, f"hard-dep {dep_id!r} missing"
            if dep.lifecycle != "done":
                return False, f"hard-dep {dep_id!r} is {dep.lifecycle}, not done"
        return True, "all hard-deps satisfied"

    # --- default validate: delegate to charter_validator subprocess ---

    def validate(self, node, branch_dir: Path, *,
                 require_codex_audit: bool = False,
                 nonce_file: Path | None = None) -> ValidationResult:
        validator = _resolve_legacy_validator()
        cmd = [sys.executable, str(validator), str(branch_dir),
               "--task-type", self.task_type]
        if require_codex_audit:
            cmd.append("--require-codex-audit")
            if nonce_file:
                cmd.extend(["--audit-nonce-file", str(nonce_file)])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # charter_validator exits: 0 PASS, 1 WARN, 2 FAIL
        # stdout: JSON {verdict, failures, warnings, evidence}
        import json
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return ValidationResult(
                verdict="FAIL",
                failures=[f"validator did not return JSON. stdout={proc.stdout[:500]!r} stderr={proc.stderr[:500]!r}"],
            )
        return ValidationResult(
            verdict=report.get("verdict", "FAIL"),
            failures=report.get("failures") or [],
            warnings=report.get("warnings") or [],
            evidence=report.get("evidence") or {},
            metric=(report.get("evidence") or {}).get("metric"),
        )

    # --- default on_completion ---

    def on_completion(self, node, graph, validation: ValidationResult) -> WorkerResult:
        if validation.verdict == "PASS":
            return WorkerResult(
                new_lifecycle="done",
                score=validation.metric,
                artifacts_update={"validation_evidence": validation.evidence},
            )
        if validation.verdict == "WARN":
            # WARN keeps the node alive; caller may want to surface to user
            return WorkerResult(
                new_lifecycle="done",
                score=validation.metric,
                artifacts_update={
                    "validation_evidence": validation.evidence,
                    "validation_warnings": validation.warnings,
                },
                next_actions=[f"surface_warning:{w}" for w in validation.warnings[:3]],
            )
        # FAIL
        return WorkerResult(
            new_lifecycle="failed",
            artifacts_update={
                "death_reason": validation.failures[0] if validation.failures else "validator FAIL",
                "death_evidence": validation.failures,
                "validation_evidence": validation.evidence,
            },
        )

    # --- default spawn_subagent_prompt: subclass must override ---

    def spawn_subagent_prompt(self, node, graph, ctx) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} must override spawn_subagent_prompt"
        )

    # --- shared prompt components ---

    def _common_prompt_header(self, node, graph, ctx) -> str:
        return (
            f"You are a research-tree executor subagent.\n\n"
            f"Branch hypothesis: {node.title}\n"
            f"Description: {node.description}\n"
            f"Task type: {self.task_type}\n"
            f"Branch workdir: .research-tree/branches/{node.id}/\n"
            f"Budget (hours, soft cap): "
            f"{node.cost_budget_hours if node.cost_budget_hours is not None else 'unspecified'}\n"
            f"Info value (1-5): {node.info_value if node.info_value is not None else 'unspecified'}\n"
        )

    def _output_modes_block(self) -> str:
        return (
            "You can end your run in EXACTLY ONE of four ways:\n\n"
            "(a) RESULT.md — work completed successfully. Include METRIC + KEY_FINDING +\n"
            "    ARTIFACTS + the charter compliance table per the requirements below.\n\n"
            "(b) DEAD.md — work cannot finish (hypothesis falsified, blocker hit, charter\n"
            "    rule violated). Single line `death_reason: <one sentence>` + paragraph.\n"
            "    Honest failure beats fake success.\n\n"
            "(c) SUBTREE_FORK.md — you discovered mid-flight that this step has 2-4 genuinely\n"
            "    distinct sub-approaches worth competing. Hand control back to orchestrator.\n"
            "    Format: '# Why fork: <one line>' + JSON candidates array.\n\n"
            "(d) SUBTREE_PIVOT.md — the entire hypothesis of this branch is wrong; a different\n"
            "    framing is needed. NOT a fork. Format: reason / suggest_new_parent_node_kind /\n"
            "    suggest_new_node_title / evidence.\n\n"
        )

    def _background_execution_block(self, node) -> str:
        return (
            "BACKGROUND EXECUTION (critical for runs > 60 seconds):\n"
            "    Any task longer than 60s MUST be launched with `nohup` so it survives\n"
            "    session termination. Write EXECUTOR.json immediately after launch with\n"
            "    {pid, pid_starttime, started_at, command, log_file, expected_outputs,\n"
            "     timeout_hours}. Return to the orchestrator immediately after confirming\n"
            "    the background process is alive. Do NOT block on long work.\n\n"
        )

    def _anti_laziness_block(self) -> str:
        return (
            "ANTI-LAZINESS:\n"
            "    The artifact requirements below are checked by charter_validator.py — a\n"
            "    separate program that reads physical files on disk. Faking these (touching\n"
            "    empty files, writing fake JSON) produces a programmatic FAIL, branch dies.\n"
            "    A codex audit (external GPT-5 thread, sees an AUDIT_NONCE you don't know\n"
            "    + reads byte-offset challenges from your files) runs after validation.\n"
            "    Honest failure beats fake success. Write DEAD.md if the work cannot be\n"
            "    completed honestly in the budget.\n\n"
        )
