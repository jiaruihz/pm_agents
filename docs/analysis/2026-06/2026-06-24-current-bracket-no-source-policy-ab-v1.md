# Current-Bracket NO Source Policy A/B V1

## 结论

这轮固定 payoff label、交易分母、ask/depth、first city-day selection 和阈值，单独比较 forecast source policy。
结果是：在当前可验证样本里，`forced_gfs` 明显优于 `preferred_route`；ECMWF/preferred routing 没有通过交易层验证。

但这不是 live promotion。非 GFS 历史仍不是 strict previous-day PIT，6/21..6/23 forward 对两种 policy 都失败。

结论等级：`inconclusive` / `shadow_only` / 不改 live。

## 数据层

- Generated at UTC: `2026-06-24T02:39:34+00:00`
- Historical rows: `4306`
- Common source rows: `499`
- Trade-base rows per policy: `499`
- Date range: `2026-05-20`..`2026-06-20`
- Split date: `2026-06-10`

## Policy Summary

| source_policy | variant | selected_trades | active_dates | cities | no_win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | selected_all_loss_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| preferred_route | baseline_trade_base | 375 | 32 | 35 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 4 |
| preferred_route | payoff_ev05_p35 | 245 | 32 | 31 | +26.9% | +20.1% | -6.5% | +46.5% | +27.7% | 5 |
| preferred_route | payoff_ev10_p40 | 225 | 32 | 31 | +28.0% | +25.6% | -0.7% | +52.4% | +29.9% | 5 |
| preferred_route | payoff_ev05_p35_max2_day | 62 | 32 | 24 | +38.7% | +102.1% | +40.6% | +169.7% | +136.7% | 12 |
| forced_gfs | baseline_trade_base | 375 | 32 | 35 | +20.5% | -8.4% | -27.1% | +10.5% | +7.0% | 4 |
| forced_gfs | payoff_ev05_p35 | 252 | 32 | 32 | +27.8% | +22.8% | -2.7% | +47.7% | +33.4% | 5 |
| forced_gfs | payoff_ev10_p40 | 211 | 32 | 32 | +31.3% | +38.4% | +9.5% | +67.2% | +53.9% | 5 |
| forced_gfs | payoff_ev05_p35_max2_day | 59 | 32 | 28 | +40.7% | +111.7% | +49.1% | +182.2% | +119.2% | 11 |

## Forward 6/21..6/23

| source_policy | variant | selected_trades | settled_trades | open_shadow_trades | settled_win_rate | settled_roi | settled_profit_usd | dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| preferred_route | baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| preferred_route | payoff_ev05_p35 | 38 | 31 | 7 | +16.1% | -46.4% | $-71.90 | 2026-06-21,2026-06-22,2026-06-23 |
| preferred_route | payoff_ev10_p40 | 36 | 29 | 7 | +13.8% | -53.1% | $-77.05 | 2026-06-21,2026-06-22,2026-06-23 |
| preferred_route | payoff_ev05_p35_max2_day | 6 | 4 | 2 | +0.0% | -100.0% | $-20.00 | 2026-06-21,2026-06-22,2026-06-23 |
| forced_gfs | baseline_trade_base | 45 | 36 | 9 | +13.9% | -53.8% | $-96.90 | 2026-06-21,2026-06-22,2026-06-23 |
| forced_gfs | payoff_ev05_p35 | 33 | 26 | 7 | +11.5% | -60.1% | $-78.18 | 2026-06-21,2026-06-22,2026-06-23 |
| forced_gfs | payoff_ev10_p40 | 29 | 22 | 7 | +9.1% | -71.1% | $-78.18 | 2026-06-21,2026-06-22,2026-06-23 |
| forced_gfs | payoff_ev05_p35_max2_day | 6 | 4 | 2 | +0.0% | -100.0% | $-20.00 | 2026-06-21,2026-06-22,2026-06-23 |

## City Detail: payoff_ev05_p35

| source_policy | city | forecast_route_model | calibration_best_model | trades | dates | win_rate | roi | avg_p_no_win | avg_margin |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forced_gfs | BuenosAires | gfs | ecmwf | 4 | 4 | +0.0% | -100.0% | 0.421 | -0.056 |
| forced_gfs | Chongqing | gfs | ecmwf | 1 | 1 | +0.0% | -100.0% | 0.562 | 0.222 |
| forced_gfs | Houston | gfs | gfs | 3 | 3 | +0.0% | -100.0% | 0.514 | -1.000 |
| forced_gfs | Munich | gfs | icon_eu | 1 | 1 | +0.0% | -100.0% | 0.591 | -0.222 |
| forced_gfs | Warsaw | gfs | icon_eu | 6 | 6 | +0.0% | -100.0% | 0.508 | 0.019 |
| forced_gfs | SaoPaulo | gfs | ecmwf | 6 | 6 | +16.7% | -44.4% | 0.516 | 0.185 |
| forced_gfs | Manila | gfs | gfs | 13 | 13 | +23.1% | -20.1% | 0.560 | 0.256 |
| forced_gfs | TelAviv | gfs | gfs | 18 | 18 | +16.7% | -17.9% | 0.547 | 0.198 |
| forced_gfs | Jeddah | gfs | ecmwf | 4 | 4 | +25.0% | -16.7% | 0.457 | 0.639 |
| forced_gfs | Lucknow | gfs | ecmwf | 8 | 8 | +12.5% | -16.7% | 0.458 | 0.208 |
| forced_gfs | Busan | gfs | jma | 15 | 15 | +20.0% | -11.3% | 0.489 | 0.104 |
| forced_gfs | LA | gfs |  | 21 | 21 | +23.8% | -10.4% | 0.615 | 0.048 |
| forced_gfs | Wuhan | gfs | ecmwf | 10 | 10 | +20.0% | -8.3% | 0.541 | 0.211 |
| forced_gfs | Taipei | gfs | gfs | 12 | 12 | +25.0% | -5.1% | 0.528 | 0.278 |
| forced_gfs | Wellington | gfs | gfs | 13 | 13 | +15.4% | -3.5% | 0.516 | 0.197 |
| forced_gfs | Shanghai | gfs | gfs | 18 | 18 | +22.2% | -2.6% | 0.477 | 0.210 |
| forced_gfs | Tokyo | gfs | gfs | 10 | 10 | +20.0% | -1.8% | 0.590 | 0.278 |
| forced_gfs | Singapore | gfs | gfs | 15 | 15 | +33.3% | +8.6% | 0.549 | 0.348 |
| forced_gfs | CapeTown | gfs | ecmwf | 8 | 8 | +25.0% | +14.9% | 0.501 | 0.208 |
| forced_gfs | Karachi | gfs | ecmwf | 2 | 2 | +50.0% | +51.5% | 0.408 | 0.611 |
| forced_gfs | Amsterdam | gfs | icon_eu | 6 | 6 | +50.0% | +59.0% | 0.567 | 0.444 |
| forced_gfs | NYC | gfs | gfs | 6 | 6 | +33.3% | +70.6% | 0.660 | 0.333 |
| forced_gfs | Helsinki | gfs | icon_eu | 9 | 9 | +33.3% | +71.5% | 0.512 | 0.309 |
| forced_gfs | Miami | gfs | gfs | 8 | 8 | +37.5% | +104.2% | 0.483 | -0.125 |
| forced_gfs | Atlanta | gfs | gfs | 6 | 6 | +50.0% | +114.6% | 0.613 | 0.667 |
| forced_gfs | Guangzhou | gfs | gfs | 10 | 10 | +60.0% | +142.7% | 0.617 | 0.600 |
| forced_gfs | SanFrancisco | gfs | ecmwf | 4 | 4 | +50.0% | +143.1% | 0.458 | 0.750 |
| forced_gfs | Beijing | gfs | ecmwf | 6 | 6 | +66.7% | +196.4% | 0.649 | 0.630 |
| forced_gfs | Seattle | gfs | gfs | 3 | 3 | +66.7% | +215.6% | 0.665 | 1.000 |
| forced_gfs | Denver | gfs | gfs | 1 | 1 | +100.0% | +257.1% | 0.898 | 3.000 |
| forced_gfs | Ankara | gfs | icon_eu | 4 | 4 | +50.0% | +282.3% | 0.546 | 0.722 |
| forced_gfs | Austin | gfs | gfs | 1 | 1 | +100.0% | +426.3% | 0.843 | 1.000 |
| preferred_route | BuenosAires | ecmwf | ecmwf | 3 | 3 | +0.0% | -100.0% | 0.432 | -0.037 |
| preferred_route | Chengdu | ecmwf | ecmwf | 1 | 1 | +0.0% | -100.0% | 0.534 | 0.000 |
| preferred_route | Chongqing | ecmwf | ecmwf | 2 | 2 | +0.0% | -100.0% | 0.511 | 0.056 |
| preferred_route | Houston | gfs | gfs | 2 | 2 | +0.0% | -100.0% | 0.437 | -1.000 |
| preferred_route | Warsaw | ecmwf | icon_eu | 6 | 6 | +0.0% | -100.0% | 0.459 | 0.019 |
| preferred_route | Wuhan | ecmwf | ecmwf | 8 | 8 | +0.0% | -100.0% | 0.530 | 0.014 |
| preferred_route | SaoPaulo | ecmwf | ecmwf | 6 | 6 | +16.7% | -44.4% | 0.484 | 0.185 |
| preferred_route | Taipei | gfs | gfs | 17 | 17 | +17.6% | -33.0% | 0.521 | 0.203 |
| preferred_route | Manila | gfs | gfs | 13 | 13 | +23.1% | -20.1% | 0.548 | 0.256 |
| preferred_route | TelAviv | gfs | gfs | 18 | 18 | +16.7% | -17.9% | 0.532 | 0.198 |
| preferred_route | Lucknow | ecmwf | ecmwf | 8 | 8 | +12.5% | -16.7% | 0.525 | 0.208 |
| preferred_route | Busan | ecmwf | jma | 15 | 15 | +20.0% | -11.3% | 0.483 | 0.104 |
| preferred_route | LA | gfs |  | 21 | 21 | +23.8% | -10.4% | 0.616 | 0.048 |
| preferred_route | Tokyo | gfs | gfs | 10 | 10 | +20.0% | -1.8% | 0.590 | 0.244 |
| preferred_route | Singapore | gfs | gfs | 15 | 15 | +33.3% | +8.6% | 0.566 | 0.348 |
| preferred_route | Shanghai | gfs | gfs | 16 | 16 | +25.0% | +9.6% | 0.505 | 0.236 |
| preferred_route | Jeddah | ecmwf | ecmwf | 3 | 3 | +33.3% | +11.1% | 0.467 | 0.815 |
| preferred_route | Wellington | gfs | gfs | 11 | 11 | +18.2% | +14.1% | 0.557 | 0.232 |
| preferred_route | CapeTown | ecmwf | ecmwf | 8 | 8 | +25.0% | +14.9% | 0.502 | 0.208 |
| preferred_route | Helsinki | gfs | icon_eu | 10 | 10 | +30.0% | +54.4% | 0.540 | 0.289 |
| preferred_route | Amsterdam | gfs | icon_eu | 6 | 6 | +50.0% | +59.0% | 0.612 | 0.444 |
| preferred_route | Miami | gfs | gfs | 7 | 7 | +28.6% | +91.3% | 0.494 | -0.286 |
| preferred_route | NYC | gfs | gfs | 5 | 5 | +40.0% | +104.8% | 0.723 | 0.400 |
| preferred_route | Guangzhou | gfs | gfs | 10 | 10 | +60.0% | +142.7% | 0.611 | 0.600 |
| preferred_route | SanFrancisco | ecmwf | ecmwf | 4 | 4 | +50.0% | +143.1% | 0.449 | 0.750 |
| preferred_route | Beijing | ecmwf | ecmwf | 6 | 6 | +66.7% | +196.4% | 0.595 | 0.630 |
| preferred_route | Ankara | gfs | icon_eu | 5 | 5 | +40.0% | +205.8% | 0.593 | 0.533 |
| preferred_route | Seattle | gfs | gfs | 3 | 3 | +66.7% | +215.6% | 0.636 | 1.000 |
| preferred_route | Atlanta | gfs | gfs | 4 | 4 | +75.0% | +222.0% | 0.662 | 1.000 |
| preferred_route | Denver | gfs | gfs | 1 | 1 | +100.0% | +257.1% | 0.927 | 3.000 |
| preferred_route | Austin | gfs | gfs | 1 | 1 | +100.0% | +426.3% | 0.803 | 1.000 |

## 读法

1. `forced_gfs` 不是新 live rule，只是源策略反事实：同一批 rows 如果不用 ECMWF/preferred routing，会怎样。
2. preferred-route 的坏处集中在 ECMWF/fallback-ECMWF 城市，但不是所有 ECMWF 城市都坏；所以不能写成 ECMWF 永久黑名单。
3. 由于 forward 两边都没过，当前动作只能是：这个 current-bracket NO 表达先锁在 shadow/research，下一步补 day-regime classifier 和真实非 GFS PIT。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_source_policy_ab_v1/summary.json`
- Policy summary: `docs/analysis/2026-06/generated/current_bracket_no_source_policy_ab_v1/policy_summary.csv`
- City summary: `docs/analysis/2026-06/generated/current_bracket_no_source_policy_ab_v1/city_policy_summary.csv`
- Forward summary: `docs/analysis/2026-06/generated/current_bracket_no_source_policy_ab_v1/forward_policy_summary.csv`
