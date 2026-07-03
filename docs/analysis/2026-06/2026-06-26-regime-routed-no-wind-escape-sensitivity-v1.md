# Regime-Routed NO Wind/Escape Sensitivity V1

Generated: `2026-06-26T11:54:41+00:00`

## Verdict

This is a sensitivity replay, not a live approval.  It keeps the existing `routed_capped_d2_no_relaxed70_best_ask` denominator and asks whether wind/mixing risk and settlement-grid escape margin should be soft sizing features.

Main read: `windy_mixing_noise` means `wind_speed_kt >= 18`.  It is not bullish or bearish by itself; it makes near-integer forecast edges less reliable.  Therefore it should interact with settlement margin, not act as a standalone city/day veto.

## Same-Denominator Summary

| weight_policy | rows | dates | cities | exec_rows | exec_dates | cost_usd | pnl_usd | roi | exec_cost_usd | exec_pnl_usd | exec_roi | hit_rate | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_size | 271 | 35 | 35 | 271 | 35 | +1355.00 | +155.73 | +11.5% | +1355.00 | +155.73 | +11.5% | +51.7% | +100.0% |
| soft_balanced | 271 | 35 | 35 | 77 | 31 | +469.32 | +123.21 | +26.3% | +212.38 | +148.45 | +69.9% | +51.7% | +34.6% |
| soft_wind_only | 271 | 35 | 35 | 75 | 30 | +456.69 | +120.17 | +26.3% | +202.49 | +149.49 | +73.8% | +51.7% | +33.7% |
| soft_wind_escape | 271 | 35 | 35 | 25 | 18 | +271.00 | +62.15 | +22.9% | +53.03 | +63.33 | +119.4% | +51.7% | +20.0% |

## Wind Regime

| wind_regime | weight_policy | rows | dates | exec_rows | cost_usd | pnl_usd | roi | exec_cost_usd | exec_pnl_usd | exec_roi | hit_rate | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| light_wind | soft_balanced | 162 | 35 | 49 | +281.65 | +104.00 | +36.9% | +135.82 | +109.64 | +80.7% | +54.3% | +34.8% |
| light_wind | soft_wind_escape | 162 | 35 | 19 | +178.20 | +52.45 | +29.4% | +40.96 | +45.13 | +110.2% | +54.3% | +22.0% |
| light_wind | soft_wind_only | 162 | 35 | 49 | +281.65 | +104.00 | +36.9% | +135.82 | +109.64 | +80.7% | +54.3% | +34.8% |
| moderate_wind | soft_balanced | 97 | 33 | 25 | +166.05 | +5.32 | +3.2% | +66.79 | +28.02 | +42.0% | +44.3% | +34.2% |
| moderate_wind | soft_wind_escape | 97 | 33 | 6 | +83.83 | +7.05 | +8.4% | +12.07 | +18.20 | +150.7% | +44.3% | +17.3% |
| moderate_wind | soft_wind_only | 97 | 33 | 24 | +157.75 | +5.05 | +3.2% | +61.09 | +28.98 | +47.4% | +44.3% | +32.5% |
| windy_mixing_noise | soft_balanced | 12 | 9 | 3 | +21.62 | +13.90 | +64.3% | +9.77 | +10.78 | +110.3% | +75.0% | +36.0% |
| windy_mixing_noise | soft_wind_escape | 12 | 9 | 0 | +8.97 | +2.65 | +29.6% | +0.00 | +0.00 | NA | +75.0% | +14.9% |
| windy_mixing_noise | soft_wind_only | 12 | 9 | 2 | +17.29 | +11.12 | +64.3% | +5.58 | +10.86 | +194.6% | +75.0% | +28.8% |

## Settlement Margin Buckets

| margin_bucket | weight_policy | rows | dates | exec_rows | cost_usd | pnl_usd | roi | exec_cost_usd | exec_pnl_usd | exec_roi | hit_rate | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| -1..0F | soft_balanced | 44 | 24 | 21 | +94.53 | +36.85 | +39.0% | +61.66 | +44.68 | +72.5% | +45.5% | +43.0% |
| -1..0F | soft_wind_escape | 44 | 24 | 0 | +21.27 | +8.08 | +38.0% | +0.00 | +0.00 | NA | +45.5% | +9.7% |
| -1..0F | soft_wind_only | 44 | 24 | 20 | +91.56 | +34.52 | +37.7% | +57.01 | +44.46 | +78.0% | +45.5% | +41.6% |
| 0..1F | soft_balanced | 56 | 25 | 22 | +119.89 | +18.89 | +15.8% | +64.11 | +29.50 | +46.0% | +48.2% | +42.8% |
| 0..1F | soft_wind_escape | 56 | 25 | 2 | +42.81 | +4.28 | +10.0% | +2.54 | +4.97 | +195.7% | +48.2% | +15.3% |
| 0..1F | soft_wind_only | 56 | 25 | 21 | +115.94 | +19.22 | +16.6% | +60.27 | +31.90 | +52.9% | +48.2% | +41.4% |
| 1..2F | soft_balanced | 43 | 28 | 17 | +88.40 | +58.18 | +65.8% | +46.73 | +55.07 | +117.9% | +58.1% | +41.1% |
| 1..2F | soft_wind_escape | 43 | 28 | 12 | +68.44 | +41.11 | +60.1% | +26.19 | +39.86 | +152.2% | +58.1% | +31.8% |
| 1..2F | soft_wind_only | 43 | 28 | 17 | +86.93 | +56.75 | +65.3% | +45.76 | +53.50 | +116.9% | +58.1% | +40.4% |
| 2F+ | soft_balanced | 109 | 34 | 11 | +133.79 | +7.86 | +5.9% | +24.41 | +18.39 | +75.4% | +54.1% | +24.5% |
| 2F+ | soft_wind_escape | 109 | 34 | 11 | +131.09 | +8.10 | +6.2% | +24.30 | +18.50 | +76.1% | +54.1% | +24.1% |
| 2F+ | soft_wind_only | 109 | 34 | 11 | +130.33 | +8.01 | +6.1% | +24.30 | +18.50 | +76.1% | +54.1% | +23.9% |
| <-1F | soft_balanced | 19 | 17 | 6 | +32.70 | +1.43 | +4.4% | +15.48 | +0.80 | +5.2% | +47.4% | +34.4% |
| <-1F | soft_wind_escape | 19 | 17 | 0 | +7.39 | +0.59 | +8.0% | +0.00 | +0.00 | NA | +47.4% | +7.8% |
| <-1F | soft_wind_only | 19 | 17 | 6 | +31.92 | +1.66 | +5.2% | +15.15 | +1.13 | +7.5% | +47.4% | +33.6% |

## Boundary

- `exec_rows` is an approximation of the current live minimum-share rule: `$5 * weight / ask >= 5 shares`.
- The overlay is deliberately soft.  It does not delete all thin-margin trades; it reduces notional when the forecast is close to the payoff-relevant integer bucket, especially under high wind.
- This does not satisfy the full three-gate live standard.  It is a mechanism check to decide what to add to the live/replay feature builder next.
