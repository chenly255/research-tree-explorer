# research-tree-explorer · 项目入口

> 本文件每次自动加载, 是常驻上下文. 只放每次都要遵守的规则 + 当前状态.
> 长篇细节在 `memory/`, 用到再查.

## 一句话目标

为 Claude Code 提供**树状自主科研工具**: 用户 init 一个 idea → autopilot 在树上 branch / execute / audit / synthesize 几小时到几天 → 产 `FINAL_REPORT.md` 和 `DONE.md`。

参考用户: **Lily** (单细胞基础模型审计, sc-bias 项目, 投 Nature Biotechnology / Nature Methods)。她不写代码、不审技术决策, 只决定 framing 和 venue。本工具的所有设计都以"无人值守 24+ 小时跑 + Lily 早晨只看 FINAL_REPORT" 为目标。

## 核心信条 (不可违反, 改任何东西前过一遍)

1. **物理验证 > LLM 自报**. `charter_validator.py` 检查磁盘上的实际文件 (test_split.json + sha256, ≥3 seed checkpoints, metrics.json 字段, ablations/ 子目录数). LLM 在 RESULT.md 里说什么不算数, validator 在文件系统里看到什么才算数. 任何 patch 涉及"信任 agent 自报", 拒绝。
2. **加密 nonce audit, 不靠协议自觉**. codex audit 用 `AUDIT_NONCE` (orchestrator 写, agent 没见过) + per-file SHA256 cross-check. 防止 agent 预先伪造 `CODEX_AUDIT.json`. 改 audit 时保留 nonce + SHA256 双层防线。
3. **主上下文不随树长大**. 每个 autopilot step = 一次 orchestration 动作然后 return. 重活全派 subagent 隔离上下文. 状态全在 `.research-tree/tree.json` 磁盘上, 不在 LLM context 里。
4. **死分支是交付物不是失败**. `death_reason` + `death_evidence` 完整记录, 进 `FINAL_REPORT.md` 的 dead-branch atlas. 改 die 路径时保留这部分。
5. **autopilot 不停下问用户"该选哪条岔路"**. 技术分岔自决 (proposer + codex audit + signal_detector). 只有 framing / venue / 资源 (账号 / 钱 / >10GB 数据) 才升级到 Lily。
6. **不抄走量, 抄精品**. Sakana 50 ideas / 走量取胜不抄. Co-Scientist Elo tournament 在我们用例 ROI 边际故意不抄. **抄什么 / 不抄什么 / 为什么** 都记在 `memory/competitor_analysis.md`, 改方向前看那里。

## 当前状态 (2026-05-26 v1.0.0 — 架构重写完成)

**v1.0.0 — Lily "从架构层面优化" 指令下的完整重写**. DESIGN-PRINCIPLES.md 六问题
全部解决, 不再走 v0.5.x 补丁路线. 详 `CHANGELOG.md` + `memory/decisions.md` v1.0.0 条目.

**架构骨架**: `research_tree/` Python 包. SKILL.md 砍到 250 行 (从 1042 行).
`scripts/tree_state.py` 是 20 行 shim, 旧 2233 行实现降级为 `scripts/tree_state_v05_legacy.py`.

**v1.0 六条架构改动**:
1. Edges 一等对象 (parent-of / hard-dep / soft-dep / merges-into / parallel-with)
2. Status 三轴正交 (lifecycle / is_branched / is_abandoned)
3. Worker 接口 (每 task_type 一个子类, artifact rules 离开 SKILL.md)
4. BranchingDecider 智能分叉 (decide_to_fork + decide_to_accept_candidate, 替代 proposer 自由判断)
5. NodeMerger 节点合并 (全新功能, v0.5 完全没有)
6. 事件日志 scheduler (cursor delta 读, poll-light)

**Lily 两个痛点都已解决**:
- 智能分叉: 5 道闸门 + per-candidate 拦截 (depth / cost-value / hard-dup / axis-overlap / fallthrough)
- 节点合并: detect-merges 命令扫互补兄弟, merge 创建 synthesis 节点 + merges-into edges

**v1.0 验收**: sc-bias 16 节点端到端迁移成功, 0 数据丢失. 14/14 真兄弟 ADD, 0 false positive.

**v1.1 backlog**: 真 inotify scheduler · charter_validator check_* 函数体进 Worker · 其他 P2 项.

## 文件地图 (按需检索)

记忆系统 (`memory/`):
- `memory/INDEX.md` — 文件夹地图
- `memory/competitor_analysis.md` — **关键** AI Scientist / AIDE / Co-Scientist / STORM / LATS / LangGraph 各自怎么做, 我们抄什么不抄什么, 为什么
- `memory/design_principles.md` — 5 条非negotiable 设计原则的详细原委
- `memory/decisions.md` — 历史决策时间线 (v0.1.0 → v0.3.0 每个版本为什么这么做)
- `memory/future_roadmap.md` — 下一步往哪走, 按 ROI 排序
- `memory/workflow.md` — Lily 的真实工作流 (sc-bias 是参考用例)

代码 (v1.0 `research_tree/` Python 包, 新主路径):
- `research_tree/graph.py` — Node / Edge / Graph 数据模型 (单一真实来源)
- `research_tree/migrator.py` — v0.5 tree.json → v1.0 graph.json (auto-trigger)
- `research_tree/branching_decider.py` — 智能分叉决策 (Lily 痛点 1)
- `research_tree/node_merger.py` — 节点合并 (Lily 痛点 2)
- `research_tree/scheduler.py` — 事件日志 + 分支扫描
- `research_tree/workers/` — task_type 特定 Worker 子类
- `research_tree/cli.py` — 薄命令行入口

代码 (v0.5 兼容层 `scripts/`, 部分仍在用):
- `scripts/tree_state.py` — 20 行 shim → `research_tree.cli:main`
- `scripts/tree_state_v05_legacy.py` — 旧 2233 行实现 (Worker.validate 仍调它)
- `scripts/charter_validator.py` — 物理产物校验 (v1.1 移入 Worker)
- `scripts/codex_audit_cli.py` — GPT-5.5 audit producer
- `scripts/{validator_repair, stale_running_handler, phase_checkpoint, signal_detector, synthesize_report}.py` — v0.5 辅助脚本, 多数仍在用

协议 (`skills/`):
- `~/.claude/skills/research-tree/SKILL.md` — Claude Code 跑 autopilot 时读的协议 (250 行, 必读)

设计文档:
- `docs/V1-ARCHITECTURE.md` — v1.0 设计合同 (写代码前定稿, 写代码后不改)
- `DESIGN-PRINCIPLES.md` — 六条结构问题 (v1.0 的输入)
- `docs/ARCHITECTURE.md` — v0.5 历史参考 (不再编辑)
- `DESIGN-v0.2-AGENT-NODES.md` — v0.2 来源
- `CHANGELOG.md` — 完整版本历史 (v1.0.0 在顶部)
- `CONTRIBUTING.md` — 怎么贡献

参考:
- `refs/aris-upstream/` — 姐妹项目 ARIS (Auto-claude-code-research-in-sleep, HF Daily Paper #1), 借鉴 idea 但不 fork

## 给你 (下一个 Claude Code session) 的建议

- 接手前**必读** `memory/competitor_analysis.md` + `memory/design_principles.md`, 不然容易抄错路 / 违反信条
- 改 SKILL.md 前在 `memory/decisions.md` 找历史决策, 别撤掉之前有意识做的设计
- 想吸收新竞品技术 → 写进 `memory/competitor_analysis.md` 比较表后再动手, 不要直接抄
- 测试 verify: 任何 patch 落地前在 sc-bias 真实树上跑一遍 (节点 1.2/1.3/1.4 是好的 sandbox), 别只跑单元测试就 ship

## 沟通

跟 Lily 用中文, 严禁中英混杂. 看不懂就是 Claude 失败. 详 `~/.claude/CLAUDE.md`.

## 当前任务方向

下一步 (Lily 拍板顺序):
1. 在 sc-bias 真实分支上验证 v0.3.0 (agent fork + phase checkpoint + 端到端 retry)
2. 如果验证 OK, Co-Scientist Elo tournament 仍是显式不抄 (ROI 边际)
3. 如果 sc-bias 之后想跑其他类型项目 (非 audit, 不复杂), 这时再评估 MCTS / 走量等
