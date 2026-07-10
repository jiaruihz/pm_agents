# Tmax Lineage Repair Replay v1

> generated_at_utc: `2026-07-10T14:38:26.517287+00:00`
> Scope: repaired-code replay only. Live runner remains paused; no order/config action.

## 结论

- live feature parity、tail-aware bracket geometry、sibling complement snapshot estimate、direct fresh executable ask 和 NaN handling 已实现并测试；`1-NO bid` 只作估价/telemetry，不冒充可直接成交的 YES ask。
- Full live features improve proper scores over the old live-missingness pattern in both dev-CV and verified forward; the higher selected-trade ROI of the missing version is selection noise, not evidence to keep missing fields.
- Of 357 repaired `below_market_ladder` city-hour rows, 357 are explained by the collector dropping lower near-binary siblings. This is a data-layer completeness bug, not proven new alpha capacity.
- Real d3+ tail quote and full-ladder A/B are contaminated by those incomplete snapshots and are negative on the 701-row matched denominator. D1 remains inconclusive until the complete-ladder collector accumulates fresh rows.
- Symmetric current-mid improves verified score/selected ROI but is slightly worse in dev-CV; keep it as a shadow candidate, not a promotion result.
- Existing survival v2 consumes already-mapped rows only. Its previous report cannot claim below-ladder capacity.
- Verdict: `live_remains_paused_pending_repaired_forward_shadow`.

## Evidence Funnel

- Historical scored rows: `8376` (2026-05-19..2026-07-08)
- Indexed snapshots: `1226`
- Full-ladder market rows materialized: `701`
- Existing live orders: `8`; exact repaired replay rows: `8`
- Geometry city-date-hour rows: `2286`

## Historical Policy Replay

| denominator | variant | rows | dates | cities | win_rate | avg_ask | pnl | roi | roi_ci_low | roi_ci_high | yes_rows | no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_scored | historical_full_features | 159 | 16 | 36 | 0.6918 | 0.6296 | 8.2244 | 0.0808 | -0.0161 | 0.1652 | 34 | 125 |
| all_scored | live_missingness_emulation | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0719 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |
| all_scored | market_current_mid_real_tail | 73 | 8 | 30 | 0.6027 | 0.6188 | -1.9493 | -0.0424 | -0.2825 | 0.1327 | 21 | 52 |
| all_scored | market_current_mid_symmetric | 157 | 15 | 36 | 0.7134 | 0.6310 | 11.2782 | 0.1120 | 0.0172 | 0.2094 | 30 | 127 |
| all_scored | market_real_tail_only | 72 | 8 | 29 | 0.5972 | 0.6199 | -2.4014 | -0.0529 | -0.2716 | 0.0949 | 20 | 52 |
| full_ladder_matched | historical_full_features | 67 | 8 | 29 | 0.6866 | 0.6503 | 1.7449 | 0.0394 | -0.0861 | 0.1579 | 11 | 56 |
| full_ladder_matched | live_missingness_emulation | 66 | 8 | 27 | 0.7273 | 0.6221 | 6.2337 | 0.1493 | -0.0015 | 0.2962 | 14 | 52 |
| full_ladder_matched | market_current_mid_real_tail | 73 | 8 | 30 | 0.6027 | 0.6188 | -1.9493 | -0.0424 | -0.2825 | 0.1327 | 21 | 52 |
| full_ladder_matched | market_current_mid_symmetric | 69 | 8 | 29 | 0.7101 | 0.6611 | 2.6852 | 0.0580 | -0.0852 | 0.1914 | 8 | 61 |
| full_ladder_matched | market_full_ladder | 72 | 8 | 30 | 0.5972 | 0.6201 | -2.4068 | -0.0530 | -0.2825 | 0.1121 | 20 | 52 |
| full_ladder_matched | market_real_tail_only | 72 | 8 | 29 | 0.5972 | 0.6199 | -2.4014 | -0.0529 | -0.2716 | 0.0949 | 20 | 52 |

## Feature Parity Proper Scores

| scope | variant | rows | dates | logloss | brier |
| --- | --- | --- | --- | --- | --- |
| dev_cv | historical_full_features | 5462 | 28 | 0.6119 | 0.3487 |
| dev_cv | live_missingness_emulation | 5462 | 28 | 0.6136 | 0.3495 |
| verified_forward | historical_full_features | 2077 | 16 | 0.5887 | 0.3283 |
| verified_forward | live_missingness_emulation | 2077 | 16 | 0.5926 | 0.3308 |

## Market Distribution A/B

| scope | variant | rows | dates | cities | logloss | brier | top1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | market_current_mid_symmetric | 6299 | 33 | 36 | 0.6347 | 0.3512 | 0.7296 |
| dev_cv | market_local_original | 6299 | 33 | 36 | 0.6335 | 0.3503 | 0.7317 |
| verified_forward | market_current_mid_real_tail | 701 | 8 | 36 | 1.2270 | 0.3805 | 0.7147 |
| verified_forward | market_current_mid_symmetric | 2048 | 15 | 36 | 0.6435 | 0.3307 | 0.7598 |
| verified_forward | market_full_ladder | 701 | 8 | 36 | 1.2994 | 0.3806 | 0.7190 |
| verified_forward | market_local_original | 2048 | 15 | 36 | 0.6489 | 0.3311 | 0.7598 |
| verified_forward | market_real_tail_only | 701 | 8 | 36 | 1.2287 | 0.3805 | 0.7175 |

### Full-ladder matched denominator

| scope | variant | rows | dates | cities | logloss | brier | top1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | market_current_mid_real_tail | 701 | 8 | 36 | 1.2270 | 0.3805 | 0.7147 |
| verified_forward | market_current_mid_symmetric | 701 | 8 | 36 | 0.6555 | 0.3364 | 0.7404 |
| verified_forward | market_full_ladder | 701 | 8 | 36 | 1.2994 | 0.3806 | 0.7190 |
| verified_forward | market_local_original | 701 | 8 | 36 | 0.6536 | 0.3366 | 0.7418 |
| verified_forward | market_real_tail_only | 701 | 8 | 36 | 1.2287 | 0.3805 | 0.7175 |

## First Live Orders Replayed

| city | target_date | original_expression | original_price | original_p_win | replay_status | repaired_same_expression_p_win | repaired_same_expression_edge_at_fill | same_expression_still_eligible | repaired_selected_expression |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Busan | 2026-07-09 | d1_no | 0.6700 | 0.7286 | ok | 0.6679 | -0.0132 | False |  |
| Beijing | 2026-07-09 | d1_no | 0.4660 | 0.5277 | ok | 0.4978 | 0.0193 | False | d1_no |
| CapeTown | 2026-07-09 | d1_yes | 0.4100 | 0.4495 | ok | 0.4963 | 0.0742 | True |  |
| Amsterdam | 2026-07-09 | d1_yes | 0.4800 | 0.5930 | ok | 0.6427 | 0.1503 | True |  |
| Atlanta | 2026-07-09 | d1_no | 0.4100 | 0.4925 | ok | 0.4675 | 0.0454 | True |  |
| Austin | 2026-07-09 | d1_yes | 0.5400 | 0.6787 | ok | 0.6746 | 0.1222 | True |  |
| Busan | 2026-07-10 | d1_yes | 0.5000 | 0.5520 | ok | 0.5795 | 0.0670 | True |  |
| CapeTown | 2026-07-10 | d1_no | 0.6500 | 0.7208 | ok | 0.6824 | 0.0210 | True |  |

## Geometry Capacity

| old_geometry_status | repaired_geometry_status | rows | dates | cities | parser_recovered |
| --- | --- | --- | --- | --- | --- |
| mapped_ok | mapped_ok | 1288 | 19 | 47 | 0 |
| unmapped | mapped_ok | 447 | 16 | 34 | 447 |
| below_market_ladder | below_market_ladder | 357 | 15 | 45 | 0 |
| top_two_ladder_truncated | top_two_ladder_truncated | 187 | 12 | 46 | 0 |
| unmapped | unmapped | 6 | 3 | 3 | 0 |
| unmapped | top_two_ladder_truncated | 1 | 1 | 1 | 1 |

### Snapshot completeness audit

| repaired_geometry_status | snapshot_completeness_class | rows | city_days | dates | cities |
| --- | --- | --- | --- | --- | --- |
| mapped_ok | geometry_intrinsic | 1734 | 410 | 18 | 47 |
| below_market_ladder | collector_missing_lower_siblings | 357 | 118 | 15 | 45 |
| top_two_ladder_truncated | collector_missing_upper_siblings | 157 | 92 | 12 | 45 |
| top_two_ladder_truncated | geometry_intrinsic | 31 | 14 | 6 | 13 |
| unmapped | geometry_intrinsic | 6 | 4 | 3 | 3 |
| mapped_ok | canonical_inventory_unavailable | 1 | 1 | 1 | 1 |

### Local-hour shape

| repaired_geometry_status | daypart | rows | city_days |
| --- | --- | --- | --- |
| below_market_ladder | 00-05 | 165 | 55 |
| below_market_ladder | 06-10 | 122 | 53 |
| below_market_ladder | 11-15 | 14 | 10 |
| below_market_ladder | 16-21 | 56 | 38 |
| mapped_ok | 00-05 | 498 | 181 |
| mapped_ok | 06-10 | 486 | 204 |
| mapped_ok | 11-15 | 481 | 172 |
| mapped_ok | 16-21 | 264 | 153 |
| mapped_ok | 22-23 | 6 | 6 |
| top_two_ladder_truncated | 11-15 | 28 | 13 |
| top_two_ladder_truncated | 16-21 | 160 | 99 |
| unmapped | 00-05 | 1 | 1 |
| unmapped | 16-21 | 5 | 3 |

`rows` here are city + target_date + local-hour states, not fills. Canonical settlement ladder inventory is used only after the fact to audit sibling collection completeness; it is never used as a PIT model feature. Genuine below-ladder rows need a new absolute-ladder target, not the old relative current/d1/d2 target.

## Fresh Collector Verification

修复后用独立临时输出运行一次 `snapshot-targeted --target-date 2026-07-10 --no-orderbook`：成功产生
31 个 city-date、341 records，每个 city-date 都是完整 11 档。Amsterdam 明确保留了价格为 `0.0005`
的 22/23/24/25 和 31/32+ near-binary siblings，证明数据层不再按价格删除梯子两端。

随后常驻 Mac collector 的首个修复后周期产生 51 个 city-date、561 records，同样全部为 11 档；因为当轮
Gamma/forecast 只覆盖 32 城，按既有 publish-quality 契约落入 partial archive，没有替换上一份完整 latest snapshot。

这个验证只证明 bracket inventory 完整；D1 real-tail/full-ladder 的 fresh orderbook 报价和结算表现仍需从修复后
forward snapshot 重新积累，不能用本次 `--no-orderbook` 快照代替。

## Three Gates

- significance: FAIL/NA for live promotion; repaired forward days have not accumulated.
- baseline: PARTIAL; historical same-denominator replay is reported above.
- forward: FAIL; code is only dry-run/replay and live remains paused.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_lineage_repair_replay_v1/`
- `docs/analysis/2026-07/2026-07-10-tmax-lineage-repair-replay-v1.json`
