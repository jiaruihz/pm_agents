# Weather Observation Parsing Parity Report v1

Date: 2026-07-05

Scope: offline parity harness only. No live runner behavior was changed, no network fetch was performed, and no CLOB/private-key/order path was touched.

Harness: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-05-weather-observation-parity-harness-v1.py`

## Input Windows

- Mac observation cache: `/Users/deepsleep/projects/weather_data_feed_service_runtime/output/observations/latest.json` (generated_at=2026-07-05T07:41:14.931473+00:00, records=34, statuses={'ok': 31, 'fetch_failed': 3}, sources={'aviationweather_metar': 34})
- Mac paper snapshot: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots/snapshot_20260705_1243.json, ts_utc=2026-07-05T04:43:57Z`
- Current Mac source_events: missing at `/Users/deepsleep/projects/weather_data_feed_service_runtime/output/source_events/latest.json`
- Legacy source_events sanity sample: `/Users/deepsleep/projects/pm_agents/runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/latest.json` (generated_at=2026-07-01T15:55:51.897103+00:00, records=80)

P10 guardrail: the main positive parity evidence uses 2026-07-05 Mac observation cache and 2026-07-03..2026-07-05 Mac paper snapshots. The 2026-07-01 N100 recovery source-events file is explicitly treated as a legacy raw-METAR/source-event parser sanity sample, not as clean Mac source-events coverage.

## Duplicate Logic Inventory

| File | Local helpers | Input assumptions | Harness conclusion |
|---|---|---|---|
| `weather_metar_cross_prev_no_shadow.py` | `parse_metar_records`, `observation_summary_from_source_event`, latest TGFTP/Synoptic summaries | AWC JSON list, data-feed source-event latest rows, or single latest station text. Running max may be carried by runner state. | Do not directly merge with full-day cache parser; source-events adapter is latest-event/latency specialized. |
| `weather_source_orderbook_timing_monitor.py` | `fetch_*_latest`, `latest_source_event_rows` | Timing rows are city/source latest events joined to orderbook; AWC/IEM parsers already call `weather_data_feed.observation_sources`. | Parser surface mostly shared already; fetch wrappers need source-payload parity before consolidation. |
| `weather_theta_current_yes_tiny_live.py` | `aviationweather_obs`, `iem_obs`, `snapshot_metar_obs`, `observation_cache_obs` | Consumes full obs series, paper snapshot METAR columns, and observation cache summaries; adds min_obs, cadence relaxation, pre-update blackout. | Freshness semantics are live-strategy specific. Shared parser import is safe only below the freshness/state layer. |
| `weather_ldm_metar_notify_monitor.py` | `parse_metar_lines`, local temp/report-time parsers | Raw LDM/PQCAT METAR lines with detect timestamp; station target mapping is optional. | Temp/DDHHMM parser is equivalent on sampled rows and is the cleanest shared-parser candidate. |

## Parity Summary

| Check | Rows | Matched | Diffs | Skipped | Verdict |
|---|---:|---:|---:|---:|---|
| LDM raw METAR parser vs shared METAR parser | 78 | 78 | 0 | 0 | `parser-equivalent` |
| AviationWeather JSON parsers: metar_cross/theta vs shared data_feed | 78 | 78 | 0 | 0 | `historical-equivalent-with-parser-edge-risk` |
| metar_cross source-events adapter (current Mac) | 0 | 0 | 0 | 0 | `not-tested` |
| metar_cross source-events adapter (legacy N100 recovery) | 78 | 78 | 0 | 0 | `latest-event-equivalent-stateful-running-max-specialization` |
| theta observation_cache_obs vs weather_data_feed observation_cache | 34 | 34 | 0 | 0 | `value-equivalent-freshness-semantic-fork` |
| theta snapshot_metar_obs vs paper_snapshot METAR fields | 33 | 31 | 0 | 2 | `value-equivalent-snapshot-specific-freshness` |

## Check Details

### 1. LDM raw METAR parser vs shared METAR parser
- Sample: `/Users/deepsleep/projects/pm_agents/runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/latest.json`
- Window: `2026-07-01T15:55:14.203765+00:00..2026-07-01T15:55:41.536831+00:00`
- Verdict: `parser-equivalent`
- Detail: No field diffs on temp_c/source_report_ts_utc.

### 2. AviationWeather JSON parsers: metar_cross/theta vs shared data_feed
- Sample: `/Users/deepsleep/projects/pm_agents/runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/latest.json`
- Window: `2026-07-01T14:51:00+00:00..2026-07-01T15:52:00+00:00`
- Verdict: `historical-equivalent-with-parser-edge-risk`
- Detail: Historical reconstructed AWC rows matched. Stress fixture exposed timestamp semantic drift when reportTime is detect/fetch time: shared rawOb DDHHMMZ=2026-07-01T15:25:00Z, metar_cross reportTime=2026-07-01T15:55:15.494719Z, theta reportTime=2026-07-01T15:55:15.494719Z.

### 3. metar_cross source-events adapter (current Mac)
- Sample: `/Users/deepsleep/projects/weather_data_feed_service_runtime/output/source_events/latest.json`
- Window: `missing`
- Verdict: `not-tested`
- Detail: Current Mac source_events/latest.json was not present.

### 4. metar_cross source-events adapter (legacy N100 recovery)
- Sample: `/Users/deepsleep/projects/pm_agents/runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/latest.json`
- Window: `2026-07-01T15:55:14.203765+00:00..2026-07-01T15:55:41.536831+00:00`
- Verdict: `latest-event-equivalent-stateful-running-max-specialization`
- Detail: Core latest-observation fields matched; running_max_c equaled latest temp in 78/78 checked rows, which is a latency/state specialization rather than full intraday max replay.

### 5. theta observation_cache_obs vs weather_data_feed observation_cache
- Sample: `/Users/deepsleep/projects/weather_data_feed_service_runtime/output/observations/latest.json`
- Window: `generated_at=2026-07-05T07:41:14.931473+00:00`
- Verdict: `value-equivalent-freshness-semantic-fork`
- Detail: weather_data_feed currently normalizes/indexes cache records; theta adds cadence-aware freshness gates. theta statuses={'ok': 31, 'fetch_failed': 3}, relaxed_age_limit_rows=31. No value diffs for accepted ok rows.

### 6. theta snapshot_metar_obs vs paper_snapshot METAR fields
- Sample: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots/snapshot_20260705_1243.json`
- Window: `snapshot_ts=2026-07-05T04:43:57Z`
- Verdict: `value-equivalent-snapshot-specific-freshness`
- Detail: theta statuses={'ok': 31, 'insufficient_obs_asof': 2}. Accepted rows preserve snapshot latest/running-max values; freshness uses theta cadence/default rules. No accepted value diffs.

## 收编清单

可收编, 但只限 parser 层:

- `weather_ldm_metar_notify_monitor.py` 的 METAR temp/DDHHMM parser 可以迁到 `weather_data_feed.observation_sources.metar` 或直接 import shared helper；采样行 temp/report timestamp 无差异。
- `weather_source_orderbook_timing_monitor.py` 的 AWC/IEM parser 已经使用 shared parser；后续若收编, 应该集中 fetch/result normalization, 而不是改 timing monitor 的 orderbook join 语义。

暂不收编, 需要保留语义差异:

- `weather_metar_cross_prev_no_shadow.py` 的 source-events adapter 是 latest-event fast path, `running_max_c` 依赖 runner state/当前 latest row，不等同 observation cache 的 full intraday running max。
- `weather_theta_current_yes_tiny_live.py` 的 `observation_cache_obs` 和 `snapshot_metar_obs` 保留 cadence relaxation、pre-update blackout、min_obs 语义；这属于 current-YES live 风控, 不是通用 parser。
- `theta.aviationweather_obs` 和 `metar_cross.parse_metar_records` 在历史 reconstructed rows 上与 shared parser 等价，但 stress fixture 显示当 `reportTime` 不是 raw METAR 的 DDHHMMZ 时会和 `weather_data_feed.parse_aviationweather_records` 分叉。收编前应先决定 reportTime/rawOb 谁是权威 timestamp。

## 未覆盖缺口

- 当前 Mac runtime 没有 `output/source_events/latest.json`; 本次不能声明 Mac source-events fast path 已逐字段 clean replay。
- SynopticData/IEM raw payload 在本地 clean window 没有完整 raw response 落盘；本次只核对已有 shared parser调用和 cache/summary语义, 不能证明 fetch wrapper 完全可合并。
- 没有触碰 `metar_cross` FOK fast path, 也没有测 latency; P12 live CLOB 收编仍应冻结。
