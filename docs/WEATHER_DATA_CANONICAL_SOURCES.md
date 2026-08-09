# Weather Data Canonical Sources

Status: current-source
Updated: 2026-08-09 Mac raw, canonical DB and archive identity boundary
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

这份文档只回答“当前从哪读、哪一层能回答什么”。脚本职责见
[WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md)，字段/分析口径见
[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)，运行边界见
[WEATHER_REPO_BOUNDARY.md](WEATHER_REPO_BOUNDARY.md)。

## 0. 先确认 identity

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
```

physical canonical DB 是 `/Volumes/jrs/pm_agents/runtime/weather.db`。仓库 `runtime/weather.db` 只是兼容入口，
必须与 physical canonical 解析到同一 device/inode。`/Volumes/jrs` 还必须匹配 production contract 中的 volume UUID。

以下任一情况均先按 P0 处理，不得任选一份继续分析或重建：

- volume UUID 不符；
- repo compatibility path 与 physical DB 不是同一 inode；
- 存在非 canonical DB consumer 或独立可写 `weather.db`；
- current raw root、active live journal 或 artifact root 绕过 production loader；
- manifest 有与目标 DB/runtime identity 相关的 critical。

## 1. 当前数据层级

```text
Mac raw runtime + exchange evidence
  ├─ forecast / observations / source events
  ├─ market_books / strategy_snapshots / market_ladder_snapshots
  └─ signal / plan / order / fill journals
            │ bounded canonical refresh / explicit rebuild
            ▼
/Volumes/jrs/pm_agents/runtime/weather.db
  ├─ signals / plans / orders / fills / settlements / runs
  ├─ fact_signal_candidates   opportunity grain
  └─ fact_trades              fill grain
            │
            ▼
analysis / API / dashboard

N100 mirrors + /Volumes/jrs-archive
  └─ historical recovery/replay inputs only; never current fallback
```

DB 是 derived analysis layer，不替代当前进程与交易所事实。问“现在是否运行/是否下单/订单是否成交”时，先读
manifest、对应 Mac raw journal 和 exchange response；问历史机会、绩效、fee-adjusted PnL 时读 canonical facts。

## 2. 任务到数据源的唯一映射

| 问题 | 当前权威输入 | 禁止旁路 |
|---|---|---|
| 当前 producer/runner 是否健康 | controller health + strict manifest + target runtime freshness/read-write probe | session 存在、FDA 开关、旧文档 active label |
| 当前盘口与完整 ladder | `market_books` raw + `market_ladder_snapshots` | `targeted_output`、`full_ladder_output`、consumer 再拉 CLOB |
| 当前 forecast/observation/source event | production loader 解析的 Mac data-feed products | N100 cache、旧 full snapshot METAR、静默 live-fetch fallback |
| 单笔为什么下/没下/成交 | 精确 raw lineage + exchange response；`fact_trades` 补 fill/fee/settlement | 为单笔问题全量 rebuild |
| 全机会、漏单、成交质量 | `fact_signal_candidates` | 从 paper snapshot 临时自建机会表 |
| 已成交绩效、PnL、ROI | `fact_trades` | raw paper ledger、旧 summary CSV/JSON、自算 fill PnL |
| 当前开放订单与敞口 | active raw/exchange + exposure skill | 把 submitted notional 或 open cost 当 realized loss |
| 余额/现金流 | authenticated fills、open-order reserve 与 reconcile skill | `order_date_bj`、把 fill cost 当亏损 |
| settlement/source-grain outcome | `settlements` + `settlement_outcomes` | 每个策略各读 raw pm_history 并自定义 fallback |
| 大型研究机器产物 | `production.yaml.research_artifact_root` 的 content-addressed artifact + manifest | 仓库 generated 目录或热盘第二份正本 |

### Raw product owners

| product | owner / 语义 |
|---|---|
| `forecast/forecast_hourly_curves/` | forecast collector 的 PIT curve/run evidence |
| `output/observations/`、`output/source_events/` 与 HF/runway families | observation/source producer 的 raw delivery 与 latest cache |
| `market_books/latest.json` + `batches/` | `weather_market_books` 唯一 raw Gamma/CLOB book 正本 |
| `strategy_snapshots/` | data-feed join 后的策略消费视图 |
| `market_ladder_snapshots/` | 完整 event/rung/two-sided distribution 与 batch completeness |
| `production.yaml.historical_paper_snapshot_root` | 旧 collector 时期不可重采的 immutable PIT strategy snapshots；只作历史训练/回放输入，不是 current fallback |
| production manifest 登记的 live order paths | 当前 strategy order/execution journals |
| canonical fill cache + authenticated CLOB | order-level fills；public activity 只能作受约束的辅助证据 |

所有 path 都通过 production loader 解析；表中的语义名不是让业务代码复制 physical path。
retired collector 的 physical mapping 只允许登记在 `production.yaml`；可执行 Python/shell/service/config 由回归测试扫描，
禁止再出现对应 path literal。旧名字留在 archive 的物理布局与历史文档中不等于它仍是 current data contract。

历史与当前不应物理混放：当前 mutable raw 由 `market_books`/`market_ladder_snapshots` 单 owner 持续写入，
历史 `paper_snapshots` 保留原始 schema 和 source path。研究代码通过 production loader 取得这两层，再投影到同一
snapshot/rung contract；development、validation、forward 只是模型 split，不能再作为 raw source 的读取边界。

### Historical ladder coverage boundary (2026-08-09 census)

- archive raw：6,569 个 `snapshot_*.json`，capture 2026-05-05..2026-08-05，约 18.5 GB；其中
  target_date 2026-05-19..2026-07-10 在 30 分钟采样后为 178,810 ladders / 1,586,613 rungs / 53 dates / 48 cities。
- current `strategy_snapshots`：3,149 个 timestamped JSON，capture 2026-07-02..2026-08-09；其中 2,798 个文件名与
  archive 重叠且 size 相同，不能把两个目录直接 concat 成训练集。
- repo compatibility `market_data/paper_snapshots` 不是另一份 raw owner。2026-08-09 对它与 current
  `strategy_snapshots` 做全文件 SHA-256 核验后，将 2,886 个完全相同文件原子改为同盘 hard link，保持旧 path/内容不变并
  释放 10,964,734,619 bytes 重复物理块；2 个同名但 size 不同与 10 个 compatibility-only 文件保留，禁止按文件名覆盖。
- retired hot collector roots 在清理前逐文件与 archive 比对：先补迁 345 个 archive 缺失文件，并把差异运行日志另名完整保留；
  复核 4,100 个文件 / 1,689,779,927 bytes 全部逐字节一致且无进程引用后，已从 NVMe 删除两棵热盘副本。历史物理布局只由
  `production.yaml.historical_*` 登记，不再允许 executable consumer 写死旧目录。
- 当前 `tmax_v2_ladder_snapshots` 覆盖 target_date 2026-07-04..2026-08-08：398,600 physical rows / 36 dates / 47 cities；按
  `(city,target_date,event_identity,source_snapshot_ts_utc)` 归并为326,370 logical captures，72,230 rows仍是多个历史路径重复物化。
  其中7/29..8/8从raw `market_books/batches`按同batch末次response/fetch保守时钟补入47,806 full-ladder rows；模型读取必须
  先按capture clock去重并优先full-ladder raw lineage，且不得把`tmax_v2`称作项目full history（5月训练仍来自immutable archive adapter）。
- 旧 archive 不是与 7 月后同质量的 weather state：上述 historical-training slice 有 163,773 / 178,810 ladders
  缺 intraday observation，107,861 / 178,810 只能使用 legacy forecast hash proxy。报告必须单列这些 coverage gap，
  不能让 imputation 把两代 schema 的差异伪装成 alpha。

## 3. Canonical tables

| 表 | grain | 用途 |
|---|---|---|
| `signals` / `plans` / `orders` / `fills` | 对应 lineage event | 原始执行事实；order 不等于 fill |
| `settlements` | condition/bracket trade join | 成交结算关联 |
| `settlement_outcomes` | `(source_system,city,target_date,bracket)` | source-grain basket/label research |
| `fact_signal_candidates` | opportunity/candidate | signal/evidence funnel、漏单、执行质量 |
| `fact_trades` | fill | fee-adjusted settled PnL 与绩效 |
| `fact_forecast_hourly_curves` | forecast curve identity/checkpoint | PIT forecast curve feature lineage |

`submitted_notional`、`posted_notional`、`actual_fill_cost`、`open_cost` 与 `realized_pnl` 必须分开。只有 settled fill
才能发布 `pnl_usd_at_fill`；未结算只能报带 `val_snapshot_ts_utc` 的 MTM。

## 4. Refresh 与 rebuild

日常增量刷新只有一个 bounded one-shot：

```bash
scripts/ops/start_weather_canonical_refresh_tmux.sh
```

它只从 production contract 解析 active journals，增量 ingest order/fill、运行 coverage/fill gate 并物化受影响 facts。
任务仍在运行时不得重复触发。

全量重建必须明确授权：

```bash
scripts/weather_dashboard/run_stack.sh --rebuild
```

重建前必须验证 raw inventory、current Mac runtime identity、historical recovery inputs、settlement 与 fill cache 均可恢复。
全量重建不是修单笔 lineage、数据陈旧或 API 失败的默认手段。

## 5. 发布分析前的 5 行 SQL

先以 WAL read-only 连接运行：

```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled)
FROM fact_signal_candidates;
SELECT o.status, COUNT(*) AS orders,
       SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
FROM orders o LEFT JOIN fills f USING(execution_id)
WHERE o.venue='polymarket_clob' GROUP BY o.status;
```

发布任何 `live_real` PnL/ROI 前还必须运行：

```bash
.venv/bin/python scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

`gate_pass` 不为 true 就先修 fill/canonical 链，不能拿 simulated/paper/public activity 填空。

## 6. 持久口径与已知风险

- pm_history 的 near-binary `0.9995/0.0005` 必须归一化为 `1/0`；修复前报告数字不可直接复用。
- raw source unit/value 必须先保留，再映射到 settlement-source native lattice；同站或高频小数值不等于同 bracket truth。
- forecast/cache 缺失必须显式失败；禁止静默 model/source fallback。
- source first-seen 必须区分 event/observed/ingested clocks、重复 poll、late backfill 与跨源同内容。
- order journal 是提交/执行 attempt，不是成交现金流；fill、fee、open reserve 与 settlement 必须对账。
- coverage 缺失属于 evidence funnel，不能伪装成策略 filter。
- “全部历史”必须声明输入、日期/城市/模型范围与逐层 funnel；任一 training slice 都不是项目全部数据。

## 7. Legacy 与 archive

`runtime/_legacy/*.db`、N100 双 `runtime/` 残留 DB、旧 `weather-predict` outputs、WSL paths、retired strategy
summaries 与 dated migration commands 都不是当前数据源。历史 raw/canonical evidence不因暂时不用而删除；由 archive root、
artifact manifest 或 git history保留。

退役/删除任何 raw、canonical 或仅语义近似的机器产物前必须另行确认。可重放派生产物也只有在 clean replay SHA 完全一致、
无 consumer 且 tombstone 完整时，才可用 artifact controller 清理。

## 8. 维护规则

- 新 raw product：登记 owner、grain、identity/clocks、mutable/append-only 语义和唯一 physical root。
- 新 derived table：登记 grain/producer，并同步 analysis contract。
- 新兼容 alias：证明同 identity，注明消费者和删除条件。
- 新数据缺口：记录受影响窗口、数量、决策重放与治理标记，不只记录机制。
- 历史演进证据进入 dated snapshot/living incident doc；不要在本 current-source 文档维护旧运行拓扑和可执行命令。
