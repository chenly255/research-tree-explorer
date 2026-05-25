# research-tree v0.2 — Agent Nodes 设计 (P2 大改)

> 锁定: 2026-05-25, by Lily 拍板. 把每个 branch 从"模板脚本 + 一次性 subagent"升级为
> "可自己 fork 子树的 Claude Code agent". 不做投机性扩展, 不超出 Lily 的工作流。

## Lily 的原话 (做事的目标层, 不是技术层)

> "每个方向都是一个 claude code 都是一个 agent 去干活 ... 可以是子agent 下面再分孙子agent...
> 比如当前session 然后下面几个分支 先跑一步 然后看一下 再选择一个分支节点 继续下一步,
> 如果好的话就继续 如果不好的话就回退 这样的树状结构来做探索性科研."

拆解出 4 条硬需求:
1. **branch = agent**: 不是脚本, 是 Claude Code agent (能读写, 能想, 能拒绝/重新规划)
2. **可嵌套**: agent 自己能 spawn 子 agent (再 spawn 孙 agent)
3. **回退机制**: 走错的分支要能回到岔口换路, 而不是永久 die
4. **步进可观察**: 跑一步 → 看一下 → 决定下一步 (不一定每步都要 Lily, 但要能停)

## 当前模型的根本错位

| 维度 | 当前 (v0.1.x) | Lily 想要 |
|---|---|---|
| branch 本质 | 模板脚本 + 一次性 subagent prompt | 长期持续 agent, 能多轮决策 |
| fork 决策权 | 只有 orchestrator (main 上下文) 能调 expand | branch agent 自己能 expand 子树 |
| 失败处理 | 死一次就永久 die, 协议明文禁止重试 | 给 N 次自愈机会 (AIDE 风格), 真不行再 die |
| 回退 | 只能 reopen 整个 node (丢 score / death context) | 可保留失败 context, 派新 sibling 换路 |
| 步进 | 1 tick = orchestrator 调一次 subagent 跑一段 | 1 tick 是 agent 自己的 think-act-observe 一轮 |

## v0.2 设计

### 节点新字段 (tree.json schema)

```json
{
  "id": "1.2",
  "...": "...",
  "agent_capable": true,              // 这个 node 是否走 agent 模式
  "repair_attempts": 0,               // AIDE 风格 buggy retry 计数
  "max_repair_attempts": 2,           // 超过就 final die
  "last_failure_context": null,       // 上次失败原因 (传给重试 agent 学习)
  "spawned_by_agent": null,           // 如果是父 agent fork 出来的, 记录父 agent 的 node_id
  "subtree_origin": "orchestrator|agent_fork|repair_retry"
}
```

### 4 种 agent 输出 (取代当前的 2 种)

当前: agent 写 `RESULT.md` (成功) 或 `DEAD.md` (失败).

v0.2 加两种:

3. **`SUBTREE_FORK.md`** — agent 跑到一半发现"这个 step 其实有 2-4 个真分岔需要竞争":
   ```yaml
   reason: "scgpt 嵌入信号弱可能是 lr / zscore / scale 三种独立架构选择, 单跑一个不够诊断"
   candidates:
     - placeholder_id: "lr_sweep"
       task_type: training
       title: "scgpt lr sweep (1e-4 / 5e-4 / 1e-3)"
       description: "..."
     - placeholder_id: "zscore_norm"
       task_type: training
       ...
   ```
   Orchestrator 自动解析 → 调 tree_state.add 创子节点 → 当前 node 标 status=`forked`
   (新状态, 介于 expanded / completed 之间) → autopilot 下一 tick 选其中一子。

4. **`SUBTREE_PIVOT.md`** — agent 发现整条假设错了, 不是 fork 而是 redirect:
   ```yaml
   reason: "假设 scgpt 弱嵌入可通过 scale up 救活, 但 5M cell 跑下来嵌入向量 cosine 跟 3M 比 r=0.99, scale 不解决 — 假设证伪, 应转向'承认 scgpt 弱嵌入 = 论文级 finding 锁进 Figure 1' 这条 framing"
   suggest_new_parent_node_kind: "narrative"
   suggest_new_node_title: "把 scgpt 弱嵌入做成论文 finding 而非工程问题"
   ```
   Orchestrator: die 当前 node 但保留 pivot 提示, signal_detector 收到后跟 Lily 谈
   要不要按 suggest 长一个新分支 (这一步 human-gate, 因为是 framing-decision 级)。

### Agent 模式的 execute 流程 (新)

```
1. Mark node running, set repair_attempts += 1 if retry
2. Spawn Agent(general-purpose) with NEW prompt template:
   - 完整 charter + brief + parent node ancestry + sibling completed nodes
   - 给 4 种输出模式 + 何时用哪种的判断规则
   - 给 Skill 工具访问权 (可以自己调 /research-tree expand <self_id> 派子)
   - 给 last_failure_context (如果是 repair retry)
   - 时间预算 + GPU 预算
3. Subagent 跑, 写 4 种文件之一
4. Orchestrator 解析:
   - RESULT.md → 走 validator + codex audit 链 (与 v0.1.x 同)
   - DEAD.md → die
   - SUBTREE_FORK.md → 解析候选 → tree_state.add 多个子 → 当前 node = forked
   - SUBTREE_PIVOT.md → die + 写 PIVOT_PROPOSAL.md → human-gate
5. 失败链 (validator FAIL OR codex FAIL):
   - if repair_attempts < max_repair_attempts:
       记 last_failure_context 给下次, status 回到 pending, 让 autopilot 重抓
   - else:
       final die
```

### Repair retry (AIDE 风格 buggy flag)

当前: 一次 fail = 永久 die. v0.2:
- 第 1 次 fail: repair_attempts=1, set pending, last_failure_context = 上次 VALIDATION.json failures + codex concerns
- 第 2 次 fail: repair_attempts=2, 同上
- 第 3 次 fail: final die

新 agent prompt 会读 `last_failure_context` (如果有), 显式 told "上次这里崩了, 别犯同一错".

### 步进 + 回退 (`/research-tree step` 新命令)

为 Lily 的 "interactive co-pilot" 模式新增:

```
/research-tree step           # 跑一步, 不进 loop, 输出: 树 + 上一步结果 + 建议下 3 个动作
/research-tree backtrack <id> # 回到 <id> 的兄弟分支, 当前 <id> 标 abandoned (不是 dead, 是"放着不挖了")
/research-tree resume-branch <id>  # 从 abandoned 状态回到 pending, 继续挖
```

Silent autopilot 仍保留 (通宵跑无人值守); step 是 Lily 想 co-pilot 时用。

## 不做的 (Lily 工作流不需要)

- **Sakana v2 走量 50 ideas**: 我们走精, Lily 论文级要求, 不是数量取胜
- **STORM Wiki 自动写**: Lily 明确说论文她自己写, autoresearch 到 DONE.md 就停
- **MCTS UCB 自动平衡 explore vs exploit**: 太重, Lily 的树最多 5 层, 简单 best-first 够
- **autogen / langgraph 完整 agent framework**: 我们用 Claude Code 自己的 subagent 工具, 不引入第三方框架

## 落地顺序 (本轮 + 下一轮)

**本轮 (v0.2.0)**:
1. tree.json schema 加 4 个字段 (agent_capable / repair_attempts / max_repair_attempts / last_failure_context)
2. tree_state.py 加 `forked` status + cmd_apply_subtree_fork + repair retry 逻辑
3. SKILL.md execute step 重写: 4 种输出 + agent template + Skill 访问
4. 写 SUBTREE_FORK / PIVOT 的辅助解析脚本

**下一轮 (v0.2.1, 等 v0.2.0 跑通)**:
5. `/research-tree step` 命令 + interactive 模式
6. backtrack / resume-branch
7. branch agent prompt 模板 (单独文件, 易迭代)

**之后 (v0.3+)**:
8. Co-Scientist Elo tournament (兄弟分支两两对比剪枝)
9. LangGraph sub-step checkpoint (branch 内更细粒度)

## 风险

- **递归爆炸**: agent fork agent fork agent... 限制: max_depth 已存在, 加 max_agent_recursion = 3 防深递归
- **状态并发**: 多 agent 同时写 tree.json. tree_state 已有 fcntl 锁, 应该够
- **agent 自己作弊**: agent 写 SUBTREE_FORK 来"逃避"实际做的事. codex audit 在 RESULT.md 路径上抓, 但 fork 路径绕过 audit. 缓解: orchestrator 强制每条 fork 链最深叶子必须是 RESULT.md/DEAD.md, 不能全是 fork (要不就是无限 fork)

## 验收标准 (Lily 怎么知道 v0.2 work 了)

跑 sc-bias 节点 1.2 (三源 ground truth 算法选型), 期望 agent 跑到一半应该自己写 SUBTREE_FORK.md, 说 "GSVA 跑了 wall-time 200s/cell 不可行, 建议两个真分岔: (a) AUCell only (b) ssGSEA only, 不要并行三个", 然后 orchestrator 自动创 1.2.1 / 1.2.2 两子节点, 下一 tick 选一个继续。Lily 看 tree 能看到 fork 痕迹。
