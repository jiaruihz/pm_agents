# Heat-Death Early Event Replay v1

Status: current-reference
Verdict: `inconclusive_zero_notional_only`

## 结论

这是一版真正按 `source event detect -> 首个后续 snapshot/quote` 对齐的早期错价重放，和 hourly-last 晚期 carry 分开。
历史事件档案只覆盖 2026-07-07..2026-07-13 的已结算日，因此无论点估如何都达不到 10 active dates / 30 settled rows 的确认门槛。

## Funnel

- unique source reports: 10176
- prebase without forecast clock: 432
- market-aligned prebase: 410
- first base city-days: 130
- first strong-partial city-days: 67
- executable settled expression rows: 5
- direct quote coverage: current YES 7/67; d1 NO 7/67

## Early expression result

| Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| current_yes | 3 | 3 | 2 | +100.0% | 0.893 | +11.5% | [+2.9%, +30.2%] |
| d1_no | 2 | 2 | 2 | +100.0% | 0.910 | +9.4% | [+2.9%, +16.8%] |

## Busan current-day sanity check

Busan 2026-07-14 在 2026-07-14T04:08:34Z 首次 strong-partial：30 YES ask=0.84，31 NO ask=0.89。该日未纳入上面的已结算 ROI。

## Feature coverage boundary

可 PIT 重建：METAR/SPECI 雨、云层、风向/风速、温度路径、forecast peak clock、首个后续直接盘口。历史仍缺 remaining-3h forecast weather 与带坐标 solar geometry，因此这里叫 `strong_partial`，不能假装是完整 weather_state_v2 回测。

## Three gates

```text
significance=FAIL_LOW_SAMPLE
baseline=NA_short_event_archive
forward=FAIL_THIN
conclusion=inconclusive_zero_notional_only
```

完整逐事件行见 `generated/heat_death_early_event_replay_v1/`。
