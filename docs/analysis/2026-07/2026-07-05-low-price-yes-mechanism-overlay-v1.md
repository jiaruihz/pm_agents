# HeadA Low-Price YES Mechanism Overlay v1

Generated: `2026-07-06T04:56:39Z`

Scope: HeadA `forecast_tail_low_price_yes` only.  This is a first-principles mechanism study over the current low-price YES denominator.  It does not change live.

## Verdict

HeadA does have plausible mechanism-improvement directions, but this run does **not** justify a new live gate.  The cleanest read is:

```text
forecast distance / station-bias: useful as probability/EV shape, not yet a superior selector
regime/weather complexity: useful diagnostic, weak as standalone selector
book state / attention: strongest unresolved mechanism; either real attention alpha or stale-quote illusion
expression alternatives: blanket hotter bracket or basket does not fix overshoot

significance=PARTIAL
baseline=PARTIAL
forward=FAIL/NA
conclusion=inconclusive_research_overlay; keep current tiny HeadA live unchanged
```

Current hot-only proxy (`price_tier_6_8_10_shares + taker fee + hold`) on the frozen denominator:

| label | period | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days | le_minus50pct_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_live_hot_dist_gt0 | full | 333 | 53.000 | 47.000 | 15.0% | 10.4% | +41.8% | +10.7% | +76.3% | +28.3% | 19.000 | 16.000 | $-11.04 |

## Data Snapshot

- Sync/rebuild before this run: Mac market-data synced through 2026-07-05 13:00 Asia/Shanghai; `run_stack.sh --api-only` rebuilt `runtime/weather.db` and fact tables.
- Denominator source: `low_price_yes_integrated_tail_v2` via `load_base()`, with canonical settlement and fact-signal fields reattached.
- Rows: 476 total; 333 `dist>0` hot-tail rows; 143 `dist<=0` rows excluded by current HeadA live boundary.
- Dates/cities: 2026-05-06..2026-06-30, 53 dates, 48 cities.
- Cost model: official Weather taker fee `shares * 0.05 * price * (1-price)`; maker/rebate upside not counted here.

## Mechanism Selectors

All rows below use the same HeadA denominator and the same execution proxy: `price_tier_6_8_10_shares + taker_weather_fee + hold`.  These are not proposed live gates; they test mechanism shape.

| label | rows | dates | cities | win_rate | avg_entry | avg_shares | roi | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_live_hot_dist_gt0 | 333 | 53.000 | 47.000 | 15.0% | 10.4% | 7.6 | +41.8% | +10.7% | +76.3% | +28.3% | 19.000 | $-11.04 |
| forecast_core_adj_dist_-0p5_to_1 | 298 | 52.000 | 47.000 | 14.8% | 10.7% | 7.7 | +39.6% | +5.0% | +78.5% | +24.8% | 19.000 | $-10.92 |
| book_feasible_hot | 212 | 41.000 | 44.000 | 11.8% | 9.9% | 7.5 | +17.8% | -14.8% | +53.4% | -6.4% | 20.000 | $-9.63 |
| book_attention_thin_or_missing_hot | 121 | 41.000 | 37.000 | 20.7% | 11.3% | 7.9 | +76.8% | +22.1% | +134.0% | +44.9% | 22.000 | $-4.76 |
| regime_score_ge4_hot | 8 | 7.000 | 8.000 | 12.5% | 10.2% | 7.5 | -12.1% | -100.0% | +314.2% | -100.0% | 6.000 | $-2.83 |
| regime_not_capped_busted_hot | 0 |  |  |  |  |  |  |  |  |  |  |  |
| composite_core_plus_attention | 111 | 41.000 | 37.000 | 18.9% | 11.5% | 8.0 | +64.1% | -0.8% | +133.4% | +29.2% | 25.000 | $-10.92 |
| composite_core_plus_regime_ge4 | 7 | 6.000 | 7.000 | 0.0% | 10.8% | 7.7 | -100.0% | -100.0% | -100.0% | -100.0% | 6.000 | $-2.83 |
| composite_core_attention_regime_ge4 | 3 | 2.000 | 3.000 | 0.0% | 12.3% | 8.0 | -100.0% |  |  |  | 2.000 | $-2.83 |
| continuous_ev_same_count | 343 | 53.000 | 48.000 | 15.5% | 11.0% | 7.8 | +38.3% | +3.3% | +77.6% | +26.1% | 23.000 | $-12.45 |

Same-denominator-ish delta versus current `dist>0` baseline, by target-date block bootstrap:

| selector | rows | dates | roi | baseline_roi | delta_roi | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| forecast_core_adj_dist_-0p5_to_1 | 298 | 52 | +39.6% | +41.8% | -2.3% | -13.8% | +9.1% |
| book_attention_thin_or_missing_hot | 121 | 41 | +76.8% | +41.8% | +35.0% | -1.5% | +70.7% |
| regime_score_ge4_hot | 8 | 7 | -12.1% | +41.8% | -54.0% | -160.3% | +327.9% |
| composite_core_plus_attention | 111 | 41 | +64.1% | +41.8% | +22.2% | -26.7% | +69.2% |
| composite_core_plus_regime_ge4 | 7 | 6 | -100.0% | +41.8% | -141.8% | -176.8% | -109.3% |
| continuous_ev_same_count | 343 | 53 | +38.3% | +41.8% | -3.6% | -18.7% | +11.8% |

Interpretation:

- `forecast_core_adj_dist_-0p5_to_1` is the most physically coherent forecast-distance candidate: avoid cold/inside tickets and avoid far-tail tickets more than one bracket away after station-bias adjustment.  It improves the story, but not enough to promote by itself.
- `regime_score_ge4_hot` and `regime_not_capped_busted_hot` are real descriptors, but they do not dominate the existing selector.  Regime is a sensor, not the steering wheel.
- `book_attention_thin_or_missing_hot` remains the big unresolved piece.  If it fills live near decision ask, it is likely the alpha carrier.  If it fails to fill, the historical ROI is partly phantom.
- `continuous_ev_same_count` confirms the previous result: the probability shape ranks rows, but paired excess over `dist>0` is not clean enough yet.

## Train vs Recent

| period | label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_ge_2026_06_21 | current_live_hot_dist_gt0 | 58 | 9.000 | 17.2% | 9.9% | +76.7% | +31.7% | +133.9% | -8.4% |
| train_le_2026_06_20 | current_live_hot_dist_gt0 | 275 | 44.000 | 14.5% | 10.5% | +35.1% | -0.1% | +76.4% | +18.7% |
| recent_ge_2026_06_21 | forecast_core_adj_dist_-0p5_to_1 | 50 | 8.000 | 16.0% | 10.4% | +63.9% | +17.4% | +107.1% | -32.8% |
| train_le_2026_06_20 | forecast_core_adj_dist_-0p5_to_1 | 248 | 44.000 | 14.5% | 10.7% | +34.9% | -4.7% | +79.7% | +17.0% |
| recent_ge_2026_06_21 | book_attention_thin_or_missing_hot | 18 | 8.000 | 11.1% | 9.5% | +1.7% | -100.0% | +81.7% | -100.0% |
| train_le_2026_06_20 | book_attention_thin_or_missing_hot | 103 | 33.000 | 22.3% | 11.6% | +86.6% | +26.0% | +150.0% | +51.0% |
| recent_ge_2026_06_21 | regime_score_ge4_hot | 1 | 1.000 | 0.0% | 7.6% | -100.0% |  |  |  |
| train_le_2026_06_20 | regime_score_ge4_hot | 7 | 6.000 | 14.3% | 10.6% | -5.5% | -100.0% | +369.5% | -100.0% |
| recent_ge_2026_06_21 | composite_core_plus_attention | 15 | 8.000 | 6.7% | 10.1% | -36.3% | -100.0% | +74.3% | -100.0% |
| train_le_2026_06_20 | composite_core_plus_attention | 96 | 33.000 | 20.8% | 11.7% | +76.6% | +4.3% | +151.3% | +38.0% |
| recent_ge_2026_06_21 | continuous_ev_same_count | 68 | 9.000 | 16.2% | 11.4% | +44.8% | +10.4% | +81.1% | -16.7% |
| train_le_2026_06_20 | continuous_ev_same_count | 275 | 44.000 | 15.3% | 10.9% | +36.6% | -5.6% | +87.4% | +21.1% |

Recent support is too thin and noisy to bless any overlay.  This is exactly why the 2026-07-04 forward clock matters more than more train slicing.

## Feature Slices

These slices explain mechanism contribution inside the hot-tail universe.  They are not independent strategies.

| slice_type | slice_value | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adj_dist_band | 0..0.5 | 128 | 49 | 17.2% | 11.3% | +57.4% | -5.1% | +131.9% | +26.1% |
| adj_dist_band | -0.5..0 | 116 | 48 | 12.9% | 10.9% | +19.3% | -33.3% | +74.4% | -20.1% |
| adj_dist_band | 0.5..1 | 53 | 33 | 13.2% | 8.4% | +41.7% | -47.6% | +160.6% | -60.6% |
| adj_dist_band | <-0.5 | 30 | 23 | 13.3% | 8.7% | +26.2% | -74.2% | +186.7% | -100.0% |
| adj_dist_band | >1 | 6 | 6 | 33.3% | 7.2% | +309.8% | -100.0% | +900.3% | -100.0% |
| book_state_v1 | feasible | 212 | 41 | 11.8% | 9.9% | +17.8% | -14.8% | +53.4% | -6.4% |
| book_state_v1 | missing | 62 | 17 | 22.6% | 10.7% | +104.6% | +18.7% | +187.0% | +38.2% |
| book_state_v1 | thin_wide | 59 | 28 | 18.6% | 11.8% | +51.1% | -19.8% | +126.4% | -17.6% |
| day_regime |  | 322 | 53 | 15.2% | 10.4% | +45.3% | +13.8% | +80.0% | +31.3% |
| day_regime | day_open_runway | 5 | 5 | 20.0% | 9.4% | +63.8% | -100.0% | +552.8% |  |
| day_regime | day_forecast_busted | 3 | 3 | 0.0% | 14.3% | -100.0% | -100.0% | -100.0% |  |
| day_regime | day_marginal_runway | 3 | 3 | 0.0% | 11.7% | -100.0% | -100.0% | -100.0% |  |
| intraday_state |  | 322 | 53 | 15.2% | 10.4% | +45.3% | +13.8% | +80.0% | +31.3% |
| intraday_state | active_warming | 7 | 6 | 0.0% | 10.0% | -100.0% | -100.0% | -100.0% | -100.0% |
| intraday_state | fresh_high | 2 | 2 | 0.0% | 14.5% | -100.0% |  |  |  |
| intraday_state | false_fade_risk | 1 | 1 | 100.0% | 6.6% | +1347.6% |  |  |  |
| intraday_state | mature_fade | 1 | 1 | 0.0% | 19.0% | -100.0% |  |  |  |
| moisture_cloud_regime |  | 322 | 53 | 15.2% | 10.4% | +45.3% | +13.8% | +80.0% | +31.3% |
| moisture_cloud_regime | mixed_moisture | 7 | 7 | 0.0% | 11.8% | -100.0% | -100.0% | -100.0% | -100.0% |
| moisture_cloud_regime | humid_convective_risk | 2 | 2 | 50.0% | 12.8% | +150.9% |  |  |  |
| moisture_cloud_regime | cloud_suppression | 1 | 1 | 0.0% | 8.5% | -100.0% |  |  |  |
| moisture_cloud_regime | dry_heat_inertia | 1 | 1 | 0.0% | 8.5% | -100.0% |  |  |  |
| price_band | 8-14c | 138 | 48 | 12.3% | 10.9% | +7.8% | -31.8% | +53.5% | -21.6% |
| price_band | 5-8c | 130 | 49 | 9.2% | 6.5% | +34.8% | -31.2% | +110.7% | -18.4% |
| price_band | 14-20c | 65 | 38 | 32.3% | 17.0% | +82.3% | +17.2% | +152.4% | +49.0% |
| wind_regime |  | 322 | 53 | 15.2% | 10.4% | +45.3% | +13.8% | +80.0% | +31.3% |
| wind_regime | light_wind | 8 | 7 | 12.5% | 11.6% | -26.3% | -100.0% | +244.5% | -100.0% |
| wind_regime | moderate_wind | 3 | 3 | 0.0% | 10.7% | -100.0% | -100.0% | -100.0% |  |

Plain read:

- Distance matters, but in a curved way: too close can be non-tail, too far becomes wish-casting.  The productive zone is around bias-adjusted near-tail, not maximum distance.
- Weather regime labels do contain information, but their standalone edge is unstable.  They should feed EV calibration.
- Price band matters because fixed cash overweights the cheapest tickets; HeadA’s current price-tier sizing is directionally more coherent.

## Expression Impact

Expression replay rebuilds same-snapshot sibling YES legs for the hot-tail rows.

| label | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| higher_plus_yes | 246 | 52 | 42 | 2.8% | 4.1% | -32.4% | -64.2% | +0.1% | -75.0% |
| next_hotter_yes | 307 | 53 | 44 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% | -10.5% |
| selected_plus_next_basket | 307 | 53 | 44 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% | +7.2% |
| selected_yes | 333 | 53 | 47 | 15.0% | 10.4% | +38.2% | +7.4% | +72.1% | +25.5% |
| two_hotter_yes | 290 | 53 | 44 | 17.2% | 18.0% | -7.3% | -29.0% | +13.9% | -15.0% |

Same row denominator checks:

| paired_alt | label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| next_hotter_yes | selected_yes | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% |
| next_hotter_yes | next_hotter_yes | 307 | 53 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% |
| higher_plus_yes | selected_yes | 246 | 52 | 15.0% | 10.3% | +39.4% | +1.6% | +81.4% |
| higher_plus_yes | higher_plus_yes | 246 | 52 | 2.8% | 4.1% | -32.4% | -64.2% | +0.1% |
| selected_plus_next_basket | selected_yes | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% |
| selected_plus_next_basket | selected_plus_next_basket | 307 | 53 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% |

Expression by book state:

| slice_value | expression | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| feasible | next_hotter_yes | 203 | 41 | 20.7% | 18.5% | +8.4% | -19.0% | +35.8% |
| feasible | selected_plus_next_basket | 203 | 41 | 32.0% | 14.7% | +8.9% | -9.4% | +26.4% |
| feasible | selected_yes | 212 | 41 | 11.8% | 9.9% | +13.8% | -18.4% | +50.2% |
| missing | next_hotter_yes | 53 | 17 | 7.5% | 14.6% | -49.9% | -100.0% | +0.0% |
| missing | selected_plus_next_basket | 53 | 17 | 34.0% | 13.2% | +28.7% | +1.9% | +56.3% |
| missing | selected_yes | 62 | 17 | 22.6% | 10.7% | +101.6% | +25.8% | +177.8% |
| thin_wide | next_hotter_yes | 51 | 27 | 13.7% | 13.8% | -3.6% | -53.3% | +45.6% |
| thin_wide | selected_plus_next_basket | 51 | 27 | 29.4% | 13.3% | +10.9% | -29.5% | +53.2% |
| thin_wide | selected_yes | 59 | 28 | 18.6% | 11.8% | +51.3% | -19.0% | +127.0% |

Conclusion: overshoot is a real failure mode, but the naive fix is bad.  `next_hotter_yes`, `higher_plus_yes`, and the small basket generally dilute the edge.  The right next experiment is not “always buy hotter”; it is an EV-ranked expression selector that only switches expression when the probability lift beats the extra ask.

## First-Principles Takeaway

HeadA should be modeled as:

```text
P(ticket wins)
  = f(
      bracket distance above forecast,
      as-of station/source bias,
      forecast uncertainty / regime complexity,
      market attention / book state
    )

trade only if P(ticket wins) - executable ask - fee > 0
then choose expression only if sibling expression has higher net EV on same snapshot
```

Regime belongs inside `f(...)`.  It should not be a standalone hard gate unless fresh-forward evidence shows a specific regime boundary has stable excess over the current selector.

## Next Work

1. Keep HeadA tiny live as-is: `dist>0`, price-tier 6/8/10 shares, maker-first dynamic lifecycle, hold to settlement.
2. Add/monitor forward telemetry for this overlay: `adj_dist_p50_br`, `regime_score`, `book_state_v1`, `continuous_ev_same_count`, and sibling-expression counterfactual.
3. The next durable script should be an executable EV selector replay: calibrate `P(win)` on train, then compare current expression vs sibling expressions with real orderbook depth and maker partial fills.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_mechanism_overlay_v1.py`
- Selector summary: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_summary.csv`
- Selector daily: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_daily.csv`
- Selector delta: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_delta_vs_current.csv`
- Feature slices: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/group_slices.csv`
- Expression replay: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/expression_replay.csv`
- Expression summaries: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/expression_overall.csv`, `expression_paired.csv`, `expression_by_group.csv`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-mechanism-overlay-v1.json`
