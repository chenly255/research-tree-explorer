"""research-tree v1.0 — DAG-based research exploration for Claude Code.

Layered architecture:
    graph.py             — Node / Edge / Graph data model (single source of truth)
    branching_decider.py — structured fork judgment (Lily pain point 1)
    node_merger.py       — sibling complementarity detection (Lily pain point 2)
    workers/             — task_type-specific Worker classes (replaces SKILL.md rules)
    scheduler.py         — inotify event-driven dispatch (replaces poll-and-gate)
    migrator.py          — v0.5 tree.json -> v1.0 graph.json (one-way, auto-trigger)
    cli.py               — thin command shell

The old scripts/tree_state.py + scripts/charter_validator.py et al. survive as
compatibility shims; new development happens here.

See docs/V1-ARCHITECTURE.md for the full design contract.
"""

__version__ = "1.0.0"
