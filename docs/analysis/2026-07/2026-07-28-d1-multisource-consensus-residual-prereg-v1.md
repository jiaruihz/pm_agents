# D-1 multi-source consensus residual prereg v1

## Target

在固定的 D-1 local 12:00–24:00 endpoint NO 分母上，检验市场跟随的
city-assigned ECMWF/GFS 与 bias-corrected multi-model consensus 分歧时，
分歧方向对应的 endpoint NO 是否相对另一条 endpoint NO 被低估。

## Frozen inputs

- Forecast version grain:
  `(city, forecast_target_date, model_label, available_at_utc)`.
- Forecast as-of rule: `available_at_utc <= book_fetched_at_utc`.
- Bias training cutoff: `2026-07-07`.
- Bias: mean `(settlement bracket midpoint F - forecast max F)`, minimum
  20 settled city-model dates.
- Corrected forecast: `forecast_max_f + frozen bias`.
- Consensus: median of eligible corrected models.
- Spread: corrected-model IQR.
- Assigned model: existing `CITY_MODEL` ECMWF/GFS assignment.

## Continuous signal

```text
delta_f = assigned_corrected_f - consensus_corrected_f
high_NO_score =  delta_f
low_NO_score  = -delta_f
```

Positive score means that endpoint NO is the preregistered underpricing
hypothesis. No absolute-delta threshold is used for eligibility.

Interpretation:

- assigned hotter than consensus -> high endpoint YES may be over-weighted by
  the market -> test high endpoint NO;
- assigned colder than consensus -> low endpoint YES may be over-weighted ->
  test low endpoint NO;
- large ensemble spread weakens the "safe tail NO" thesis and remains a
  continuous uncertainty feature.

## Fixed denominator and metrics

Signal funnel:

```text
full-ladder city-date -> D-1 local 12–24 -> PIT consensus available
-> both endpoint NO legs logged -> directional score
```

Evidence funnel:

```text
PIT forecast versions -> archived NO ask/depth -> settlement
-> fee-adjusted hypothetical outcome -> actual fill (always zero in v1)
```

Primary frozen-forward comparison after sufficient settled dates:

1. paired fee-adjusted ROI difference between the positive-score endpoint NO
   and the opposite endpoint NO in the same city-date;
2. monotonicity of `directional_no_score_f` versus endpoint-NO outcome and
   fee-adjusted return;
3. date-block bootstrap, with city/date counts and no post-hoc city deletion.

The collector records every eligible endpoint leg, including missing-book
coverage rows. This is zero-notional shadow only and cannot submit orders.

## Promotion boundary

Remain `shadow_candidate/inconclusive` until strict PIT forecast versions,
fresh books and settlements cover a frozen forward window and the directional
leg beats both the opposite leg and same-row market baseline after fees.
