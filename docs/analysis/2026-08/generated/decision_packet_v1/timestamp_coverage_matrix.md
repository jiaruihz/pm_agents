# 三套时间戳(observation / publication / receive)覆盖矩阵(D2 只读盘点)

数据 as-of:2026-08-21。全部结论来自只读扫描:观测 11 天 97,408 行、盘口 2026-08-20 批次 535,128 行、raw forecast 曲线 08-16~08-19 共 7,756 行、pre_live_scores 4,735 行、live_orders 377 行、canonical DB 计数。

## 1. 覆盖矩阵

| 数据源 | observation(事件发生/报文观测) | publication(源发布/我方拉取) | receive(进入我方系统/可用) | 三套可分离? |
|---|---|---|---|---|
| 天气观测 obs 文件(2026-08-16 起) | `last_obs_utc` ✓ | `fetched_at_utc` ✓ | `ingested_at_utc` / `available_at_utc` ✓ | **可以**。08-16 起行级齐全率 98.5–99.3%(缺失=awaiting_first_obs/fetch_failed) |
| 天气观测 obs 文件(2026-08-16 前) | `last_obs_utc` ✓ | `fetched_at_utc` ✓ | **无 `ingested_at_utc`**;仅 `observation_cache_generated_at_utc` 代理 | **不可以**,receive 只能代理 |
| 观测在 score 行内的投影 | `source_report_ts_utc` ✓ | `fetched_at_utc` ✓ + `obs_age_min` | 无独立字段 | 部分(obs 时刻可,receive 只能靠 obs 文件回溯) |
| 预报 valid time | `hourly_curve[].time_local` ✓(24h valid 序列;DB `valid_from/to` 为空,需从内容推导) | — | — | valid 可,publication 无 |
| 预报 issue/run time | **不可得**:`issued_at_utc` 0/7,756、`forecast_run_ts_utc` 0/7,756、lineage=`source_response_does_not_expose_run_timestamp`(open-meteo 不暴露);仅 snapshot 的 `model_init_utc_estimated`(“00Z”启发式)+`model_run_age_hours_estimated` | — | — | **fundamental 缺口** |
| 预报 receive time | raw `forecast_hourly_curves_*.jsonl`:`forecast_first_seen_utc` ✓ + `available_at_utc`/`detected_at_utc`/`snapshot_ts_utc` ✓ | `source_fetch_start/end_utc` ✓ | ✓ | **raw 文件可以**;canonical `tmax_v2_forecast_captures` 2026-08-08 后停止入表,`fact_forecast_hourly_curves.available_at_utc` 100% NULL |
| 盘口 batches | `exchange_book_ts_utc` + `exchange_book_ts_raw`(交易所侧)✓ 100% | 文件名 BJ 批次时刻 + `fetched_at_utc`/`request_started_at_utc`/`response_received_at_utc`/`parsed_at_utc` ✓ | `available_at_utc`/`detected_at_utc`/`first_seen_at_utc` ✓ | **可以,覆盖最好** |
| 评分 checkpoint(pre_live_scores) | — | — | `created_at_utc`(写入) vs `as_of_ts_utc`/`decision_snapshot_ts_utc`(数据 epoch),lag 中位 20s / p95 267s | 数据 epoch 与写入时刻可分离;无独立 obs/book receive 字段(需回溯源文件) |
| 订单(live_orders) | `decision_snapshot_ts_utc`(决策数据时点) | — | `created_at_utc`(下单写入);`data_epoch_ts_utc` 仅 178/377=47.2% 非空(早期 config 为空串) | 部分;epoch 字段覆盖不全 |

## 2. 关键量化

- 观测 obs→fetch(源发布到我方拉取)lag 中位 22.1 分钟(p95 57.8);fetch→ingest 中位 8.4 秒。即 publication 延迟主导,receive 链路本身很快。
- 盘口 snapshot−exchange_book lag 中位 5.2s,p95 188.7s,max 4,788.9s(约 80 分钟,存在陈旧盘口混入批次);available−snapshot 中位 6.4s。
- `tmax_v2_forecast_captures` 最后 snapshot_ts=2026-08-08T16:45:58Z;此后 canonical 无 forecast capture,只能读 raw 文件(raw collector 最后写入 2026-08-19T23:04 BJ,2026-08-20 起缺失)。
- paper_snapshots(决策 as-of 快照)保留到 2026-08-19 23:08 BJ;Amsterdam 08-20 触发行引用的 `snapshot_20260820_2043.json` 不存在。

## 3. 缺口清单(按严重度)

1. **预报 issue/run time 无任何来源**(0/7,756):open-meteo 响应不含 run ts。要补只能换/加暴露 run time 的源,或把 `model_init` 估计从启发式升级为显式记录(含依据)。这是 decision packet 中唯一 truly unavailable 的时间轴。
2. **观测 receive 时间 2026-08-16 前缺失**:`ingested_at_utc`/`available_at_utc` 字段自 08-16 才写入。历史只能用 `observation_cache_generated_at_utc` 代理(同义近邻:cache 生成≈入库)。
3. **canonical forecast 表链路中断**:`tmax_v2_forecast_captures` 08-08 停表、`fact_forecast_hourly_curves.available_at_utc` 全 NULL、raw 曲线 08-20 起缺失、paper_snapshots 08-20 起缺失 → 08-20 的 Amsterdam packet 的 forecast 血缘只能到 trigger 行的 forecast_* 特征,拿不到曲线 hash 与 receive。
4. **score 行不自含 receive**:pre_live_scores 每行有 obs/book 的“时刻”投影(source_report_ts_utc、current_yes_book_fetched_at_utc),但没有指向原始 obs 行/盘口行的 ID(无 observation_history_id / book_capture_id 外键),as-of 重建必须回扫全量文件。
5. **live_orders.data_epoch_ts_utc 覆盖 47.2%**:早期订单行无 data epoch 引用。
6. 盘口 batch 内存在 max ~80 分钟的 exchange_book 陈旧行(p95 之内健康,但 packet 应校验 lag 上限而非默认新鲜)。

## 4. 对 decision packet 的直接推论

- 观测三套:08-16 起完全可分离;此前 receive 用 proxy 并显式标注。
- 盘口三套:完全可分离,packet 直接引用 batch 文件 + `exchange_book_ts_utc` + `available_at_utc`。
- 预报:valid(receive 侧)可用 `forecast_first_seen_utc`(raw)或 captures 表(≤08-08);**issue 侧标记 `unavailable_in_history: true`**,需生产改造(见 write_contract_design.md)。
