# Current YES Peak-Forming vs Fade-Confirmed v1

Status: research_only / no_live_change
Generated: 2026-06-16T16:52:57+00:00

Target metrics:

- `current_yes_peak_forming_ev`: buy current running-max bracket YES while `decline_c <= 0.1C`; EV is `sum(p_yes_win - executable_ask) / sum(executable_ask)` on time-aligned current-YES quotes.
- `current_yes_fade_confirmed_ev`: buy current running-max bracket YES after visible fade (`decline_c >= 0.5C`); same EV formula and quote grain.

Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + current_bracket` current-YES quote. Same-day paired rows use the first peak-forming quote and the first later fade-confirmed quote for the same `city + target_date`.

## 数据快照

- 数据源: `runtime/weather.db` self-check + `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` shared feature factory, collapsed to one current-YES state row per city/date/hour/current bracket.
- 数据快照时间: fact_built_at_utc `2026-06-16T15:50:05.971834+00:00`; feature rows mtime `2026-06-16T16:02:08+00:00`.
- 记录行数: raw factory quote rows=88621; state feature_rows=11512; dropped unlabeled state rows=0; usable current-YES rows=618; holdout usable rows=338.
- unsettled 占比: 90 / 4400 fact_trades rows.
- missing_bracket 数: 0.
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 90}, {'settlement_status': 'settled', 'rows': 4310}]`。
- fact_signal_candidates coverage: `{'rows': 30919, 'eligible': 10685, 'paper_ordered': 4123, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0；本报告不发布 live_real PnL。

## 人话结论

结论：不要把 peak-forming 直接升级成主规则。迁到 shared feature factory 后，证据比旧 replay 更明确地偏向 fade-confirmed：fade-confirmed 是后续应继续研究的主 timing head；peak-forming 只保留一个很窄的 early shadow sleeve，用来验证 h13 附近是否能稳定拿到便宜价且不被二次升温吃掉。

固定规则下，peak-forming: ROI -7.1%, win 72.0%, avg ask 0.755, EV ROI +9.9%；fade-confirmed: ROI +10.6%, win 90.3%, avg ask 0.797, EV ROI +10.0%。
train 选规则再投 holdout 后，peak-forming: ROI +2.5%, win 79.7%, avg ask 0.757, EV ROI +9.9%；fade-confirmed: ROI +10.2%, win 90.0%, avg ask 0.797, EV ROI +9.9%。

同 city/date 配对里，早买平均省 0.208 价格点，但只有 5 对，而且这几对 same-bracket rate 是 100.0%；+2c 后 peak ROI +13.3%，fade ROI -12.5%。也就是说，早买确实能省钱，但当前 paired sample 主要是“没换档”的成功日，不能证明它已经覆盖了二次升温/后来换档风险。

交易动作：继续 shadow，不改 N100/live。后续研究优先放在 `fade-confirmed` 的 execution freshness / ask slippage gate；`peak-forming` 只在 h13/edge 足够厚/fresh ask 未跳价时零 notional 记录；不要做 h14-h15 的宽 plateau 追单。

## 数据漏斗

- raw factory quote rows: `88621`; collapsed current-YES state rows: `11512` from `2026-05-19` to `2026-06-14`。
- usable rows after ask/model/liquidity basics: `618`。
- fixed peak-forming holdout rows: `25`；fixed fade-confirmed holdout rows: `31`。
- paired same city/date rows: `5` over `5` active dates; average later gap `1.400` hours; same-bracket rate `100.0%`。

## Fixed Interpretable Rules

| head | rows | days | win | ROI +2c | CI95 | model EV ROI +2c | avg ask | avg p | avg decline | rule |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `current_yes_fade_confirmed` | 31 | 11 | 90.3% | +10.6% | [-1.8%, +21.8%] | +10.0% | 0.797 | 0.899 | 1.254 | `current_yes_fade_confirmed|h13-15|decline>=0.5|ask>=0.55|p>=0.5|edge>=0.05` |
| `current_yes_peak_forming` | 25 | 11 | 72.0% | -7.1% | [-31.6%, +19.9%] | +9.9% | 0.755 | 0.852 | 0.000 | `current_yes_peak_forming|h13-13|decline<=0.1|ask>=0.55|p>=0.5|edge>=0.05` |

## Train -> Holdout Rule Selection

Small grid only: decline state, local hour window, ask floor, and edge floor. Rules were selected on train dates before 2026-06-01, then evaluated on holdout.

| head | rows | days | win | ROI +2c | CI95 | model EV ROI +2c | avg ask | avg p | avg decline | rule |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `current_yes_fade_confirmed` | 30 | 11 | 90.0% | +10.2% | [-2.7%, +21.8%] | +9.9% | 0.797 | 0.897 | 1.278 | `current_yes_fade_confirmed|h13-15|decline>=1|ask>=0.55|p>=0.5|edge>=0.05` |
| `current_yes_peak_forming` | 64 | 14 | 79.7% | +2.5% | [-14.1%, +16.2%] | +9.9% | 0.757 | 0.854 | 0.000 | `current_yes_peak_forming|h14-15|decline<=0.01|ask>=0.55|p>=0.5|edge>=0.05` |

## Slippage Sensitivity

| head | slippage | rows | win | ROI | model EV ROI | avg ask |
|---|---:|---:|---:|---:|---:|---:|
| `current_yes_fade_confirmed` | 0c | 31 | 90.3% | +13.3% | +12.8% | 0.797 |
| `current_yes_fade_confirmed` | 1c | 31 | 90.3% | +11.9% | +11.4% | 0.797 |
| `current_yes_fade_confirmed` | 2c | 31 | 90.3% | +10.6% | +10.0% | 0.797 |
| `current_yes_fade_confirmed` | 5c | 31 | 90.3% | +6.7% | +6.1% | 0.797 |
| `current_yes_peak_forming` | 0c | 25 | 72.0% | -4.6% | +12.9% | 0.755 |
| `current_yes_peak_forming` | 1c | 25 | 72.0% | -5.9% | +11.4% | 0.755 |
| `current_yes_peak_forming` | 2c | 25 | 72.0% | -7.1% | +9.9% | 0.755 |
| `current_yes_peak_forming` | 5c | 25 | 72.0% | -10.5% | +5.8% | 0.755 |

## Prefix Walk-Forward

Each target date chooses a rule from prior dates only, separately for the two heads.

| head | rows | days | win | ROI +2c | CI95 | model EV ROI +2c | avg ask |
|---|---:|---:|---:|---:|---:|---:|---:|
| `current_yes_fade_confirmed` | 36 | 14 | 91.7% | +11.0% | [+0.0%, +21.1%] | +9.7% | 0.806 |
| `current_yes_peak_forming` | 76 | 17 | 80.3% | +3.4% | [-10.2%, +16.3%] | +10.2% | 0.756 |

## Failure Reasons / Rules

- Peak-forming's advantage is price: it can be cheaper before the market reprices after visible fade.
- Peak-forming's failure mode is path risk: a later reheat can make the early current bracket stale, while a later fade entry may buy the updated current bracket.
- Fade-confirmed's failure mode is execution: by the time the fade is visible, the current YES ask is often high and the edge is sensitive to 1-2c fresh-book slippage.
- Forecast peak clock is still a necessary upstream field; fixed local h13 is only a proxy, not a deployable universal clock.

## 三道门

- significance=PARTIAL/LOW_SAMPLE：fixed fade-confirmed ROI 为正但 CI 下沿略低于 0；prefix walk-forward fade 的 CI 下沿刚过 0；peak-forming fixed holdout 为负且 CI 跨 0。
- baseline=PARTIAL：comparison is against sibling timing head on the same current-YES expression, not a full zero-model or live fill baseline.
- forward=PARTIAL：prefix walk-forward 支持 fade-confirmed 继续研究，但还不足以做 live timing switch。
- conclusion=shadow_only：后续主线应研究 fade-confirmed 的 execution freshness gate；peak-forming 只保留 early shadow sleeve；禁止 live change。

## Outputs

- Script: `scripts/analysis/reheat_risk/research_current_yes_peak_vs_fade_v1.py`
- JSON: `docs/analysis/2026-06/2026-06-16-current-yes-peak-vs-fade-v1.json`
- CSV: `docs/analysis/2026-06/generated/current_yes_peak_vs_fade_v1/paired_city_date_rows.csv`
- CSV: `docs/analysis/2026-06/generated/current_yes_peak_vs_fade_v1/train_rule_grid.csv`
