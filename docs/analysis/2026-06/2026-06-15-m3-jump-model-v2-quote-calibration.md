# reheat-risk Jump Model v2 Quote Calibration

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `m3_jump_model_v1/scored_quotes.csv` + `runtime/weather.db` self-check.
- DB fact built at: `2026-06-15T14:43:25.893487+00:00`.
- CLOB gate: `gate_pass=True`, `missing_order_rows=0`, `over_order_keys=0`.
- `fact_trades` by class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- `fact_signal_candidates`: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`.

## Target Metric

`exact_bracket_theta_no_quote_calibration` = can the v1 jump model be calibrated into an exact-bracket `P(NO loses)` that selects positive-EV BUY_NO quotes in default-WU cities?

Row grain: one quote row is `city + target_date + decision_hour_local + bracket`; selected trades dedupe to first qualifying `city + target_date + bracket`.

## Funnel

- Raw scored quotes: `10275`.
- Train quotes `< 2026-06-01`: `6023`.
- Holdout quotes `>= 2026-06-01`: `4252`.
- Candidate rules: `432`.

## Calibration

| model | train Brier | holdout Brier | holdout logloss |
|---|---:|---:|---:|
| `raw_v1` | 0.21828 | 0.21779 | 0.67596 |
| `raw_v0` | 0.20797 | 0.21193 | 0.62916 |
| `iso_all` | 0.18250 | 0.18567 | 0.55308 |
| `iso_dist` | 0.17924 | 0.18480 | 0.56026 |


The isotonic maps improved holdout quote calibration versus raw v1/v0. The important warning is different: better probability calibration still did not create a robust tradable rule once same-price baselines and walk-forward selection were applied.

## Train-Selected Rules On Holdout

| rule | selected rows | ROI | excess CI | same-price excess | active dates |
|---|---:|---:|---|---:|---:|
| `iso_dist|ev>=0.08|d1|h13-17|ask<=0.75` | 151 | -5.1% | -2.4% [-15.2%..+8.3%] | +1.4% | 9 |
| `iso_dist|ev>=0.08|d1|h13-17|ask<=0.85` | 159 | -2.3% | +2.7% [-10.9%..+15.4%] | +3.4% | 9 |
| `iso_dist|ev>=0.08|d1-3|h15-17|ask<=0.75` | 61 | -22.6% | -8.5% [-19.6%..+1.7%] | +1.7% | 9 |
| `iso_dist|ev>=0.08|d1|h13-17|ask<=0.97` | 159 | -2.3% | +1.4% [-13.4%..+15.1%] | +4.8% | 9 |
| `raw_v1|ev>=0.08|d1|h13-17|ask<=0.75` | 161 | +2.1% | +4.8% [-4.9%..+12.1%] | +6.3% | 9 |
| `iso_dist|ev>=0.05|d1|h13-17|ask<=0.75` | 170 | -3.6% | -0.9% [-14.0%..+9.5%] | +2.2% | 9 |
| `iso_dist|ev>=0.01|d1|h13-17|ask<=0.75` | 192 | -1.9% | +0.9% [-7.7%..+7.9%] | +3.4% | 9 |
| `iso_dist|ev>=0.03|d1|h13-17|ask<=0.75` | 176 | -2.3% | +0.4% [-11.5%..+10.2%] | +3.4% | 9 |


Top train-selected rule `iso_dist|ev>=0.08|d1|h13-17|ask<=0.75` held out at ROI -5.1%, rule-baseline excess -2.4%, and same-price excess +1.4%.

## Prefix Walk-Forward

- Test days: `13`.
- Selected rows: `222`.
- ROI: `-3.0%`.
- Positive days: `6`.
- Daily t: `-0.49`.

## Interpretation

This v2 answered the immediate question: the v1 weather model is useful, but the exact-bracket trading layer is still not robust. Calibrating `P(NO loses)` directly did not produce a holdout-positive, same-price-positive, walk-forward-positive theta-NO rule.

The likely next move is more targeted, not broader: restrict to d1, increase the forward sample through shadow telemetry, and model exact bracket loss directly with a proper train window once there are more recent quote outcomes.

## Verdict

significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive

Action: continue research only; no shadow/paper/live promotion from this v2.
