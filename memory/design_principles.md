# Design Principles — 5 条非 Negotiable 信条

> 改任何东西前过一遍. 信条之间冲突时, 序号小的优先。
> 改信条本身是大事, 必须 Lily 拍板, 同步更新 CLAUDE.md 和本文件。

## 信条 1: 物理验证 > LLM 自报

**规则**: 所有"branch 完成"判定基于磁盘上的实际文件, 不基于 LLM 在 RESULT.md 里写的字。

**怎么落地**:
- `charter_validator.py` 检查文件存在 + 大小 + sha256 + JSON schema
- training task 要求 ≥3 个 seed checkpoint 文件 + ≥4 个 ablations/ 子目录 + metrics.json 含 `param_count` ≥10M 等
- 任何 task_type 加新规则时, 规则必须是"看磁盘", 不是"看文本"

**为什么 (历史教训)**:
- v0.1.2 之前曾允许"LLM 在 RESULT.md 里说我做了 X" 算数 → 实测发现 LLM 会胡编实验数字, RESULT.md 漂亮但磁盘上没东西
- v0.1.3 引入 charter_validator 后, 失败率从~40% (LLM 胡编) 降到 ~5% (真完成)
- Lily 投顶刊不能容忍胡编

**注意 v0.1.9 新增 `validator_repair.py` 不违反这条**: 它只动 cosmetic (文件名 / 表头标点 / 缺 requirements.txt), 不动数字 / 不动 sha256 / 不动 metric。

---

## 信条 2: 加密 nonce + SHA256 cross-check audit

**规则**: codex 外审不能"信他自觉读了文件 + 自觉给 verdict"。必须用加密机制证明他确实看过文件且没预先伪造结论。

**怎么落地**:
- 每次 audit 前 orchestrator 生成新 `AUDIT_NONCE` (32 字节随机) 写盘, agent 看不到这个文件 (它在 orchestrator 进程里)
- 给 codex 的 prompt 把 nonce embed 进去, 要求回显在 JSON 的 `nonce` 字段
- codex 返回的 JSON 必须含 `files_read: {path: sha256}` 字典, validator 重新算 sha256 cross-check
- 任一不对 → `external_audit_unavailable` 死分支

**为什么 (历史教训)**:
- v0.1.2 早期信任 codex 自报 verdict, 发现 agent 会预先写 fake CODEX_AUDIT.json 跳过审计
- v0.1.3 引入 nonce + SHA256, agent 无法预测 nonce, 也无法伪造 SHA256 (因为 sha256 跟文件内容 bit-exact 绑定)
- 这是工具的 trust kernel, 任何"简化 audit 流程" 都要先过这一关

---

## 信条 3: 主上下文不随树长大

**规则**: 每个 `autopilot` 调用 = 一次 orchestration 动作然后 return。重活全派 subagent 隔离上下文。状态全在 `.research-tree/tree.json` 磁盘上, 不在 LLM context 里。

**怎么落地**:
- `autopilot` 是 single-step, 不是 in-prompt for-loop
- 长跑用 `/loop 30m /research-tree autopilot --silent` 外部调度
- subagent 跑完 → 写文件 → return JSON 引用 → orchestrator 读文件, 不直接读 subagent 全文输出
- session-step counter (默认 silent 模式 80, chatty 模式 10) 防止单 session 累积太多上下文
- `human-gate` fast-exit (v0.1.8) 让 idle ticks 成本 ~0 token

**为什么 (历史教训)**:
- v0.1.0 早期 autopilot 内部 for-loop, 跑 20 步上下文飙到 80%+
- v0.1.5 拆成 single-step + 外部 /loop, 上下文稳在 ~20%
- 跑 24+ 小时无人值守是核心目标, 必须严格保持

---

## 信条 4: 死分支是交付物, 不是失败

**规则**: 每个 dead 节点必须有 `death_reason` (一行) + `death_evidence` (文件路径) 完整记录。`FINAL_REPORT.md` 包含 "dead-branch atlas" section, 列所有死分支供 paper supplementary。

**怎么落地**:
- `tree_state.py die` 命令强制要求 `--reason`, 可选 `--evidence`
- `synthesize_report.py` 自动汇总所有死分支进最终报告
- death 路径不允许"安静死掉", 至少要有一行解释

**为什么 (Lily 拍板)**:
- 顶刊 reviewer 经常问 "你们试过 X 吗? 为什么没做?"
- 答 "试过, 死在 reason Y, 证据 evidence Z" 比 "没考虑过" 强 10 倍
- 死亡数据本身就是 ablation 的一部分, 不能丢

---

## 信条 5: autopilot 不停问"该选哪条岔路"

**规则**: 技术分岔自决 (proposer + codex audit + signal_detector). 只有 framing / venue / 资源 (账号 / 钱 / >10GB 数据) 才升级到 Lily。

**怎么落地**:
- 节点 task_type 含 `framing-decision` 类型, 这种节点 `human_only: true`, autopilot pick-next 跳过
- 死信号检测 (`signal_detector.py`) 自动判断"全 NULL 该 pivot"
- AGENT_PIVOT 提议会写 `PIVOT_PROPOSAL.md` 挂 human-gate, 但**不自己改 framing**, 让 Lily 看
- 红线列表 (CLAUDE.md 中) 锁死哪些要 Lily, 其他都 autopilot 自决

**为什么 (Lily 反复强调)**:
- Lily 没时间审技术分岔 ("子代理选 GSVA 还是 AUCell" 这种)
- Lily 也不懂技术细节 (她是计算生物 PI, 不写代码)
- 让 Lily 决定 "走 Nat Biotech 还是 Nat Methods" 而不是 "用 lr=1e-3 还是 5e-4"
- 第一次给 Lily 太多技术问题, 她明确说 "你怎么又卡住问我"

**注意**: 这条跟"co-pilot interactive step" 不冲突. `/research-tree step` 是 Lily **主动选**要一步一步看时用; autopilot --silent 是她**不想被打扰**时用。两个模式并存, 默认 silent。

---

## 信条优先级 (冲突时怎么办)

序号小的优先。例: 信条 1 (物理验证) vs 信条 5 (不停问 Lily):
- 物理验证失败 → 不能因为"不想问 Lily"就让它 pass
- 但可以走 repair-retry (v0.2.0) 给 agent 2 次重试机会, 真的不行再 die, 也不需要问 Lily (直接死, 不是停下问)

---

## 反信条 (我们曾经考虑但拒绝的设计)

1. **"信任 LLM 自报, 失败就重新生成"** — 违反信条 1。早期版本试过, 失败。
2. **"audit verdict 直接信 LLM 字面意思"** — 违反信条 2。早期试过, agent 伪造 CODEX_AUDIT。
3. **"autopilot 在 prompt 里循环"** — 违反信条 3。早期试过, 20 步上下文爆。
4. **"死分支安静丢掉, 报告只列存活"** — 违反信条 4。早期试过, Lily 找不回 ablation 证据。
5. **"每个技术分岔都给 Lily 选项"** — 违反信条 5。早期试过, Lily 烦死。

---

## 看完该做什么

- 如果你来改代码: 先在脑子里跑一遍, 你的 patch 违反哪条信条? 违反就停下问 Lily
- 如果你看代码: 哪里跟信条不一致, 这要么是 bug 要么是历史决策的例外 — 看 `memory/decisions.md` 找原委, 别擅自改
