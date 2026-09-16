# 2026-06-21 Current-YES Decline Fade Modes v1

Generated UTC: `2026-06-21T06:16:00+00:00`

## Verdict

先不把新加坡这种 `decline_c >= 0.5C` 直接加成 live 硬规则。更合理的改法是把它做成 current-YES fade head 的一个候选状态特征/soft gate，并且先 shadow：`h15-21 + decline>=0.5C + minutes_since_running_max>=90` 比裸 `decline>=0.5C` 稳；`h10-14 + humid/cloudy deep dip` 反而是典型 false-fade 风险。

本轮 mature fade slice: state_rows=2136, active_dates=33, physical_confirmed_rate=98.1%, settled YES proxy ROI=-0.2%。
相对同小时 late no-decline baseline 的日期 bootstrap ROI delta=+4.2%, 95% CI [+1.7%, +6.9%]；relative delta 过显著性门，但绝对 proxy ROI 仍略负且没有 forward shadow/live 证据，所以结论等级 `inconclusive / shadow_candidate only`。

## Data Snapshot

- Feature rows: `docs/analysis/2026-06/generated/current_yes_decline_fade_20260620_feature_factory/reheat_feature_rows.csv`
- State grain: one row per city/date/hour/current bracket; not fill/PnL grain.
- Feature target_date range: `2026-05-19`..`2026-06-20`; state rows=8907; cities=36.
- CLOB fill gate: `True`; live_real PnL is not used in this report.
- `run_stack.sh` rebuilt fact tables but exited non-cleanly on frontend port 5174; DB/fact/gate artifacts were still produced.

## Mandatory 5-Line Self-Check

1. fact_trades freshness: rows=4400, max_built=2026-06-21T06:14:26.972074+00:00.
2. trade_class distribution: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
3. settlement distribution: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`.
4. fact_signal_candidates coverage: `{'rows': 34520, 'eligible': 12259, 'paper_ordered': 4772, 'live_filled': 348, 'min_event_date': '2026-05-05', 'max_event_date': '2026-06-22', 'max_fact_built_at_utc': '2026-06-21T06:14:50.101127+00:00'}`.
5. CLOB order/fill join: `[{'order_status': 'error', 'orders': 33, 'with_fill': 0}, {'order_status': 'submitted', 'orders': 961, 'with_fill': 855}]`.

## Singapore 2026-06-20 Read

The completed observation path says the 30.0C -> 27.2C drop was not actually a confirmed daily high. It later printed 31.1C, so this is a false-fade example, not a clean fade-confirmed example.

| hour | current C | running max C | decline C | final max C | ask | minutes since max | RH | sky | wind kt |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 28.9 | 30.0 | 1.1 | 31.1 | 0.320 | 60 | 79 | 2 | 13 |
| 11 | 27.2 | 30.0 | 2.8 | 31.1 | 0.520 | 120 | 89 | 1 | 6 |
| 12 | 27.8 | 30.0 | 2.2 | 31.1 | 0.760 | 181 | 89 | 1 | 12 |
| 13 | 27.2 | 30.0 | 2.8 | 31.1 | 0.700 | 241 | 79 | 1 | 6 |
| 14 | 30.0 | 30.0 | 0.0 | 31.1 | 0.610 | 1 | 75 | 1 | 7 |
| 15 | 31.1 | 31.1 | 0.0 | 31.1 | 0.870 | 1501 | 66 | 1 | 8 |
| 16 | 31.1 | 31.1 | 0.0 | 31.1 | 0.960 | 1562 | 66 | 1 | 6 |
| 17 | 30.0 | 31.1 | 1.1 | 31.1 | 0.999 | 1620 | 70 | 1 | 7 |
| 18 | 30.0 | 31.1 | 1.1 | 31.1 | 0.999 | 1681 | 70 | 1 | 4 |

## Mode Summary

| Mode | Rows | Dates | Cities | Future break >=0.5C | Physical confirmed | Settled YES ROI proxy |
|---|---:|---:|---:|---:|---:|---:|
| `cooling_trend_fade` | 229 | 32 | 35 | 8.7% | 91.3% | -1.1% |
| `early_humid_convective_dip` | 322 | 32 | 30 | 30.1% | 69.9% | +1.3% |
| `early_recent_or_small_dip` | 293 | 32 | 36 | 48.5% | 51.5% | -3.1% |
| `generic_decline` | 868 | 33 | 36 | 14.2% | 85.8% | -0.5% |
| `late_mature_cooldown` | 1616 | 33 | 36 | 0.7% | 99.3% | -0.1% |
| `late_soft_cooldown` | 218 | 33 | 33 | 3.2% | 96.8% | -0.2% |
| `not_declined` | 5361 | 33 | 36 | 61.3% | 38.7% | -4.9% |

## Rule Slices

| Rule | Rows | Dates | Cities | Avg decline C | Future break >=0.5C | Settled YES win | Settled YES ROI proxy |
|---|---:|---:|---:|---:|---:|---:|---:|
| `all_decline_ge_0_5` | 3546 | 33 | 36 | 1.96 | 11.3% | 89.2% | -0.3% |
| `early_h10_14_decline_ge_0_5` | 930 | 32 | 36 | 1.70 | 36.7% | 64.3% | -0.6% |
| `early_humid_convective_dip_h10_14_decline_ge_1_0` | 322 | 32 | 30 | 2.43 | 30.1% | 70.1% | +1.3% |
| `mature_fade_h15_21_decline_ge_0_5_min_since_ge_90` | 2136 | 33 | 36 | 2.16 | 1.9% | 98.3% | -0.2% |
| `late_mature_cooldown_h16_21_decline_ge_1_0` | 1616 | 33 | 36 | 2.45 | 0.7% | 99.4% | -0.1% |
| `recent_peak_decline_ge_0_5_min_since_lt_60` | 228 | 33 | 25 | 1.09 | 39.5% | 61.5% | -6.9% |
| `no_decline_h15_21_baseline` | 1617 | 33 | 36 | 0.00 | 26.5% | 76.0% | -4.4% |

## Interpretation

1. `late_mature_cooldown` is the only form that looks structurally aligned with fade-confirmed current YES: the high is old enough, the day is late enough, and future new-high risk is lower.
2. `early_humid_convective_dip` is the Singapore-like trap: rain/cloud/sea-breeze or convective cooling can knock the observation down hard before the true high is done. It should be a risk flag, not a buy trigger.
3. `early_recent_or_small_dip` is mostly observation noise or a brief plateau; treating `decline>=0.5C` alone as confirmation overfires.
4. The trading layer still needs forward shadow evidence. Opportunity proxy is useful for triage, but this report does not satisfy significance/baseline/forward gates for a live change.

## Contract Verdict

significance=PASS for relative mature-vs-no-decline delta, baseline=FAIL on absolute proxy ROI, forward=NA, conclusion=inconclusive/shadow_candidate. Action: do not add a live hard rule; add a research/shadow feature candidate for mature fade, and add an early humid dip veto/risk telemetry candidate.

## Outputs

- JSON: `docs/analysis/2026-06/2026-06-21-current-yes-decline-fade-modes-v1.json`
- Markdown: `docs/analysis/2026-06/2026-06-21-current-yes-decline-fade-modes-v1.md`
