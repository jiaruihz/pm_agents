# Current-YES No-Reheat Segment Breakdown v1

Status: research-only
Generated: 2026-06-23T18:13:34+00:00

## 一句话结论

横切后有几个点估计不错的低价/中价 no-reheat 子段，但没有城市、城市族群或 market-repricing 维度同时通过日期 CI 和 same-segment baseline；现在更像 shadow research，而不是 city-pool 直接筛选。

## 数据范围

- Feature rows: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv`
- Feature target-date range: `2026-05-19`..`2026-06-20`
- Holdout: `2026-06-01`..`2026-06-20`
- Tradable holdout rows: 1575 rows / 20 dates / 36 cities

## 物理族群横切

| segment | state | bucket | rows | dates | cities | break | avg ask | ROI | CI | same-seg baseline | excess |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| city_family=humid_low_latitude | stalled_high_ge2obs | ask_35_50 | 29 | 15 | 12 | +44.8% | 0.412 | +34.0% | [-11.6%, +79.3%] | -0.5% | +34.5% |
| city_family=europe_cloud_break | all_current_yes_tradable | ask_35_50 | 30 | 15 | 5 | +50.0% | 0.415 | +20.3% | [-39.3%, +67.9%] | +20.3% | +0.0% |
| city_family=europe_cloud_break | all_current_yes_tradable | ask_50_70 | 34 | 16 | 5 | +35.3% | 0.604 | +7.1% | [-24.0%, +27.1%] | +7.1% | +0.0% |
| city_family=humid_low_latitude | strict_no_reheat_candidate | all_ask_35_97 | 36 | 17 | 11 | +11.1% | 0.833 | +6.7% | [-6.2%, +19.1%] | -1.0% | +7.7% |
| city_family=southern_or_maritime | fade_all_decline_ge_0_5 | ask_70_90 | 43 | 17 | 10 | +14.0% | 0.807 | +6.7% | [-24.5%, +25.7%] | -2.3% | +9.0% |
| city_family=humid_low_latitude | stalled_high_ge2obs | ask_50_70 | 37 | 17 | 12 | +35.1% | 0.613 | +5.7% | [-22.0%, +32.0%] | +0.6% | +5.2% |
| city_family=southern_or_maritime | all_current_yes_tradable | ask_50_70 | 93 | 19 | 10 | +36.6% | 0.603 | +5.1% | [-12.9%, +24.2%] | +5.1% | +0.0% |
| city_family=humid_low_latitude | stalled_no_warming | all_ask_35_97 | 147 | 20 | 13 | +22.4% | 0.760 | +2.0% | [-8.6%, +12.1%] | -1.0% | +3.0% |
| city_family=humid_low_latitude | fade_all_decline_ge_0_5 | all_ask_35_97 | 172 | 20 | 13 | +20.9% | 0.777 | +1.8% | [-11.2%, +11.2%] | -1.0% | +2.7% |
| city_family=southern_or_maritime | stalled_no_warming | ask_90_97 | 26 | 15 | 8 | +3.8% | 0.945 | +1.8% | [-7.3%, +6.6%] | -1.8% | +3.6% |
| city_family=southern_or_maritime | fade_all_decline_ge_0_5 | all_ask_35_97 | 92 | 19 | 10 | +18.5% | 0.802 | +1.6% | [-17.6%, +15.9%] | -0.9% | +2.5% |
| city_family=humid_low_latitude | stalled_no_warming | ask_90_97 | 39 | 16 | 12 | +5.1% | 0.936 | +1.4% | [-7.5%, +7.0%] | -0.4% | +1.8% |

## 城市横切

| segment | state | bucket | rows | dates | cities | break | avg ask | ROI | CI | same-seg baseline | excess |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| city=SanFrancisco | all_current_yes_tradable | all_ask_35_97 | 33 | 15 | 1 | +9.1% | 0.743 | +22.3% | [+3.0%, +34.7%] | +22.3% | +0.0% |
| city=Chongqing | all_current_yes_tradable | all_ask_35_97 | 53 | 18 | 1 | +11.3% | 0.725 | +22.2% | [+7.1%, +37.9%] | +22.2% | +0.0% |
| city=Shanghai | stalled_high_ge2obs | all_ask_35_97 | 25 | 10 | 1 | +4.0% | 0.797 | +20.4% | [-3.8%, +29.6%] | +4.0% | +16.4% |
| city=Manila | stalled_high_ge2obs | all_ask_35_97 | 26 | 12 | 1 | +15.4% | 0.703 | +20.4% | [-16.6%, +50.3%] | +18.7% | +1.7% |
| city=Manila | all_current_yes_tradable | all_ask_35_97 | 60 | 19 | 1 | +20.0% | 0.674 | +18.7% | [-8.0%, +43.0%] | +18.7% | +0.0% |
| city=TelAviv | all_current_yes_tradable | all_ask_35_97 | 41 | 18 | 1 | +17.1% | 0.701 | +18.2% | [-5.5%, +34.0%] | +18.2% | +0.0% |
| city=BuenosAires | all_current_yes_tradable | all_ask_35_97 | 44 | 17 | 1 | +15.9% | 0.725 | +16.0% | [-8.1%, +34.6%] | +16.0% | +0.0% |
| city=Houston | fade_all_decline_ge_0_5 | all_ask_35_97 | 25 | 11 | 1 | +8.0% | 0.798 | +15.3% | [-4.5%, +24.4%] | +0.3% | +15.0% |
| city=Warsaw | all_current_yes_tradable | all_ask_35_97 | 40 | 20 | 1 | +10.0% | 0.795 | +13.2% | [+1.0%, +23.8%] | +13.2% | +0.0% |
| city=Tokyo | all_current_yes_tradable | all_ask_35_97 | 37 | 16 | 1 | +18.9% | 0.751 | +8.0% | [-13.0%, +24.9%] | +8.0% | +0.0% |
| city=Shanghai | all_current_yes_tradable | all_ask_35_97 | 49 | 20 | 1 | +22.4% | 0.745 | +4.0% | [-23.2%, +20.9%] | +4.0% | +0.0% |
| city=Taipei | all_current_yes_tradable | all_ask_35_97 | 55 | 18 | 1 | +21.8% | 0.762 | +2.6% | [-23.6%, +24.1%] | +2.6% | +0.0% |
| city=Amsterdam | all_current_yes_tradable | all_ask_35_97 | 49 | 17 | 1 | +24.5% | 0.736 | +2.6% | [-24.3%, +24.7%] | +2.6% | +0.0% |
| city=Istanbul | stalled_no_warming | all_ask_35_97 | 29 | 12 | 1 | +17.2% | 0.807 | +2.5% | [-21.8%, +23.3%] | +0.0% | +2.5% |
| city=LA | all_current_yes_tradable | all_ask_35_97 | 42 | 16 | 1 | +19.0% | 0.796 | +1.7% | [-24.3%, +20.2%] | +1.7% | +0.0% |
| city=Helsinki | all_current_yes_tradable | all_ask_35_97 | 49 | 18 | 1 | +26.5% | 0.723 | +1.5% | [-25.4%, +23.2%] | +1.5% | +0.0% |

## 时间 / Forecast / Market Repricing 横切

| segment | state | bucket | rows | dates | cities | break | avg ask | ROI | CI | same-seg baseline | excess |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_reprice_bin=mid_unrepriced | stalled_no_warming | ask_50_70 | 36 | 15 | 18 | +27.8% | 0.600 | +20.4% | [-17.2%, +53.4%] | -3.5% | +23.9% |
| market_reprice_bin=mid_unrepriced | stalled_no_warming | all_ask_35_97 | 37 | 15 | 18 | +29.7% | 0.596 | +17.9% | [-19.5%, +52.0%] | -3.7% | +21.6% |
| market_reprice_bin=cheap_unrepriced | stalled_high_ge2obs | all_ask_35_97 | 70 | 20 | 30 | +52.9% | 0.420 | +12.3% | [-18.0%, +40.2%] | -1.2% | +13.5% |
| market_reprice_bin=cheap_unrepriced | stalled_high_ge2obs | ask_35_50 | 70 | 20 | 30 | +52.9% | 0.420 | +12.3% | [-18.0%, +40.2%] | -1.2% | +13.5% |
| market_reprice_bin=mid_unrepriced | fade_all_decline_ge_0_5 | all_ask_35_97 | 52 | 19 | 26 | +36.5% | 0.597 | +6.3% | [-27.1%, +32.3%] | -3.7% | +10.0% |
| market_reprice_bin=mid_unrepriced | fade_all_decline_ge_0_5 | ask_50_70 | 52 | 19 | 26 | +36.5% | 0.597 | +6.3% | [-27.1%, +32.3%] | -3.5% | +9.8% |
| market_reprice_bin=already_repriced_up | stalled_high_ge2obs | ask_90_97 | 86 | 20 | 31 | +1.2% | 0.939 | +5.2% | [+2.8%, +6.6%] | -1.5% | +6.8% |
| market_reprice_bin=already_repriced_up | stalled_no_warming | ask_90_97 | 78 | 20 | 27 | +1.3% | 0.940 | +5.0% | [+2.2%, +6.5%] | -1.5% | +6.5% |
| local_hour_bin=h10_13 | stalled_no_warming | ask_50_70 | 25 | 16 | 13 | +24.0% | 0.606 | +25.4% | [-7.2%, +52.9%] | +4.9% | +20.5% |
| local_hour_bin=h13_15 | all_current_yes_tradable | ask_35_50 | 60 | 20 | 21 | +51.7% | 0.430 | +12.3% | [-24.2%, +45.4%] | +12.3% | +0.0% |
| local_hour_bin=h10_13 | stalled_high_ge2obs | ask_35_50 | 49 | 19 | 25 | +53.1% | 0.422 | +11.2% | [-25.9%, +45.0%] | -5.3% | +16.5% |
| local_hour_bin=h13_15 | strict_no_reheat_candidate | all_ask_35_97 | 41 | 17 | 16 | +12.2% | 0.791 | +11.0% | [+1.2%, +20.4%] | -4.6% | +15.5% |
| local_hour_bin=h10_13 | stalled_high_ge2obs | ask_50_70 | 39 | 18 | 19 | +33.3% | 0.604 | +10.4% | [-16.9%, +34.8%] | +4.9% | +5.5% |
| forecast_gap_bin=forecast_near_or_below | stalled_high_ge2obs | ask_50_70 | 30 | 17 | 16 | +20.0% | 0.618 | +29.5% | [+3.5%, +53.1%] | +5.0% | +24.5% |
| forecast_gap_bin=forecast_near_or_below | all_current_yes_tradable | ask_35_50 | 79 | 19 | 24 | +49.4% | 0.426 | +19.0% | [-13.2%, +53.7%] | +19.0% | +0.0% |
| forecast_gap_bin=forecast_below_running_ge1 | fade_all_decline_ge_0_5 | all_ask_35_97 | 47 | 17 | 16 | +6.4% | 0.835 | +12.2% | [+0.2%, +21.9%] | -1.5% | +13.6% |
| forecast_gap_bin=forecast_above_gt2 | fade_all_decline_ge_0_5 | ask_70_90 | 29 | 15 | 11 | +13.8% | 0.798 | +8.1% | [-10.5%, +22.7%] | -10.1% | +18.2% |
| forecast_gap_bin=forecast_above_gt2 | all_current_yes_tradable | ask_50_70 | 65 | 19 | 19 | +35.4% | 0.601 | +7.5% | [-19.2%, +30.9%] | +7.5% | +0.0% |

## 解释

- 城市族群有差异，但没有出现能直接 live 的稳定族群：很多正 ROI 是低价样本给出的点估计，日期 CI 仍跨 0。
- 真正拖累不是“天气完全没信号”，而是 future-break hazard 下降得不够快；当 hazard 足够低时，YES ask 通常也已高。
- `cheap_unrepriced` / `mid_unrepriced` 是下一步该盯的 market-lag transition；`high` 或 `already_repriced_up` 更像已被盘口吃掉。

## Verdict

significance=FAIL / baseline=FAIL / forward=NA / conclusion=inconclusive

这些横切支持继续研究 no-reheat market-lag，但不支持只按城市或城市族群恢复真钱 current-YES peak/no-reheat。

## Outputs

- segment summary CSV: `docs/analysis/2026-06/generated/current_yes_no_reheat_segment_breakdown_v1/segment_summary.csv`
- json: `docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-segment-breakdown-v1.json`
