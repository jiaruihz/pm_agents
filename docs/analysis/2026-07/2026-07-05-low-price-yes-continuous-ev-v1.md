# Low-Price YES Continuous EV v1

Generated: 2026-07-04T17:19:00Z

## Verdict

```text
significance=FAIL
baseline=FAIL
forward=NA (diagnostic on existing HeadA denominator; no live action)
conclusion=inconclusive
```

人话结论：连续 station-bias / adjusted-distance 现在只能当解释层，不能替代 live 选择器。
这版同票数 A/B 没有证明它比现行 `dist>0` 更好；它能帮我们看清哪些票更像真钱 tail，
但还不能拿来调仓或加新 gate。

## Data Snapshot

- Denominator: current HeadA edge>=0.20, ask 5-20c, settled binary rows from integrated-tail/refinement layer.
- Rows / dates / cities: 476 / 53 / 48.
- Target dates: 2026-05-06 .. 2026-06-30; train <= 2026-06-20; recent >= 2026-06-21.
- Main replay: `price_tier_6_8_10_shares`, official Weather taker fee, hold-to-settlement.

## Same-Count A/B

- Threshold rule: pick the same number of train tickets as current `dist>0`; theta=+0.0189.
- Train excess continuous vs current: +1.4% CI [-14.4%, +18.4%].
- Recent excess continuous vs current: -32.0% CI [-88.9%, +11.2%].

| label | period | rows | dates | cities | win_rate | avg_entry | avg_cost | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| current_dist_gt0 | full | 333 | 53 | 47 | 0.150 | 0.104 | 0.885 | 0.418 | 0.107 | 0.763 | 19 | 16 | -11.038 |
| current_dist_gt0 | train_le_2026_06_20 | 275 | 44 | 46 | 0.145 | 0.105 | 0.899 | 0.351 | -0.001 | 0.764 | 18 | 15 | -11.038 |
| current_dist_gt0 | recent_ge_2026_06_21 | 58 | 9 | 28 | 0.172 | 0.099 | 0.820 | 0.767 | 0.317 | 1.339 | 1 | 1 | -4.264 |
| continuous_same_count | full | 343 | 53 | 48 | 0.155 | 0.110 | 0.966 | 0.383 | 0.033 | 0.776 | 23 | 18 | -12.455 |
| continuous_same_count | train_le_2026_06_20 | 275 | 44 | 48 | 0.153 | 0.109 | 0.953 | 0.366 | -0.056 | 0.874 | 21 | 17 | -12.455 |
| continuous_same_count | recent_ge_2026_06_21 | 68 | 9 | 32 | 0.162 | 0.114 | 1.016 | 0.448 | 0.104 | 0.811 | 2 | 1 | -1.512 |
| continuous_only | full | 48 | 31 | 14 | 0.146 | 0.137 | 1.307 | 0.052 | -0.648 | 0.831 | 25 | 25 | -5.639 |
| continuous_only | train_le_2026_06_20 | 32 | 23 | 13 | 0.125 | 0.128 | 1.198 | -0.009 | -1.000 | 1.098 | 20 | 20 | -5.639 |
| continuous_only | recent_ge_2026_06_21 | 16 | 8 | 10 | 0.188 | 0.154 | 1.526 | 0.147 | -1.000 | 1.192 | 5 | 5 | -3.954 |
| dist_only | full | 38 | 26 | 15 | 0.105 | 0.089 | 0.690 | -0.009 | -0.798 | 1.212 | 22 | 22 | -3.424 |
| dist_only | train_le_2026_06_20 | 32 | 21 | 13 | 0.062 | 0.093 | 0.730 | -0.401 | -1.000 | 0.641 | 19 | 19 | -3.424 |
| dist_only | recent_ge_2026_06_21 | 6 | 5 | 6 | 0.333 | 0.071 | 0.478 | 3.184 | -1.000 | 8.712 | 3 | 3 | -0.711 |

## Calibration Shape

- Train decile Spearman: +0.837.

| decile | rows | win_rate | avg_p | avg_ask | avg_raw_dist | avg_adj_dist |
|---|---|---|---|---|---|---|
| 0.000 | 39.000 | 0.051 | 0.071 | 0.063 | -0.300 | -0.664 |
| 1.000 | 38.000 | 0.079 | 0.086 | 0.072 | -0.050 | -0.465 |
| 2.000 | 38.000 | 0.079 | 0.102 | 0.077 | 0.317 | -0.252 |
| 3.000 | 38.000 | 0.053 | 0.118 | 0.083 | 0.447 | -0.102 |
| 4.000 | 39.000 | 0.077 | 0.135 | 0.087 | 0.775 | 0.108 |
| 5.000 | 38.000 | 0.158 | 0.151 | 0.100 | 0.668 | 0.190 |
| 6.000 | 38.000 | 0.079 | 0.166 | 0.114 | 0.527 | 0.062 |
| 7.000 | 38.000 | 0.211 | 0.186 | 0.132 | 0.467 | 0.017 |
| 8.000 | 38.000 | 0.211 | 0.227 | 0.144 | 0.771 | 0.186 |
| 9.000 | 39.000 | 0.308 | 0.295 | 0.175 | 0.837 | 0.183 |

## Mechanism Buckets

| group | bucket | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
|---|---|---|---|---|---|---|---|---|
| raw_dist_band | 0..0.25 | 49 | 33 | 0.122 | 0.107 | 0.109 | -0.656 | 1.045 |
| raw_dist_band | 0.25..0.5 | 64 | 31 | 0.156 | 0.106 | 0.467 | -0.205 | 1.152 |
| raw_dist_band | 0.5..1 | 111 | 47 | 0.153 | 0.107 | 0.443 | -0.096 | 1.003 |
| raw_dist_band | <=0 | 143 | 50 | 0.105 | 0.107 | -0.073 | -0.558 | 0.450 |
| raw_dist_band | >1 | 109 | 46 | 0.156 | 0.098 | 0.515 | -0.136 | 1.196 |
| adj_dist_band | -0.5..0 | 171 | 49 | 0.140 | 0.112 | 0.226 | -0.274 | 0.737 |
| adj_dist_band | 0..0.5 | 135 | 49 | 0.170 | 0.114 | 0.556 | -0.036 | 1.243 |
| adj_dist_band | 0.5..1 | 55 | 33 | 0.127 | 0.087 | 0.294 | -0.524 | 1.393 |
| adj_dist_band | <-0.5 | 109 | 45 | 0.083 | 0.094 | -0.240 | -0.687 | 0.310 |
| adj_dist_band | >1 | 6 | 6 | 0.333 | 0.072 | 3.098 | -1.000 | 9.003 |
| price_band | 14-20c | 102 | 44 | 0.255 | 0.169 | 0.447 | -0.055 | 1.016 |
| price_band | 5-8c | 184 | 52 | 0.082 | 0.066 | 0.188 | -0.316 | 0.734 |
| price_band | 8-14c | 190 | 52 | 0.126 | 0.109 | 0.110 | -0.300 | 0.535 |
| book_state_v1 | feasible | 296 | 41 | 0.108 | 0.100 | 0.058 | -0.252 | 0.385 |
| book_state_v1 | missing | 91 | 19 | 0.198 | 0.109 | 0.748 | -0.128 | 1.691 |
| book_state_v1 | thin_wide | 89 | 34 | 0.169 | 0.117 | 0.370 | -0.187 | 1.022 |

## Action

- Do not change HeadA live selector from this result.
- Keep current `dist>0 + price_tier_6_8_10_shares + maker-first + hold` forward probe.
- Use this score as telemetry only; the useful next research is partial-fill/queue modeling, because current forward pain is more fill/maker-rate than probability ranking.
