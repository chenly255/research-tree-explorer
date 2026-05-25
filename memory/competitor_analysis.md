# Competitor Analysis — Autoresearch Framework 调研

> **本文件是改方向的最高 priority 参考**. 想吸收任何新技术 / patch / feature 前, 先在比较表里看清楚:
> (1) 哪个竞品做过 (2) 我们抄了吗 (3) 没抄就回答 "为什么不抄". 不能跳过这一步直接动手。

最新更新: 2026-05-25 (v0.3.0). 新框架 / 新论文 出现时, 进比较表加一行。

---

## 比较表 (按对 Lily 工作流 ROI 倒序)

| 技术 | 来源 | 我们的对应实现 | 状态 | ROI for sc-bias | 备注 |
|---|---|---|---|---|---|
| 物理产物校验 (filesystem-level) | **我们独创** | `charter_validator.py` | ✅ 核心 | 极高 | 别人都靠 LLM self-report, 我们靠程序看磁盘 |
| 加密 nonce + SHA256 cross-check audit | **我们独创** | `codex_audit_cli.py` + `validator_pass_2` | ✅ 核心 | 极高 | 防 agent 预先伪造 CODEX_AUDIT.json |
| task-type-aware schema | **我们独创** | `VALID_TASK_TYPES` + 5 套 artifact 规则 | ✅ 核心 | 极高 | training / audit / analysis / data-acquisition / framing-decision |
| 死分支当交付物 (death atlas) | **我们独创** (受 ARIS 启发) | `death_reason` + `death_evidence` + FINAL_REPORT 包含 | ✅ 核心 | 高 | Sakana / AIDE 都丢失败, 我们存进 supplementary |
| AIDE buggy + debug_depth | AIDE (Jiang 2025) / Sakana v2 (Yamada 2025) | `repair_attempts` + `last_failure_context` (v0.2.0) | ✅ 已抄 | 高 | 我们叫 repair_attempts, 同思路 |
| 节点级 checkpoint (内部 step 持久化) | LangGraph (持久 checkpointer) | `phase_log.jsonl` + `phase_checkpoint.py` (v0.3.0) | ✅ 已抄 | 高 | 训练崩了能续上, 不丢 80% 进度 |
| Fractal agent (子→孙代理) | **Lily 原创需求**, LangGraph 间接支持 | subagent 通过 `Agent` 工具 spawn 孙代理 (v0.3.0) + SUBTREE_FORK.md 两路径 | ✅ 已抄 | 高 | 两路径区分: 内部子代理 vs 树级 fork |
| Interrupt 原语 (人在 loop 中暂停) | LangGraph | `human-gate` sentinel + `awaiting_human.md` (v0.1.8) | ✅ 已抄 | 高 | 我们靠文件不靠 framework |
| 后台进程跨 session 恢复 | **我们独创** (受 nohup 启发) | `EXECUTOR.json` + `stale_running_handler.py` | ✅ 核心 | 高 | Sakana 跑死一次重头来; 我们续 |
| Auto-pivot 死信号检测 | **我们独创** | `signal_detector.py` 检测 NULL signal junction | ✅ 已实现 | 中 | 5 路径里某条全 NULL → 自动 re-frame |
| Backtrack ≠ Die 双语义 | **我们独创** (受 git checkout 启发) | `abandoned` 状态 vs `dead` 状态 (v0.2.1) | ✅ 已抄 | 中 | LangGraph 没这个区分 |
| 自动 cascade-reap (父死下游不 zombie) | **我们独创** | `cascade-reap` 命令 (v0.1.9) | ✅ 已实现 | 中 | 别人没这个问题因为没我们这种 depends_on 依赖图 |
| Co-pilot interactive step | **我们独创** (受 Agent Lab co-pilot mode 启发) | `/research-tree step` + `suggest-next` (v0.2.1) | ✅ 已实现 | 中 | Lily 想要的"一步一步看"模式 |
| **Elo tournament between branches** | Google Co-Scientist (DeepMind 2025) | **没做** | ❌ 故意不抄 | 低 | sc-bias 5 路径互补不竞争; 评估为越级吸收, 实际不需要 |
| **Evolution agent (mutation 弱→强)** | Co-Scientist | **没做** | ❌ 故意不抄 | 低 | 同上, Lily 用例每路径设计是独立的, 不互相借鉴 |
| **MCTS UCT exploration policy** | LATS (Zhou 2023) / Sakana v2 (best-first) | **没做** | ❌ 故意不抄 | 低 | 树深 ≤5, UCB 价值有限 |
| **走量 50 ideas → fitness 筛选** | Sakana v1/v2 | **不会做** | ❌ 故意不抄 | 极低 | Lily 要 Nature 顶刊精品, 不是 arxiv 走量 |
| **自动写论文 / Markdown → LaTeX** | Sakana v1, STORM | **不会做** | ❌ 红线 | 0 | Lily 明确说"论文我自己写, autoresearch 到 DONE.md 截止" |

---

## 各竞品深度档案

### Sakana AI Scientist v1 (Lu et al. 2024)

**做什么**: 全自动从 idea 到 PDF, 一次跑 50 个 ideas, fitness 筛选发哪几个。
**架构**: 线性 pipeline (ideate → experiment → write), 单层 agent。
**失败处理**: 实验失败 retry-in-place 或换 idea, 没有 tree backtrack。
**状态持久化**: JSON checkpoint, 跑死整跑丢。
**人参与度**: 完全无人。

**我们抄了什么**: 死分支记录 (类似但更详细)。
**我们故意不抄什么**: 走量 + 全自动写论文 (跟 Lily 红线冲突)。
**为什么**: Lily 投顶刊, 走量 ≠ 质量; 论文她自己写, autoresearch 帮收集证据就行。

---

### Sakana AI Scientist v2 (Yamada et al. 2025, ICLR-W)

**做什么**: 升级版, 基于 best-first tree search (内核是 AIDE) 做实验探索。
**架构**: 树状, 节点 = 代码 + 实验结果。一个 experiment manager 调度 N 个 worker, 不是 fractal 嵌套。
**失败处理**: buggy 节点优先 debug, debug 深度超限剪枝 (= AIDE 风格)。
**状态持久化**: 完整树 + 节点 artifacts 持久化。
**人参与度**: 完全无人, 但 ideation 阶段可注入指令。

**我们抄了什么**: best-first tree search 思路 + AIDE buggy/debug_depth → `repair_attempts` 2 次预算。
**我们故意不抄什么**: 走量 + 自动写论文 + Multi-worker 并行 (会乱并发写 tree.json)。
**为什么**: 同 v1; multi-worker 是 future work, 现阶段单 orchestrator 够用。

---

### AIDE (Jiang et al. 2025)

**做什么**: Sakana v2 的内核, ML 实验自动调参 / 选模型。
**架构**: 树, 节点带 `is_buggy` flag, debug 操作专修 bug。
**失败处理**: debug_depth 上限, 超了剪枝。
**状态持久化**: journal-style, 任何节点可重选为父。
**人参与度**: 完全无人。

**我们抄了什么**: 节点级 retry 预算 (`repair_attempts`) + `last_failure_context` 传给下次。
**我们故意不抄什么**: `is_buggy` 显式 flag → 我们用 `repair_attempts > 0` 隐式标记; debug_depth 优先级 → 我们用 pick-next 默认顺序。
**为什么**: 我们树最深 5 层, debug-first 调度复杂度回报不值。

---

### Google Co-Scientist (DeepMind 2025)

**做什么**: 给科学家提 hypothesis, 多 agent 协作筛优。
**架构**: Supervisor 拆研究目标 → 调 5+ 专长 agent (Generation/Reflection/Ranking/Evolution/Meta-review) 并行。
**失败处理**: 多 agent 并行生成, 烂 hypothesis 在 Elo tournament 里自然淘汰, 不重试单个 agent。
**回退**: 没有显式 rollback, 靠 tournament + Evolution agent 把弱版本合并到强版本。
**状态持久化**: persistent memory。
**人参与度**: 科学家给目标, 最后看 proposal。中间无人。

**我们故意不抄什么**: Elo tournament + Evolution agent。
**为什么 (深度)**:
- sc-bias 的 5 路径 (A 文本库 / B 盲区图谱 / C 多维度审计 / D per-FM 归因 / E 修复头) 是**互补不竞争**的 — 论文要 4 个 FM 各漏各的 ablation, 不是只留赢家
- Elo tournament 适合 "10 个解决同一问题的不同 hypothesis 选 1 个发表" → Lily 不是这个用例
- Evolution mutation 假设弱分支可以学强分支的 trick → 但我们的分支 task_type 不同 (data-acquisition vs training vs audit), trick 不通用
- 如果未来 Lily 跑"找最好的模型架构"这种竞争性 task, **再抄 Co-Scientist tournament**, 记得加 `memory/decisions.md`

**未来吸收触发条件**: 若有用户跑 task_type 全 training 的纯模型架构对比, 加这个

---

### Stanford STORM (Shao et al. 2024)

**做什么**: 多视角 agent 写 Wikipedia article。
**架构**: 两阶段 pipeline (research → outline → write), 多 "persona" agent 并行问问题。
**失败处理**: 失败 retry, 无 tree。
**人参与度**: Co-STORM 变体支持人工 in-loop 引导话题。

**我们抄了什么**: 多 persona 思路 → SKILL.md 里给 subagent 不同 task_type prompt 走不同 schema。
**我们故意不抄什么**: 整套写作流程 (跟 Lily 红线冲突, 论文她自己写)。

---

### Agent Laboratory (Schmidgall et al. 2025, EMNLP)

**做什么**: 3 阶段 agent 团队跑 ML 实验。
**架构**: phase-level checkpoint, 子任务失败可从 state_saves 重载。固定 3 阶段, 无递归。
**人参与度**: 支持 co-pilot mode (每阶段问人), **评估发现 co-pilot 比全自动质量更高** — 这条重要。

**我们抄了什么**: co-pilot mode → `/research-tree step` interactive 模式 (v0.2.1)。
**我们故意不抄什么**: 固定 3 阶段 (太刚性, 不同 task type 阶段不同)。

**未来吸收**: 如果 Lily 想要"每阶段都过 co-pilot" 而不只是单步 step, 可以加 `--co-pilot` flag 让 autopilot 每步问。

---

### LATS (Zhou et al. 2023, ICML 2024)

**做什么**: Language Agent Tree Search — MCTS + LLM self-reflection 解推理任务。
**架构**: 严格 MCTS 树, 节点 = state-action。单 agent 三身份 (actor/value/reflector)。
**失败处理**: MCTS 节点失败 → LM 写 critique 进 memory, backprop 更新 value。
**回退**: MCTS UCT 天然支持。

**我们故意不抄什么**: 整套 MCTS UCB exploration policy。
**为什么**:
- 我们树最深 5 层, UCB 平衡 explore/exploit 收益边际
- MCTS 假设可以多次 rollout 同一状态, 但我们一个 training branch 跑 8 小时, 不能"快速 rollout"
- self-reflection critique 我们用 `last_failure_context` 替代

**未来吸收触发条件**: 若用户跑短任务 (秒级 rollout) 且树非常宽 (50+ 兄弟), 考虑加

---

### LangGraph (节点级 checkpointing + interrupt)

**做什么**: agent state graph framework, 不是 autoresearch 本身。
**架构**: 状态图, 每个 node 是 checkpoint 边界, 内置 `interrupt()` 原语支持人工 approve。
**失败处理**: 每 node checkpoint, 失败从 node 起点重跑; `error_handler` 可路由到补偿分支。
**回退**: durable execution + 命名 checkpoint thread。

**我们抄了什么**:
1. 节点级 checkpoint → `phase_log.jsonl` + `phase_checkpoint.py` (v0.3.0)
2. interrupt 原语 → `human-gate` sentinel (v0.1.8)

**我们故意不抄什么**: 完整 LangGraph framework 依赖 (跟 Claude Code 集成困难, 引入第三方 framework 与"用 Claude Code 自己的 subagent 工具"原则冲突)。

**未来吸收**: 如果某天发现 `phase_log.jsonl` 表达力不够 (比如 phase 之间有数据流), 看 LangGraph state graph 的 typed schema, 是否需要在 phase_log 里加 `outputs: {key: value}`。

---

### ARIS — Auto-claude-code-research-in-sleep (姐妹项目)

**做什么**: HuggingFace Daily Paper #1, 5 步 loop (plan / draft / 对抗审 / 迭代 / 持久化), 比我们更全面 (包含写作)。
**关系**: 思路 sibling, 不是竞争 — Lily 在用 ARIS template 启发 research-tree 设计。
**位置**: `refs/aris-upstream/` (本地 mirror, 不 fork)。

**我们抄了什么**:
- 5 步 loop 的"对抗审"理念 → codex audit + 物理 validator (但我们更严, 加密 nonce)
- "持久化" 理念 → `.research-tree/` 全套磁盘状态

**我们故意不抄什么**: 写作流程 (跟 Lily 红线冲突); ARIS 跨 IDE 移植性 (Claude Code 是我们唯一目标用户)。

**协作机会**: ARIS team 可能感兴趣 charter_validator + nonce audit 的 anti-fabrication 设计。

---

## 怎么用本文件 (workflow)

**收到"加 X 功能"请求 → 走这 5 步**:

1. 在比较表里看 X 是不是别人做过的
2. 如果做过, 读那家的深度档案, 看我们的"故意不抄"理由是否还成立
3. 如果理由还成立 → 跟 Lily 拍板"我们不抄, 这是为什么", 不动手
4. 如果理由不再成立 (用例变了 / 数据变了 / Lily 拍板要抄) → 加进 `memory/decisions.md` 决策, 然后动手
5. 抄完后回来更新本表 (状态 ❌→✅) + 说明实现位置

**发现新框架 / 新论文 → 加一行进比较表**:

最少字段: 技术名 · 来源 · 状态 · 我们抄/不抄 · 原因。可以以后补深度档案。
