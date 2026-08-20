# Decision Packet 缺口清单(7 个亏损 signal)

配套文件:`loss_decision_packets.json`。所有字段均由只读源拼接:pre_live_scores.jsonl、paper_snapshots、forecast_hourly_curves raw、observations 文件、canonical weather.db(orders/fills/fact_trades)。

## 逐包状态

| signal | last_neg_ckpt | weather_asof | forecast hash / receive | forecast issue | outcome |
|---|---|---|---|---|---|
| Singapore 07-27 | ✓(1 个前序) | ✓ ingest=proxy | ✓ / ✓ 05:44Z(触发前 60min) | unavailable | settled L |
| Chengdu 07-27 | ✓(4 个前序) | ✓ proxy | ✓ / ✓ 07:58Z(触发前 95min) | unavailable | settled L |
| Lucknow 07-31 | ✓(1 个前序) | ✓ proxy | ✓ / ✓ 07:47Z | unavailable | settled L |
| Manila 08-07 | ✗ 无前序(见 G1) | ✓ proxy | ✓ / ✓ 05:39Z(触发前 80s) | unavailable | settled L |
| Busan 08-17 | ✓(4 个前序) | ✓ 真实 ingested_at_utc | ✓ / ✓ 03:43Z(raw 文件回填) | unavailable | settled L |
| Warsaw 08-18 | ✓ 但退化(见 G2),另有 informative 前序 | ✓ 真实 | ✓ / ✓ 03:21Z(raw 回填) | unavailable | missing_bracket,外部确认 L |
| Amsterdam 08-20 | ✓(1 个前序) | ✓ 真实 | ✗(见 G3) | unavailable | missing_bracket,外部确认 L |

## 缺口与根因

- **G1 Manila 08-07 无 last_negative checkpoint**:该 city-day 在 pre_live_scores 只有 1 行评分(checkpoint_hour=13 local,即 13–17 研究窗口的起点)且直接触发。窗口开始前的 p/ask 轨迹从未被评分记录 → `unavailable_in_history`。回填需要 shadow 全窗口评分(生产改造见 write_contract_design.md),否则 first-positive 的“首”字在窗口边界不可验证。
- **G2 Warsaw 08-18 触发前最近 checkpoint 退化**:12:35:52Z 行 p=None、ask=0.001、blocker=`valid_two_sided_current_yes_book`(bracket 19 死盘口)。packet 保留该行并附 `last_informative_negative_checkpoint`(11:43:09Z,p=0.7805,ask=0.74)。教训:packet 取“最近负检查点”必须同时给出可用性判据,否则 adverse-selection 分析会被退化行误导。
- **G3 Amsterdam 08-20 forecast 血缘断裂**:三层同时缺——paper_snapshots 无 `snapshot_20260820_2043.json`(保留截止 08-19 23:08 BJ)、forecast_hourly_curves raw 无 2026-08-20 目录(collector 最后写入 08-19 23:04 BJ)、tmax_v2_forecast_captures 08-08 后停表。→ 曲线 hash、receive、init 估计全部 `unavailable_in_history`。
- **G4 预报 issue/run time 全体不可得**(7/7):raw 曲线 `issued_at_utc`/`forecast_run_ts_utc` 0/7,756 非空,lineage 注明 `source_response_does_not_expose_run_timestamp`。packet 仅能给 `model_init_utc_estimated`(“00Z”启发式)。需要源侧或双源改造,无法从历史回填。
- **G5 2026-08-16 前观测 receive 用代理**(Singapore/Chengdu/Lucknow/Manila 4/7):`ingested_at_utc` 自 08-16 才有,此前用 `observation_cache_generated_at_utc`(已在 packet 标 `ingest_ts_basis=..._proxy`)。
- **G6 Warsaw/Amsterdam outcome**:canonical `settlement_status=missing_bracket`、pnl 无值;packet 记录外部(coordinator 链上核对)确认输,但不自行填 pnl。

## 可回填性总结

| 字段 | 历史可回填? | 回填途径 |
|---|---|---|
| last_negative checkpoint(窗口内) | 是(除 G1) | pre_live_scores 按 city+date+ts<trigger 取最大 |
| weather as-of(obs/fetch/ingest) | 是(08-16 前缺 ingest,proxy 可用) | observations/<UTC日>/ 文件按 ingested<=trigger 过滤 |
| forecast 曲线 hash + receive | 08-19 前是;08-20 起否 | paper_snapshots record → hash → raw forecast_hourly_curves `forecast_first_seen_utc` |
| forecast issue/run time | **否** | 无任何历史来源;需生产改造 |
| orders/fills/settlement | 是 | canonical orders+fills+fact_trades |
