# Workflow — 真实用户怎么用本工具

> 用工具不能脱离用户. 改设计前先确认你脑子里的"用户"跟 Lily 的真实情况一致。
> 偏离了, 大概率做错产品。

## 参考用户: Lily

**身份**: 单细胞基础模型审计研究者, 投 Nature Biotechnology / Nature Methods 级期刊。

**技术栈**:
- 不写代码 (Python / Bash 都不写)
- 不审核技术细节 (不懂 lr / batch size / 哪个 normalize 方法)
- 懂生物学 + 评审品味 + venue 选择

**硬件**: 4×A800 80GB, /data3 ~3TB 空闲 (训练只用 3 张, 留 1 张给别人), 喜欢 batch 拉大提速。

**项目**: sc-bias (4 个冻结 scFM 的压缩损失审计框架)。完整 5 路径 (A-E) 详 `/data3/liying/sc-bias/CLAUDE.md`。

---

## Lily 跟工具交互的模式 (3 种)

### 模式 1: 通宵无人值守 (默认)

**用户体验**:
```
晚上: /research-tree autopilot --continuous --silent
+ /loop 30m
+ task-monitor 邮件守望

睡觉

早上: 看邮件
  - DONE 邮件 → review 看 paper finding
  - ROOT_FAILURE 邮件 → /idea-pipeline 重选方向
  - STUCK 邮件 → 看 AWAITING_HUMAN.md 决策
  - 没邮件 → 还在跑, 中午看
```

**给工具的硬要求**:
- silent 模式 token 漂亮 (每 tick 不超过 100 token 进主上下文)
- 失败要么自愈要么明确通知, 不能默默 stuck
- 后台进程跨 session 恢复
- session-step cap silent 模式至少 80 (40+ 小时无人值守)
- 邮件通道: DONE / ROOT_FAILURE / STUCK 三种事件

**Lily 不可接受**:
- 早上起来发现整夜没进展 (cron 卡住 / agent 死锁 / 验证失败循环)
- 邮件刷屏 (每 tick 一封)
- 工具自己改 paper framing 不通知她

---

### 模式 2: Co-pilot interactive step (Lily 有空想看一下)

**用户体验**:
```
Lily 看完上一步结果觉得有趣, 想直接 hand-pick 下一步:

/research-tree step
  → 显示树 + 上步结果 + 建议下 3-5 步

Lily 选 "expand 1.4" 或 "backtrack 1.3 然后试 sibling"

/research-tree step
  → 跑下一步, 再显示
```

**给工具的硬要求**:
- 每 step 给清晰的 "下 X 个动作" 选项
- backtrack 是 reversible (不要让她怕"我说错了怎么办")
- 显示树 ≤15 行 (她不要看完整 tree)
- 每步报告 ≤1 段, 重点是 "what changed + what next"

**Lily 不可接受**:
- step 跑完才发现做错事 (太慢了应该中间问)
- 选项里没有"我手动加个 idea" 的入口
- 树太大不知道当前在哪

---

### 模式 3: 调试 / 救火 (出问题时)

**用户体验**:
```
邮件说 STUCK, Lily 打开 IDE 看:

cat .research-tree/AWAITING_HUMAN.md          # 看为什么 stuck
/research-tree get 1.2                        # 看那个节点状态
/research-tree deps 1.2                       # 看依赖
/research-tree suggest-next                   # 想看接下来该怎么走

然后:
- /research-tree resume   # 信工具, 继续
- /research-tree backtrack 1.2   # 不信, 换路
- 自己 hack 一下 RESEARCH_CHARTER.md → /research-tree resume
```

**给工具的硬要求**:
- 错误信息要让 Lily 一眼能懂 (不能是"validator FAIL")
- AWAITING_HUMAN.md 必须包含"她有几个选项 + 后果是什么"
- 任何状态都可以 recover (没有"卡死无法继续"的死路径)

**Lily 不可接受**:
- 错误信息全是 stack trace
- "你修代码吧" (她不修)
- 状态被破坏到只能 `init` 重头

---

## Lily 的红线 (绝对不要碰的事)

1. **改论文 framing / claim / venue** — 这是 Lily 的目标层. autopilot 提议可以, 改一定要她拍板
2. **要 >10GB 商业数据下载** — 走带宽 (走 17891 代理, 17890 是 Claude Code 自己烧流量的 EqualVPN 上游)
3. **花钱的事** (额外 API 额度 / 云算力) — 任何 $ 决策要她
4. **改 ~/.codex/auth.json 或全局配置** — 影响她其他项目, 不能动
5. **跑她的 sc-bias 训练 > 24h 单实验** — 资源协调
6. **公开 / push 涉及她私有数据的东西到 GitHub** — 数据合规

---

## Lily 期望的"工具的样子" (我们的北极星)

> "Claude Code 自己拉取数据、自己尝试分析、自己看哪个效果好、自己判断哪条路失败了 + 怎么切换. 所有资源 (GPU / 网络 / 数据访问) 我都可以提供. 我只在 Claude 实在拿不到东西 (没权限 / 没账号 / 没钱) 时才介入. **终极目标: 我什么都不用管, 自动跑出可以发表的论文 (我自己写)**."

中文原话出自 NEXT-SESSION-HANDOFF.md, 2026-05-23。

---

## 为什么 sc-bias 是好的 sandbox

- 复杂度恰好 (5 路径 + 各 3-5 子节点 + 多种 task_type 混合)
- 跨多种 task_type (data-acquisition + training + audit + analysis + framing-decision 全都用得到)
- 真训练时长 (8-24 小时), 不是 toy
- Lily 关心结果 (真的会 review, 不会"反正测试用例")
- 失败有真实代价 (浪费 3 张 A800 一晚)

未来如果有人 fork research-tree 跑别的项目, **以 sc-bias 为对照基准**, 不要随便加 feature 让 sc-bias 跑不了。

---

## 给非 Lily 用户用本工具时的注意事项

如果未来有第二个用户:
1. 先让他写 `RESEARCH_CHARTER.md` (charter 是工具行为的源头)
2. 让他声明红线 (不可碰的事), 写进项目级 CLAUDE.md
3. 让他描述工作流 (通宵 vs co-pilot vs 调试), 看哪种为主
4. 让他确认是否接受信条 1-5 (`memory/design_principles.md`)。不接受的话, 他用错了工具, 让他看 Sakana / ARIS

我们工具的核心市场是: **想要无人值守 + 不胡编 + 顶刊精品 + 自己写论文** 的科研人. 不是想要"完全自动产 arxiv"的人。
