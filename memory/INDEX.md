# memory/ — research-tree-explorer 持久记忆

> 项目级记忆系统. CLAUDE.md 是入口 (≤150 行常驻); 细节在这里, 按需检索, 不自动加载。

## 文件清单

| 文件 | 什么时候读 |
|---|---|
| `competitor_analysis.md` | 想吸收新 autoresearch 技术 / 评估某个 feature 是否值得抄 / 写论文比较 |
| `design_principles.md` | 改任何东西前 (5 条非 negotiable 信条详细原委) |
| `decisions.md` | 不确定某个设计为什么这么做 / 怀疑要不要撤回某个 patch |
| `future_roadmap.md` | 想知道下一步往哪走 / 该补什么 / 别抄什么 |
| `workflow.md` | 不清楚 Lily 怎么用这工具 / sc-bias 这个参考用例 |

## 记忆系统的边界

**这里放什么**:
- 持久不变的设计原则 / 信条
- 历史决策和原委 (避免反复犯错)
- 竞品调研结论 (避免重复研究)
- 用户真实工作流 (避免做错产品)
- 长期路线图 (避免短视优化)

**这里不放什么**:
- 当前进行中的 task (用 git branch / git stash)
- 代码细节 / API 文档 (代码本身 + ARCHITECTURE.md 是权威)
- 当前 bug 列表 (issue tracker / TODO 注释)
- 单次实验结果 (那是用户项目级别, 不是工具级别)

## 维护守则

- 凡 commit 涉及"为什么这么做"的决策, 同步进 `decisions.md`
- 凡发现新竞品 / 新论文 / 新 framework, 进 `competitor_analysis.md` 比较表
- 凡 Lily 拍板某个方向 / 否决某个方案, 写进 `decisions.md` 时间线
- 修信条 (5 条) 是大事, 必须 Lily 拍板; 改完更新 `design_principles.md`
- 任何 memory 文件过时了 (跟代码现状对不上), 立刻修, 不要让记忆系统说谎
