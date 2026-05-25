# Decisions — 历史决策时间线

> 每个版本为什么这么做. 怀疑某个 patch 该不该撤回时, 来这里查原委。
> 新决策按倒序追加 (最新在顶部)。

## 2026-05-25 — v0.3.0: 闭 LangGraph + fractal agent 两个 gap

**决策**: 加 `phase_checkpoint.py` (LangGraph 风格 sub-step checkpoint) + 在 SKILL.md 文档化 fractal agent (subagent 也能用 Agent 工具)。

**触发**: Lily 问"我们的工具已经能做到我的要求并且超过竞品了吗", 我做了诚实 gap 分析, 她拍板"两个 gap 都补"。

**为什么这两个不是其他**:
- sub-step checkpoint 是 sc-bias 训练分支 (8-12 小时) 崩了能续上, ROI 极高
- 真 fractal agent 是 Lily 最初愿景 ("子→孙代理"), 已经在 SKILL.md prompt 里告诉 subagent 它有 Agent 工具
- Elo tournament 显式跳过 (5 路径互补不竞争)
- MCTS UCB 显式跳过 (树深 ≤5)

**未来回头看**: 如果 sc-bias 之后跑别的项目 (短任务, 宽树), Co-Scientist / MCTS 那些再评估。

---

## 2026-05-25 — v0.2.1: interactive co-pilot 模式

**决策**: 加 `/research-tree step` + `backtrack` + `resume-branch` + `suggest-next`。

**触发**: Lily 想要"跑一步, 看一下, 再选下一步, 不好就回退"。silent 通宵模式跟 co-pilot 不冲突, 并存。

**为什么 backtrack 不是 die**:
- `die` 是永久, 进 dead atlas, 下游会 cascade-reap
- `backtrack` 是临时, 不进 dead atlas, 下游不 cascade, 可以 `resume-branch` 复活
- Lily 用例: "1.2 跑出来一般, 我想先看 1.3 再回来", 这不是 die 是 park

**未来吸收**: Agent Lab 的 co-pilot mode 评估发现"co-pilot > 全自动质量", 我们这个模式可以更深入 (每个 phase 都问? 加 `--co-pilot` flag?)。

---

## 2026-05-25 — v0.2.0: agent 节点 (Lily 的"agent of agents" 愿景)

**决策**: 加 4 输出模式 (RESULT/DEAD/SUBTREE_FORK/SUBTREE_PIVOT) + AIDE 风格 `repair_attempts` 2 次预算 + 新状态 (`forked` / `abandoned`)。

**触发**: Lily 看完 v0.1.x 觉得"branch 还是模板脚本, 不是 agent". 她想"每分支是 Claude Code agent, 可嵌子孙代理"。

**为什么 SUBTREE_FORK 而不是直接递归 spawn subagent**:
- 直接递归会让 subagent A 在自己上下文里 spawn 子 B, B 又 spawn C... 主 orchestrator 失去对树的可见性
- SUBTREE_FORK.md 让 agent 写文件, orchestrator 解析后 add 子节点, 树结构始终一致
- v0.3.0 又补了真递归路径 (subagent 用 Agent 工具直接 spawn 内部子代理), 跟 SUBTREE_FORK 双路径并存

**为什么 repair 只给 2 次而不是 1 次或 5 次**:
- 1 次: 不够 (cosmetic 失败需要 1 次自修 + 1 次重跑)
- 5 次: 容易无限循环
- 2 次: 同样 cosmetic 失败给 1 次, 重跑后还有 1 次 buffer; agent 应该能学到 last_failure_context

**未来回头看**: 如果 retry 模式真打过仗, 可能需要按 task_type 调整 (training 给 1 次因为太贵, audit 给 3 次因为快)。

---

## 2026-05-25 — v0.1.9: brittleness patch trio

**决策**: 加 `cascade-reap` + `validator_repair.py` + `codex_audit_cli.py` + signal_detector parent_id bug fix + session-step silent threshold 提升。

**触发**: sc-bias 通宵第一次跑就撞墙. 节点 1.1 数据成功落地但 RESULT.md schema 字符串不对就 die, 下游 1.2/1.3/1.4 cascade 锁死, codex MCP 没注册全 fail-CLOSED。

**为什么不直接放松 validator 而是加 repair_pre_pass**:
- 放松 validator 违反信条 1 (物理验证)
- repair_pre_pass 只动 cosmetic (文件名 / 表头 / 缺 requirements), 不动数字 / sha256 / metric — 不破坏信条
- 是"格式化层和验证层分离", 不是"验证层放松"

**为什么 codex CLI 不是 MCP**:
- Lily 机器没注册 codex MCP, 但有 codex CLI + ~/.codex/auth.json (OpenAI key 直连 api.biom.autos)
- SKILL.md 之前 hardcode `mcp__codex__codex`, fail-CLOSED 直接死
- 写 `codex_audit_cli.py` 用 OpenAI SDK 直接调 GPT-5.5, 保留 nonce + SHA256 不绕审计
- MCP 路径保留为 fallback (如果别的项目有 MCP)

**为什么 silent 阈值 80 而不是无限**:
- 真的需要"40 小时无人值守", 80 步 × 30 分钟 = 40 小时
- 完全无限会让 session context 真的累积过头 (即使 silent fast-exit 也 ~10 token / tick)

---

## 2026-05-23 — v0.1.7: auto-pivot + 自动拉数据

**决策**: 加 `signal_detector.py` 自动检测全 NULL 死信号, autopilot 自动 re-frame 派新候选; cellxgene_download.sh 模板让 autopilot 自己拉数据。

**触发**: Lily 终极目标是"Claude Code 自己拉数据, 自己分析, 自己判断哪条死". 之前 stuck-on-data-not-pulled 太多。

---

## 2026-05-22 — v0.1.6: task-type-aware 校验

**决策**: 加 `task_type` 字段 (training / audit / analysis / data-acquisition / framing-decision), validator 按 task_type 应用不同 schema。

**触发**: sc-bias 实际是 audit-style 项目, 没有 checkpoint, validator 一刀切 training schema 全死。

**为什么不一开始就 task-type aware**: v0.1.0 只想着训练任务, 没考虑 audit 用例。Lily 项目暴露这个 gap。

---

## 2026-05-21 — v0.1.5: smart branching cadence

**决策**: depth ≥1 时 proposer 可以 `skip_expansion: true` 直接 mark direct_executable; `autopilot --continuous` 链式跑直到所有 live 都 running。

**触发**: v0.1.4 实际跑发现"很多 step 是 canonical 评估, 强行分 3 个 candidate 是假分岔". 用户 (我 dogfood 时) 觉得过度分支。

---

## 2026-05-20 — v0.1.4: 跨 session 后台进程恢复

**决策**: 训练用 `nohup` 启动并写 `EXECUTOR.json` 记 PID + log。`stale_running_handler.py` 跨 session 启动时扫每个 running 节点, PID 死了就归类 (validation / death / abandoned)。

**触发**: Lily 关 IDE 后 subagent 跟着死, 整跑丢。

---

## 2026-05-19 — v0.1.3: hardline 反 fabrication (信条 1 + 2 锁定)

**决策**: 加 `charter_validator.py` (物理产物校验) + 加密 nonce 制 codex audit。

**触发**: dogfood 发现 agent 会"在 RESULT.md 里说我跑了 3 个 seed 的训练" 但磁盘上没文件; 会"在 CODEX_AUDIT.json 里预先写 verdict=PASS" 跳过审计。

**重要**: 这是工具 trust kernel 锁定时刻, 后续任何"简化"都不能动这两层。

---

## 2026-05-18 — v0.1.2: anti-laziness charter + silent mode

**决策**: 加 `RESEARCH_CHARTER.md` 模板, silent autopilot 模式 (减少 token 浪费)。

---

## 2026-05-18 — v0.1.1: convergence handoff + ROOT_FAILURE

**决策**: 加 DONE.md 自动产生 (charter done_criteria 满足) + ROOT_FAILURE.md (全树死时通知 pivot)。

---

## 2026-05-17 — v0.1.0: 初版

**决策**: 树 + 子代理隔离 + 状态在磁盘 + autopilot single-step。

**为什么这个架构**: ARIS 项目 (姐妹 sibling) 已经验证 Claude Code skill + 磁盘状态 + subagent 隔离的可行性, 我们 fork 思路自己实现。

---

## 决策反向索引 (按主题查)

| 主题 | 在哪个决策 |
|---|---|
| 为什么物理验证 | v0.1.3 |
| 为什么 nonce + SHA256 | v0.1.3 |
| 为什么 task_type aware | v0.1.6 |
| 为什么 silent + chatty 双模式 | v0.1.5, v0.1.8 |
| 为什么 session-step cap | v0.1.5 (引入 20), v0.1.8 (降到 10), v0.1.9 (silent 升 80) |
| 为什么 SUBTREE_FORK 不直接递归 | v0.2.0 |
| 为什么 fractal agent 后来又加上 | v0.3.0 |
| 为什么 backtrack ≠ die | v0.2.1 |
| 为什么不抄 Co-Scientist Elo | v0.3.0, 详 `competitor_analysis.md` |
| 为什么不抄 MCTS | v0.3.0 |
| 为什么不抄 Sakana 走量 / 自动写论文 | v0.1.0 起反复, 详 `competitor_analysis.md` |
