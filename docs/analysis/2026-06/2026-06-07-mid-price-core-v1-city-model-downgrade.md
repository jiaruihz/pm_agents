# mid_price_core_v1 city × model 降级清单

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`。
- DB mtime UTC：`2026-06-07T05:49:19.463464+00:00`；`MAX(fact_built_at_utc)`：`2026-06-07T05:48:57.787507+00:00`。
- 分母：`strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `execution_policy=mid_price_core_v1` / `entry_price_window=0.25-0.75` / `trade_class=live_real` / `settlement_status=settled`。
- PnL 只读 `fact_trades.pnl_usd_at_fill`；candidate 表只作机会 mix 参考。

trade_class：
| trade_class | rows |
| --- | --- |
| live_real | 1302 |
| live_simulated | 1077 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status：
| settlement_status | rows |
| --- | --- |
| [NULL] | 388 |
| settled | 4912 |

candidate coverage：
| rows | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23299 | 7567 | 2795 | 503 |

## 结论

- 6 月后不是简单“ECMWF 全坏 / GFS 全好”。ECMWF 总体是拖累，但有城市/side/model 交互；GFS 总体为正，但 NYC、LA 等局部组合仍亏。
- 明确 ECMWF 降级候选：BuenosAires, Munich, Jeddah, Karachi, Moscow, Ankara。其中 `BuenosAires, Munich, Jeddah, Karachi, Moscow, Ankara` post 期没有足够 GFS 对照，不能说 keep GFS，只能先把 ECMWF/city 组合 shadow。
- 全城市/全模型降级候选：none。
- 可保留小 size live 的城市：Tokyo, Miami, Shanghai, Madrid, LA。
- GFS 单独降级候选：NYC。
- 最重要的交易含义：ECMWF 门槛应按 city 调整；不要对所有城市一刀切，也不要因为 GFS 总体为正就放过 GFS 的弱 city-side 组合。

## 1. 城市 × 模型 action rollup

| city | post_dominant_model_by_cost | dominant_cost_share | pre_fills | pre_pnl | pre_roi | post_fills | post_pnl | post_roi | post_ecmwf_fills | post_ecmwf_pnl | post_gfs_fills | post_gfs_pnl | action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BuenosAires | ecmwf | 100.0% | 13 | -3.06 | -8.7% | 14 | -28.59 | -63.6% | 14 | -28.59 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| Amsterdam | ecmwf | 100.0% | 11 | +8.60 | 28.9% | 7 | -19.92 | -100.0% | 7 | -19.92 | 0 | +0.00 | sample_insufficient_shadow |
| Munich | ecmwf | 100.0% | 8 | -16.85 | -67.6% | 15 | -18.05 | -52.0% | 15 | -18.05 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| Jeddah | ecmwf | 100.0% | 11 | -3.42 | -13.7% | 18 | -17.81 | -45.0% | 18 | -17.81 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| Karachi | ecmwf | 100.0% | 18 | -1.86 | -3.5% | 16 | -14.96 | -37.7% | 16 | -14.96 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| NYC | gfs | 100.0% | 35 | -18.92 | -21.3% | 22 | -13.27 | -25.0% | 0 | +0.00 | 22 | -13.27 | downgrade_gfs_keep_ecmwf |
| Moscow | ecmwf | 100.0% | 10 | -6.78 | -20.5% | 17 | -11.71 | -23.2% | 17 | -11.71 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| Lucknow | ecmwf | 100.0% | 3 | -1.61 | -16.7% | 4 | -10.00 | -100.0% | 4 | -10.00 | 0 | +0.00 | sample_insufficient_shadow |
| Ankara | ecmwf | 100.0% | 15 | -9.95 | -22.8% | 15 | -8.02 | -20.4% | 15 | -8.02 | 0 | +0.00 | downgrade_ecmwf_city_shadow |
| Istanbul | ecmwf | 100.0% | 10 | +8.13 | 24.8% | 7 | -4.89 | -25.0% | 7 | -4.89 | 0 | +0.00 | shadow_or_min_size |
| Chengdu | ecmwf | 78.6% | 4 | +5.74 | 38.3% | 8 | -4.50 | -19.3% | 7 | +0.49 | 1 | -5.00 | shadow_or_min_size |
| Guangzhou | gfs | 100.0% | 7 | -1.69 | -7.2% | 8 | -3.35 | -10.0% | 0 | +0.00 | 8 | -3.35 | shadow_or_min_size |
| Singapore | gfs | 100.0% | 6 | -4.38 | -21.9% | 7 | -2.95 | -29.5% | 0 | +0.00 | 7 | -2.95 | sample_insufficient_shadow |
| London | ecmwf | 100.0% | 33 | +24.82 | 24.4% | 16 | -2.70 | -5.8% | 16 | -2.70 | 0 | +0.00 | shadow_or_min_size |
| Seattle | gfs | 100.0% | 12 | +22.54 | 84.0% | 3 | -1.80 | -18.1% | 0 | +0.00 | 3 | -1.80 | sample_insufficient_shadow |
| Manila | gfs | 100.0% | 10 | -17.99 | -72.7% | 13 | -0.91 | -2.3% | 0 | +0.00 | 13 | -0.91 | shadow_or_min_size |
| Warsaw | ecmwf | 100.0% | 34 | +44.05 | 56.8% | 15 | -0.89 | -2.5% | 15 | -0.89 | 0 | +0.00 | shadow_or_min_size |
| Austin |  |  | 16 | +3.58 | 7.2% | 0 | +0.00 |  | 0 | +0.00 | 0 | +0.00 | sample_insufficient_shadow |
| Beijing |  |  | 13 | -17.08 | -44.0% | 0 | +0.00 |  | 0 | +0.00 | 0 | +0.00 | sample_insufficient_shadow |
| Chicago |  |  | 4 | -0.70 | -4.7% | 0 | +0.00 |  | 0 | +0.00 | 0 | +0.00 | sample_insufficient_shadow |
| Paris |  |  | 27 | +7.34 | 9.3% | 0 | +0.00 |  | 0 | +0.00 | 0 | +0.00 | sample_insufficient_shadow |
| Tokyo | gfs | 100.0% | 31 | +24.56 | 29.7% | 8 | +8.70 | 44.6% | 0 | +0.00 | 8 | +8.70 | keep_small_live |
| Miami | gfs | 100.0% | 41 | +16.92 | 15.3% | 21 | +12.51 | 29.2% | 0 | +0.00 | 21 | +12.51 | keep_small_live |
| Shanghai | gfs | 100.0% | 8 | -0.26 | -0.9% | 10 | +12.54 | 36.7% | 0 | +0.00 | 10 | +12.54 | keep_small_live |
| Madrid | ecmwf | 100.0% | 13 | +8.07 | 18.5% | 14 | +17.71 | 45.1% | 14 | +17.71 | 0 | +0.00 | keep_small_live |
| LA | gfs | 100.0% | 37 | +29.27 | 30.1% | 19 | +23.80 | 44.6% | 0 | +0.00 | 19 | +23.80 | keep_small_live |

## 2. 6 月后 city-model 亏损排行

| period | city | model_version | fills | days | cost_usd | pnl_usd | roi | win_rate | fill_share | cost_share | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BuenosAires | ecmwf | 14 | 5 | 44.97 | -28.59 | -63.6% | 14.3% | 100.0% | 100.0% | 0.146 |
| post_2026_06_01 | Amsterdam | ecmwf | 7 | 2 | 19.92 | -19.92 | -100.0% | 0.0% | 100.0% | 100.0% | 0.218 |
| post_2026_06_01 | Munich | ecmwf | 15 | 4 | 34.69 | -18.05 | -52.0% | 26.7% | 100.0% | 100.0% | 0.171 |
| post_2026_06_01 | Jeddah | ecmwf | 18 | 5 | 39.62 | -17.81 | -45.0% | 33.3% | 100.0% | 100.0% | 0.196 |
| post_2026_06_01 | Karachi | ecmwf | 16 | 5 | 39.73 | -14.96 | -37.7% | 43.8% | 100.0% | 100.0% | 0.165 |
| post_2026_06_01 | NYC | gfs | 22 | 5 | 53.04 | -13.27 | -25.0% | 31.8% | 100.0% | 100.0% | 0.168 |
| post_2026_06_01 | Moscow | ecmwf | 17 | 5 | 50.40 | -11.71 | -23.2% | 35.3% | 100.0% | 100.0% | 0.194 |
| post_2026_06_01 | Lucknow | ecmwf | 4 | 2 | 10.00 | -10.00 | -100.0% | 0.0% | 100.0% | 100.0% | 0.307 |
| post_2026_06_01 | Ankara | ecmwf | 15 | 5 | 39.41 | -8.02 | -20.4% | 33.3% | 100.0% | 100.0% | 0.237 |
| post_2026_06_01 | Chengdu | gfs | 1 | 1 | 5.00 | -5.00 | -100.0% | 0.0% | 12.5% | 21.4% | 0.223 |
| post_2026_06_01 | Istanbul | ecmwf | 7 | 3 | 19.59 | -4.89 | -25.0% | 28.6% | 100.0% | 100.0% | 0.138 |
| post_2026_06_01 | Guangzhou | gfs | 8 | 5 | 33.48 | -3.35 | -10.0% | 62.5% | 100.0% | 100.0% | 0.158 |
| post_2026_06_01 | Singapore | gfs | 7 | 1 | 9.99 | -2.95 | -29.5% | 28.6% | 100.0% | 100.0% | 0.132 |
| post_2026_06_01 | London | ecmwf | 16 | 5 | 46.84 | -2.70 | -5.8% | 43.8% | 100.0% | 100.0% | 0.167 |
| post_2026_06_01 | Seattle | gfs | 3 | 1 | 9.99 | -1.80 | -18.1% | 66.7% | 100.0% | 100.0% | 0.288 |
| post_2026_06_01 | Manila | gfs | 13 | 4 | 38.91 | -0.91 | -2.3% | 61.5% | 100.0% | 100.0% | 0.182 |
| post_2026_06_01 | Warsaw | ecmwf | 15 | 5 | 35.37 | -0.89 | -2.5% | 46.7% | 100.0% | 100.0% | 0.167 |
| post_2026_06_01 | Chengdu | ecmwf | 7 | 3 | 18.30 | 0.49 | 2.7% | 57.1% | 87.5% | 78.6% | 0.218 |
| post_2026_06_01 | Tokyo | gfs | 8 | 4 | 19.50 | 8.70 | 44.6% | 75.0% | 100.0% | 100.0% | 0.266 |
| post_2026_06_01 | Miami | gfs | 21 | 4 | 42.88 | 12.51 | 29.2% | 57.1% | 100.0% | 100.0% | 0.281 |
| post_2026_06_01 | Shanghai | gfs | 10 | 4 | 34.22 | 12.54 | 36.7% | 80.0% | 100.0% | 100.0% | 0.174 |
| post_2026_06_01 | Madrid | ecmwf | 14 | 5 | 39.27 | 17.71 | 45.1% | 71.4% | 100.0% | 100.0% | 0.168 |
| post_2026_06_01 | LA | gfs | 19 | 5 | 53.43 | 23.80 | 44.6% | 57.9% | 100.0% | 100.0% | 0.292 |

## 3. city-model pre/post 转弱清单

| city | model_version | pre_fills | pre_pnl | pre_roi | post_fills | post_pnl | post_roi | delta_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Warsaw | ecmwf | 34 | +44.05 | 56.8% | 15 | -0.89 | -2.5% | -44.93 |
| Amsterdam | ecmwf | 11 | +8.60 | 28.9% | 7 | -19.92 | -100.0% | -28.52 |
| London | ecmwf | 33 | +24.82 | 24.4% | 16 | -2.70 | -5.8% | -27.52 |
| BuenosAires | ecmwf | 13 | -3.06 | -8.7% | 14 | -28.59 | -63.6% | -25.53 |
| Seattle | gfs | 12 | +22.54 | 84.0% | 3 | -1.80 | -18.1% | -24.35 |
| Tokyo | gfs | 31 | +24.56 | 29.7% | 8 | +8.70 | 44.6% | -15.86 |
| Jeddah | ecmwf | 11 | -3.42 | -13.7% | 18 | -17.81 | -45.0% | -14.40 |
| Karachi | ecmwf | 18 | -1.86 | -3.5% | 16 | -14.96 | -37.7% | -13.11 |
| Istanbul | ecmwf | 10 | +8.13 | 24.8% | 7 | -4.89 | -25.0% | -13.02 |
| Lucknow | ecmwf | 3 | -1.61 | -16.7% | 4 | -10.00 | -100.0% | -8.38 |
| Paris | gfs | 27 | +7.34 | 9.3% | 0 | +0.00 |  | -7.34 |
| LA | gfs | 37 | +29.27 | 30.1% | 19 | +23.80 | 44.6% | -5.46 |
| Chengdu | ecmwf | 4 | +5.74 | 38.3% | 7 | +0.49 | 2.7% | -5.25 |
| Moscow | ecmwf | 10 | -6.78 | -20.5% | 17 | -11.71 | -23.2% | -4.92 |
| Miami | gfs | 41 | +16.92 | 15.3% | 21 | +12.51 | 29.2% | -4.41 |
| Austin | gfs | 16 | +3.58 | 7.2% | 0 | +0.00 |  | -3.58 |
| Guangzhou | gfs | 7 | -1.69 | -7.2% | 8 | -3.35 | -10.0% | -1.66 |
| Munich | ecmwf | 8 | -16.85 | -67.6% | 15 | -18.05 | -52.0% | -1.20 |
| Singapore | gfs | 6 | -4.38 | -21.9% | 7 | -2.95 | -29.5% | +1.44 |
| Ankara | ecmwf | 15 | -9.95 | -22.8% | 15 | -8.02 | -20.4% | +1.93 |
| NYC | gfs | 35 | -18.92 | -21.3% | 22 | -13.27 | -25.0% | +5.65 |
| Madrid | ecmwf | 13 | +8.07 | 18.5% | 14 | +17.71 | 45.1% | +9.64 |
| Shanghai | gfs | 8 | -0.26 | -0.9% | 10 | +12.54 | 36.7% | +12.80 |
| Beijing | ecmwf | 13 | -17.08 | -44.0% | 0 | +0.00 |  | +17.08 |
| Manila | gfs | 10 | -17.99 | -72.7% | 13 | -0.91 | -2.3% | +17.08 |

## 4. 6 月后 city-model-side 亏损组合

| period | city | model_version | side | fills | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BuenosAires | ecmwf | BUY_YES | 5 | 15.00 | -15.00 | -100.0% | 0.0% | 0.117 |
| post_2026_06_01 | BuenosAires | ecmwf | BUY_NO | 9 | 29.97 | -13.59 | -45.3% | 22.2% | 0.162 |
| post_2026_06_01 | NYC | gfs | BUY_YES | 14 | 23.64 | -13.55 | -57.3% | 7.1% | 0.155 |
| post_2026_06_01 | Munich | ecmwf | BUY_NO | 13 | 29.89 | -13.25 | -44.3% | 30.8% | 0.172 |
| post_2026_06_01 | Warsaw | ecmwf | BUY_YES | 10 | 17.01 | -10.08 | -59.2% | 20.0% | 0.128 |
| post_2026_06_01 | Jeddah | ecmwf | BUY_YES | 9 | 10.00 | -10.00 | -100.0% | 0.0% | 0.210 |
| post_2026_06_01 | Amsterdam | ecmwf | BUY_YES | 3 | 10.00 | -10.00 | -100.0% | 0.0% | 0.189 |
| post_2026_06_01 | Moscow | ecmwf | BUY_YES | 8 | 9.99 | -9.99 | -100.0% | 0.0% | 0.137 |
| post_2026_06_01 | Karachi | ecmwf | BUY_NO | 14 | 34.73 | -9.97 | -28.7% | 50.0% | 0.157 |
| post_2026_06_01 | Amsterdam | ecmwf | BUY_NO | 4 | 9.92 | -9.92 | -100.0% | 0.0% | 0.239 |
| post_2026_06_01 | Jeddah | ecmwf | BUY_NO | 9 | 29.62 | -7.81 | -26.4% | 66.7% | 0.182 |
| post_2026_06_01 | Ankara | ecmwf | BUY_YES | 4 | 5.00 | -5.00 | -100.0% | 0.0% | 0.111 |
| post_2026_06_01 | Miami | gfs | BUY_NO | 9 | 22.89 | -4.52 | -19.8% | 44.4% | 0.282 |
| post_2026_06_01 | London | ecmwf | BUY_YES | 9 | 18.52 | -3.37 | -18.2% | 33.3% | 0.130 |
| post_2026_06_01 | Guangzhou | gfs | BUY_NO | 8 | 33.48 | -3.35 | -10.0% | 62.5% | 0.158 |
| post_2026_06_01 | Ankara | ecmwf | BUY_NO | 11 | 34.41 | -3.02 | -8.8% | 45.5% | 0.283 |
| post_2026_06_01 | Singapore | gfs | BUY_NO | 7 | 9.99 | -2.95 | -29.5% | 28.6% | 0.132 |
| post_2026_06_01 | Istanbul | ecmwf | BUY_YES | 4 | 10.00 | -2.76 | -27.6% | 25.0% | 0.139 |
| post_2026_06_01 | Istanbul | ecmwf | BUY_NO | 3 | 9.59 | -2.13 | -22.2% | 33.3% | 0.136 |
| post_2026_06_01 | Seattle | gfs | BUY_NO | 3 | 9.99 | -1.80 | -18.1% | 66.7% | 0.288 |
| post_2026_06_01 | Moscow | ecmwf | BUY_NO | 9 | 40.41 | -1.72 | -4.2% | 66.7% | 0.244 |
| post_2026_06_01 | NYC | gfs | BUY_NO | 8 | 29.40 | 0.28 | 0.9% | 75.0% | 0.191 |
| post_2026_06_01 | Chengdu | ecmwf | BUY_NO | 7 | 18.30 | 0.49 | 2.7% | 57.1% | 0.218 |
| post_2026_06_01 | London | ecmwf | BUY_NO | 7 | 28.31 | 0.68 | 2.4% | 57.1% | 0.214 |
| post_2026_06_01 | Shanghai | gfs | BUY_YES | 3 | 14.23 | 3.62 | 25.5% | 33.3% | 0.148 |
| post_2026_06_01 | Manila | gfs | BUY_NO | 11 | 33.91 | 4.09 | 12.1% | 72.7% | 0.190 |
| post_2026_06_01 | LA | gfs | BUY_NO | 8 | 23.71 | 4.23 | 17.8% | 62.5% | 0.349 |
| post_2026_06_01 | Madrid | ecmwf | BUY_NO | 9 | 29.27 | 8.48 | 29.0% | 66.7% | 0.146 |
| post_2026_06_01 | Tokyo | gfs | BUY_NO | 8 | 19.50 | 8.70 | 44.6% | 75.0% | 0.266 |
| post_2026_06_01 | Shanghai | gfs | BUY_NO | 7 | 19.99 | 8.92 | 44.6% | 100.0% | 0.185 |
| post_2026_06_01 | Warsaw | ecmwf | BUY_NO | 5 | 18.36 | 9.19 | 50.1% | 100.0% | 0.245 |
| post_2026_06_01 | Madrid | ecmwf | BUY_YES | 5 | 10.00 | 9.22 | 92.3% | 80.0% | 0.206 |
| post_2026_06_01 | Miami | gfs | BUY_YES | 12 | 19.99 | 17.03 | 85.2% | 66.7% | 0.280 |
| post_2026_06_01 | LA | gfs | BUY_YES | 11 | 29.72 | 19.58 | 65.9% | 54.5% | 0.250 |

## 5. 6 月后 city-model-raw_edge 亏损组合

| period | city | model_version | raw_edge_bin | fills | cost_usd | pnl_usd | roi | win_rate | avg_raw_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BuenosAires | ecmwf | 0.10-0.15 | 11 | 34.98 | -18.60 | -53.2% | 18.2% | 0.127 |
| post_2026_06_01 | NYC | gfs | 0.15-0.25 | 12 | 30.96 | -13.85 | -44.7% | 25.0% | 0.186 |
| post_2026_06_01 | Munich | ecmwf | 0.15-0.25 | 9 | 19.78 | -12.23 | -61.8% | 33.3% | 0.189 |
| post_2026_06_01 | Ankara | ecmwf | >0.25 | 7 | 19.42 | -11.96 | -61.6% | 14.3% | 0.341 |
| post_2026_06_01 | Miami | gfs | 0.15-0.25 | 6 | 19.90 | -10.29 | -51.7% | 16.7% | 0.196 |
| post_2026_06_01 | BuenosAires | ecmwf | 0.15-0.25 | 3 | 9.99 | -9.99 | -100.0% | 0.0% | 0.217 |
| post_2026_06_01 | Amsterdam | ecmwf | 0.10-0.15 | 3 | 9.93 | -9.93 | -100.0% | 0.0% | 0.143 |
| post_2026_06_01 | Munich | ecmwf | 0.10-0.15 | 5 | 9.91 | -9.91 | -100.0% | 0.0% | 0.117 |
| post_2026_06_01 | Jeddah | ecmwf | 0.15-0.25 | 8 | 24.63 | -9.76 | -39.6% | 62.5% | 0.192 |
| post_2026_06_01 | LA | gfs | >0.25 | 13 | 35.85 | -7.92 | -22.1% | 38.5% | 0.346 |
| post_2026_06_01 | Karachi | ecmwf | 0.15-0.25 | 11 | 24.99 | -7.88 | -31.5% | 54.5% | 0.190 |
| post_2026_06_01 | Warsaw | ecmwf | 0.10-0.15 | 11 | 22.00 | -7.83 | -35.6% | 27.3% | 0.126 |
| post_2026_06_01 | Moscow | ecmwf | 0.10-0.15 | 9 | 12.64 | -7.83 | -61.9% | 11.1% | 0.136 |
| post_2026_06_01 | Karachi | ecmwf | 0.10-0.15 | 5 | 14.74 | -7.09 | -48.1% | 20.0% | 0.110 |
| post_2026_06_01 | Guangzhou | gfs | 0.15-0.25 | 4 | 13.49 | -5.69 | -42.2% | 50.0% | 0.204 |
| post_2026_06_01 | Jeddah | ecmwf | >0.25 | 3 | 5.00 | -5.00 | -100.0% | 0.0% | 0.340 |
| post_2026_06_01 | Istanbul | ecmwf | 0.10-0.15 | 7 | 19.59 | -4.89 | -25.0% | 28.6% | 0.138 |
| post_2026_06_01 | Manila | gfs | 0.10-0.15 | 8 | 19.59 | -4.31 | -22.0% | 62.5% | 0.136 |
| post_2026_06_01 | Jeddah | ecmwf | 0.10-0.15 | 7 | 10.00 | -3.06 | -30.6% | 14.3% | 0.139 |
| post_2026_06_01 | Singapore | gfs | 0.10-0.15 | 7 | 9.99 | -2.95 | -29.5% | 28.6% | 0.132 |
| post_2026_06_01 | Moscow | ecmwf | >0.25 | 5 | 24.57 | -2.93 | -11.9% | 60.0% | 0.286 |
| post_2026_06_01 | Ankara | ecmwf | 0.10-0.15 | 5 | 10.00 | -2.07 | -20.7% | 20.0% | 0.118 |
| post_2026_06_01 | NYC | gfs | 0.10-0.15 | 9 | 17.08 | -1.99 | -11.7% | 33.3% | 0.128 |
| post_2026_06_01 | Seattle | gfs | >0.25 | 3 | 9.99 | -1.80 | -18.1% | 66.7% | 0.288 |
| post_2026_06_01 | Chengdu | ecmwf | 0.15-0.25 | 5 | 14.85 | -1.06 | -7.1% | 40.0% | 0.182 |
| post_2026_06_01 | Moscow | ecmwf | 0.15-0.25 | 3 | 13.19 | -0.95 | -7.2% | 66.7% | 0.214 |
| post_2026_06_01 | London | ecmwf | >0.25 | 3 | 13.40 | -0.94 | -7.0% | 66.7% | 0.287 |
| post_2026_06_01 | Shanghai | gfs | 0.15-0.25 | 4 | 14.22 | -0.04 | -0.3% | 75.0% | 0.177 |
| post_2026_06_01 | Manila | gfs | >0.25 | 4 | 14.55 | 0.71 | 4.9% | 50.0% | 0.281 |
| post_2026_06_01 | Tokyo | gfs | >0.25 | 5 | 9.50 | 1.86 | 19.6% | 60.0% | 0.358 |
| post_2026_06_01 | Guangzhou | gfs | 0.10-0.15 | 4 | 19.99 | 2.33 | 11.7% | 75.0% | 0.113 |
| post_2026_06_01 | London | ecmwf | 0.10-0.15 | 11 | 28.52 | 3.16 | 11.1% | 45.5% | 0.130 |
| post_2026_06_01 | Ankara | ecmwf | 0.15-0.25 | 3 | 9.99 | 6.00 | 60.1% | 100.0% | 0.192 |
| post_2026_06_01 | Madrid | ecmwf | 0.10-0.15 | 6 | 19.75 | 6.11 | 30.9% | 66.7% | 0.112 |
| post_2026_06_01 | Tokyo | gfs | 0.10-0.15 | 3 | 10.00 | 6.83 | 68.4% | 100.0% | 0.113 |

## 6. candidate 机会 mix 参考

这张表不是实盘 PnL，只看 eligible opportunity 在 city-model 上的来源和反事实 PnL。

| period | city | model_version | eligible | paper_ordered | live_filled | cf_pnl | avg_raw_edge_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | NYC | gfs | 6 | 6 | 5 | -18.55 | 0.209 |
| post_2026_06_01 | Miami | gfs | 6 | 6 | 4 | -18.45 | 0.343 |
| post_2026_06_01 | Jeddah | ecmwf | 7 | 7 | 7 | -12.20 | 0.201 |
| post_2026_06_01 | Amsterdam | ecmwf | 5 | 5 | 3 | -8.20 | 0.220 |
| post_2026_06_01 | BuenosAires | ecmwf | 4 | 4 | 4 | -6.15 | 0.129 |
| post_2026_06_01 | Guangzhou | gfs | 4 | 4 | 4 | -5.75 | 0.169 |
| post_2026_06_01 | Ankara | ecmwf | 8 | 8 | 7 | -5.50 | 0.247 |
| post_2026_06_01 | Istanbul | ecmwf | 3 | 3 | 3 | -5.05 | 0.125 |
| post_2026_06_01 | Moscow | ecmwf | 8 | 8 | 8 | +1.15 | 0.203 |
| post_2026_06_01 | Tokyo | gfs | 3 | 3 | 3 | +3.15 | 0.321 |
| post_2026_06_01 | Manila | gfs | 6 | 6 | 6 | +4.50 | 0.177 |
| post_2026_06_01 | LA | gfs | 8 | 8 | 6 | +8.06 | 0.308 |
| post_2026_06_01 | Shanghai | gfs | 3 | 3 | 3 | +9.70 | 0.215 |
| post_2026_06_01 | Karachi | ecmwf | 7 | 7 | 6 | +10.40 | 0.206 |
| post_2026_06_01 | Warsaw | ecmwf | 8 | 8 | 7 | +10.45 | 0.145 |
| post_2026_06_01 | London | ecmwf | 8 | 8 | 6 | +11.90 | 0.240 |
| post_2026_06_01 | Madrid | ecmwf | 3 | 3 | 3 | +14.35 | 0.209 |

## 不停实盘的规则建议

1. **ECMWF hard downgrade**：BuenosAires、Munich、Jeddah、Karachi、Moscow、Ankara 这类 post ECMWF 亏损且样本够的城市，ECMWF live 提高到 `raw_edge>0.30` 或直接 shadow。
2. **ECMWF conditional keep**：若城市 post 总体仍正，ECMWF 不全停，但必须叠加 `raw_edge>0.30`、blended_edge>=0.10、timing 子任务通过。
3. **GFS 不全开**：GFS 可作为相对保留模型，但 NYC 的 GFS BUY_YES / 中低 raw edge 组合要 shadow，不能被 GFS 总体正收益掩盖。
4. **城市级保留池**：LA、Miami、Tokyo、Madrid、Shanghai 先保留小 size，但仍应用模型/side/edge/timing 过滤；LA 总体 GFS 强正，但内部 raw_edge 子桶有反常，不能无条件放大。
5. **执行上线方式**：先做 city-model allow/deny list 的配置层开关，不改 PnL 公式、不改 raw model；每晚按 city-model PnL 自动生成 downgrade list。

## 口径限制

- post settled 样本当前覆盖到 target_date `2026-06-05`，6/6-6/7 未结算不纳入 realized PnL。
- `model_version` 是决策记录里的模型源；若 5/31 之后实例默认参数污染，仍需 signal->plan->order->fact 四层审计确认。
- 少于 5 fills 或少于 3 active target days 的 city-model 不作强黑名单，只 shadow/降权观察。
