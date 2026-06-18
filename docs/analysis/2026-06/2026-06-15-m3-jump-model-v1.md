# reheat-risk Jump Model v1

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `runtime/weather.db` self-check + `m3_observed_max_v3_h10_21` residual detail + `m3_exhaustion_no_v0` quote artifacts + IEM extended ASOS cache.
- DB fact built at: `2026-06-15T14:43:25.893487+00:00`.
- `fact_trades` by class: `live_real=855`, `live_simulated=624`, `paper=2285`, `snapshot_replay=636`.
- `fact_trades` by settlement: `settled=4250`, blank/unsettled-like rows `150`.
- `fact_signal_candidates`: `rows=30132`, `eligible=10364`, `paper_ordered=3961`, `live_filled=348`.
- CLOB order/fill join: `error=33/0 fills`, `submitted=961/855 fills`.
- CLOB gate: `gate_pass=true`, `missing_order_rows=0`, `over_order_keys=0`, `db_fill_cost_minus_fact_cost=0`.

## Target Metric

`no_reheat_model_quality` = can a METAR-path weather model predict same-day bucket jumps better than the old `(city, hour, decline_bucket)` climatology?

Trading check: after scoring tail-NO quotes, does `model_p_no - ask` create taker-positive BUY_NO selections in default-WU/source-aligned cities?

Row grain:
- model row = `city + target_date + decision_hour_local`;
- quote row = `city + target_date + decision_hour_local + bracket`.

## What Was Tried Before

- `research_m3_cross_section_no.py`: historical METAR climatology only, using `(city, hour, decline_bucket)` with shrinkage. It did not use dewpoint/cloud/wind/path features.
- `research_m3_nwp_locked_no.py`: decision-time forecast max + METAR decline, but still not a full METAR path model.
- `research_m3_jump_model_v1.py`: this is the first run of the intended direction: temperature path + dewpoint/RH/wind/cloud features.

## Model

Script: `scripts/analysis/reheat_risk/research_m3_jump_model_v1.py`

Training:
- train: `target_date < 2026-05-19`, `321082` rows.
- eval: `target_date >= 2026-05-19`, `9672` rows.
- 36 default-WU/source-aligned cities.
- GroupKFold by `target_date`, then isotonic calibration.

Features:
- temperature path: `d1h_b`, `d2h_b`, `d3h_b`, `hours_since_max`, `rise_rate_b`, `day_range_b`;
- threshold geometry: `gap_cur_to_thresh_b`, `frac_to_next_b`, `decline_b`;
- METAR/IEM weather state: dewpoint depression, RH, wind speed, sky cover, 3h dew/RH deltas;
- city and decision hour.

Feature coverage was strong: dew/RH/wind features were `>99.8%` non-null; sky cover was `76.1%`; running-max recompute mismatch was `0.7057%`.

## Model Quality

OOS evaluation on `target_date >= 2026-05-19`:

| target | model | Brier | logloss |
|---|---|---:|---:|
| jump_ge1 | marginal | 0.21794 | 0.62759 |
| jump_ge1 | old v0 climatology | 0.07916 | 0.24951 |
| jump_ge1 | METAR path v1 | **0.07143** | **0.22461** |
| jump_ge2 | old v0 climatology | 0.06889 | 0.21365 |
| jump_ge2 | METAR path v1 | **0.06150** | **0.19138** |
| jump_ge3 | old v0 climatology | 0.05386 | 0.16812 |
| jump_ge3 | METAR path v1 | **0.04878** | **0.15226** |

Daily clustered t-stat for `v1 Brier - v0 Brier` on `jump_ge1` = `-4.89`, so this is a real modeling improvement, not just vibes with a trench coat.

Top feature importances:

| feature | importance |
|---|---:|
| decision_hour_local | 0.7157 |
| city | 0.1046 |
| gap_cur_to_thresh_b | 0.0787 |
| sknt_now | 0.0162 |
| relh_now | 0.0087 |
| decline_b | 0.0052 |
| d1h_b | 0.0049 |
| sky_now | 0.0042 |
| dep_f | 0.0025 |

Interpretation: the intended weather features do matter, especially wind/RH/sky after hour and city. The model is not merely re-labeling decline.

## Trading Check

Taker scan on default-WU tail-NO quotes:

| model | threshold | distance | trades | ROI | daily t |
|---|---:|---|---:|---:|---:|
| v1 | EV>=0.00 | d1 | 1043 | +1.23% | +0.76 |
| v1 | EV>=0.02 | d1 | 949 | **+2.76%** | +1.53 |
| v1 | EV>=0.05 | d1 | 817 | +2.33% | +1.15 |
| v1 | EV>=0.02 | d1-3 | 1705 | -0.95% | -1.32 |
| v0 | EV>=0.02 | d1 | 952 | -2.14% | -1.26 |
| v0 | EV>=0.02 | d1-3 | 1670 | -3.14% | -4.76 |

This is directionally encouraging only for d1. It is not enough for live/shadow promotion because:
- no bootstrap/forward gate has been added for this v1 selection;
- d1-3 remains negative;
- quote-level calibration is worse than v0: dedup quote Brier `v1=0.27172` vs `v0=0.24434`;
- high predicted lose-probability buckets are overconfident, which means the mapping from jump model to exact bracket NO pricing needs repair.

## Answer

Yes, this was the right previous direction, but it had not actually been completed as an artifact until this run. Earlier work did *not* fully use METAR path + dewpoint/cloud/wind features; `jump_model_v1` now does and shows a real physical/modeling improvement.

The next research step is not to restart from scratch. Continue from `research_m3_jump_model_v1.py`, but fix the quote-level mapping/calibration:

- calibrate exact bracket `P(NO loses)` directly, not only jump thresholds;
- evaluate d1 separately from d2/d3;
- add train/holdout or walk-forward selection on `model_ev` thresholds;
- compare against market ask buckets and same-price baselines;
- only then decide whether a zero-notional theta-NO shadow journal is justified.

## Verdict

significance=PASS for model-quality improvement; FAIL/NA for trade action.
baseline=PASS for predicting `jump_ge1/2/3` vs v0; FAIL/NA for executable trading edge.
forward=NA for a deployable theta-NO rule.
conclusion=inconclusive for live/shadow/paper action.

