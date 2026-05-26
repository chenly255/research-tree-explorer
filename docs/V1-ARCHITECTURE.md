# research-tree v1.0 架构定稿

写于 2026-05-26. Lily 解冻后的完整重写设计. 本文是落地合同, 写代码前定稿; 写完后不再改.

## 出发点 (六条结构问题, DESIGN-PRINCIPLES.md 已识别)

1. 树是错的数据结构, DAG 才对
2. status 单字段过载
3. 五个 action 不正交
4. 轮询而非事件驱动
5. 双 subagent prompt 浪费
6. SCHEMA_VERSION 存在本身就是设计失败

**Lily 拍板** (2026-05-26 此次会话):
- 完整 v1.0 重写, 六条全做
- sc-bias 现有 `.research-tree/` 自动迁移、原地升级
- 优化完了 commit + push 云端, 然后她才开始 sc-bias

**Lily 两个具体痛点 (顶层目标)**:
- 智能分叉: 不要"在没什么意义的事情上做分叉"
- 节点合并: "什么时候应该跟其他节点合并" 完全没做

## 顶层模块图

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 3 — SKILL.md (~300 行, 从 1042 行砍下来)                      │
│   只描述 subcommand dispatch + autopilot 一步流程                   │
│   不再有 task_type artifact rules, 不再有版本号注释                 │
└─────────────────────────────────────────────────────────────────────┘
                              │ 调用
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 2 — Python 包 research_tree/                                  │
│                                                                     │
│   graph.py            — Node / Edge / Graph 数据类 + 持久化         │
│   branching_decider.py — 结构化分叉判据 (智能分叉)                  │
│   node_merger.py      — 兄弟节点合并检测 (节点合并)                 │
│   workers/            — Worker 接口 + 4 个子类                      │
│     base.py            (Worker Protocol, WorkerResult)              │
│     training.py        (TrainingWorker, 含 charter 验证)            │
│     audit.py           (AuditWorker)                                │
│     analysis.py        (AnalysisWorker)                             │
│     data_acquisition.py (DataAcquisitionWorker)                     │
│   scheduler.py        — inotify 事件驱动 + polling fallback         │
│   migrator.py         — v0.5 (tree.json) → v1.0 (graph.json)        │
│   cli.py              — 薄命令行入口 (scripts/tree_state.py 重写)   │
└─────────────────────────────────────────────────────────────────────┘
                              │ 读写
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 1 — 磁盘状态 .research-tree/                                  │
│                                                                     │
│   graph.json          — 新格式 (Nodes + Edges 并列)                 │
│   tree.json           — v0.5 旧文件, migrator 读完落新格式后保留    │
│                         一个 milestone 不删, 之后纯靠 graph.json    │
│   branches/<node_id>/ — 同 v0.5, 每分支隔离工作目录                 │
│   audits/             — 同 v0.5                                     │
│   events.log          — 新增, scheduler 发生的事件序列              │
│   progress.log        — 同 v0.5                                     │
│   AWAITING_HUMAN.md   — 同 v0.5, 但事件驱动后大幅减少触发频率       │
└─────────────────────────────────────────────────────────────────────┘
```

## Layer 1: 磁盘 schema 定稿

### graph.json (v1.0 新主文件)

```json
{
  "project": "sc-bias",
  "project_root": "/data3/liying/sc-bias",
  "root_idea": "...",
  "created_at": "2026-05-26T...",
  "updated_at": "2026-05-26T...",
  "nodes": {
    "<node_id>": {
      "id": "<node_id>",
      "kind": "approach|architecture|experiment|ablation|narrative|synthesis|custom",
      "task_type": "training|audit|analysis|data-acquisition|framing-decision|mixed",
      "title": "<≤200 chars>",
      "description": "<2-4 sentences>",

      "lifecycle": "created|running|done|failed",
      "is_branched": false,
      "is_abandoned": false,

      "cost_budget_hours": null,
      "info_value": null,

      "score": null,
      "artifacts": {
        "branch_dir": ".research-tree/branches/<id>",
        "result_md": null,
        "death_reason": null,
        "death_evidence": null,
        "completion_proof": null,
        "junction_audit_id": null,
        "spawned_by_agent": null,
        "repair_attempts": 0,
        "last_failure_context": null
      },

      "created_at": "...",
      "updated_at": "..."
    }
  },
  "edges": [
    {
      "src": "1",
      "dst": "1.1",
      "kind": "parent-of",
      "created_at": "...",
      "metadata": {}
    },
    {
      "src": "1.4.3",
      "dst": "1.4.1",
      "kind": "hard-dep",
      "created_at": "...",
      "metadata": {}
    },
    {
      "src": "1.2",
      "dst": "2.1",
      "kind": "merges-into",
      "created_at": "...",
      "metadata": {"basis": "complementary atlases"}
    }
  ],
  "audits": { /* 同 v0.5 */ },
  "global_constraints": {
    "max_depth": 5,
    "max_branches_per_junction": 4,
    "max_total_nodes": 30,
    "max_gpu_hours_total": 48.0
  },
  "stats": {
    "nodes_total": 0,
    "by_lifecycle": {"created": 0, "running": 0, "done": 0, "failed": 0},
    "by_abandoned": 0,
    "gpu_hours_used": 0.0
  }
}
```

**关键改变 vs v0.5**:

| v0.5 字段 | v1.0 去处 | 说明 |
|---|---|---|
| `node.parent` | `parent-of` edge | 节点不再持有父亲 id |
| `node.children` | 从 edges 反查 | `graph.children_of(node_id)` |
| `node.depends_on` | `hard-dep` edges | 不再是节点字段 |
| `node.depends_on_soft` | `soft-dep` edges | 不再是节点字段 |
| `node.parallel_group` | `parallel-with` edges (m:n) | 不再是字符串 tag |
| `node.status` enum 6 值 | `lifecycle` 4 值 + `is_branched` + `is_abandoned` | 三轴正交 |
| `node.branch_dir` | `node.artifacts.branch_dir` | artifacts 子对象内聚 |
| `node.death_reason` | `node.artifacts.death_reason` | 同上 |
| `node.budget_hours_min` / `full` | `node.cost_budget_hours` 单字段 | 不再 min/full 分裂 (proposer 估的 min 就是 budget) |
| `node.info_value_score` | `node.info_value` | 改名简化 |
| `node.direct_executable` | 推断: `not is_branched and not has_pending_children` | 不再是字段 |
| `SCHEMA_VERSION` | **不存在** | 永远 additive, 新字段默认 null |

**新字段不破坏旧读者**: 任何老代码读 graph.json 都能跳过自己不认识的字段. 新增字段总是 optional + default null/empty.

### tree.json (v0.5 旧文件)

`migrator.py` 第一次跑时把 `tree.json` 读进来, 转成 `graph.json` 落盘. **不删除 `tree.json`**, 保留一个 milestone, 方便 Lily 想看老格式时回查. 主代码自此只读 graph.json.

### events.log (新文件)

scheduler 写入. 每行一个 JSON 事件:

```json
{"t":"2026-05-26T11:30:00","kind":"background_process_exit","node":"1.2","pid":1753176,"exit_code":0,"result_md":true}
{"t":"...","kind":"audit_complete","node":"1.4.3","verdict":"PASS"}
{"t":"...","kind":"merge_proposed","src_nodes":["1.2","1.4.3"],"target":"5.1"}
```

事件类型 (扩展 list 在 scheduler.py):
- `background_process_exit` (executor 进程退出)
- `result_md_written` (RESULT.md 落盘)
- `dead_md_written` (DEAD.md 落盘)
- `subtree_fork_written`
- `subtree_pivot_written`
- `audit_complete`
- `node_lifecycle_changed`
- `merge_proposed`

## Layer 2: 模块详细设计

### graph.py — 核心数据模型

```python
@dataclass
class Node:
    id: str
    kind: str
    task_type: str
    title: str
    description: str
    lifecycle: Literal["created", "running", "done", "failed"]
    is_branched: bool
    is_abandoned: bool
    cost_budget_hours: float | None
    info_value: int | None     # 1-5
    score: float | None
    artifacts: dict
    created_at: str
    updated_at: str

@dataclass
class Edge:
    src: str
    dst: str
    kind: Literal["parent-of", "hard-dep", "soft-dep",
                  "merges-into", "derived-from", "parallel-with"]
    created_at: str
    metadata: dict

class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]

    # 关系查询 (替代 v0.5 的 node.children / node.parent / node.depends_on)
    def parent_of(self, node_id) -> str | None
    def children_of(self, node_id) -> list[str]
    def depth_of(self, node_id) -> int     # 从 root parent-of 链路计算
    def hard_deps_of(self, node_id) -> list[str]
    def soft_deps_of(self, node_id) -> list[str]
    def parallel_peers_of(self, node_id) -> list[str]
    def merge_sources_of(self, node_id) -> list[str]    # synthesis 节点的源
    def merge_targets_of(self, node_id) -> list[str]    # 节点被合并到哪些 synthesis

    # 写入 (state-lock 内)
    def add_node(self, node: Node) -> None
    def add_edge(self, edge: Edge) -> None
    def remove_edge(self, src, dst, kind) -> None

    # 三轴 status helpers
    def is_alive(self, node_id) -> bool      # lifecycle != failed and not is_abandoned
    def is_pickable(self, node_id) -> bool   # lifecycle == created and hard_deps all done
    def has_pending_children(self, node_id) -> bool

    # 持久化
    def save(self, path: Path) -> None
    @classmethod
    def load(cls, path: Path) -> "Graph"
```

### 三轴 status 映射 (v0.5 status → v1.0 三轴)

| v0.5 `status` | `lifecycle` | `is_branched` | `is_abandoned` |
|---|---|---|---|
| `pending` | `created` | `false` | `false` |
| `expanded` | `created` 或 `done` | `true` | `false` |
| `running` | `running` | (保留) | `false` |
| `completed` | `done` | (保留) | `false` |
| `dead` | `failed` | (保留) | `false` |
| `abandoned` | (保留) | (保留) | `true` |

**关键点**: 一个 expanded 节点的 lifecycle 是它**自己工作**的状态; is_branched 只表示"已加过子"; 这俩是正交的, 一个父节点可以"已展开 + 自己也跑完了"或"已展开 + 自己跑挂了".

### branching_decider.py — 智能分叉 (Lily 痛点 1)

```python
@dataclass
class BranchingDecision:
    kind: Literal["FORK", "DIRECT_EXECUTE", "MERGE_WITH", "ASK_PROPOSER"]
    reason: str                          # 给 progress.log 留痕
    target_node_id: str | None = None    # MERGE_WITH 时填
    min_candidates: int | None = None    # FORK 时填 (root depth=0 必 ≥2)
    constraints: dict | None = None      # ASK_PROPOSER 时填 (info_value gate / similarity gate)

def decide(node: Node, graph: Graph, ctx: Context) -> BranchingDecision:
    """五条判据顺序检查, 第一条命中即返回. 不让 LLM 自由发挥."""

    # 判据 1: 相似节点检测 (防止"在没什么意义的事情上做分叉")
    #   - description 余弦相似度 (Levenshtein fallback) ≥ 0.85
    #   - 或者 title 完全包含同一关键短语
    #   → 返回 MERGE_WITH(existing_id)
    similar = find_similar_nodes(node, graph, threshold=0.85)
    if similar:
        return BranchingDecision("MERGE_WITH", f"sim={similar[0].sim_score:.2f} with {similar[0].id}", target_node_id=similar[0].id)

    # 判据 2: info_value × cost 闸门
    #   - info_value ≤ 2 且 cost_budget_hours > 4 → DIRECT (低价值高成本不值得探索分叉)
    #   - info_value 是 5 → 强制 FORK (头条候选必须 head-to-head)
    if node.info_value is not None and node.info_value <= 2 and (node.cost_budget_hours or 0) > 4:
        return BranchingDecision("DIRECT_EXECUTE", f"low-value (iv={node.info_value}) high-cost ({node.cost_budget_hours}h)")
    if node.info_value == 5:
        return BranchingDecision("FORK", "headline-load-bearing must fork", min_candidates=2)

    # 判据 3: 深度规则 (charter §2 + 物理边界)
    depth = graph.depth_of(node.id)
    if depth == 0:
        return BranchingDecision("FORK", "depth=0 root must diversify", min_candidates=2)
    if depth >= ctx.max_depth - 1:
        return BranchingDecision("DIRECT_EXECUTE", f"depth={depth} near max, no more sub-forks")

    # 判据 4: 兄弟已探索相同维度 (防止重复 fork)
    siblings = graph.parallel_peers_of(node.id) + [s for s in graph.children_of(graph.parent_of(node.id) or "root") if s != node.id]
    if has_explored_same_axis(node, siblings, graph):
        return BranchingDecision("DIRECT_EXECUTE", f"sibling already explored same axis")

    # 判据 5: 真正的 multi-arm decision? 交给 proposer, 但带结构化约束
    return BranchingDecision(
        "ASK_PROPOSER",
        "honest research judgment required",
        constraints={
            "must_diversify_axis": True,    # 候选必须在某个维度上正交
            "min_info_value": 3,            # 候选 info_value 必须 ≥ 3, 否则不值得 fork
            "max_candidates": min(4, ctx.max_branches),
        }
    )
```

**相似度检测细节**:
- 主路径: TF-IDF + 余弦相似度 (sklearn 已装), description + title 拼接, threshold 0.85
- Fallback: 当 sklearn 不可用时, 用 Levenshtein 字符级距离归一化, threshold 0.30 (距离越小越像)
- 不用 embedding model (避免 GPU/网络依赖). 0.85 是从 sc-bias 历史观察经验值

**"已探索相同维度"细节** (`has_explored_same_axis`):
- 提取节点 description 的 axis keywords (用正则匹配 "vs / 对比 / 切换 / 选型")
- 看兄弟里有没有 axis keyword 完全相同的
- 例: 兄弟 "GSVA vs AUCell vs ssGSEA 算法选型" 已存在时, 当前节点 "AUCell vs GSVA 速度对比" axis 重叠, DIRECT

### node_merger.py — 节点合并 (Lily 痛点 2)

```python
@dataclass
class MergeProposal:
    source_nodes: list[str]
    proposed_kind: str            # 默认 "synthesis"
    proposed_task_type: str       # 默认 "analysis"
    rationale: str
    confidence: float             # 0-1, 来自 codex audit
    complementary_axes: list[str] # 互补维度 (atlas / cell type / metric)

def detect_merge_opportunities(graph: Graph, ctx: Context) -> list[MergeProposal]:
    """扫描全图, 找 sibling 完成节点是否互补. 不动数据, 只产 proposal."""

    proposals = []
    for parent_id in graph.nodes:
        parent_children = [
            graph.nodes[c] for c in graph.children_of(parent_id)
            if graph.nodes[c].lifecycle == "done" and not graph.nodes[c].is_abandoned
        ]
        if len(parent_children) < 2:
            continue

        # 已经被合并过的不重复检测
        if any(graph.merge_targets_of(c.id) for c in parent_children):
            continue

        # 互补判据 (三层):
        # (1) Worker 提取 RESULT.md 中的 KEY_FINDING + METRIC + DIMENSIONS
        # (2) 比较 DIMENSIONS 是否正交 (e.g. 不同 atlas / cell type / metric)
        # (3) codex audit 二次确认: "这些 RESULT 合在一起讲一个故事吗?"
        dims = [extract_dimensions(c) for c in parent_children]
        if not are_orthogonal(dims):
            continue

        # codex audit
        verdict = codex_audit_merge_candidates([c.id for c in parent_children], graph, ctx)
        if verdict["complementary"] and verdict["confidence"] >= 0.6:
            proposals.append(MergeProposal(
                source_nodes=[c.id for c in parent_children],
                proposed_kind="synthesis",
                proposed_task_type="analysis",
                rationale=verdict["rationale"],
                confidence=verdict["confidence"],
                complementary_axes=verdict["axes"],
            ))

    return proposals

def apply_merge(proposal: MergeProposal, graph: Graph) -> str:
    """创建 synthesis 节点, 加 merges-into edges 指向源节点. 返回 synthesis 节点 id."""

    synth_id = graph.next_id_under(graph.parent_of(proposal.source_nodes[0]) or "root")
    synth_node = Node(
        id=synth_id,
        kind=proposal.proposed_kind,
        task_type=proposal.proposed_task_type,
        title=f"Synthesis: {proposal.complementary_axes[0]} × {len(proposal.source_nodes)} sources",
        description=f"Merge of {', '.join(proposal.source_nodes)}. {proposal.rationale}",
        lifecycle="created",
        is_branched=False,
        is_abandoned=False,
        cost_budget_hours=2.0,    # synthesis 默认轻量
        info_value=4,             # 合并出来的故事天然 info_value 高
        ...
    )
    graph.add_node(synth_node)

    # parent-of edge: synthesis 跟源节点的父亲一致 (兄弟级别)
    parent_of_sources = graph.parent_of(proposal.source_nodes[0])
    if parent_of_sources:
        graph.add_edge(Edge(parent_of_sources, synth_id, "parent-of", ...))

    # merges-into edges
    for src in proposal.source_nodes:
        graph.add_edge(Edge(src, synth_id, "merges-into",
                            metadata={"basis": proposal.rationale}, ...))

    return synth_id
```

**触发**: autopilot 每 N 步检查一次 (默认 5 步), 类似 v0.5 的 reflection 节奏. 也可以手动 `/research-tree detect-merges` 触发.

**synthesize_report 改动**: synthesis 节点作为 figure-load-bearing 输出, merges-into 的源节点列为 supplementary. dead-branch atlas 不变.

### workers/ — Worker 接口

```python
class WorkerResult(NamedTuple):
    new_lifecycle: Literal["running", "done", "failed"]
    score: float | None
    artifacts_update: dict           # 写回 node.artifacts
    new_edges: list[Edge]            # 比如 SUBTREE_FORK 产生的 parent-of edges
    pivot_proposal: dict | None      # SUBTREE_PIVOT 时填
    next_actions: list[str]          # autopilot 下一步建议 (e.g. ["audit:1.2"])

class Worker(Protocol):
    task_type: str

    def can_run(self, node: Node, graph: Graph) -> tuple[bool, str]:
        """检查 hard-dep edges 是否全 done. 返回 (能否, 理由)."""

    def spawn_subagent_prompt(self, node: Node, graph: Graph, ctx: Context) -> str:
        """生成子代理 prompt. task_type 特定的 artifact 要求嵌在这里, 不再放 SKILL.md."""

    def validate(self, node: Node, branch_dir: Path) -> ValidationResult:
        """物理 artifact 校验. 从 charter_validator.py 拆出来."""

    def on_completion(self, node: Node, graph: Graph, validation: ValidationResult) -> WorkerResult:
        """validator + codex audit 都通过后, 该写什么状态."""

# 注册表 (kind/task_type → Worker)
WORKERS = {
    "training": TrainingWorker(),
    "audit": AuditWorker(),
    "analysis": AnalysisWorker(),
    "data-acquisition": DataAcquisitionWorker(),
    "framing-decision": HumanOnlyWorker(),
    "mixed": MixedWorker(),
}
```

`charter_validator.py` 被拆: 通用部分留在 base.py (parse_charter_table / find_rule_verdict / sha256_file), task-specific 部分 (check_training_rules / check_audit_artifacts / 等) 移到各 Worker 子类.

### scheduler.py — 事件驱动

```python
class Event(NamedTuple):
    t: str           # ISO timestamp
    kind: str        # background_process_exit / result_md_written / dead_md_written / ...
    payload: dict

class Scheduler:
    def __init__(self, root: Path, graph: Graph):
        self.root = root
        self.graph = graph
        self.events_log = root / ".research-tree" / "events.log"

    def watch(self) -> Iterator[Event]:
        """主路径: inotify on .research-tree/. Fallback: 60s polling.
        每个事件 yield 一次."""

    def emit(self, kind: str, payload: dict) -> None:
        """子代理或 Worker 调用, 追加到 events.log + 触发等待 watcher."""

    def dispatch(self, event: Event) -> list[Action]:
        """事件 → 动作列表 (e.g. background_process_exit → [validate, codex_audit])"""
```

**fallback 行为**: 没 inotify 的环境 (老 Linux 内核 / mac / WSL), 退回 60 秒 polling. 没有 30 分钟 cron, 没有 AWAITING_HUMAN.md 频繁触发. 真的需要等人就直接 sleep 等事件, scheduler 不烧 token.

**vs v0.5 human-gate 关系**:
- v0.5: 每 /loop tick 检查 AWAITING_HUMAN.md → fast-exit, 5-10 token 浪费
- v1.0: scheduler 知道 lifecycle 改变才唤醒, 不需要 polling. AWAITING_HUMAN.md 仍然存在, 但只在**真需要 human decision** 时写 (framing-decision task type / pivot proposal 等), 不再为 "全在 running, 没活干" 写

### migrator.py — v0.5 → v1.0 自动迁移

```python
def migrate(tree_json_path: Path, graph_json_path: Path) -> MigrationReport:
    """读 v0.5 tree.json, 写 v1.0 graph.json. 全自动, 不询问 Lily."""

    with tree_json_path.open() as f:
        old = json.load(f)

    graph = Graph(...)

    # 1. 逐节点转换
    for old_id, old_node in old["nodes"].items():
        new_node = Node(
            id=old_id,
            kind=old_node["kind"],
            task_type=old_node.get("task_type", "mixed"),
            title=old_node["title"],
            description=old_node["description"],

            # 状态三轴映射
            lifecycle=STATUS_TO_LIFECYCLE[old_node["status"]],
            is_branched=bool(old_node.get("children")) or old_node["status"] == "expanded",
            is_abandoned=(old_node["status"] == "abandoned"),

            cost_budget_hours=old_node.get("budget_hours_min") or old_node.get("budget_hours_full"),
            info_value=old_node.get("info_value_score"),
            score=old_node.get("score"),

            artifacts={
                "branch_dir": old_node.get("branch_dir"),
                "death_reason": old_node.get("death_reason"),
                "death_evidence": old_node.get("death_evidence"),
                "completion_proof": old_node.get("completion_proof"),
                "junction_audit_id": old_node.get("junction_audit_id"),
                "spawned_by_agent": old_node.get("spawned_by_agent"),
                "repair_attempts": old_node.get("repair_attempts", 0),
                "last_failure_context": old_node.get("last_failure_context"),
            },
            created_at=old_node.get("created_at", now_iso()),
            updated_at=old_node.get("created_at", now_iso()),
        )
        graph.add_node(new_node)

    # 2. 重建 edges
    for old_id, old_node in old["nodes"].items():
        if old_node.get("parent"):
            graph.add_edge(Edge(old_node["parent"], old_id, "parent-of", now_iso(), {}))
        for dep in old_node.get("depends_on", []):
            graph.add_edge(Edge(dep, old_id, "hard-dep", now_iso(), {}))
        for soft in old_node.get("depends_on_soft", []):
            graph.add_edge(Edge(soft, old_id, "soft-dep", now_iso(), {}))

    # parallel_group → m:n parallel-with edges
    by_group = {}
    for old_id, old_node in old["nodes"].items():
        pg = old_node.get("parallel_group")
        if pg:
            by_group.setdefault(pg, []).append(old_id)
    for group_members in by_group.values():
        for i, a in enumerate(group_members):
            for b in group_members[i+1:]:
                graph.add_edge(Edge(a, b, "parallel-with", now_iso(), {"group": pg}))

    # 3. 其他字段
    graph.audits = old.get("audits", {})
    graph.global_constraints = old.get("global_constraints", DEFAULTS)

    graph.save(graph_json_path)
    return MigrationReport(...)

STATUS_TO_LIFECYCLE = {
    "pending":   "created",
    "expanded":  "created",     # 父节点本身的工作可能还没做; 用 is_branched=True 表示已加过子
    "running":   "running",
    "completed": "done",
    "dead":      "failed",
    "abandoned": "created",     # 配 is_abandoned=True
}
```

**migrator 由 CLI 入口自动触发**: 任何 `tree_state.py <cmd>` 调用都先 check graph.json 是否存在, 不存在但 tree.json 存在则自动 migrate, 之后只读 graph.json. tree.json 保留为只读快照.

### cli.py — 薄命令行 (scripts/tree_state.py 重写)

从 2233 行砍到 ~600 行. 每个命令是 5-30 行壳, 调用 graph.py / branching_decider / node_merger / workers / scheduler.

新增命令:
- `decide-branching <node_id>` — 调用 BranchingDecider, 输出 BranchingDecision JSON
- `detect-merges` — 调用 NodeMerger.detect_merge_opportunities, 输出 proposals JSON
- `merge --proposal <path>` — apply 一个 MergeProposal

删除命令 (有更好替代):
- `cascade-reap` — 事件驱动后不需要, lifecycle 改变时同步检查 dependents
- `human-gate auto-clear-if-stale` — 事件驱动后不需要 polling
- `session-step` — 事件驱动后不需要计数

保留命令: `init / add / set / get / list / tree / stats / pick-next / running / complete / die / backtrack / resume-branch / repair-retry / suggest-next / apply-subtree-fork / audit-add / budget-check / migrate / human-gate`.

## Layer 3: SKILL.md 重写大纲 (~300 行)

```
# Research Tree v1.0

## Overview                              (15 行)
## Locations                             (10 行)
## Subcommands                           (40 行, 表格 + 一句话)
## Charter Setup                         (20 行)
## Autopilot Step Flow                   (60 行)
  0. Event check (替代 human-gate)
  1. Pick next pickable node
  2. branching_decider.decide(node)
  3. Dispatch worker.spawn_subagent_prompt(node) OR apply branching decision
  4. On completion: worker.validate() + codex audit
  5. Periodic: node_merger.detect_merge_opportunities
  6. Emit events, log progress
## Single Subagent Role                  (40 行)
  - 不再分 proposer / executor
  - 一个 Agent role + mode 参数 (propose / execute)
  - 共同 prompt 前缀 (charter + brief) 复用
## Output Modes                          (30 行)
  - RESULT.md / DEAD.md / SUBTREE_FORK / SUBTREE_PIVOT
## Error Handling                        (30 行)
## Reference                             (20 行, 链到 V1-ARCHITECTURE.md)

总计 ~300 行
```

task_type-specific artifact rules 完全删除, 移到 workers/*.py. 子代理通过 `python3 -c "from research_tree.workers import get_worker; print(get_worker(<task_type>).spawn_subagent_prompt_template())"` 拉自己的 schema.

## v0.5 → v1.0 行为变化清单

下面这些 Lily 重启 sc-bias 时会观察到:

1. `.research-tree/tree.json` 第一次 CLI 调用后变成只读快照, 同目录多出 `graph.json` (新主文件) + `events.log`
2. `tree_state.py tree` 输出多一列 abandoned 标记 (`⏸`), lifecycle 列从 6 字符标识变 4 字符 (created/running/done/failed)
3. 自动出现 merges-into edges: 当兄弟节点完成且互补时, scheduler 会在 progress.log 写 `merge_proposed`, Lily 可以 `merge --apply` 或忽略
4. 分叉发生时多一行 progress.log: `branching_decision <node> kind=FORK reason=...` 或 `kind=DIRECT_EXECUTE reason=...`. 减少"没意义的分叉"
5. `autopilot --silent` 在 /loop 30m 下烧的 token 进一步降低 (事件驱动, 没事干就 sleep)
6. AWAITING_HUMAN.md 触发减少: 只在 framing-decision task / pivot proposal / 真死路 时升
7. `SCHEMA_VERSION` 字段消失. 新字段任何时候可加, 老 graph.json 不需要 migrate

## 不在 v1.0 范围 (留 v1.1)

- 多机器分布式 (P2 roadmap)
- Co-Scientist Elo tournament (sc-bias 不需要)
- MCTS UCT (树深 ≤ 5, 收益边际)
- 跨 Claude Code 实例协作 (用户基数 = 1)
- 自动写论文 (Lily 红线)

## 落地顺序 (按依赖)

1. `graph.py` (基础数据类) → 任务 #2
2. `migrator.py` (能读老 tree.json) → 任务 #3
3. `workers/` (charter_validator 拆分) → 任务 #4
4. `branching_decider.py` (智能分叉) → 任务 #5
5. `node_merger.py` (节点合并) → 任务 #6
6. `scheduler.py` (事件驱动) → 任务 #7
7. `cli.py` 重写 (薄命令行) → 任务 #9 (与 SKILL.md 一起)
8. `SKILL.md` 重写 → 任务 #8
9. 测试 + sc-bias 端到端验证 → 任务 #10
10. commit + push → 任务 #11

写代码前本文档定稿. 写代码过程中如果发现某个设计不对, 必须先回来改本文档, 不允许直接改代码偷偷偏离.

— Claude, 2026-05-26
