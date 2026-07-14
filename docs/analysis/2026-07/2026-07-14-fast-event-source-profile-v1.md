# Fast-event source profile v1

> 2026-07-14; current-reference; zero-notional only; no live policy or order change.

## 结论

- profile 共 22 条 / 21 城；`live_eligible=0`。
- 本次研究 47 城中，21 城有 fast-event profile，26 城没有。无 profile 只是不运行 source-event head，不是全局禁用城市。
- current-NO 候选全体 42 行 ROI 11.6%；profile-covered 36 行 ROI 4.4%，CI [-19.5%,33.7%]，仍未过 live 门。

## Profile basis

| source basis | profiles |
|---|---:|
| cross_station_proxy | 1 |
| cross_station_reference | 1 |
| official_settlement_feed | 1 |
| same_airport_alternate_sensor | 3 |
| same_city_secondary_sensor | 1 |
| same_station_alternate_feed | 4 |
| same_station_mirror | 11 |

## Replay denominator

| slice | rows | dates | cities | win | ROI | CI |
|---|---:|---:|---:|---:|---:|---:|
| all | 42 | 14 | 17 | 45.2% | 11.6% | [-11.5%,37.4%] |
| profile_covered | 36 | 14 | 12 | 41.7% | 4.4% | [-19.5%,33.7%] |
| no_profile | 6 | 4 | 5 | 66.7% | 50.0% | [-6.9%,208.2%] |

## 历史污染半径

| output | events wrong-unit | quote rows wrong-unit | live orders affected |
|---|---:|---:|---:|
| `/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow` | 8 | 581 | 0 |
| `/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_stale_book` | 36 | 1458 | 0 |

旧 v1 telemetry 必须按 schema 分层；美国错误单位行不可用于 repricing 结论。本修复没有真实订单影响。

## 无快源城市怎么处理

这些城市只从 `source_event` head 排除；forecast、regime、lottery、tmax、普通 METAR/WU 策略仍按各自证据和 policy 独立决定。不能用“没有快源”推导成“城市不可交易”。

## 当前状态

`calibration_only_zero_notional_no_live`。下一阶段按 city × source 收集 side-neutral YES/NO repricing，不能把 pooled current-NO 点估直接升级为 5-share live。

## 现存 live runner 边界

本次没有修改或停止既有 `fast_source_prev_no_trial_v1` live runner。运行态核对显示它仍以 Helsinki / Busan / Singapore / Tokyo 为 live cities；2026-07-14 Busan 有一次 FOK 失败，随后一笔订单 `0x65ef26…f444d` 返回 `matched`（making 8.899998 USDC，taking 10.348835 shares）。

这四城在新 profile 中均尚未 `live_eligible`：Helsinki、Busan、Tokyo 需要做 alternate-feed / sensor 与 settlement basis 对齐；Singapore 是 cross-station reference，当前明确 blocked。因此 profile 结论与这个旧 live runner 的现状冲突，推荐将该 runner 暂停或改为 shadow-only；这是生产行为变更，本次未越权执行。
