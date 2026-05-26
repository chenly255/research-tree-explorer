#!/usr/bin/env python3
"""research-tree CLI shim — v1.0 thin entry.

The 2233-line v0.5 implementation lives at scripts/tree_state_v05_legacy.py
as a read-only reference (its check_* validation functions are still called
by research_tree/workers/base.py via subprocess). All new development goes
in the research_tree/ package.

This shim exists so existing SKILL.md invocations (`python3 tree_state.py ...`)
keep working through v1.0. The CLI surface is preserved at the subcommand
level but the implementations are in research_tree.cli.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from research_tree.cli import main as v1_main
    return v1_main()


if __name__ == "__main__":
    sys.exit(main())
