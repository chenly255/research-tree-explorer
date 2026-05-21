# 用法手册：把 research-tree 跑到群体级细胞基础模型项目上

这份文档假设：

- 你已经在某台机器上把 research-tree-explorer 克隆到 `/data3/liying/research-tree-explorer/`
- 已经跑过 `bash scripts/install.sh`，`/research-tree` 在该机器上可见
- 你已经打开了 Claude Code，并 cd 到 `/data3/liying/group-cell-fm/`

## 三步起跑

### 1. 准备 RESEARCH_BRIEF.md

`research-tree` 起跑时会自动读 `<项目根>/RESEARCH_BRIEF.md`（如果存在）。这是给所有子代理共享的"项目说明书"，写一次以后整棵树都用它。模板见后面。

### 2. 起一次 run

```
/research-tree init "群体级细胞基础模型：让 AI 把一群细胞作为整体当成基础单位来学习，填补 spatial FM 只到单细胞 token 的方法学空白"
/research-tree autopilot
```

`autopilot` 跑完一次循环（取下一个未处理节点 → 展开或执行 → 必要时审计），然后停下来给你一段进度。**每次都要重新调一次 `autopilot`** 才会跑下一轮——这是设计：每轮之间留一个口子，让你能瞄一眼 `.research-tree/tree.json` 或者 `status`，确认方向没跑偏，然后继续。

如果你想真正"放着不管让它跑到底"，组合 `/loop` 这个 skill：

```
/loop 30m /research-tree autopilot
```

每 30 分钟自动调一次 autopilot，直到树收敛或达到预算上限。

### 3. 看进度 / 看结果

```
/research-tree status      # ASCII 树 + 统计
cat .research-tree/FINAL_REPORT.md   # 最新合成报告（每次 synthesize 后更新）
```

或者直接打开 `.research-tree/tree.json`——这是单一真实状态。

## 这棵树在群体级细胞基础模型项目上会长成什么样

举例（不是承诺，实际由 autopilot 自己决定）：

```
root: 群体级细胞基础模型
├── 1 approach: Set Transformer——把细胞群当 set，permutation-invariant 自注意力
├── 2 approach: Perceiver IO——cross-attention 把细胞群压成 latent token
├── 3 approach: GNN on cell graph——k-NN 图上做消息传递
└── 4 approach: 等变 CNN on spatial grid——按 spatial 坐标格点化后卷
    ├── 4.1 architecture: 平移等变（标准 CNN）
    ├── 4.2 architecture: 旋转等变（E(2)-CNN）
    │   ├── 4.2.1 experiment: 8 类组织 zero-shot
    │   └── 4.2.2 experiment: cross-平台 transfer（10x → Stereo-seq）
    └── 4.3 architecture: 仿射等变
```

每个节点的 `branch_dir`（`.research-tree/branches/<id>/`）里会有：

- `RESULT.md`（如果成功）或 `DEAD.md`（如果死了）
- `fit_script.py` 或 `train.py`（branch 自己写的代码）
- 中间产物：checkpoints、logs、metrics

死掉的分支**不会被删**——它们是论文的 supplementary atlas，回头写讨论章节时直接拿。

## 关于 GPU——临时方案

这台机器只有 4 张 A800，跑大模型预训练不够。短期方案：

1. **autopilot 的 execute 阶段会先在 A800 上跑一个 pilot**（小数据 + 小模型），1-2 小时内出信号
2. 信号好的分支，autopilot 会在 RESULT.md 里标 `NEEDS_FULL_SCALE: true`，但**不会自己去其他机器**——它会在合成报告的 "Suggested next move" 里写"分支 X 需要在 H100 机器上 full-scale，建议从 .research-tree/branches/X/ 同步代码过去"
3. 你看到这条建议后，手动把那个 branch 目录 rsync 到 H100 机器，在那里继续跑

**跨机器自动分发是路线图里的事**（README 里有），现在还是手动桥。这台机器上的 4 张 A800 可以并行跑 4 个分支的 pilot，恰好对应 root 下 4 个 approach。

## RESEARCH_BRIEF.md 模板

放在 `/data3/liying/group-cell-fm/RESEARCH_BRIEF.md`：

```markdown
# Research Brief — 群体级细胞基础模型

## 目标
做一个把"一群细胞作为整体"当成基础 token 单位的细胞基础模型，填补当前 spatial FM 只到单细胞 token 的方法学空白。投 Nature Methods / Nature Machine Intelligence。

## 约束
- 数据：见 `data/raw/`，主要是 10x Visium、Stereo-seq、MERFISH 三个平台的公开数据
- 本机算力：4 × A800（80GB），仅做 pilot 用，full-scale 留给 H100 机器
- 时间预算：本机每个分支 pilot ≤ 2 小时
- 必须避开的方向：CONCERT 已经做的纯单细胞 token + 邻域聚合（差异化定位见 `docs/`）

## 已知优先方向
1. 直接学群体级 token：把 ~50 细胞的局部 niche 当一个 token，避开"先单细胞 token 再聚合"的两步走
2. 等变性：空间坐标的平移/旋转等变是天然的归纳偏置
3. 多平台对齐：cross-tech transfer 是审稿人最常追问的点

## 死路（之前 session 已淘汰）
- 纯 cell2cell graph attention：被 CellPLM、CellFM 等多个工作占据
- 只用 RNA modality 没有空间信息：审稿人会问"那你跟 scGPT 比啥"

## 评估
- 5 个 downstream tasks：tissue zero-shot classification、niche detection、cell-cell communication、cross-tech transfer、扰动响应预测
- 每个 task 都要有一个 SOTA baseline 数字可比（参见 `refs/baselines.md`）
```

## 什么时候你该停下来介入

按 `/data3/liying/group-cell-fm/CLAUDE.md` 的"操作军规"，autopilot 应该**不主动找你**。它停下来找你的情况只有：

- 需要不可逆操作（推送、删数据、申请新算力配额）
- 整棵树超过 3 天没新增 completed 节点——可能严重跑偏了，需要你看一眼
- 某个分支的 DEAD.md 里写了"需要 Lily 决策的目标层问题"

其它情况它都自己拍板。你的工作是定期（每隔一两天）打开 `FINAL_REPORT.md` 看一眼现状，给一句话反馈或者直接让它继续。

## 常见坑

- **状态文件丢了**：`.research-tree/tree.json` 是单一真实状态。如果意外删除，所有分支的工作还在 `.research-tree/branches/`，但树状态需要重建。建议每天 `cp .research-tree/tree.json .research-tree/tree.$(date +%Y%m%d).json` 做备份。
- **autopilot 跑飞了想停**：直接 ctrl-C 中断当前 Claude Code 输出；状态会停留在最后一次成功的 `set` 调用。下次 `/research-tree resume` 接着跑。
- **想强制重来某个分支**：`python3 .../tree_state.py set <id> status=pending`，然后下次 autopilot 会重新执行它。
- **想加新候选到一个已经满的 junction**：先 prune 一个死分支（`set status=dead`），然后...哦不对，状态机不让在原 children 列表上新增。短期解法是 `max_branches_per_junction` 调大后重新 init；长期 v2 会支持 replace。

## 一个具体的 Lily 工作流

每天早上 10 分钟：

```
cd /data3/liying/group-cell-fm
cat .research-tree/FINAL_REPORT.md   # 看现状
/research-tree autopilot              # 推一轮
# 如果觉得方向偏了：
/research-tree prune <id> "<理由>"
# 或者补一个想法：
/research-tree expand root            # 在根下加新候选（如果还有 budget）
```

晚上回家前：

```
/loop 60m /research-tree autopilot    # 让它通宵每小时跑一次
```

第二天早上看新的 FINAL_REPORT.md。每三五天对 `tree.json` 做一次手动 review，看看死的分支理由对不对、活的分支评分有没有水分。

## 报错和反馈

研究阶段难免遇到问题。Skill 设计成"任何 Python 脚本失败都会把 stderr 抛给你"。如果发现某个机制不顺手——比如 budget 太死、合成报告漏内容、subagent prompt 不够明确——直接改 `/data3/liying/research-tree-explorer/skills/research-tree/SKILL.md` 或 `scripts/*.py`，commit，下次自动生效。Skill 是活文档，按 `~/.claude/CLAUDE.md` §7 的原则当场改。
