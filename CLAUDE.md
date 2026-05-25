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

## 当前状态 (2026-05-25 v0.3.0)

最新 commit: `e1d1717 v0.3.0 — sub-step checkpointing + fractal agent recursion`

**已实现**: branch = Claude Code agent (4 输出模式 RESULT/DEAD/SUBTREE_FORK/SUBTREE_PIVOT) · AIDE 风格 2 次 repair retry · cascade-reap 防 zombie · backtrack / resume-branch / suggest-next · validator-repair cosmetic 自动修 · codex CLI fallback (不依赖 MCP) · LangGraph 风格 sub-step phase_log.jsonl checkpoint · 真 fractal agent (subagent 也能用 Agent 工具)

**故意没做** (低 ROI for Lily, 详 `memory/competitor_analysis.md`): Co-Scientist Elo tournament · Sakana MCTS UCB · Sakana 走量 50 ideas

**未实战验证**: SUBTREE_FORK + SUBTREE_PIVOT 路径 wired 了, 但没在真实 sc-bias 分支上跑过. 第一次实战在 sc-bias 节点 1.2/1.3/1.4 (训练 task) 上。

## 文件地图 (按需检索)

记忆系统 (`memory/`):
- `memory/INDEX.md` — 文件夹地图
- `memory/competitor_analysis.md` — **关键** AI Scientist / AIDE / Co-Scientist / STORM / LATS / LangGraph 各自怎么做, 我们抄什么不抄什么, 为什么
- `memory/design_principles.md` — 5 条非negotiable 设计原则的详细原委
- `memory/decisions.md` — 历史决策时间线 (v0.1.0 → v0.3.0 每个版本为什么这么做)
- `memory/future_roadmap.md` — 下一步往哪走, 按 ROI 排序
- `memory/workflow.md` — Lily 的真实工作流 (sc-bias 是参考用例)

代码 (`scripts/`):
- `tree_state.py` — 核心状态机 + 24 个子命令
- `charter_validator.py` — 物理产物校验 (validator pass 1 + 2)
- `codex_audit_cli.py` — GPT-5.5 audit producer (MCP fallback)
- `validator_repair.py` — cosmetic 自动修复 (charter 表头 / DATA_MANIFEST 别名 / requirements 合成)
- `stale_running_handler.py` — 后台进程崩溃恢复 + phase_log 接续
- `phase_checkpoint.py` — sub-step checkpointing (v0.3.0 新)
- `signal_detector.py` — auto-pivot signal
- `synthesize_report.py` — FINAL_REPORT 生成

协议 (`skills/`):
- `skills/research-tree/SKILL.md` — Claude Code 跑 autopilot 时读的协议 (~960 行, 必读)

设计文档:
- `docs/ARCHITECTURE.md` — 三层架构 (skill / scripts / disk state)
- `DESIGN-v0.2-AGENT-NODES.md` — v0.2 agent 节点重设计的来源 + 决策
- `CHANGELOG.md` — 完整版本历史
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
