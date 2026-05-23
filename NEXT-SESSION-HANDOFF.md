# NEXT-SESSION HANDOFF — research-tree-explorer v0.1.7 → integration (2026-05-23)

> **新 session 第一件事**: 完整读这份文件. 自包含, 不依赖任何 chat 上下文.

## Lily 的终极目标 (反复读, 永远不变)

> **"Claude Code 自己拉取数据、自己尝试分析、自己看哪个效果好、自己判断哪条路失败了 + 怎么切换. 所有资源 (GPU / 网络 / 数据访问) 我都可以提供. 我只在 Claude 实在拿不到东西 (没权限 / 没账号 / 没钱) 时才介入. 终极目标: 我什么都不用管, 自动跑出可以发表的论文."**

红线 (必须升级给 Lily, 其他全部 autopilot 自决):
- 需要新数据下载权限 (>10GB 商业数据 / 需要 IRB 数据)
- 需要花钱 (API 额度 / 云算力)
- 需要外部 API key
- 论文 framing / venue / 头条 wording 改动 (这是目标层)
- 所有探索路线全死 (ROOT_FAILURE), 需要 pivot 整个 idea

## 现状 (2026-05-23 完成)

### research-tree-explorer 工具 — v0.1.7 已 commit + push

- **分支**: `v0.1.7-data-acquisition-and-auto-pivot` (基于 `v0.1.6-task-type-aware`)
- **commit**: `5c45748 v0.1.7 — autopilot pulls data + auto-pivots on dead signals`
- **PR URL**: https://github.com/chenly255/research-tree-explorer/pull/new/v0.1.7-data-acquisition-and-auto-pivot
- **未 merge**: v0.1.6 PR 和 v0.1.7 PR 都还没 merge. 新 session 可以基于 v0.1.7 接着开发, 或等 Lily review.

### v0.1.7 加了什么 (具体到能在 sc-bias 上用)

P3 — autopilot 自己拉数据:
- `examples/data-acquisition/cellxgene_discover.py` — CELLxGENE Discover 搜索 + 列 collection + 解析 dataset asset URL (用 curation API, 不是 dp/v1)
- `examples/data-acquisition/cellxgene_download.sh` — 模板: 传 DATASET_ID + COLLECTION_ID, 自动解析 asset URL, 用 17891 代理下载, 自动算 n_cells, 自动写 DATA_MANIFEST.json + RESULT.md
- `examples/data-acquisition/geo_figshare_download.sh` — 通用单 URL puller (GEO / figshare / Zenodo / GitHub releases)
- `examples/data-acquisition/README.md` — subagent recipe + proxy 政策 + 受限数据升级合约
- `skills/research-tree/SKILL.md` execute 步骤的 task_type=data-acquisition 块全面重写, 教 subagent 用模板 + nohup + EGA/dbGaP 受限数据走 DEAD.md
- `skills/research-tree/SKILL.md` expand 步骤新规则: proposer 必须先检查数据存在性, 缺数据 → 自动加 task_type=data-acquisition 兄弟节点 + 通过 placeholder_id 模式 wire depends_on
- proposer JSON schema 加 `placeholder_id` + `depends_on_placeholders`, orchestrator 两轮 add 解析

P2 — autopilot 失败-切路线自动化:
- `scripts/signal_detector.py classify` — 一个 branch → STRONG/WEAK/NULL/UNKNOWN. CI 排除零优先于 p_value 语义 (修了 sc-bias 的 Krishna P=1.0 boost-strap reproducibility 误判 case)
- `scripts/signal_detector.py aggregate <parent_id>` — 兄弟节点聚合 → ALL_NULL/MOSTLY_NULL/MIXED_POSITIVE/ALL_STRONG/...
- `scripts/signal_detector.py check-pivot --write-proposal` — 扫所有 junction, ALL_NULL/MOSTLY_NULL → 写 `.research-tree/AUTO_PIVOT_PROPOSAL.md` + exit 10
- SKILL.md autopilot step 7.5: 跑 check-pivot, dead-signal junction 自动 expand 一个 re-framing prompt (规则: 改问题不是改协议), 写完改名 `.handled.md` 防再触发
- RESEARCH_CHARTER 模板加 §Data acquisition rules + §Pivot trigger rules + signal_thresholds yaml

测试 (全 PASS, 无回归):
- `tests/test_signal_detector.sh` 12 cases
- `tests/test_data_acquisition.sh` 8 cases
- v0.1.3 / v0.1.4 / v0.1.5 / v0.1.6 四个老 test suite 仍 PASS

### sc-bias 项目 — Perez 2022 SLE atlas 后台下载中

**正在跑的进程** (新 session 进来时检查):
- PID **122504** (`bash cellxgene_download.sh`)
- 输出目录: `/data3/liying/sc-bias/data/atlases/perez2022_sle/`
- 文件: `perez2022_sle.h5ad` (目标 11.38 GiB, 起跑时 2026-05-23 16:00:48)
- 进度查看: `tail -f /data3/liying/sc-bias/data/atlases/perez2022_sle/download.log`
- ETA: ~40 分钟 (5 MB/s 走 17891)
- 完成后自动写: RESULT.md + DATA_MANIFEST.json + perez2022_sle.h5ad
- EXECUTOR.json 已写, stale_running_handler.py 进 sc-bias 后会自动接管验证

**Perez 2022 SLE 元数据 (用于 Stage 2)**:
- 论文: Perez et al., Science 2022, DOI 10.1126/science.abf1970
- collection_id: `436154da-bcf1-4130-9c8b-120ff9a888f2`
- dataset_id: `218acb0f-9f2f-4f76-b90b-15a4b7c7f629`
- assets[].url: `https://datasets.cellxgene.cziscience.com/4118e166-34f5-4c1f-9eed-c64b90a3dace.h5ad`
- cell_count: 1,263,676 PBMCs
- tissue: blood; disease: ["normal", "systemic lupus erythematosus"]
- 162 SLE cases, 99 healthy controls; multiplexed scRNA-seq, 10X 3' v2
- **为什么是 Stage 2 黄金候选**: 自带 paired normal cohort (within-atlas audit 协议直接可用), disease 是 autoimmune (跨 indication 多样性, 不再是 cancer), 后 CellxGENE 2024-07-01 LTS, 4 FM 训练时大概率没见过

## 还差什么 (新 session 该做的事, 按优先级)

### P0 (最优先) — 验证 Perez 下载完成 + 真的能跑 stale_running_handler

新 session 进来时:
```bash
# 1. 检查下载是否完成
ls -la /data3/liying/sc-bias/data/atlases/perez2022_sle/
# 应该看到 perez2022_sle.h5ad ~11.4 GiB + RESULT.md + DATA_MANIFEST.json + executor.log

# 2. 如果还在跑
kill -0 122504 2>&1
tail -20 /data3/liying/sc-bias/data/atlases/perez2022_sle/download.log
# 如果死了但没写 RESULT.md, 看 executor.log 找原因; 大概率是 wget 重连重试可以接着 wget -c

# 3. 完成后, 手动跑 validator 验证 manifest schema 通过:
python3 /data3/liying/research-tree-explorer/scripts/charter_validator.py \
    /data3/liying/sc-bias/data/atlases/perez2022_sle/ \
    --task-type data-acquisition
# 应该 verdict=PASS
```

### P1 — 接着拉 Stage 2 第二个 atlas

handoff 列的 Stage 2 候选名单还差: **Adams 2020 IPF lung** / **Wu 2021 breast cancer** / **Magen 2023** / **Stephenson** (已有).

推荐先拉 **Adams 2020 IPF lung** (idiopathic pulmonary fibrosis), 因为:
- disease 类别又换了 (autoimmune → fibrotic), 跨 indication 多样性进一步增加
- 也是 PBMC 之外的 tissue (肺组织 vs 血), 验证 audit 协议 tissue-agnostic
- 有 paired control (健康 vs IPF)

操作步骤 (用 v0.1.7 的工具):
```bash
# 1. 搜
http_proxy=http://127.0.0.1:17891 https_proxy=http://127.0.0.1:17891 \
python3 /data3/liying/research-tree-explorer/examples/data-acquisition/cellxgene_discover.py \
    search --query "Adams pulmonary fibrosis" --limit 5
# 拿到 collection_id

# 2. 列 dataset
http_proxy=http://127.0.0.1:17891 https_proxy=http://127.0.0.1:17891 \
python3 /data3/liying/research-tree-explorer/examples/data-acquisition/cellxgene_discover.py \
    list-collection --collection-id <cid>
# 拿到 dataset_id + n_cells

# 3. 后台下
mkdir -p /data3/liying/sc-bias/data/atlases/adams2020_ipf && cd /data3/liying/sc-bias/data/atlases/adams2020_ipf
cp /data3/liying/research-tree-explorer/examples/data-acquisition/cellxgene_download.sh .
COLLECTION_ID=<cid> DATASET_ID=<did> ATLAS_ID=adams2020_ipf \
ATLAS_LABEL="Adams 2020 IPF lung scRNA-seq (paired normal/IPF)" \
PAPER_DOI=10.1126/sciadv.aba1972 PROXY=http://127.0.0.1:17891 OUT_DIR=. \
nohup bash cellxgene_download.sh > executor.log 2>&1 &
echo "{\"pid\": $!, \"started_at\": \"$(date -Iseconds)\", ...}" > EXECUTOR.json
```

### P2 — 真正启动 sc-bias autopilot

前提: v0.1.7 PR 合并 (或者直接在 v0.1.7 分支上跑也行 — `RESEARCH_TREE_REPO=/data3/liying/research-tree-explorer` 已经指向本地分支).

```bash
cd /data3/liying/sc-bias

# 删 v0.1.5 时代留下的 MISMATCH.md (v0.1.6 / v0.1.7 已修复)
rm -f .research-tree/MISMATCH.md

# (可选) 在 RESEARCH_CHARTER.md 加 §Data sources + §Pivot trigger
# rules; 复制 v0.1.7 模板的对应段就行

# 重启 autopilot. Lily 的指示是"通宵跑不要打扰", 所以 --silent + /loop:
# /loop 30m /research-tree autopilot --silent

# 监控:
# tail -f .research-tree/progress.log
# 邮件触发用 task-monitor skill 监控关键事件 (DONE / ROOT_FAILURE / STUCK)
```

### P3 (中) — UI 可视化 + 子代理观测

v0.1.7 的 autopilot 跑起来后, debug 时需要看 tree 状态. 现在只有 ascii (`tree_state.py tree`). 长跑 30+ 节点时 ascii 难看. 可考虑加一个简单 web dashboard:
- `scripts/dashboard.py` — 起 flask 服务, 实时读 tree.json + progress.log, 渲染 d3 force-directed tree
- 不优先, autopilot 没卡的时候不需要看

### P4 — codex audit cost / quota 监控 (从 v0.1.6 handoff 沿用)

如果 codex MCP 限流, autopilot 应该 backoff 而不是 fail-CLOSE 杀分支. 现在 v0.1.3 起是 hard dependency. 暂未发现实际触发, 但 Lily 长跑可能撞.

## 关键技术约束 (新 session 必读, 跟 v0.1.6 handoff 一样)

- **代理分流**: research-tree-explorer 仓库 git push 走 17891. autopilot 启动的子进程 **绝不走 17890** (Lily 自用按量计费). 新加的 cellxgene_download.sh / geo_figshare_download.sh 都 ✓ 用 17891.
- **GPU**: 4 × A800 80GB, 训练只用 3 张.
- **Codex MCP**: 是 hard dependency. 没 codex 整个 autopilot fail-CLOSE 杀分支.
- **铁律 1**: 不重复犯错. P0/P1/P2 设计已经规避了 sc-bias "POC 看到 +0.338 → 一路狂奔 → 跨 atlas 归零 → 全推倒重来" 的覆辙 — signal_detector 现在能区分单 atlas 单 dataset 信号和跨 atlas 复现信号 (CI 排除零是金标准, 不靠 p_value).

## 给 Lily 的承诺 (跟 v0.1.6 handoff 一样, 不重复)

1. 不问 Lily 技术细节. 2. 只在红线问 Lily. 3. 每个 phase commit + push. 4. tests 必须先 PASS. 5. 铁律 1 不重复犯错. 6. 完成后写新 handoff 给下一个 session.

## 仓库当前状态

```
/data3/liying/research-tree-explorer/                  ← 工具仓库
├── CHANGELOG.md                                        ← [0.1.7] 段最新加了啥
├── README.md                                           ← 待更新 v0.1.7 announcement
├── NEXT-SESSION-HANDOFF.md                             ← 这份文件
├── scripts/
│   ├── tree_state.py                                   ← v0.1.6 未动
│   ├── charter_validator.py                            ← v0.1.6 未动 (data-acquisition schema 早就有)
│   ├── synthesize_report.py                            ← v0.1.3 未动
│   ├── stale_running_handler.py                        ← v0.1.4 未动
│   └── signal_detector.py                              ← v0.1.7 新加 (P2)
├── examples/
│   ├── toy_classification/                             ← 早期 demo
│   └── data-acquisition/                               ← v0.1.7 新加 (P3)
│       ├── README.md
│       ├── cellxgene_discover.py
│       ├── cellxgene_download.sh
│       └── geo_figshare_download.sh
├── skills/research-tree/SKILL.md                       ← v0.1.7 加了 execute task_type=data-acquisition + expand auto-propose + autopilot step 7.5
├── templates/RESEARCH_CHARTER.md                       ← v0.1.7 加了 §Data acquisition rules + §Pivot trigger rules
└── tests/
    ├── test_tree_state.sh                              ← 仍 PASS
    ├── test_charter_validator.sh                       ← 仍 PASS
    ├── test_stale_running_handler.sh                   ← 仍 PASS
    ├── test_task_type_aware.sh                         ← 仍 PASS
    ├── test_data_acquisition.sh                        ← v0.1.7 新加 (8 cases)
    └── test_signal_detector.sh                         ← v0.1.7 新加 (12 cases)

/data3/liying/sc-bias/                                  ← 第一个客户项目 (autopilot 待重启)
├── CLAUDE.md                                           ← Phase 17 02-ROUTE-PIVOT.md framing 仍在
├── data/atlases/
│   ├── schulte_schrepping_covid_pbmc/                  ← 已有 (Stage 2)
│   ├── stephenson_haniffa_covid_pbmc/                  ← 已有 (Stage 2)
│   ├── external_rcc/li2022/zhang2021/gondal2025/       ← Phase 17 用过
│   └── perez2022_sle/                                  ← v0.1.7 dogfood 下载中 (PID 122504)
│       ├── cellxgene_download.sh                       ← 模板副本
│       ├── EXECUTOR.json                               ← 后台进程注册
│       ├── executor.log                                ← 跑时日志
│       ├── download.log                                ← wget 进度
│       └── perez2022_sle.h5ad                          ← 下载中
└── .research-tree/                                     ← 暂停状态, 17 alive + 4 dead 节点
```

## 估时

- P0 验 Perez 下载: 30 分钟 (大部分是等下载完)
- P1 拉 Adams IPF: 1 天 (含 discover + 后台下载 + 验证)
- P2 重启 sc-bias autopilot: 0.5 天 + 多日 unattended run
- P3 dashboard: 0.5 天 (低优, 可后排)
- P4 codex backoff: 0.5 天 (低优, 等真撞)

## 终止条件

下个 session 跑 ROOT_FAILURE / 卡死 / 拿不到资源 — 写一份新的 `NEXT-SESSION-HANDOFF.md` 把状态交给再下个 session, 不要叫 Lily 醒着. Lily 只在邮件通知触发时回来.
