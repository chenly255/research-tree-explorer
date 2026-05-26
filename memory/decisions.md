# Decisions — 历史决策时间线

> 每个版本为什么这么做. 怀疑某个 patch 该不该撤回时, 来这里查原委。
> 新决策按倒序追加 (最新在顶部)。

## 2026-05-26 — v1.0.0: 架构重写, 把 13 层补丁合并为 6 条结构改动

**决策**: Lily 给了"不要打补丁, 从架构层面优化"指令 + DESIGN-PRINCIPLES.md 解冻。
执行完整 v1.0 重写, 六个结构问题全做掉。Worker 接口 / 事件驱动 / SKILL.md 砍到 300 行
全部落地。一天密集工作 17k 行新代码 + 旧 5000 行降级为兼容层。

**触发**: Lily 看完 sc-bias 节点 1.3 / 1.4 出现"实质重复的 fork"现象后说
"避免再出现像之前sc-bias一样 在没什么意义的事情上做分叉". 她明确要求**不要再加补丁**,
从根本上解决两个痛点 (智能分叉 + 节点合并) + 整体架构升级。"等优化完了 提交到云端
同步 然后我再开始sc-bias" 表明她接受重写带来的工具停机时间。

**做了什么 (六条架构改动, 全部完成)**:

1. **Edges 一等对象**: 节点不再持有 `parent / children / depends_on /
   depends_on_soft / parallel_group` 字段. 所有关系走 `Edge(src, dst, kind)`,
   kind ∈ {`parent-of`, `hard-dep`, `soft-dep`, `merges-into`, `derived-from`,
   `parallel-with`}. 关系查询从 `graph.children_of()` 等 helper 走, 不污染节点 schema.

2. **Status 三轴正交**: 旧 6 值 enum (`pending / expanded / running / completed /
   dead / abandoned`) 拆为 `lifecycle ∈ {created, running, done, failed}` +
   `is_branched: bool` + `is_abandoned: bool`. 旧每个状态对应一个三元组组合.
   不再因为加一个状态值就要改 N 个判断分支.

3. **Worker 接口**: 每个 `task_type` 一个 Worker 子类
   (`workers/{training, audit, analysis, data_acquisition, framing_decision}.py`),
   各自封装 spawn_subagent_prompt + validate + on_completion. SKILL.md 不再
   嵌入 task_type-specific artifact rules (1042 → 250 行, 76% 缩减).
   增加第 6 种 task_type = 加一个 Worker 类, 不动 SKILL.md / CLI dispatcher.

4. **BranchingDecider 智能分叉**: 替代 v0.5 proposer subagent 自由判断的
   `skip_expansion` 字段. 两个 API:
   - `decide_to_fork(parent)`: 在 proposer 调用前用结构化闸门 (cost-value /
     depth 规则) 决定该不该 fork. 防止"没意义的分叉" 在 expand 阶段就被拦截.
   - `decide_to_accept_candidate(cand, parent)`: 对 proposer 返回的每个候选
     做相似度检查 + 显式 axis 重叠检查. cosine ≥ 0.85 → MERGE_WITH; axis
     "X vs Y" 重叠 → REJECT.
   - sc-bias 14/14 现有兄弟节点都正确 ADD (0 false positive), bit-identical
     重复正确 MERGE_WITH, "GSVA vs AUCell" 重 fork 正确 REJECT.

5. **NodeMerger 节点合并**: 全新功能, v0.5 完全没有.
   `detect_merge_opportunities()` 扫完成兄弟节点, 用保守 regex whitelist
   (atlas/cell_type/fm/metric/disease) 抽 axis 值, 至少一条 axis 互补
   (jaccard ≤ 0.4) 即触发提议. `apply_merge()` 创建 synthesis 节点 +
   双向 merges-into edges. autopilot 每 N 步扫一次, 落 proposal 到
   progress.log 等 Lily 决定 apply.

6. **事件日志 scheduler**: `events.log` 追加事件流, `scheduler_cursor.txt`
   持久化游标. autopilot 每步只读 cursor delta, 不全树扫描. branch 扫描
   合成 RESULT.md / DEAD.md / 后台进程死亡 等事件. 替代 v0.5 的 human-gate
   auto-raise / auto-clear-if-stale polling. 不依赖 inotify (v1.1 再考虑).

**关键工程纪律**:
- 每写完一个模块, 立刻在 sc-bias 真实 16 节点树上跑端到端测试.
- 迁移器写完后第一件事是 verify 已完成节点的 score / death_reason 全部保留.
- 修任何东西先回 DESIGN-PRINCIPLES.md 看是否在解决六问题之一, 还是又在加补丁.

**坚持没做 (Lily 红线 / 越级)**:
- 自动写论文 (永远不做 — Lily 明确)
- 走量 50 ideas (Sakana 模式不适合顶刊用例)
- Co-Scientist Elo tournament (sc-bias 5 路径互补不竞争)
- MCTS UCT (树深 ≤ 5, 边际收益)
- 跨机器分布式 (用户基数 = 1)

**v1.1 backlog**:
- 真 inotify 调度 (现在 scheduler 是 poll-on-demand 事件源)
- charter_validator.py check_* 函数体迁移进 Worker (现在还 subprocess 调用旧脚本)
- v0.5 future_roadmap P2 列表里其他 21 项

**未来回头看**:
- Worker 接口 + 事件 log 模式后续应该让"加新 task_type"成为分钟级工作, 不再是
  跨 4 个文件的改动. 这是 KISS 的核心收益.
- BranchingDecider 在中文长描述上的相似度判断仍偏保守 (cosine 0.65-0.85 灰区
  让它们 ADD, 由 NodeMerger 后续审). 实战 sc-bias 跑下来如果灰区误漏导致重复
  工作, v1.1 可考虑加 codex 灰区兜底, 但默认不开 (避免 API 依赖污染纯度).

**关于"重复 fork"为什么 v1.0 仍非 100% 拦截**: 静态文本相似度对中文+长描述
+ 共享父亲 vocabulary 的兄弟天生不灵敏. v1.0 的真正防线是: (a) `decide_to_fork`
的 cost-value 闸门把"不值得 fork 的父节点"早早 DIRECT_EXECUTE; (b) proposer
的结构化 constraints (must_diversify_axis / min_info_value=3) 在 prompt
阶段就引导避开; (c) `decide_to_accept_candidate` 拦截 hard duplicate + axis
explicit re-explore; (d) NodeMerger 后续扫描灰区. 四层组合比纯相似度判断稳健.

---

## 2026-05-25 — v0.4.0: 质量第一模式 — 三轮 codex audit, 不留尾巴

**决策**: Lily 触发 "质量第一" 模式 ("不要留尾巴 一起完善好"). v0.3.1 留下的 3 个
"deferred to v0.3.2" 全做完, 但 quality-first 不只是做 deferreds — 还要顶住
**三轮 codex audit** 让真的没漏洞。三轮发现的所有 P0/P1/P2 都修了, 没有任何
"deferred to v0.4.1"。linus-review skill 同步加 "质量第一" 覆盖模式。

**关键教训 (写进 lessons learned)**:

1. **AI 修代码会引入新漏洞**: Round 2 之后 Claude 修了 7 个 P1, Round 3 又找
   到 2 个 NEW P1 ("Claude 修复时引入的"). 例: 修 challenge-fragment 时 Claude
   把 expected_text 预写到 agent 可读的 branch_dir, 彻底击穿了新加的 anti-
   fabrication 防御. 修 cmd_complete trust kernel 时让 codex audit 变可选, 
   绕过了整个外部审计层。**结论: 每个修复 round 都要 codex re-audit, 不能
   "改完就 OK"**.

2. **trust-kernel 是 Claude 盲点**: 三轮所有的 P0 trust kernel 绕过都是 Claude
   自己审漏的, 都是 codex 找出来的. Claude 对自己写的 "trust kernel" 层有
   过度自信。

3. **N=3 rounds 才够**: Round 2 找了 4 个 Claude 漏的, Round 3 找了 6 个 Claude
   修出来的, Round 4 (本轮即将完成) verify. **任何 trust-kernel 类改动至少 3 轮
   codex audit**.

**触发**: Lily 看完 v0.3.1 commit 后说 "为什么还要留下一些东西啊 不能一起做完吗
你帮我全都优化好 不要在乎工作量和时间 我们以质量为第一目标". 这是显式覆盖 KISS/
YAGNI 的指令, 必须按"做到完整"做。

**做了什么**:

1. **challenge-fragment 替代 SHA-echo** (codex round 2 P1-3 的根本修)
   - 旧设计 (v0.3.1): orchestrator 把每文件 SHA 写进 prompt 给模型 echo. 模型不读
     文件也能 echo, SHA cross-check 通过. 是 illusion of security.
   - 新设计 (v0.4.0): orchestrator 在调模型前选 N 个 (file, random_byte_offset,
     length=64) 窗口, 写 AUDIT_CHALLENGES.json. prompt 要求模型 verbatim quote
     each window. validator 重读 disk + byte-for-byte cross-check. 模型不读
     文件无法构造 64-byte verbatim fragment.
   - 真 anti-fabrication, 不是工程便利。

2. **forked → expanded 合并** (Linus #5 + codex P2-2)
   - 删 forked 状态 (7→6); 状态合并消掉 7 处分支判断。
   - load_state migration: 老 tree 的 forked → expanded, 同时清掉 3 个 dead 字段
     (agent_capable / subtree_origin / max_repair_attempts).
   - synthesize_report 加入 abandoned 到 alive bucket (修漏算).

3. **PID 链 → $RESEARCH_TREE_SESSION_ID** (Linus #6 + codex P2-1)
   - 删 30 行 /proc 解析, 改 env var 1 行查询.
   - 跨平台 + Claude Code restart 不误判 + autopilot 入口 export uuidgen.

4. **SKILL.md 接口对齐** — cmd_complete 改新签名, EXECUTOR.json 加 pid_starttime
   字段, autopilot 入口 export RESEARCH_TREE_SESSION_ID, forked → expanded 文档化。

5. **测试**: 加 6 个 v0.4 specific tests, 6/6 套件全绿.

6. **linus-review skill 加质量第一模式** — 触发词 "质量第一 / 不留尾 / 不要在乎工作
   量" 立即切换 skill 行为: 不允许 deferred, 必须做完整修 (e.g. 不能写"留 v0.4.1
   再做"), 必须 codex final 二审. 见 `~/.claude/skills/linus-review/SKILL.md`.

**为什么这次彻底**: v0.3.1 心态是"P0/P1 修了 P2 留下", 是 "默认务实" 心态。但当用户
明确说 "质量第一" 时, P2 也是必须修, 不然 "known limitation" 留着就是潜在用户痛点。
这次的教训写进 linus-review skill 让以后 skill 自己懂得切换。

**未来回头看**: 第三方使用者用 research-tree-explorer 时, 应该看 design_principles.md
里"质量第一覆盖原则" — 工具默认 KISS, 但项目级 ROI 要求 (顶刊审稿人 / production) 触
发时, 必须能切换到 "全做完" 模式。

---

## 2026-05-25 — v0.3.1: Linus + codex 双盲审吸收 + trust kernel 加固

**决策**: Linus 风格 review 找 10+ 问题, codex 独立盲审又找 4 个 Claude 漏看的 (含 2 个 trust kernel 绕过), 全部修。新增"Claude 写 + Claude 自审" 必须配 codex 二审的实践规则。

**触发**: Lily "用 linus 扫一下我们的 research-tree 这个项目". Claude 自己审找了 race condition + counter 不一致 + YAGNI 一堆中等问题。Lily 接着说"那你自己调用 codex 再审一遍 然后自己改了呗" — 后续 codex 二审找出 Claude 完全漏看的 2 个 P0 trust kernel 洞:
- cmd_complete 接受任意路径的 `{"verdict":"PASS"}` json
- charter_validator --require-codex-audit 不传 nonce-file 时整段 SHA cross-check 被跳过

**为什么这两个 Claude 自己审会漏**: 因为信条 1/2 (物理验证 + nonce+SHA) 是 Claude 觉得"自己写的最得意的层", 对这层的怀疑性最低。AI 写代码 + AI 自审, 同一个 AI 的盲点会保留。**codex 二审就是为了破这个盲点**。

**修了什么**:
- P0 trust kernel: cmd_complete 自己 fork validator (subprocess), 不接受 user JSON; nonce 检查变 mandatory; test_split.json + DATA_MANIFEST.json 真重算 sha256
- P0 race + counter: 3 个 mutate cmd 补 state_lock; 4 个 status 路径走 _apply_status_transition
- P1 fork 预算: apply_subtree_fork 加 max_depth / max_branches / max_total_nodes
- P1 stale_running: 加 pid_starttime cross-check 防 PID 复用
- P2 state_lock: 用 O_NOFOLLOW 防 symlink 截断
- P2 cleanup: 删 agent_capable / max_repair_attempts 节点字段 / subtree_origin, 抽 _build_new_node

**留 v0.3.2 的**:
- codex_audit_cli SHA-echo 根本设计缺陷 (challenge-fragment 重设计)
- forked/abandoned 状态合并
- PID 链 → $RESEARCH_TREE_SESSION_ID

**未来回头看**: 这次的"Linus + codex 双审" pattern 应该成为 tool 改动的标准实践 — Claude 自己审完一定要 codex 二审, 不然 trust kernel 这种 Claude "自我感觉良好"的层会留洞。memory/competitor_analysis.md 应该补一条 "AI 自审有系统性盲点"。

---

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
