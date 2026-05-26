"""Structured branching decision — Lily pain point 1.

Design contract: docs/V1-ARCHITECTURE.md (section "branching_decider.py").

The v0.5 approach gave the proposer subagent too much latitude — it picked
the number of candidates AND filled in any "skip_expansion" reasoning. The
sc-bias tree ended up with siblings whose work was essentially identical.

The v1.0 split: TWO deterministic decision points, both run BEFORE the
proposer's choices are committed.

decide_to_fork(parent, graph, ctx) → ForkDecision
    Called BEFORE expand spawns the proposer. Says: "should we even invoke
    a proposer on this parent, or should it just direct-execute?"
    Uses cost-value gates + depth gates. No candidates yet, so no
    similarity check possible.

decide_to_accept_candidate(candidate, parent_id, graph, ctx) → CandidateDecision
    Called AFTER the proposer returns each candidate, BEFORE it is added to
    the graph. Says: "is this candidate genuinely new, or is it a re-skin of
    an existing sibling?"
    Uses similarity check + axis overlap. THIS is where most "no-meaning
    forks" get caught — the proposer might propose 3 candidates that are
    actually the same work; the Decider keeps the first, MERGE_WITH-redirects
    the rest.

Both decisions are logged to progress.log so future Claude sessions can
trace why a fork did or didn't happen.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from .graph import Graph, Node


# Same-parent threshold: tighter, because sibling vocabulary tends to be high
# (they share the parent's domain). 0.65 is empirically tuned on sc-bias —
# 1.3.1 vs synthetic "essentially same as 1.3.1" → 0.73 (triggers); 1.3.1 vs
# 1.3.2 (real architectural fork) → 0.71 (also triggers, false positive on
# vocabulary alone). Solution: require axis-overlap as an AND condition for
# same-parent matches in the 0.65-0.85 grey zone.
SIMILARITY_SAME_PARENT_THRESHOLD = 0.65
SIMILARITY_SAME_PARENT_HARD_THRESHOLD = 0.85   # above this, always MERGE_WITH regardless of axis

# Cross-tree threshold: stricter, because cross-tree similar nodes are often
# child/parent vocabulary leak (e.g. 1.3 vs 1.3.2 = 0.80 because parent embeds
# child concept). We don't want to merge them. Real cross-tree duplicates need
# >= 0.85 cosine.
SIMILARITY_CROSS_TREE_THRESHOLD = 0.85

# Cost-value gate thresholds.
LOW_INFO_VALUE_CUTOFF = 2
HIGH_COST_HOURS_CUTOFF = 4.0


@dataclass
class ForkDecision:
    kind: Literal["FORK", "DIRECT_EXECUTE"]
    reason: str
    min_candidates: int | None = None
    constraints: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "min_candidates": self.min_candidates,
            "constraints": self.constraints,
        }


@dataclass
class CandidateDecision:
    kind: Literal["ADD", "MERGE_WITH", "REJECT"]
    reason: str
    merge_target: str | None = None   # MERGE_WITH the node with this id
    similarity_score: float | None = None
    axis_overlap_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "merge_target": self.merge_target,
            "similarity_score": self.similarity_score,
            "axis_overlap_score": self.axis_overlap_score,
        }


@dataclass
class DeciderContext:
    max_depth: int = 5
    max_branches: int = 4


# =====================================================================
# API 1 — decide whether to fork at all (called before proposer)
# =====================================================================


def decide_to_fork(parent: Node, graph: Graph, ctx: DeciderContext | None = None) -> ForkDecision:
    """Decide whether to invoke the proposer on `parent`, or skip expansion
    and direct-execute the parent itself.

    Gates run in order. First match wins.

    Gate A (cost-value): low info_value + high cost → not worth forking.
                          info_value=5 → forced FORK (headline competition).
    Gate B (depth):       depth 0 → forced FORK (root must diversify).
                          depth >= max-1 → DIRECT_EXECUTE (no room).
    Gate C (fallthrough): FORK with constraints. The proposer will propose
                          1-4 candidates; the per-candidate Decider in API 2
                          will catch duplicates.
    """
    ctx = ctx or DeciderContext()

    # Gate A: cost-value
    iv = parent.info_value
    cost = parent.cost_budget_hours or 0
    if iv is not None:
        if iv <= LOW_INFO_VALUE_CUTOFF and cost > HIGH_COST_HOURS_CUTOFF:
            return ForkDecision(
                kind="DIRECT_EXECUTE",
                reason=f"low value (info_value={iv}) + high cost ({cost}h) → not worth forking",
            )
        if iv >= 5:
            return ForkDecision(
                kind="FORK",
                reason="info_value=5 (headline-load-bearing) requires head-to-head competition",
                min_candidates=2,
                constraints={"must_diversify_axis": True, "min_info_value": 4},
            )

    # Gate B: depth
    depth = graph.depth_of(parent.id)
    if depth == 0:
        return ForkDecision(
            kind="FORK",
            reason="depth=0 (root) must diversify across approach families (charter §2)",
            min_candidates=2,
            constraints={"must_diversify_axis": True, "min_info_value": 3},
        )
    if depth >= ctx.max_depth - 1:
        return ForkDecision(
            kind="DIRECT_EXECUTE",
            reason=f"depth={depth} at/near max_depth={ctx.max_depth}, no sub-forking allowed",
        )

    # Gate C: fallthrough — fork with constraints. Proposer free to propose
    # 1-N candidates; API 2 catches the duplicates.
    return ForkDecision(
        kind="FORK",
        reason="no early gate fired, fork with proposer (per-candidate filtering applies)",
        min_candidates=1,
        constraints={
            "must_diversify_axis": True,
            "min_info_value": 3,
            "max_candidates": min(4, ctx.max_branches),
        },
    )


# =====================================================================
# API 2 — decide whether to accept a proposer's candidate (called per candidate)
# =====================================================================


def decide_to_accept_candidate(
    candidate: Node,
    parent_id: str,
    graph: Graph,
    ctx: DeciderContext | None = None,
) -> CandidateDecision:
    """Decide whether to add `candidate` as a child of `parent_id`, or
    redirect to an existing sibling via MERGE_WITH.

    The candidate Node does NOT need to be in the graph yet — this is called
    BEFORE add_node. `candidate.id` may be a placeholder or a freshly
    allocated id; that's fine.

    Gates run in order. First match wins.

    Gate 1 (same-parent hard duplicate):
        Same parent, cosine ≥ 0.85 → MERGE_WITH
        This catches the most painful case: the proposer returned 2-4
        candidates and ≥2 of them are essentially the same work re-skinned.

    Gate 2 (axis-only re-exploration):
        The candidate's OWN description names an explicit axis ("X vs Y",
        "X 选型", etc.) that a sibling has already explored. cosine is low
        (proposer used different vocabulary) but axis tokens overlap. This
        catches the proposer trying to dress up a re-fork as new work.

    Gate 3 (cross-tree hard duplicate):
        Different parent, cosine ≥ 0.85, NOT in lineage → MERGE_WITH.
        Rare but worth catching (the "rename a path from A to F" case).

    Gate 4 (default): ADD.

    Notes:
    - We deliberately do NOT use a soft-duplicate gate on cosine 0.65-0.85.
      Sibling candidates under a forked parent naturally share vocabulary
      (they're variants of the same parent decision), and the false-positive
      rate is too high. Real soft duplicates get caught later by NodeMerger
      after candidates have been EXECUTED and we can compare actual results.
    """
    ctx = ctx or DeciderContext()
    text_a = _node_text(candidate)
    if not text_a:
        return CandidateDecision(
            kind="ADD",
            reason="candidate has no text content — pass through",
        )

    # Compute similarity to every other node, partitioned by same-parent vs cross-tree.
    same_parent_matches: list[tuple[float, str]] = []
    cross_tree_matches: list[tuple[float, str]] = []
    for other_id, other in graph.nodes.items():
        if other_id == candidate.id or other_id == "root":
            continue
        if other.is_abandoned or other.lifecycle == "failed":
            continue
        text_b = _node_text(other)
        if not text_b:
            continue
        sim = _cosine_tfidf(text_a, text_b)
        if graph.parent_of(other_id) == parent_id:
            same_parent_matches.append((sim, other_id))
        else:
            cross_tree_matches.append((sim, other_id))

    same_parent_matches.sort(reverse=True)
    cross_tree_matches.sort(reverse=True)

    # ---- Gate 1: same-parent hard duplicate ----
    if same_parent_matches and same_parent_matches[0][0] >= SIMILARITY_SAME_PARENT_HARD_THRESHOLD:
        sim, other_id = same_parent_matches[0]
        return CandidateDecision(
            kind="MERGE_WITH",
            reason=f"hard duplicate of sibling {other_id} (cosine={sim:.3f} ≥ {SIMILARITY_SAME_PARENT_HARD_THRESHOLD})",
            merge_target=other_id,
            similarity_score=sim,
        )

    # ---- Gate 2: explicit axis re-exploration ----
    # Use ONLY the candidate's own description (no parent context). If the
    # candidate explicitly names an axis a sibling already explored, reject.
    cand_self_axes = _extract_axes(candidate)
    if cand_self_axes:
        for sim, other_id in same_parent_matches:
            sib_self_axes = _extract_axes(graph.nodes[other_id])
            if not sib_self_axes:
                continue
            axis_score = _axis_overlap_score(cand_self_axes, sib_self_axes)
            if axis_score >= 0.5:
                common = next(iter(cand_self_axes & sib_self_axes), list(sib_self_axes)[0])
                return CandidateDecision(
                    kind="REJECT",
                    reason=f"axis {common!r} already explored by sibling {other_id} (axis overlap={axis_score:.2f})",
                    similarity_score=sim,
                    axis_overlap_score=axis_score,
                )

    # ---- Gate 3: cross-tree hard duplicate ----
    if cross_tree_matches and cross_tree_matches[0][0] >= SIMILARITY_CROSS_TREE_THRESHOLD:
        sim, other_id = cross_tree_matches[0]
        if not _is_in_lineage(candidate.id, other_id, graph, parent_id):
            return CandidateDecision(
                kind="MERGE_WITH",
                reason=f"cross-tree hard duplicate of {other_id} (cosine={sim:.3f} ≥ {SIMILARITY_CROSS_TREE_THRESHOLD})",
                merge_target=other_id,
                similarity_score=sim,
            )

    # Gate 4: default
    return CandidateDecision(
        kind="ADD",
        reason="no duplicate detected",
    )


def _is_in_lineage(candidate_id: str, other_id: str, graph: Graph, candidate_parent_id: str) -> bool:
    """Check if `other_id` is an ancestor or descendant of where we'd place
    `candidate`. Used to suppress parent-child vocabulary-leak false positives.

    Since candidate isn't in graph yet, ancestor is checked via candidate_parent_id.
    Descendant is checked via candidate_id (if it's already been allocated).
    """
    # ancestor walk from candidate_parent_id
    cur = candidate_parent_id
    seen = set()
    while cur and cur != "root" and cur not in seen:
        seen.add(cur)
        if cur == other_id:
            return True
        cur = graph.parent_of(cur)
    # descendant walk: if candidate.id is in graph, see if other is in its subtree
    # (rare case: candidate added but Decider being run for sanity check)
    if candidate_id in graph.nodes:
        stack = [candidate_id]
        seen_d = set()
        while stack:
            cur = stack.pop()
            if cur in seen_d:
                continue
            seen_d.add(cur)
            if cur == other_id:
                return True
            stack.extend(graph.children_of(cur))
    return False


# ---------- text + similarity helpers ----------


def _node_text(node: Node) -> str:
    parts = [node.title or "", node.description or ""]
    return " ".join(p.strip() for p in parts if p.strip()).lower()


def _tokenize(text: str) -> list[str]:
    """Mixed CJK + ASCII tokenizer. Each CJK char is its own token;
    ASCII word boundaries via \\w+."""
    tokens = []
    for m in re.finditer(r"[一-鿿]|[a-z0-9]+", text):
        tok = m.group(0)
        if len(tok) >= 1:
            tokens.append(tok)
    return tokens


def _cosine_tfidf(a: str, b: str) -> float:
    """Length-aware similarity: max(cosine, containment).

    Plain cosine collapses when two texts have very different lengths
    (a 100-char near-duplicate of a 600-char text scores low because the
    longer text's L2 norm dominates the denominator).

    `containment(a, b)` = (overlap tokens, weighted by min-count) /
    (total tokens of the shorter text). Captures the "short text is mostly
    inside long text" case that pure cosine misses.

    We take max of the two so neither failure mode wins.
    """
    toks_a = _tokenize(a)
    toks_b = _tokenize(b)
    if not toks_a or not toks_b:
        return 0.0
    ca, cb = Counter(toks_a), Counter(toks_b)
    shared = set(ca) & set(cb)
    if not shared:
        return 0.0

    # cosine
    dot = sum(ca[t] * cb[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    cosine = dot / (norm_a * norm_b) if norm_a > 0 and norm_b > 0 else 0.0

    # containment: how much of the shorter document is inside the longer one
    short, long_ = (ca, cb) if sum(ca.values()) <= sum(cb.values()) else (cb, ca)
    short_total = sum(short.values())
    overlap = sum(min(short[t], long_[t]) for t in short if t in long_)
    containment = overlap / short_total if short_total > 0 else 0.0

    return max(cosine, containment)


# ---------- axis extraction (per-candidate + parent context) ----------


_AXIS_PATTERNS = [
    # "A vs B" (English) — explicit "vs" between two distinct tokens
    re.compile(r"([\w\-]+(?:\s+vs\s+[\w\-]+)+)", re.IGNORECASE),
    # "X 对比 Y" / "X 选型" / "X 切换"
    re.compile(r"([一-鿿\w\-]+\s*(?:对比|选型|切换|对照)\s*[一-鿿\w\-]+)"),
    # "在 X 与 Y 之间"
    re.compile(r"在([一-鿿\w\-]+)\s*[与和]\s*([一-鿿\w\-]+)\s*之间"),
]


def _extract_axes(node: Node) -> set[str]:
    text = _node_text(node)
    axes = set()
    for pat in _AXIS_PATTERNS:
        for m in pat.finditer(text):
            phrase = m.group(0).strip().lower()
            phrase = re.sub(r"\s+", " ", phrase)
            # filter out spurious "head vs ..." matches that are too generic
            if len(phrase) >= 6:
                axes.add(phrase)
    return axes


def _extract_axes_with_context(node: Node, parent_id: str, graph: Graph) -> set[str]:
    """Include the parent node's axis tokens. Critical for sibling pairs
    where the parent's title carries the axis (e.g. 1.3 = "translator
    architecture: 3 独立 StateHead vs 1 共享 head + source-id embedding")
    while individual children don't repeat the explicit "vs" phrase."""
    axes = _extract_axes(node)
    if parent_id in graph.nodes:
        parent_axes = _extract_axes(graph.nodes[parent_id])
        axes |= parent_axes
    return axes


def _axis_overlap_score(axes_a: set[str], axes_b: set[str]) -> float:
    """Jaccard-on-token overlap between two axis phrase sets. Robust to word
    reordering ("GSVA vs AUCell" vs "AUCell vs GSVA") and surrounding noise.
    """
    def axis_tokens(axes: set[str]) -> set[str]:
        toks = set()
        STOP = {"vs", "对比", "选型", "切换", "对照", "在", "与", "和", "之间", "v"}
        for a in axes:
            for t in _tokenize(a):
                if t not in STOP and len(t) > 1:
                    toks.add(t)
        return toks

    ta, tb = axis_tokens(axes_a), axis_tokens(axes_b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------- CLI ----------


def main() -> int:
    """python -m research_tree.branching_decider <project_root> [--node ID --mode fork|candidate]"""
    import argparse
    import json
    import sys
    from pathlib import Path
    from .graph import graph_path

    p = argparse.ArgumentParser(description="Branching decision diagnostics")
    p.add_argument("project_root")
    p.add_argument("--mode", choices=["fork", "candidate", "scan"], default="scan",
                   help="fork: decide_to_fork on one node; candidate: hypothetical candidate vs siblings; scan: run fork-decision on every leaf")
    p.add_argument("--node", help="node id (required for --mode fork)")
    p.add_argument("--parent", help="parent id (required for --mode candidate)")
    p.add_argument("--candidate-title", help="--mode candidate hypothetical title")
    p.add_argument("--candidate-desc", help="--mode candidate hypothetical description")
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--max-branches", type=int, default=4)
    args = p.parse_args()

    root = Path(args.project_root).resolve()
    gp = graph_path(root)
    if not gp.exists():
        print(f"ERROR: no graph.json at {gp}", file=sys.stderr)
        return 2
    g = Graph.load(gp)
    ctx = DeciderContext(max_depth=args.max_depth, max_branches=args.max_branches)

    if args.mode == "fork":
        if args.node not in g.nodes:
            print(f"ERROR: node {args.node!r} not found", file=sys.stderr)
            return 2
        d = decide_to_fork(g.nodes[args.node], g, ctx)
        print(json.dumps(d.to_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.mode == "candidate":
        if not args.parent or not args.candidate_title:
            print("ERROR: --parent and --candidate-title required", file=sys.stderr)
            return 2
        candidate = Node(
            id="__candidate__",
            kind="custom",
            task_type="mixed",
            title=args.candidate_title,
            description=args.candidate_desc or args.candidate_title,
        )
        d = decide_to_accept_candidate(candidate, args.parent, g, ctx)
        print(json.dumps(d.to_dict(), indent=2, ensure_ascii=False))
        return 0
    # scan mode — fork-decision on every non-done non-abandoned node
    for nid, n in sorted(g.nodes.items()):
        if n.lifecycle in ("done", "failed") or n.is_abandoned:
            continue
        d = decide_to_fork(n, g, ctx)
        print(f"{nid:8} {d.kind:14} {d.reason}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
