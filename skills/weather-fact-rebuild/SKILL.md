---
name: weather-fact-rebuild
description: 同步、补全或重建 weather canonical 数据层与 JRS physical canonical weather.db。用于数据陈旧、目标日期缺失、settlement 或 fill 未进入 fact_trades/fact_signal_candidates、fee/fill 修复后重放、重新结算、sync/rebuild/resync。先验证 production manifest 与 DB identity，再按 Mac 当前生产、N100 历史恢复和 canonical 三层判断；禁止把 split DB、N100 或 WSL 当默认当前真相，也禁止为单笔 raw lineage 无条件全量重建。
---

# Weather fact refresh and rebuild

把“补当前 raw 数据”“刷新 canonical DB”“全量重建”分开。当前机器是 Mac；N100 在磁盘事故恢复完成前只提供历史/抢救数据，不是 present-state truth。

## 先读

依次读：

1. `AGENTS.md`
2. `docs/WEATHER_ANALYSIS_CONTRACT.md`
3. `docs/WEATHER_DATA_PIPELINE.md`
4. `docs/WEATHER_DATA_CANONICAL_SOURCES.md`

## 数据层级

| 层 | 当前来源 | 用途 |
|---|---|---|
| current raw market | `/Volumes/jrs/weather_data_feed_service_runtime`（旧 `~/projects/weather_data_feed_service_runtime` 为 symlink） | 最新 snapshot、orderbook、forecast、observation/source event |
| current raw execution | 本仓库 `runtime/weather_edge_v1/` 与各 active strategy runtime | 当前 Mac signal/plan/order/fill 证据 |
| historical remote | N100 `weather-predict` / `pm_agent` 镜像 | 事故前历史、备份抢救；不可冒充当前运行态 |
| canonical analysis | `/Volumes/jrs/pm_agents/runtime/weather.db`（`runtime/weather.db` 仅为同 inode 兼容入口） | `fact_signal_candidates` 机会粒度、`fact_trades` fill 粒度 |

## 决策顺序

0. 先运行 `.venv/bin/python scripts/ops/weather_production_ctl.py health` 与 `.venv/bin/python scripts/ops/weather_production_manifest.py --strict`。若 DB route 为 split、存在非 canonical consumer 或 manifest critical，停止 sync/rebuild；先完成 production identity/DB cutover，禁止挑一份 DB 当真相继续写。无关的 manifest warning 逐项记录，但不冒充 DB identity 故障。
1. 单笔订单、当前 runner、某次触发：直接读精确 raw 文件，不 sync、不 rebuild。
2. 历史分析且 DB 已覆盖目标窗：只读查询现有 DB。
3. 问“最新/今天”且 Mac market mirror 落后：先增量同步当前 Mac market raw。
4. 当前 live order/fill/fee 缺口：先走已登记的 bounded canonical refresh one-shot，只重放
   `order -> fill -> fact_trades -> coverage gate`，不无条件重算全部 candidate。
5. WCIR candidate/label/coverage 或其他派生层缺目标窗：只运行对应的、已审批的增量
   materializer；固定 `candidate_grain_version` 与输入 build，不直接改 raw journal。
6. 只在 schema/全历史派生层失效、全量输入需重算，或用户明确要求全量重建时，才执行
   `run_stack.sh --rebuild`。WCIR 迁移或单个 raw lineage 不能自动升级为全量重建。
7. 只有历史抢救问题才同步 N100；先注明 N100 覆盖截止时间与可达状态。

## 标准命令

同步当前 Mac market data：

```bash
scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only
```

预演历史 N100 同步：

```bash
scripts/ops/sync_weather_remote.sh --dry-run
```

当前 live execution 的最小 canonical refresh：

```bash
scripts/ops/start_weather_canonical_refresh_tmux.sh
```

该入口是 bounded one-shot，由 canonical JRS helper 承载；LaunchAgent 只触发它，不直接承载 JRS
子进程。先查 `runtime/weather_edge_v1/canonical_refresh/last_exit_status` 和日志，不得在它仍运行时另起第二个 refresh。
不得直接调用 refresh 内部的 ingest、fill sync 或 fact builder 来制造第二条生产刷新链。

全量 canonical 重建（显式动作；用户说“重建/重跑底表”即已授权，否则先说明影响）：

```bash
scripts/weather_dashboard/run_stack.sh --rebuild
```

无参数 `run_stack.sh` 只启动/复用 API 和 FE，不刷新 DB。不要手工删除 `runtime/weather.db`；`--recreate-db` 比 `--rebuild` 更强，必须有明确授权。

不要用 WSL 命令。项目 Python 默认使用 `.venv/bin/python`。

## 重建前检查

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
sqlite3 -batch -cmd ".timeout 1000" runtime/weather.db "
SELECT 'fact_trades', MAX(fact_built_at_utc), COUNT(*) FROM fact_trades;
SELECT 'fact_signal_candidates', MAX(fact_built_at_utc), COUNT(*) FROM fact_signal_candidates;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
"
```

同时比较目标 raw 文件最新时间。DB 新鲜度不能只看 mtime；必须看目标窗口和 `fact_built_at_utc`。
分析/重放开始时还必须保存 DB realpath、device/inode、canonical materialization `build_id`
（若该层已提供）与 `observed_at_utc`。同一份报告运行中若 build 发生变化，必须重启该次查询或明确分层，
不得静默混合两个分母。

## 重建后五项自检

1. `fact_trades` / `fact_signal_candidates` build 时间、build identity、`candidate_grain_version` 与目标窗口。
2. `trade_class` 分布，已知存在真实成交时 `live_real` 不得意外归零。
3. settlement 覆盖、`missing_event` / `missing_bracket` / unresolved 数量。
4. 机会覆盖：`eligible` / `paper_ordered` / `live_filled` 与 `decision_window_missing`。
5. CLOB order/fill、fee evidence 与 canonical cost 一致性。

发布 `live_real` 结果前必须运行：

```bash
.venv/bin/python scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

`gate_pass=false` 时停止 live PnL/ROI 发布，先修 fill 链。不能以 live_real 行数“看起来合理”替代 gate。

## Fill 与 fee 规则

- fill 数量优先 matched response / authenticated order-trade；public activity 只能作受 cap 约束的 fallback。
- fee 证据链与 fill 数量证据链分开；即时 matched 不能把 fee 默认为 0。
- `fills` append-only。历史 fee 修正写 `clob_fill_fee_adjustments.jsonl` 并物化到 `fill_fee_adjustments`，不 UPDATE/DELETE 原 fill。
- fee 修复先在 canonical JRS 上运行 `scripts/ops/reconcile_clob_fill_fees.py` dry-run，核对 alias/excluded fill 已从分母剔除；再按证据写 append-only journal。优先 tx-exact，其次 maker-zero，无法取得精确证据时才单列 estimate，不能把 estimate 说成实际 fee。
- journal 写入后必须重新 import/materialize facts，并核对 journal 行数、DB adjustment 行数、effective fee delta 和受影响 settled PnL delta；只生成 journal 不算完成重放。
- 重建后检查 `fact_trades.base_fees_usd`、`fee_adjustment_usd`、`fees_usd`、`fee_source`、`fee_evidence_class`。
- maker zero、tx-exact public activity、Weather fee curve estimate 分层报告。

## 修复后的影响半径

若本次不是普通刷新，而是修数据源、builder、settlement、fill 或 fee bug，交付不能停在“重建成功”。必须：

1. 定出污染窗口和受影响 grain。
2. 用修复后数据重放同一窗口。
3. 给出受影响订单/机会数量与逐条清单。
4. 说明哪些旧结论、标签或 live 决策变化。
5. 把污染窗口写进现有数据治理/权威文档，不另建零散规则文档。

## 交付证据

报告：同步源与命令、raw 覆盖截止、DB build 时间、两张 fact 行数、settlement 覆盖、CLOB gate、失败日志，以及是否完成影响重放。任何一步没做都明确写出原因。
