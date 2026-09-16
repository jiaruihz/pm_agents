# Current-YES carry forecast innovation v1

## 结论与动作

把 innovation 放进当前最接近可交易的 `current_yes_core_carry` 概率头，检验它相对同分母 native-lattice baseline 的 overshoot risk 增量。本轮是 research replay，不改 live。

## Feature contract

`innovation = latest source temp - expanding source/settlement basis - assigned-model temperature at the true decision local minute`。
模型当前温度由当时缓存的 GFS/ECMWF hourly curve 线性插值；assigned model 继续使用固定 CITY_MODEL。β 由 expanding OOF residual 学习。

## Coverage

```json
{
  "input_rows": 1349,
  "dates": 31,
  "cities": 36,
  "cache_files": 772,
  "model_temp_covered": 1349,
  "raw_innovation_covered": 1349,
  "basis_innovation_covered": 1349,
  "basis_prior_covered": 1349,
  "source_first_seen_limitation": "historical source report is latest report_ts<=decision archive PIT proxy; first-seen unavailable"
}
```

## Probability

| model | rows | city_days | dates | brier | logloss | brier_delta_vs_lattice | brier_delta_ci_low | brier_delta_ci_high | logloss_delta_vs_lattice | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| core | 909 | 538 | 23 | 0.07445 | 0.26433 | -0.00023 | -0.00060 | 0.00058 | -0.00073 | -0.00270 | 0.00216 |
| lattice | 909 | 538 | 23 | 0.07468 | 0.26506 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| lattice_plus_innovation | 909 | 538 | 23 | 0.07481 | 0.26564 | 0.00013 | -0.00033 | 0.00066 | 0.00058 | -0.00109 | 0.00256 |

## Five-share first-positive-EV replay

| model | opportunity_city_days | trades | dates | wins | capital | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| core | 538 | 89 | 22 | 86 | 405.95137 | 24.04863 | 0.05924 | 0.01331 | 0.09631 |
| lattice | 538 | 92 | 22 | 89 | 419.73235 | 25.26765 | 0.06020 | 0.01275 | 0.09794 |
| lattice_plus_innovation | 538 | 92 | 23 | 89 | 420.32597 | 24.67403 | 0.05870 | 0.01167 | 0.09525 |

## Candidate vs lattice paired daily PnL

```json
{
  "dates": 23,
  "candidate_pnl": 24.674031000000003,
  "baseline_pnl": 25.267652800000004,
  "pnl_delta": -0.5936218000000011,
  "date_mean_pnl_delta": -0.025809643478260917,
  "date_mean_delta_ci_low": -0.15044920217391317,
  "date_mean_delta_ci_high": 0.09854510869565204
}
```

## Three gates

- absolute fee-adjusted execution：PASS
- innovation probability/significance：FAIL
- paired baseline delta：FAIL
- fresh frozen forward：NA

```text
status=inconclusive
live_action=none
```

历史 source event 只有 `report_ts<=decision` 的 archive PIT proxy，缺 first-seen；即使两项历史门通过，也只能先进入 zero-notional forward。
