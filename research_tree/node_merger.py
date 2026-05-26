"""Sibling node merging — Lily pain point 2.

Design contract: docs/V1-ARCHITECTURE.md (section "node_merger.py").

v0.5 had no merge concept. Dead branches accumulated in a dead-branch
atlas; completed sibling branches sat next to each other in the tree
forever, each one a leaf. When sibling A audited GSVA on cohort X and
sibling B audited AUCell on cohort Y, the two results SHOULD form a
"merge" — a synthesis node that tells the combined story — but v0.5
had no machinery for that.

v1.0 NodeMerger:

detect_merge_opportunities(graph) → list[MergeProposal]
    Periodic scan (autopilot every N steps). Two completed sibling
    nodes are merge candidates iff:
      1. Same parent (siblings)
      2. Both lifecycle=done, neither abandoned/failed
      3. Neither already merged into a synthesis target (no prior
         merges-into edge from either node)
      4. Their work was COMPLEMENTARY, not redundant. We check this
         by extracting structured "axes" from each node's RESULT.md
         (atlas, cell type, metric, dimension) and demanding they
         touch DIFFERENT values on at least one axis.

apply_merge(proposal, graph) → str
    Creates a synthesis node with merges-into edges from the sources.
    Synthesis node is placed as a sibling of the sources (same parent).
    Its task_type is "analysis", its kind is "synthesis". info_value
    inherits max(sources.info_value).

Trigger: autopilot calls this every N steps (default 5). The orchestrator
surfaces proposals to Lily via a one-line progress.log entry; she can
opt-in to apply via `/research-tree merge <proposal_id>`.

Why no auto-apply: a merge changes the synthesis story Lily writes into
the paper. That's the "narrative" axis, which charter §2 says is the
user's call, not autopilot's.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .graph import Edge, Graph, Node, now_iso


# Axes we look for in node text. Each axis pattern produces a normalized
# "value" the node took on that axis. The patterns are CONSERVATIVE — false
# positives produce spurious merge proposals, which waste codex tokens later.
# We prefer to MISS a merge opportunity than to surface a noisy one; the
# scheduler re-runs detection every N steps so a real complementary pair
# will be re-considered as siblings accumulate more results.
AXIS_PATTERNS = {
    # atlas / dataset / cohort — only triggered by EXPLICIT "atlas X" or
    # "cohort X" wording, OR a known canonical author-disease-year pattern
    # like "krishna_rcc", "li_2022", "schulte_covid". We refuse to treat
    # arbitrary snake_case identifiers as atlas IDs (the v0.5 way caught
    # field names like "data_manifest" as atlases).
    "atlas": [
        re.compile(r"\b(atlas|cohort|dataset)\s+([a-z][\w]+(?:_[a-z]+)?)\b", re.IGNORECASE),
        # canonical author_disease (or author_disease_year) — at most one
        # underscore-separated suffix. Disease/tissue side must be a known
        # token to anchor; arbitrary identifiers don't match.
        re.compile(
            r"\b([a-z]{3,12}_(?:pdac|brca|tnbc|rcc|hcc|nsclc|covid|sle|lupus|"
            r"crohn|alzheimer|parkinson|asthma|diabetes|gbm|aml|cll|mds|"
            r"ms|ibd|psa|cd|uc|t2d|t1d|lupus|sjogren|ssc))(?:_\d{2,4})?\b",
            re.IGNORECASE,
        ),
        # known atlas project names (whitelist)
        re.compile(
            r"\b(tabula_sapiens|tabula_muris|hca|gtex_v\d|hubmap|"
            r"allen_brain|cellxgene_census|krishna|peng|wu_bc|li_2022|"
            r"schulte|perez)\b",
            re.IGNORECASE,
        ),
    ],
    # cell type — limited to canonical immunology / oncology terms. CL term
    # IDs (`CL:0000XXXX`) also caught.
    "cell_type": [
        re.compile(
            r"\b(t[_\- ]?cell|b[_\- ]?cell|fibroblast|monocyte|macrophage|"
            r"dendritic[_\- ]?cell|nk[_\- ]?cell|plasma[_\- ]?cell|"
            r"acinar|hepatocyte|epithelial|endothelial|stromal|neuron|"
            r"astrocyte|microglia|cardiomyocyte|pericyte|"
            r"cd4(?:\+|_pos)?[a-z]*|cd8(?:\+|_pos)?[a-z]*|treg|"
            r"exhausted|anergic|naive[_\- ]?cd[48])\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bcl[:_]\d{6,8}\b", re.IGNORECASE),
    ],
    # metric — sc-bias canonical metrics
    "metric": [
        re.compile(
            r"\b(fn[_\- ]?delta|over[_\- ]?estimation[_\- ]?ratio|"
            r"spearman|pearson|p[_\- ]?value|effect[_\- ]?size|"
            r"f1[_\- ]?score|auroc|auprc|accuracy|"
            r"odds[_\- ]?ratio|hazard[_\- ]?ratio)\b",
            re.IGNORECASE,
        ),
    ],
    # disease / phenotype
    "disease": [
        re.compile(
            r"\b(covid(?:[_\- ]?19)?|sle|lupus|pdac|brca|tnbc|rcc|hcc|nsclc|"
            r"alzheimer|parkinson|diabetes|t1d|t2d|asthma|copd|crohn|"
            r"ibd|gbm|aml|cll|sjogren|ssc|psa)\b",
            re.IGNORECASE,
        ),
    ],
    # foundation model name (sc-bias domain)
    "fm": [
        re.compile(
            r"\b(scgpt|geneformer|scfoundation|sccello|uce|"
            r"transcriptformer)\b",
            re.IGNORECASE,
        ),
    ],
}


# Minimum overlap_jaccard < this means the two siblings' work is on
# DIFFERENT values of at least one axis → complementary.
COMPLEMENTARY_AXIS_OVERLAP_MAX = 0.4

# To avoid proposing merges of trivially-redundant work, require that the
# total number of distinct axis values across the two siblings is at least
# this many (i.e., the merge story has some breadth).
MIN_TOTAL_AXIS_VALUES = 3


@dataclass
class MergeProposal:
    proposal_id: str
    source_nodes: list[str]
    parent_id: str
    proposed_kind: str = "synthesis"
    proposed_task_type: str = "analysis"
    proposed_title: str = ""
    rationale: str = ""
    complementary_axes: dict[str, list[str]] = field(default_factory=dict)
    # axis name → [values seen across source nodes]
    confidence: float = 0.0   # 0-1, currently a heuristic 0.5-0.9

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "source_nodes": self.source_nodes,
            "parent_id": self.parent_id,
            "proposed_kind": self.proposed_kind,
            "proposed_task_type": self.proposed_task_type,
            "proposed_title": self.proposed_title,
            "rationale": self.rationale,
            "complementary_axes": self.complementary_axes,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MergeProposal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------- detection ----------


def detect_merge_opportunities(graph: Graph, project_root: Path | None = None) -> list[MergeProposal]:
    """Scan the graph for sibling completed nodes whose work is complementary.

    Returns a list of MergeProposals — the caller decides whether to apply.
    Each proposal has a stable proposal_id derived from the source node ids
    so re-running detect won't duplicate proposals.
    """
    proposals: list[MergeProposal] = []

    # group completed-non-abandoned-non-already-merged nodes by parent
    completed_by_parent: dict[str, list[str]] = {}
    for nid, n in graph.nodes.items():
        if n.lifecycle != "done":
            continue
        if n.is_abandoned:
            continue
        # skip if already merged into a synthesis target
        if graph.merge_targets_of(nid):
            continue
        p = graph.parent_of(nid)
        if not p:
            continue
        completed_by_parent.setdefault(p, []).append(nid)

    for parent_id, sibling_ids in completed_by_parent.items():
        if len(sibling_ids) < 2:
            continue

        # try all pairs (small N — siblings under one parent rarely > 4)
        for i, a in enumerate(sibling_ids):
            for b in sibling_ids[i+1:]:
                proposal = _evaluate_pair(a, b, parent_id, graph, project_root)
                if proposal is not None:
                    proposals.append(proposal)

    return proposals


def _evaluate_pair(
    a_id: str,
    b_id: str,
    parent_id: str,
    graph: Graph,
    project_root: Path | None,
) -> MergeProposal | None:
    """Decide whether nodes a, b form a complementary pair worth merging."""
    a = graph.nodes[a_id]
    b = graph.nodes[b_id]

    # collect text for axis extraction: title + description + (optionally)
    # KEY_FINDING from RESULT.md if branch_dir is on disk
    text_a = _node_full_text(a, project_root)
    text_b = _node_full_text(b, project_root)

    # extract axis values from each
    axes_a = _extract_axis_values(text_a)
    axes_b = _extract_axis_values(text_b)

    # complementary: at least one axis where both have values AND they differ
    differing_axes: dict[str, list[str]] = {}
    for axis in AXIS_PATTERNS:
        vals_a = axes_a.get(axis, set())
        vals_b = axes_b.get(axis, set())
        if not vals_a or not vals_b:
            continue
        # Jaccard overlap on this axis. Low overlap = complementary on this axis.
        union = vals_a | vals_b
        intersection = vals_a & vals_b
        overlap = len(intersection) / len(union) if union else 0
        if overlap <= COMPLEMENTARY_AXIS_OVERLAP_MAX:
            differing_axes[axis] = sorted(union)

    if not differing_axes:
        return None

    total_axis_values = sum(len(v) for v in differing_axes.values())
    if total_axis_values < MIN_TOTAL_AXIS_VALUES:
        return None

    # build proposal
    primary_axis = max(differing_axes, key=lambda ax: len(differing_axes[ax]))
    proposal_id = f"merge-{a_id.replace('.','_')}-{b_id.replace('.','_')}"
    rationale = (
        f"siblings {a_id} and {b_id} explored complementary values on "
        f"{len(differing_axes)} axis/axes: " +
        ", ".join(f"{ax}={{{', '.join(differing_axes[ax][:4])}}}" for ax in differing_axes)
    )
    title = f"Synthesis: {primary_axis} × {len(differing_axes[primary_axis])} (from {a_id} + {b_id})"

    # confidence heuristic: more axes touched + more distinct values = higher
    confidence = min(0.9, 0.4 + 0.1 * len(differing_axes) + 0.05 * total_axis_values)

    # info_value of synthesis = max of sources, +1 if both ≥ 3
    iv_a = a.info_value if a.info_value is not None else 3
    iv_b = b.info_value if b.info_value is not None else 3

    return MergeProposal(
        proposal_id=proposal_id,
        source_nodes=[a_id, b_id],
        parent_id=parent_id,
        proposed_kind="synthesis",
        proposed_task_type="analysis",
        proposed_title=title,
        rationale=rationale,
        complementary_axes=differing_axes,
        confidence=confidence,
    )


def _node_full_text(node: Node, project_root: Path | None) -> str:
    """Combine title + description + RESULT.md KEY_FINDING if branch_dir exists.

    We deliberately do NOT fall back to scanning the whole RESULT.md when
    KEY_FINDING is absent — full RESULT.md contains code identifiers, file
    paths, library names that the conservative axis patterns still pick up
    as false positives (e.g. "data_manifest" looked like an atlas in the
    v0.5-style pattern). KEY_FINDING only, or title+description only.
    """
    parts = [node.title or "", node.description or ""]
    if project_root and node.artifacts.get("branch_dir"):
        result_md = project_root / node.artifacts["branch_dir"] / "RESULT.md"
        if result_md.exists():
            try:
                text = result_md.read_text(errors="replace")
                kf_match = re.search(
                    r"key[_\- ]?finding\s*[:=]\s*(.{0,800})",
                    text,
                    re.IGNORECASE | re.DOTALL,
                )
                if kf_match:
                    parts.append(kf_match.group(1))
                # no fallback — if no KEY_FINDING block, just use title+description
            except OSError:
                pass
    return " ".join(p.strip() for p in parts if p.strip())


def _extract_axis_values(text: str) -> dict[str, set[str]]:
    """Run all axis patterns over `text`, return axis → set of matched values.

    When a pattern has multiple groups, we take the LAST non-empty group as
    the value (so "atlas X" patterns return "X", not the "atlas" trigger).
    """
    out: dict[str, set[str]] = {}
    text_lower = text.lower()
    for axis, patterns in AXIS_PATTERNS.items():
        vals: set[str] = set()
        for pat in patterns:
            for m in pat.finditer(text_lower):
                # take the last non-empty group; if no groups, the whole match
                value = None
                if m.groups():
                    for g in reversed(m.groups()):
                        if g:
                            value = g
                            break
                if value is None:
                    value = m.group(0)
                v = value.strip().lower()
                v = re.sub(r"[_\-\s]+", "_", v)
                if len(v) >= 2:
                    vals.add(v)
        if vals:
            out[axis] = vals
    return out


# ---------- application ----------


def apply_merge(proposal: MergeProposal, graph: Graph) -> str:
    """Apply a MergeProposal: create the synthesis node + merges-into edges.

    Returns the new synthesis node id.

    Caller is responsible for graph_lock + persistence.
    """
    # double-check sources still exist + are done
    for sid in proposal.source_nodes:
        if sid not in graph.nodes:
            raise ValueError(f"merge source {sid!r} not in graph")
        if graph.nodes[sid].lifecycle != "done":
            raise ValueError(f"merge source {sid!r} is {graph.nodes[sid].lifecycle}, expected done")

    # allocate id under the shared parent
    new_id = graph.next_id_under(proposal.parent_id)
    title = proposal.proposed_title or f"Synthesis of {', '.join(proposal.source_nodes)}"

    # info_value: max of sources, + 1 if multi-axis merge (more complete story)
    src_iv = [graph.nodes[sid].info_value for sid in proposal.source_nodes]
    src_iv = [v for v in src_iv if v is not None]
    max_iv = max(src_iv) if src_iv else 3
    if len(proposal.complementary_axes) >= 2 and max_iv < 5:
        max_iv += 1

    synth = Node(
        id=new_id,
        kind=proposal.proposed_kind,
        task_type=proposal.proposed_task_type,
        title=title[:200],
        description=(
            f"Synthesis of sibling completions {', '.join(proposal.source_nodes)}. "
            f"{proposal.rationale} "
            f"Complementary axes: {json.dumps(proposal.complementary_axes, ensure_ascii=False)}"
        ),
        lifecycle="created",
        is_branched=False,
        is_abandoned=False,
        cost_budget_hours=2.0,
        info_value=max_iv,
        artifacts={
            "branch_dir": f".research-tree/branches/{new_id}",
            "merge_proposal_id": proposal.proposal_id,
            "merge_sources": proposal.source_nodes,
            "complementary_axes": proposal.complementary_axes,
            "confidence": proposal.confidence,
        },
    )
    graph.add_node(synth)

    # parent-of edge from shared parent
    if proposal.parent_id in graph.nodes or proposal.parent_id == "root":
        graph.add_edge(Edge(
            src=proposal.parent_id,
            dst=new_id,
            kind="parent-of",
        ))

    # merges-into edges from sources
    for sid in proposal.source_nodes:
        graph.add_edge(Edge(
            src=sid,
            dst=new_id,
            kind="merges-into",
            metadata={
                "proposal_id": proposal.proposal_id,
                "confidence": proposal.confidence,
            },
        ))

    return new_id


# ---------- CLI ----------


def main() -> int:
    """python -m research_tree.node_merger <project_root> [--apply <proposal_id>]"""
    import argparse
    import sys
    from .graph import Graph, graph_path, graph_lock

    p = argparse.ArgumentParser(description="Detect / apply node merges")
    p.add_argument("project_root")
    p.add_argument("--apply", help="proposal_id to apply (else just detects and prints)")
    args = p.parse_args()

    root = Path(args.project_root).resolve()
    gp = graph_path(root)
    if not gp.exists():
        print(f"ERROR: no graph.json at {gp}", file=sys.stderr)
        return 2

    if args.apply:
        with graph_lock(root):
            g = Graph.load(gp)
            proposals = detect_merge_opportunities(g, project_root=root)
            target = next((p for p in proposals if p.proposal_id == args.apply), None)
            if target is None:
                print(f"ERROR: no current proposal with id {args.apply!r}", file=sys.stderr)
                print("Available proposals:", file=sys.stderr)
                for prop in proposals:
                    print(f"  - {prop.proposal_id}", file=sys.stderr)
                return 2
            new_id = apply_merge(target, g)
            g.save(gp)
            print(new_id)
            return 0

    # detect mode
    g = Graph.load(gp)
    proposals = detect_merge_opportunities(g, project_root=root)
    print(json.dumps([p.to_dict() for p in proposals], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
