# Weather 看板重做设计文档（v1）

Status: `design-approved-pending-spec-review`
Date: 2026-06-26
Owner: deepsleep
Scope: 前端整套重写 · 后端复用 + 扩接口 · 旧 PMM/ARB 页面收进归档区

> 一句话目标：把看板从"PnL/持仓排行榜"重做成"**前向取证探针的健康台 + 研究证据登记册**"，
> 让一个不带上下文的人打开就能看懂"现在哪几条策略在跑、数据新不新鲜、能不能信、该不该动手"。

---

## 0. 为什么要重做（问题陈述）

现状 `frontend/strategy_dashboard`（React/Vite SPA）+ `weather_dashboard/api`（FastAPI 读 `runtime/weather.db`）有三个硬伤：

1. **信息架构停在旧时代**。导航里混着已不是主线的旧 PMM/ARB 页面（Dashboard / Strategies / Instances / Accounts / Backtests），首页默认跳 `/weather/strategies`，但当前主线（current-YES tiny-live、metar-cross、regime-routed NO、regime atlas 研究线）没有对应一等视图。
2. **看的是错的镜头**。`/weather/live` 只读已结算 `fact_trades` 做持仓/PnL。但 registry 的结论是"**当前没有任何已确认稳定盈利的 live alpha**，在跑的都是 tiny-live 前向取证（$5–$10 微仓），按执行质量/滑点评估、不按早期 PnL"。看板却仍把 PnL 当主轴 → 误导。
3. **不给人看**。满屏生硬英文字段（`cap_order_notional` / `posted_notional` / `heartbeat_age_min` / `leave_best_out_ev` / `avoided_loss_usd`），橙蓝渐变玻璃拟态花哨但不清晰，缺空状态/旧数据/拦截原因的人话解释。

**实时数据在哪**：每条 live/shadow 探针在 N100 跑，向 `runtime/weather_edge_v1/<probe>/` 写
`latest_summary.json`（快照新鲜度、audit 计数、candidates、caps、status）、`summary_history.jsonl`、
`forward_telemetry.jsonl`、`latest_candidates.json`。这些是"实时抓取模块"的脉搏，**现在的看板完全没暴露**。
DB 侧已有 `weather_strategy_runtime_registry` / `weather_strategy_runtime_artifacts` 两张注册表汇总它们。

---

## 1. 目标与非目标

**目标**
- 四个一等职责，各有主视图：① 在跑探针的**健康/执行质量** ② **前向研究证据**累积 ③ **已结算绩效/对账** ④ **单日血缘**逐笔复盘。
- 中文为主、字段悬浮原名+口径；红绿灯语义统一；每个数字回答"是什么/该不该担心/我能做什么"。
- 复用后端血缘（canonical 表 + 现有 routers），只新增读 JSONL 脉搏、聚合研究证据、字段字典三类接口。

**非目标（YAGNI）**
- 不重写后端血缘链（`signal→plan→order→fill→settlement` 是永久基建，不动）。
- 不删旧 PMM/ARB 页面（dormant 保留），只收进 `/archive` 折叠区、不在主导航露出。
- 不做用户系统/权限/多租户；不做移动端专门适配（桌面优先，能用即可）。
- 不在看板里做下单/改 live 任何写操作（硬边界：资金/不可逆动作不经看板）。看板**只读**。

---

## 2. 信息架构（方案 A：按探针生命周期）

```
今日总览   /                    脉搏首页
探针在跑   /probes              live & shadow 探针健康 + 执行质量（核心新增）
  探针详情 /probes/:instance    单探针：脉搏史 + 今日候选/拦截 + 执行质量 + caps
研究证据   /research            前向证据登记册（各策略线 holdout/forward/CI/live-gate）
  证据详情 /research/:lineId    单条研究线：指标表 + CI + 直链分析 doc
绩效对账   /performance         已结算 ROI + 现金流(fill_date_bj) + gate_pass 横幅
单日血缘   /lineage/:date       某日 signal→plan→order→fill→settlement 逐笔
─────────────────────────────
术语字典   /glossary            canonical 字段 → 中文/口径（也作全站 hover 数据源）
归档/legacy /archive            旧 PMM/ARB 页面入口（折叠，dormant 不删）
```

侧边导航只列上面 6 个主项 + 底部 2 个次级（术语字典、归档）。默认落地 `/`（今日总览）。

---

## 3. 贯穿全站的人类交互原则（本次重点）

这一节是"搞的人类交互理解好一点"的核心，所有页面都遵守：

1. **先给人话结论，再给字段**。每张探针卡 / 研究线 / 绩效块，顶部一行自然语言判断
   （借鉴各 doc 的"一句话结论"），原始指标收在下方"看原始字段"折叠里。
   例：不是 `snapshot_age_min: 96.357`，而是 `🟡 行情快照 96 分钟前更新（超过 20 分钟新鲜线，偏旧）`。
2. **红绿灯语义全站统一**，只定义一次：🟢 新鲜/健康/已通过 · 🟡 注意/偏旧/CI 跨 0 · 🔴 需处理/断流/gate 未过 · ⚪ 无数据/今日未触发。
3. **每个数字回答三问**：这是什么（hover 字段+口径）/ 该不该担心（颜色+阈值说明）/ 我能做什么（关联动作或链接）。
4. **镜像同步 ≠ 生产健康**（防误导关键点）。本机是 N100 的只读镜像，可能滞后。
   新鲜度一律分两层显示：「镜像同步脉搏 `heartbeat_age_min`」与「生产是否断流」。
   后者**不**用镜像新旧推断，而是给一个明确提示 + N100 doctor 命令链接：
   `ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'`。
5. **悬浮即口径**。所有 canonical 字段名以"点状下划线"提示可悬浮，hover 弹出
   `字段原名 · 中文 · 口径定义（来源 WEATHER_SYSTEM_CONTRACT）`。数据源 = `/glossary`。
6. **渐进披露**。人话摘要在上；需要精确时点"看原始字段/原始 JSON"展开 canonical 表。
   研究线点开看完整指标表与 CI；探针点开看 `summary_history` 时间线。
7. **空/旧/错状态是一等公民**，都有人话：
   - 空：`今天 13:00 前还没产候选（探针在等盘口/新鲜观测）` 而不是空表。
   - 旧：黄/红条 + 明示 `snapshot_ts_utc` 与超过的新鲜线。
   - 错：`观测缓存缺失 observation_cache_missing（Chongqing）→ 该城今天不可下单` 而不是静默吞掉。
8. **时间双标**：涉及现金流/结算的地方同时给北京时间与 UTC，并标注用的是 `fill_date_bj`
   （现金流口径）还是 `order_date_bj`（仅诊断，受回填污染）。
9. **口径护栏内联**，把踩过的坑写在界面上：
   - `open cost ≠ 亏损`（开仓成本块旁注）。
   - 未结算只报 MTM 并附 `val_snapshot_ts_utc`；估值旧就标"估值偏旧"。
   - 顶部常驻 `gate_pass` 横幅：红就提示"先修数据链，当前 PnL 不可发布"。
   - near-binary `0.9995/0.0005` 已归一化 `1/0`，旧口径数字标注作废。
10. **一键下钻固定路径**：探针卡 → 探针详情 → 单日血缘逐笔；研究线 → 分析 doc；绩效行 → 单日血缘。

---

## 4. 页面设计

### 4.1 今日总览 `/`
顶部三块**脉搏卡**：
- **探针健康**：每条 live/shadow 探针一颗红绿灯（来源 §4.2）。汇总"N 条在跑 / M 条偏旧 / K 条断流待查"。
- **在险资金**：live_real 未结算 `open_cost`（注明"开仓成本，非亏损"）+ 今日已用 notional vs caps。
- **今日候选 / 拦截**：今天各探针产出的 candidate 数与 top 拦截原因（`obs_not_ok` / `stale_obs` / `no_market`…）。

下方两条带：
- **累计已结算 ROI 条带**：硬口径 settled ROI + `gate_pass` 状态点（链到绩效页）。
- **最近变更的研究线**：按 doc mtime 排 3–5 条，带状态徽章（链到研究页）。

数据：`/api/probes/health`（新）+ `/api/live/summary`（复用）+ `/api/research/lines`（新）。

### 4.2 探针在跑 `/probes`（核心新增）
列表，每条探针一张卡。数据底座 = `weather_strategy_runtime_registry`（注册表即白名单，已排除 smoke/tmp）
叠加 `latest_summary.json` 实时脉搏。卡内：
- **人话结论行** + 状态徽章（live / shadow / telemetry / blocked / stale，来自 `lifecycle_status`）。
- **新鲜度（双层）**：镜像同步 `heartbeat_age_min` 红绿灯；生产健康提示 + N100 doctor 链接。
- **今日**：candidate 数、execution_eligible、是否下单、top skip/audit 原因（人话）。
- **执行质量**（live 探针重点，按它评估而非 PnL）：fresh-ask 滑点、`max_obs_age_min` 命中率、
  maker 成交占比（若有）、caps（`$5/单`、`$15/天` 等用人话标）。
- 折叠"看原始字段"：`latest_summary.json` 关键字段表（悬浮口径）。

**探针详情 `/probes/:instance`**：`summary_history.jsonl` 画脉搏时间线（快照年龄/候选数随时间）；
今日 `latest_candidates.json` 候选表；`live_orders.jsonl` 今日实单；下钻单日血缘。
数据：`/api/probes/{instance}`（新）+ 复用 `/api/strategy-runtime/{instance}/detail`。

### 4.3 研究证据 `/research`
前向证据**登记册**。每条策略线一行：状态徽章、holdout ROI、forward ROI、CI 是否跨 0、
excess vs baseline、是否够 live-gate（三门）、数据窗口、直链分析 doc 与 `summary.json`。
顶部筛选：分支（pre_predict / reheat_risk）、状态、是否够 gate。
数据：`/api/research/lines`（新，聚合 `docs/analysis/**/generated/*/summary.json` + registry）。
**详情 `/research/:lineId`**：完整指标表（train/holdout/forward）、变体对比、CI 图、doc 正文链接。
复用 `/api/research/*`、`/api/compare`。

### 4.4 绩效对账 `/performance`
硬口径已结算绩效。顶部常驻 `gate_pass` 横幅（来自 `weather_clob_fill_coverage_gate`）。
分列展示 `submitted_notional` / `posted_notional` / `actual_fill_cost` / `open_cost` / `realized_pnl`
（绝不混报）；现金流按 `fill_date_bj`；未结算只报 MTM + `val_snapshot_ts_utc`。
切片：按城市 / 方向 / 探针。数据：复用 `/api/live/summary`、`/api/configs/strategies/*`、`/api/runs/*`。

### 4.5 单日血缘 `/lineage/:date`
某城某日 `signal → plan → order → fill → settlement` 重排成可读时间线，解释"为什么下/不下这单"。
复用 `/api/runs/{run_id}/trades/{signal_id}`，按 城市×日 索引。

### 4.6 术语字典 `/glossary` + 归档 `/archive`
- `/glossary`：canonical 字段 → 中文 + 口径 + 来源 doc，可搜索；同时是全站 hover 的数据源。
- `/archive`：旧 PMM/ARB 五页（Dashboard/Strategies/Instances/Accounts/Backtests）原样保留在折叠入口，
  顶部横幅注明"legacy / dormant，非当前主线"。

---

## 5. 后端改动（复用为主，新增三类只读接口）

复用（不动）：`/api/live/*`、`/api/strategy-runtime/*`、`/api/runs/*`、`/api/configs/strategies/*`、
`/api/research/*`、`/api/compare`、`/api/copy-trade/*`。

新增（只读）：
- `GET /api/probes/health` — 遍历 `weather_strategy_runtime_registry` 的活跃探针，读各
  `latest_summary.json`，返回归一化健康行（状态、`heartbeat_age_min`、`snapshot_age_min`、
  candidate/eligible、top audit、caps）。缺文件/解析失败 → 该行 `status=unknown` 且不崩。
- `GET /api/probes/{instance}` — 单探针脉搏史（`summary_history.jsonl` 尾部 N 行）+
  今日 `latest_candidates.json` + `live_orders.jsonl` 尾部。复用现有 `_read_recent_jsonl` 工具。
- `GET /api/research/lines` — 聚合 `docs/analysis/**/generated/*/summary.json` + registry，
  产出研究线登记行（状态、holdout/forward ROI、CI、excess、gate、doc 路径）。
- `GET /api/glossary` — 静态字段字典（`field → {zh, definition, source_doc}`），
  源自 `WEATHER_SYSTEM_CONTRACT.md`，落一个 `weather_dashboard/api/glossary.json` 维护。

显式失败：快照旧/缺一律带状态返回并标注 `*_ts_utc`，**不静默换旧数据冒充新鲜**（项目工程姿态）。

---

## 6. 视觉与组件语言

- 去掉橙蓝渐变玻璃拟态。改浅色信息密集风：近白背景、克制留白、表格优先、卡片次之。
- 语义色仅四档（绿/黄/红/灰），不滥用强调色；强调色仅用于"需动手"。
- 字体：正文用系统中文无衬线（可读优先），数字/字段用等宽（对齐）。
- 复用组件：`PageFrame`、`Sparkline`、`MetricsCard`；新增 `HealthDot`（红绿灯）、
  `GlossaryTerm`（点状下划线 + hover）、`VerdictLine`（人话结论行）、`FreshnessBadge`（双层新鲜度）、
  `EmptyState`（人话空状态）。
- 数据层：复用 `weather-http` provider；新增 probes/research-lines/glossary 三个 client 方法与类型。

---

## 7. 数据流与错误处理

```
N100 探针 → runtime/weather_edge_v1/<probe>/latest_summary.json (+ history/telemetry/candidates)
          → sync 到 mac 镜像
DB:  weather_strategy_runtime_registry / _artifacts（白名单 + 心跳）
FastAPI: probes/health 读 registry 选探针 → 读各 latest_summary.json → 归一化
FE: 30s 轮询 /probes/health 与 /live/summary；其余页按需拉取
```

错误/旧数据：
- 镜像滞后 → 黄/红"镜像偏旧"+ doctor 链接，**不**判生产死亡。
- 单探针文件缺失 → 该卡"无脉搏文件"，其余正常。
- `gate_pass=false` → 绩效页红横幅，禁用"可发布 PnL"语气。

---

## 8. 测试

- 后端：对 `probes/health`、`probes/{instance}`、`research/lines`、`glossary` 做契约测试 ——
  新鲜度分级正确、缺文件/坏 JSON 不崩、字段齐全。用临时 fixture 目录构造 `latest_summary.json`。
- 前端：关键页（今日总览、探针在跑、研究证据）Playwright 冒烟 —— 加载、空状态、hover 字典出现。
- 口径回归：绩效页 `open_cost` 不进 PnL、未结算只出 MTM、`fill_date_bj` 用于现金流，用断言锁住。

---

## 9. 分期落地

- **P0 脚手架**：新导航/路由/视觉骨架 + `/glossary` 字典 + 旧页收进 `/archive`。
- **P1 探针在跑**（最高价值）：`probes/health` + `probes/{instance}` + 今日总览脉搏卡。
- **P2 研究证据**：`research/lines` 聚合 + 研究页。
- **P3 绩效对账 + 单日血缘**：复用接口接绩效页与 lineage 页，补口径护栏与 gate 横幅。
- 每期可独立 demo；P1 完成即覆盖"现在哪条在跑、新不新鲜、能不能信"的核心诉求。

---

## 10. 待确认 / 风险

- **研究线聚合口径**：`research/lines` 从 `summary.json` 取哪些字段需对齐 STRATEGY_REVIEW_PIPELINE，
  各 doc 的 summary 结构可能不齐 → 解析要容错、缺字段显"—"。
- **N100 生产健康**：看板默认只读镜像；是否要在看板内嵌一个"调 doctor"的只读状态拉取（不触发重启），
  还是仅给命令链接，待定（倾向只给链接，避免看板触达生产）。
- **glossary 维护**：字典手工维护成本；初版只覆盖看板出现的字段，逐步补全。
```
