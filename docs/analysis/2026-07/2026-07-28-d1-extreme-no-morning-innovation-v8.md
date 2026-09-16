# D-1 extreme-NO：morning innovation v8

## 数据快照

- 数据源：`/Users/deepsleep/projects/pm_agents/runtime/weather.db`（canonical ladder/settlement）+ `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv`（IANA local checkpoint）+ `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv`（D-1 basket replay）。
- DB last_modified_utc：`2026-07-28T11:30:58.160486+00:00`。
- checkpoint state rows：853；morning executable baskets：72。
- unsettled=0；missing_bracket=0（输入已限定 settled checkpoint states）。
- actual fills=0；以下均为 PIT snapshot research replay。

## Target 与修正

本报告只研究原策略：每个 city-day 等份买最低挂牌档 NO 与最高挂牌档 NO。D-1、09:00、12:00 使用同一选档规则；innovation 只允许更新联合 tail-hit probability 和是否入场。此前单档 NO / Current-YES carry 不属于本策略证据。

## Contract

- local checkpoints=(9, 12)；IANA timezone 来自已审计 checkpoint artifact。
- 每个时点按该时点仍挂牌的完整 ladder 重新取最低/最高档；因此 D-1 与 morning 是同一选档规则，但不保证是同一对 bracket。
- basket payout：两端都不命中=2；任一端命中=1；两端不可能同时命中。
- cost：两条 direct NO ask + 每腿 `0.05*p*(1-p)`。
- primary eligibility：`P(tail hit) <= 2-cost-0.005`。
- expanding OOF：至少 5 个 prior target dates；C=0.1；无 city/region selector。

## Signal / evidence funnel

- morning settled checkpoint states：853。
- direct-NO executable two-tail baskets：72 / 4 dates。
- full two-sided YES ladder diagnostic coverage：72 baskets；不作为 eligibility。
- expanding OOF baskets：0 / 0 dates。
- actual fills=0；全部为 research replay。
- raw orderbook archive 覆盖全部 checkpoint 日期；但两条最外档 direct NO ask 仅在 2026-07-15..18 同时存在。这不是 canonical 未同步；raw 中未保存的历史报价无法靠 rebuild 创造，且现有记录不能区分当时空 book 与 collector 未捕获。

## 同一选档规则的 D-1 → morning timing 对比

| d1_policy | checkpoint_hour_local | paired_baskets | dates | cities | same_extreme_pair | d1_mean_cost | morning_mean_cost | d1_roi | morning_roi | mean_return_delta | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D-1_12_18_first | 9 | 27 | 3 | 26 | 2 | 1.9780 | 1.9765 | 0.0111 | -0.0069 | -0.0182 | -0.0393 | -0.0035 |
| D-1_12_18_first | 12 | 21 | 3 | 16 | 0 | 1.9579 | 1.7827 | 0.0215 | -0.0117 | -0.0428 | -0.1068 | 0.0152 |
| D-1_18_24_first | 9 | 27 | 3 | 26 | 6 | 1.9798 | 1.9765 | -0.0085 | -0.0069 | 0.0015 | -0.0018 | 0.0059 |
| D-1_18_24_first | 12 | 22 | 3 | 16 | 0 | 1.9485 | 1.7643 | 0.0264 | -0.0468 | -0.0841 | -0.1538 | 0.0198 |

## Joint tail probability（expanding OOF）

| checkpoint_hour_local | variant | baskets | dates | tail_hits | date_equal_logloss | date_equal_brier | logloss_delta_vs_forecast | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Fee-adjusted basket replay

| checkpoint_hour_local | policy | opportunity_baskets | trades | dates | tail_hits | mean_cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | mechanical_all | 43 | 43 | 4 | 2 | 1.9710 | -0.7528 | -0.0089 | -0.0250 | 0.0088 |
| 12 | mechanical_all | 29 | 29 | 4 | 7 | 1.8140 | -1.6071 | -0.0305 | -0.0740 | 0.0192 |

## Gates

- innovation probability delta：NA。
- innovation-selected fee-adjusted execution：NA。
- fresh frozen forward：NA。
- 当前只有 4 个 executable target dates，少于 5-date expanding train 门槛，所以 innovation 不能形成可解释的 OOF 交易结论。

```text
status=inconclusive
live_action=none
```
