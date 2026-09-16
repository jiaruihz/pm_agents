# reheat-risk Observed-Max Residual v0 — Physical Layer Experiment

Status: snapshot
Updated: 2026-06-10
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; docs/analysis/reheat_risk.md

## 数据快照

本报告只验证 reheat-risk 的第一层物理问题：当地傍晚已观测 running max 到最终当日 max 的残差。
本轮不读取 market price、orderbook、fills、PnL，也不输出 live 动作。

本次未执行完整 N100 `sync_weather_remote.sh` + `run_stack.sh`，使用本机缓存：

| 项 | 值 |
|---|---:|
| DB | `runtime/weather.db` |
| DB mtime | `2026-06-10 01:38:04 +0800` |
| `MAX(fact_built_at_utc)` | `2026-06-09T17:37:40.513320+00:00` |
| `fact_trades.live_real` | 1405 |
| `fact_trades.live_simulated` | 1147 |
| `fact_trades.paper` | 2285 |
| `fact_trades.snapshot_replay` | 636 |
| `settled` | 5241 |
| unsettled / NULL status | 232 |
| `fact_signal_candidates` rows | 25117 |
| `SUM(eligible)` | 8306 |
| `SUM(paper_ordered)` | 3139 |
| `SUM(live_filled)` | 554 |
| CLOB orders `submitted` / with fill | 1635 / 1405 |
| CLOB orders `error` / with fill | 151 / 0 |

CLOB coverage gate 通过；但本报告没有发布 live_real PnL：

```text
gate_pass=true
fact_trades_live_real.rows=1405
fact_trades_live_real.fill_ids=1405
missing_order_rows=0
over_order_keys=0
db_fill_cost_minus_fact_cost=0.0
db_vs_primary_cache.db_not_in_cache=0
db_vs_primary_cache.cache_not_in_db=0
```

观测源：

```text
runtime/weather_edge_v1/market_data/cache/wu_obs/wu_obs_<ICAO>.csv
```

可用窗口：

| universe | cities | rows | local date range |
|---|---:|---:|---|
| all cached city/stations | 49 | 90,180 | 2024-04-30 to 2026-05-12 |
| current live core 9 | 9 | 26,472 | 2024-04-30 to 2026-05-06 |

## Target Metric

```text
m3_observed_max_residual_c
= final_daily_max_c - observed_running_max_c_at_decision_time
```

决策时点固定为当地时间：

```text
18:00, 19:00, 20:00, 21:00
```

v0 物理候选门：

```text
city_days >= 30
p95_residual_c < 1.0
floor_c_bucket_delta_ge_1_rate <= 10%
```

`floor_c_bucket_delta` 只是 v0 proxy，用 `floor(final_max_c) - floor(running_max_c)` 粗略表示是否跨过 1°C bracket；真实合约 rounding / bracket 规则仍需单独验证，不能直接拿它下单。

## 实验产物

脚本：

```text
scripts/analysis/observed_max/research_m3_observed_max_residual.py
```

全量输出：

```text
docs/analysis/2026-06/generated/m3_observed_max_v0/manifest.json
docs/analysis/2026-06/generated/m3_observed_max_v0/m3_observed_max_cache_coverage.csv
docs/analysis/2026-06/generated/m3_observed_max_v0/m3_observed_max_residual_detail.csv
docs/analysis/2026-06/generated/m3_observed_max_v0/m3_observed_max_residual_by_city_hour.csv
docs/analysis/2026-06/generated/m3_observed_max_v0/m3_observed_max_residual_by_hour.csv
```

Core 9 输出：

```text
docs/analysis/2026-06/generated/m3_observed_max_core9_v0/manifest.json
docs/analysis/2026-06/generated/m3_observed_max_core9_v0/m3_observed_max_residual_detail.csv
docs/analysis/2026-06/generated/m3_observed_max_core9_v0/m3_observed_max_residual_by_city_hour.csv
docs/analysis/2026-06/generated/m3_observed_max_core9_v0/m3_observed_max_residual_by_hour.csv
```

## Headline Result

物理层第一关通过，尤其是 19:00 以后。

全量 49 城按小时汇总：

| decision_hour_local | city-day rows | cities | P50 residual C | P90 residual C | P95 residual C | residual >=1C | bucket delta >=1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 18 | 22,545 | 49 | 0.0 | 0.0 | 0.0 | 1.26% | 1.41% |
| 19 | 22,545 | 49 | 0.0 | 0.0 | 0.0 | 0.86% | 0.97% |
| 20 | 22,545 | 49 | 0.0 | 0.0 | 0.0 | 0.66% | 0.76% |
| 21 | 22,545 | 49 | 0.0 | 0.0 | 0.0 | 0.50% | 0.59% |

全量候选门：

| decision_hour_local | physical candidates | total city-hours |
|---:|---:|---:|
| 18 | 45 | 49 |
| 19 | 49 | 49 |
| 20 | 49 | 49 |
| 21 | 49 | 49 |

18 点没过门的城市只有：

| city | icao | city_days | P95 residual C | bucket delta >=1 |
|---|---|---:|---:|---:|
| Paris | LFPG | 735 | 1.11 | 5.17% |
| Amsterdam | EHAM | 365 | 1.11 | 5.48% |
| Helsinki | EFHK | 365 | 1.11 | 5.48% |
| Madrid | LEMD | 735 | 1.11 | 5.31% |

## Core 9

当前 live allowlist 的 core 9 在物理层全部过 v0 门。

按小时汇总：

| decision_hour_local | city-day rows | cities | P50 residual C | P90 residual C | P95 residual C | residual >=1C | bucket delta >=1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 18 | 6,618 | 9 | 0.0 | 0.0 | 0.0 | 1.59% | 2.09% |
| 19 | 6,618 | 9 | 0.0 | 0.0 | 0.0 | 1.15% | 1.50% |
| 20 | 6,618 | 9 | 0.0 | 0.0 | 0.0 | 0.91% | 1.21% |
| 21 | 6,618 | 9 | 0.0 | 0.0 | 0.0 | 0.66% | 0.89% |

Core 9 中相对最弱的是 Boston / NYC / London：

| city | hour | city_days | P95 residual C | P99 residual C | residual >=1C | bucket delta >=1 | max residual C |
|---|---:|---:|---:|---:|---:|---:|---:|
| Boston | 18 | 735 | 0.56 | 2.78 | 3.95% | 5.71% | 4.44 |
| NYC | 18 | 735 | 0.56 | 1.67 | 2.59% | 5.03% | 3.33 |
| NYC | 19 | 735 | 0.00 | 1.48 | 2.18% | 3.81% | 3.33 |
| Boston | 19 | 735 | 0.00 | 2.03 | 1.90% | 3.13% | 4.44 |
| London | 18 | 735 | 0.00 | 2.22 | 2.59% | 2.59% | 3.33 |

读法：

- Core 9 到 18:00 本地时，P95 已经低于 1°C；多数日子的最终最高温已经不会再刷新。
- 但尾部不能忽略，尤其 Boston/NYC/London 的冬季/锋面型晚间回暖会出现 3-4°C 级别残差。
- 19:00-21:00 明显更干净；如果后续进入价格层，第一版不应从 18:00 贸然下手，应优先测试 20:00/21:00。

## Bad Cases

全量最大残差样例：

| city | date | hour | running C | final C | residual C | bucket delta |
|---|---|---:|---:|---:|---:|---:|
| Denver | 2025-05-27 | 18/19 | 9.44 | 15.00 | 5.56 | 6 |
| Paris | 2026-02-15 | 18 | 3.89 | 8.89 | 5.00 | 5 |
| Madrid | 2026-01-26 | 18 | 7.78 | 12.22 | 4.44 | 5 |
| Paris | 2026-04-15 | 18-21 | 17.78 | 22.22 | 4.44 | 5 |
| Moscow | 2026-01-09 | 18 | -7.78 | -3.89 | 3.89 | 4 |
| Ankara | 2026-01-10 | 18-21 | 1.11 | 5.00 | 3.89 | 4 |

Core 9 最大残差样例：

| city | date | hour | running C | final C | residual C | bucket delta |
|---|---|---:|---:|---:|---:|---:|
| Boston | 2026-04-03 | 18 | 14.44 | 18.89 | 4.44 | 4 |
| Boston | 2026-01-21 | 18/19 | -2.78 | 1.67 | 4.44 | 4 |
| Boston | 2026-03-11 | 18-21 | 10.56 | 14.44 | 3.89 | 4 |
| London | 2024-12-06 | 18/19 | 8.89 | 12.22 | 3.33 | 4 |
| NYC | 2026-01-09 | 18-20 | 8.89 | 12.22 | 3.33 | 4 |
| Tokyo | 2026-03-31 | 18 | 17.78 | 21.11 | 3.33 | 4 |

这些 bad cases 符合 reheat-risk 设计里说的“二次升温 / 晚间反弹”尾部风险。P2 结果支持进入 P3：专门研究 bad case 是否可由事前风向、温度斜率、云量/天气现象过滤。

## 结论分级

```text
significance=NA
baseline=NA
forward=NA
conclusion=physical_candidate
```

这不是 `confirmed`，也不是 live 候选。它只说明 reheat-risk 第一性物理假设在当前 WU 历史缓存上成立：

```text
在 2024-04-30 至 2026-05-12 的 49 城 WU 缓存中，当地 19:00 以后，
observed running max 到 final daily max 的 P95 residual 为 0°C，
跨 1°C bucket 的比例低于 1%；结论等级 physical_candidate。
```

## 风险与缺口

- 本轮用的是 WU cache 中的 `IEM_ASOS_METAR_FALLBACK` 字段；需要和 `pm_history` 结算站点/结算规则核对，确认站点和温度口径完全一致。
- `floor_c_bucket_delta` 是 proxy，不等于合约最终 bracket 规则；下一步必须接 settlement bracket 做交叉验证。
- 时区映射目前硬编码在实验脚本里，应迁移到 station profile 或共享 contract。
- WU cache 覆盖到 2026-05-12 / core 9 到 2026-05-06，不覆盖最新 6 月 live；需要补最近一月观测再复跑。
- 本轮没有做 train/holdout 规则选择，也没有做市场价格层；禁止从本文推出真钱 live。

## 下一步

1. P3 bad case filter：在 `residual_c >= 1°C` 或 `bucket_delta >= 1` 样本上，计算 decision-time 可见特征是否能提前识别。
2. 把 `final_daily_max_c` 和 `pm_history` settled bracket 对齐，验证 WU/IEM 观测口径和合约结算口径。
3. 补最近 6 月 WU/IEM 观测缓存，至少覆盖当前 live core 9。
4. 先以 20:00/21:00 local 为第一版交易窗口；18:00 仅作为观察窗口，尤其避开 Boston/NYC/London 的高反弹 regime。
5. P2/P3 都过后，再接 decision-time orderbook best ask 做 P4 可成交回测。
