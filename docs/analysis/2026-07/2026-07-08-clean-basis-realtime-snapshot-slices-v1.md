# Clean Basis Realtime Snapshot Slices v1

Status: `snapshot`
Generated: `2026-07-08T12:43:22.395042+00:00`

## Verdict

四个 clean-basis 城市的高频源/METAR 时间轴可以回放；盘口价格当前多数没有抓到，状态主要是 `orderbook_budget_exhausted`。
所以这里能验证天气快源领先关系，但不能验证 stale-book 价格反应。

## Coverage

- `Busan`: rows `26`, priced_rows `4`, book_status `{'orderbook_scope_skipped': 51, 'orderbook_budget_exhausted': 6, 'ok': 5, 'missing': 16}`
- `Helsinki`: rows `33`, priced_rows `0`, book_status `{'missing': 17, 'orderbook_budget_exhausted': 82}`
- `Singapore`: rows `32`, priced_rows `0`, book_status `{'missing': 31, 'orderbook_budget_exhausted': 65}`
- `Tokyo`: rows `33`, priced_rows `0`, book_status `{'orderbook_budget_exhausted': 88, 'missing': 11}`

## Representative Slices

| city | type | src obs/detect | srcT | prev METAR | next METAR | snapshot | prev book | src book | next book |
|---|---|---|---|---|---|---|---|---|---|
| Busan | stable_no_cross | 07:15/07:17 | 28 | 07:00->28 | 08:00->27 (49.521m) | 07:09 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 Y 0.02/0.047 |
| Busan | stable_no_cross | 07:30/07:32 | 28 | 07:00->28 | 08:00->27 (34.934m) | 07:24 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 Y 0.02/0.038 |
| Busan | stable_no_cross | 07:49/07:50 | 27 | 07:00->28 | 08:00->27 (16.95m) | 07:39 | 29 orderbook_scope_skipped | 27 Y 0.012/0.03 | 27 Y 0.012/0.03 |
| Busan | stable_no_cross | 11:40/11:41 | 26 | 11:00->26 | 11:49->26 (21.213m) | 11:28 | 29 Y 0.35/0.36 |  missing |  missing |
| Busan | cross_hit | 04:06/04:07 | 28 | 04:00->27 | 05:00->28 (59.269m) | 04:04 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | cross_hit | 04:19/04:21 | 28 | 04:00->27 | 05:00->28 (45.34m) | 04:20 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | cross_false_positive | 06:01/06:03 | 29 | 05:00->28 | 06:00->28 (2.02m) | 05:52 | 29 orderbook_scope_skipped | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | stable_no_cross | 03:43/03:45 | 28 | 03:00->29 | 04:00->27 (19.721m) | 03:32 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 orderbook_scope_skipped |
| Busan | stable_no_cross | 03:54/03:56 | 27 | 03:00->29 | 04:00->27 (9.039m) | 03:48 | 29 orderbook_scope_skipped | 27 orderbook_scope_skipped | 27 orderbook_scope_skipped |
| Helsinki | overshoot_hit | 03:40/03:46 | 12 | 03:20->11 | 03:50->12 (4.977m) | 03:32 |  missing | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted |
| Helsinki | missed_cross | 05:30/05:33 | 12 | 05:20->12 | 05:50->13 (19.592m) | 05:21 |  missing |  missing | 13 orderbook_budget_exhausted |
| Helsinki | stable_no_cross | 03:50/03:57 | 12 | 03:50->12 | 04:20->12 (25.621m) | 03:48 |  missing |  missing |  missing |
| Helsinki | stable_no_cross | 04:00/04:07 | 12 | 03:50->12 | 04:20->12 (15.208m) | 04:04 | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted |
| Singapore | overshoot_hit | 04:44/04:49 | 30 | 04:30->29 | 05:00->30 (14.36m) | 04:36 |  missing | 30 orderbook_budget_exhausted | 30 orderbook_budget_exhausted |
| Singapore | cross_hit | 04:04/04:07 | 29 | 04:00->28 | 04:30->29 (27.56m) | 04:04 |  missing |  missing |  missing |
| Singapore | cross_hit | 04:19/04:21 | 29 | 04:00->28 | 04:30->29 (13.575m) | 04:20 |  missing |  missing |  missing |
| Singapore | cross_false_positive | 03:54/03:56 | 29 | 03:30->28 | 04:00->28 (8.893m) | 03:48 |  missing |  missing |  missing |
| Singapore | missed_cross | 04:29/04:35 | 29 | 04:30->29 | 05:00->30 (29.009m) | 04:20 |  missing |  missing | 30 orderbook_budget_exhausted |
| Singapore | stable_no_cross | 03:44/03:46 | 28 | 03:30->28 | 04:00->28 (19.601m) | 03:32 |  missing |  missing |  missing |
| Singapore | stable_no_cross | 05:29/05:33 | 31 | 05:30->31 | 06:00->31 (29.241m) | 05:21 | 31 orderbook_budget_exhausted | 31 orderbook_budget_exhausted | 31 orderbook_budget_exhausted |
| Tokyo | cross_hit | 10:50/11:06 | 26 | 10:30->25 | 11:00->26 (1.588m) | 10:58 | 28 orderbook_budget_exhausted |  missing |  missing |
| Tokyo | cross_false_positive | 10:20/10:30 | 26 | 10:00->25 | 10:30->25 (20.513m) | 10:28 | 28 orderbook_budget_exhausted | 26 orderbook_budget_exhausted |  missing |
| Tokyo | stable_no_cross | 03:20/03:46 | 28 | 03:30->28 | 04:00->28 (19.668m) | 03:32 | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted |
| Tokyo | stable_no_cross | 03:30/03:46 | 28 | 03:30->28 | 04:00->28 (19.668m) | 03:32 | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted |

## Per-City Slice Tables


### Busan

| city | type | src obs/detect | srcT | prev METAR | next METAR | snapshot | prev book | src book | next book |
|---|---|---|---|---|---|---|---|---|---|
| Busan | stable_no_cross | 07:15/07:17 | 28 | 07:00->28 | 08:00->27 (49.521m) | 07:09 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 Y 0.02/0.047 |
| Busan | stable_no_cross | 07:30/07:32 | 28 | 07:00->28 | 08:00->27 (34.934m) | 07:24 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 Y 0.02/0.038 |
| Busan | stable_no_cross | 07:49/07:50 | 27 | 07:00->28 | 08:00->27 (16.95m) | 07:39 | 29 orderbook_scope_skipped | 27 Y 0.012/0.03 | 27 Y 0.012/0.03 |
| Busan | stable_no_cross | 11:40/11:41 | 26 | 11:00->26 | 11:49->26 (21.213m) | 11:28 | 29 Y 0.35/0.36 |  missing |  missing |
| Busan | cross_hit | 04:06/04:07 | 28 | 04:00->27 | 05:00->28 (59.269m) | 04:04 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | cross_hit | 04:19/04:21 | 28 | 04:00->27 | 05:00->28 (45.34m) | 04:20 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | cross_false_positive | 06:01/06:03 | 29 | 05:00->28 | 06:00->28 (2.02m) | 05:52 | 29 orderbook_scope_skipped | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped |
| Busan | stable_no_cross | 03:43/03:45 | 28 | 03:00->29 | 04:00->27 (19.721m) | 03:32 | 29 orderbook_scope_skipped | 28 orderbook_scope_skipped | 27 orderbook_scope_skipped |
| Busan | stable_no_cross | 03:54/03:56 | 27 | 03:00->29 | 04:00->27 (9.039m) | 03:48 | 29 orderbook_scope_skipped | 27 orderbook_scope_skipped | 27 orderbook_scope_skipped |

### Helsinki

| city | type | src obs/detect | srcT | prev METAR | next METAR | snapshot | prev book | src book | next book |
|---|---|---|---|---|---|---|---|---|---|
| Helsinki | overshoot_hit | 03:40/03:46 | 12 | 03:20->11 | 03:50->12 (4.977m) | 03:32 |  missing | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted |
| Helsinki | missed_cross | 05:30/05:33 | 12 | 05:20->12 | 05:50->13 (19.592m) | 05:21 |  missing |  missing | 13 orderbook_budget_exhausted |
| Helsinki | stable_no_cross | 03:50/03:57 | 12 | 03:50->12 | 04:20->12 (25.621m) | 03:48 |  missing |  missing |  missing |
| Helsinki | stable_no_cross | 04:00/04:07 | 12 | 03:50->12 | 04:20->12 (15.208m) | 04:04 | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted | 12 orderbook_budget_exhausted |

### Singapore

| city | type | src obs/detect | srcT | prev METAR | next METAR | snapshot | prev book | src book | next book |
|---|---|---|---|---|---|---|---|---|---|
| Singapore | overshoot_hit | 04:44/04:49 | 30 | 04:30->29 | 05:00->30 (14.36m) | 04:36 |  missing | 30 orderbook_budget_exhausted | 30 orderbook_budget_exhausted |
| Singapore | cross_hit | 04:04/04:07 | 29 | 04:00->28 | 04:30->29 (27.56m) | 04:04 |  missing |  missing |  missing |
| Singapore | cross_hit | 04:19/04:21 | 29 | 04:00->28 | 04:30->29 (13.575m) | 04:20 |  missing |  missing |  missing |
| Singapore | cross_false_positive | 03:54/03:56 | 29 | 03:30->28 | 04:00->28 (8.893m) | 03:48 |  missing |  missing |  missing |
| Singapore | missed_cross | 04:29/04:35 | 29 | 04:30->29 | 05:00->30 (29.009m) | 04:20 |  missing |  missing | 30 orderbook_budget_exhausted |
| Singapore | stable_no_cross | 03:44/03:46 | 28 | 03:30->28 | 04:00->28 (19.601m) | 03:32 |  missing |  missing |  missing |
| Singapore | stable_no_cross | 05:29/05:33 | 31 | 05:30->31 | 06:00->31 (29.241m) | 05:21 | 31 orderbook_budget_exhausted | 31 orderbook_budget_exhausted | 31 orderbook_budget_exhausted |

### Tokyo

| city | type | src obs/detect | srcT | prev METAR | next METAR | snapshot | prev book | src book | next book |
|---|---|---|---|---|---|---|---|---|---|
| Tokyo | cross_hit | 10:50/11:06 | 26 | 10:30->25 | 11:00->26 (1.588m) | 10:58 | 28 orderbook_budget_exhausted |  missing |  missing |
| Tokyo | cross_false_positive | 10:20/10:30 | 26 | 10:00->25 | 10:30->25 (20.513m) | 10:28 | 28 orderbook_budget_exhausted | 26 orderbook_budget_exhausted |  missing |
| Tokyo | stable_no_cross | 03:20/03:46 | 28 | 03:30->28 | 04:00->28 (19.668m) | 03:32 | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted |
| Tokyo | stable_no_cross | 03:30/03:46 | 28 | 03:30->28 | 04:00->28 (19.668m) | 03:32 | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted | 28 orderbook_budget_exhausted |

## Output Files

- `all_slice_rows_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/clean_basis_realtime_snapshot_slices_v1/all_slice_rows.csv`
- `representative_slice_rows_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/clean_basis_realtime_snapshot_slices_v1/representative_slice_rows.csv`
- `report_md`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-08-clean-basis-realtime-snapshot-slices-v1.md`
