# Heat-Death Early Event Replay v1

Status: current-reference
Verdict: `inconclusive_zero_notional_only`

## 结论

**上一版“最终只有 5 笔”的说法作废。5 是盘口档案缺口再叠加任意价格带后的可计算行数，不是策略信号数。**
这次审计把 signal funnel 与 quote/settlement evidence funnel 分开，价格只作为连续 EV 输入，不再作为 eligibility hard gate。
历史事件档案只覆盖 2026-07-07..2026-07-13 的已结算日，因此仍不足以确认策略；forward runner 继续是 zero-notional。

## Signal funnel（这里才是策略漏斗）

- unique source reports: 10176
- local 13:00-17:00 event rows: 1761
- + decline >= 0.5: 632
- + running high age >= 60m: 485
- + flat/cooling path: 432
- market-aligned event rows: 410
- first base signal city-days: 130
- first support>=2 diagnostic city-days: 67

## Evidence coverage（不是策略筛选）

- base direct quote coverage: current YES 19/130; d1 NO 18/130
- support>=2 direct quote coverage: current YES 8/67; d1 NO 7/67
- settled executable rows: base 37; support>=2 15
- settled indicative rows (not executable): base 222; support>=2 110

## Direct executable ask result

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 19 | 6 | 8 | +100.0% | 0.974 | +2.5% | [+0.9%, +6.8%] |
| base | d1_no | 18 | 6 | 7 | +100.0% | 0.981 | +1.9% | [+0.7%, +4.6%] |
| strong_partial | current_yes | 8 | 6 | 4 | +100.0% | 0.955 | +4.5% | [+1.0%, +12.5%] |
| strong_partial | d1_no | 7 | 6 | 3 | +100.0% | 0.968 | +3.1% | [+0.7%, +8.1%] |

## Broad indicative-price diagnostic（不可当成成交回测）

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 111 | 6 | 36 | +93.7% | 0.924 | +1.2% | [-1.7%, +3.8%] |
| base | d1_no | 111 | 6 | 36 | +94.6% | 0.942 | +0.2% | [-3.0%, +3.3%] |
| strong_partial | current_yes | 55 | 6 | 21 | +94.5% | 0.950 | -0.6% | [-5.0%, +2.5%] |
| strong_partial | d1_no | 55 | 6 | 21 | +94.5% | 0.960 | -1.7% | [-6.9%, +2.0%] |

## support count diagnostic（base cohort，非门槛）

| Support | Expression | Rows | Dates | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | current_yes | 32 | 6 | +93.8% | 0.901 | +3.9% | [+2.5%, +5.1%] |
| 0 | d1_no | 32 | 6 | +96.9% | 0.938 | +3.0% | [+1.3%, +4.7%] |
| 1 | current_yes | 31 | 6 | +93.5% | 0.916 | +1.9% | [-6.1%, +6.6%] |
| 1 | d1_no | 31 | 6 | +93.5% | 0.922 | +1.2% | [-7.0%, +5.3%] |
| 2 | current_yes | 26 | 6 | +88.5% | 0.919 | -4.0% | [-11.4%, +3.3%] |
| 2 | d1_no | 26 | 6 | +88.5% | 0.938 | -6.0% | [-14.5%, +2.7%] |
| 3+ | current_yes | 22 | 5 | +100.0% | 0.975 | +2.4% | [+1.8%, +3.1%] |
| 3+ | d1_no | 22 | 5 | +100.0% | 0.979 | +2.0% | [+1.4%, +2.7%] |

## Busan executed anchor case

Busan 2026-07-14 在 2026-07-14T04:08:34Z 首次 strong-partial：30 YES ask=0.84，31 NO ask=0.89。该日未纳入上面的已结算 ROI。

用户确认这是实际人工成交的 anchor trade；本报告此前称为 sanity case 不准确。

- replay 首次 strong signal：2026-07-14T04:08:34Z。
- forward runner 首条 Busan row：2026-07-14T11:47:59Z（晚 459.4 分钟），mode=zero_notional_shadow，live_action=none。
- canonical 中匹配 `30 YES / 31 NO` 的 strategy fill：0 行。
- 因此断点不在 signal selector：回放确实选中了该形态；断在 runner 启动时点、zero-notional 执行边界，以及人工成交未进入 strategy order/fill lineage。

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
