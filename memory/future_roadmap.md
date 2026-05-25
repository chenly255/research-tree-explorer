# Future Roadmap — 下一步往哪走

> 按对参考用户 (Lily / sc-bias) 的 ROI 倒序. 改动方向时先看本文件, 别拍脑袋。
> 完成的项目移到 `decisions.md` 时间线, 本文件只留 backlog。

最新更新: 2026-05-25 (v0.3.0 后)。

---

## P0 — 必须做 (但还没做)

### 1. **v0.3.0 实战验证**

**做什么**: 在 sc-bias 真实分支 (节点 1.2 三源 ground truth 算法选型) 上跑一次完整流程, 验证:
- agent 能不能正确判断"应该写 SUBTREE_FORK"
- phase_log.jsonl 能不能在真实训练中续接
- repair_attempts 触发时 last_failure_context 是不是有用
- 真 fractal agent (subagent 用 Agent 工具) 能不能 spawn 孙代理

**为什么 P0**: v0.3.0 是 wired 但没打过仗. 所有 patch syntax 通过 + 节点 1.1 round-trip 验证, 但 fork / pivot / phase / fractal 四件套都没在真实分支触发过。**如果哪个不 work, 之前所有版本号都是空头支票**。

**怎么做**:
- sc-bias 节点 1.2 task_type=training, 描述里就提到 3 个算法选型 — 完美 SUBTREE_FORK 触发场景
- 让 autopilot 跑 1.2, 看 agent 是不是写了 SUBTREE_FORK
- 如果没写, 看 prompt 里哪句没说清楚, 不动核心逻辑只改 prompt
- 测完更新 `decisions.md` "v0.3.0 实战验证" 条目

---

## P1 — Lily 用例触发条件下补

### 2. **PIVOT_PROPOSAL 自动 expand**

**做什么**: 当 agent 写 `SUBTREE_PIVOT.md` 触发 human-gate, Lily 看完拍板"按你的提议派新分支"后, 应该有命令 `/research-tree apply-pivot <node_id> --new-parent <parent_id>` 一键把提议的新分支建好, 不要 Lily 手动 `add`。

**触发条件**: 当 sc-bias 第一次真触发 SUBTREE_PIVOT 且 Lily 接受时。

**ROI**: 中. 单次省 Lily 30 秒, 长期累积。

---

### 3. **branch dir 自动归档**

**做什么**: 节点 die 之后, `.research-tree/branches/<id>/` 可能有几 GB checkpoint + log. 应该有命令 `/research-tree archive-dead --to-tar` 自动打包压缩, 释放磁盘。

**触发条件**: 当 sc-bias 跑出 10+ 死分支, 总磁盘占用超过 50GB 时。

**ROI**: 中. Lily NAS 有 3TB 空间, 短期不缺, 但通宵跑几天会累积。

---

### 4. **Co-pilot 模式深化: 每 phase 都 step**

**做什么**: 现在 `/research-tree step` 是 branch 级 step (跑完一整个 branch 才看). Agent Lab 评估发现 phase-level co-pilot 质量更高. 加 `/research-tree step --phase-level` 让 Lily 每 phase 都决定 continue/skip。

**触发条件**: 当 Lily 反馈"branch 跑完才看反馈太晚, 想中间看一眼"时。

**ROI**: 中. 不到那个反馈就不需要。

---

## P2 — 大改, 等用例触发再做

### 5. **Co-Scientist Elo tournament**

**做什么**: 兄弟分支两两 head-to-head 比较, 输的剪枝, Evolution agent 把强分支的 audit 协议复制到弱分支重跑。详 `competitor_analysis.md` Co-Scientist 档案。

**触发条件**: 当某个用户跑"找最好的模型架构 / 最好的 hyperparameter" 这种**竞争性 task** (兄弟分支同 task_type 同目标, 选 winner 而不是 4 个 ablation 都要), 当前 Lily 用例不是。

**为什么不现在做**:
- sc-bias 5 路径互补不竞争
- 工程代价: 需要 codex 跑 N×(N-1)/2 次两两审, GPU 没问题但 codex API 调用费时
- 抄完 Lily 用例用不上, 是越级吸收

**怎么评估触发**: 看 `competitor_analysis.md` 的 Co-Scientist "未来吸收触发条件"。

---

### 6. **完整 MCTS UCT exploration policy**

**做什么**: pick-next 不再用简单 heuristic, 用 UCB1 平衡 explore/exploit. 详 LATS 档案。

**触发条件**: 当某用户跑短 task (秒级 rollout 而不是小时级 training) + 宽树 (50+ 兄弟) + 重复同状态可行时。

**为什么不现在做**:
- sc-bias 训练 8 小时不能"快速 rollout"
- 树深 ≤5, UCB 收益边际
- 大改 pick-next 影响整树行为, 需要充分测试

---

### 7. **多 worker 并行执行多个 branch**

**做什么**: 当前 autopilot single-threaded, 一次只跑一个 branch. 加并行让 N 个 branch 同时进行。

**触发条件**: 当 Lily 等不及单线程跑完, 且 GPU 资源充足 (≥4 块 80GB) 时。

**为什么不现在做**:
- 并发写 tree.json 需要更严的锁机制 (现在 fcntl 锁可能不够)
- 容易乱并发 + 错综复杂的 race condition
- Lily 当前用例不需要 (4 GPU 一次训一个 8M cell + 100K Hallmark 翻译器够快)

**风险**: 并发 BUG 找不出来时, 静默丢数据. 必须有 transactional state + dry-run 模式。

---

### 8. **跨 Claude Code 实例的工作流 (像 ARIS-Anything)**

**做什么**: 让 research-tree 不只在一台机器一个用户用, 而是支持多人协作 / 跨机器 sync。

**触发条件**: 当有 ≥1 个非 Lily 用户开始用, 且想共享 charter / 复用 dead-branch atlas 时。

**为什么不现在做**: 当前用户基数 = 1 (Lily). 单机够用。

---

## P3 — 永远不做 (除非用例彻底变化)

### 9. **自动写论文 / 自动产 PDF**

**为什么不做**: Lily 明确说"论文我自己写, autoresearch 到 DONE.md 截止". 红线信条。即使别的用户问, 也是"我们不做, 看 Sakana / ARIS"。

### 10. **走量生成 50 ideas → fitness 筛选**

**为什么不做**: Lily 投顶刊精品, 不是走量取胜. Sakana 的 v1 模式不适合我们目标用户。

### 11. **Markdown → LaTeX 自动转换**

**为什么不做**: 同 #9, 写作不是工具职责。

---

## 怎么决定下一步做什么

1. 看 P0, 没完成就做 P0 第一个未完成项
2. 看 P1, 触发条件有没有满足. 满足了挑 ROI 最高的做
3. 看 P2, 触发条件有没有满足. 多数情况下没满足, 跳过
4. 看 P3, **永远不做** (无视即使有人问)

**反过来**: 不要从 P2 / P3 直接开始. P0 没完成时做 P2 是越级吸收, Lily 没要求, 浪费精力。

---

## 别人提议加 X 功能时

走 `competitor_analysis.md` workflow 的 5 步:
1. 比较表里看 X 是不是别人做过
2. 读深度档案确认"故意不抄"理由
3. 理由还成立 → 拒绝, 不动手
4. 理由不成立 → 加进 `decisions.md`, 然后做
5. 做完更新 `competitor_analysis.md` 表
