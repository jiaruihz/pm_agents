# Forecast / Update-Time Repricing V0

Status: snapshot research; no live action

## 数据快照

- 数据源: N100 filtered raw timing logs + local synced paper snapshots.
- sources rows: `215395`; cycles rows: `194061`; books rows: `154518`.
- observation events: `1373`; forecast state events: `421`; bracket reaction rows: `7176`.
- 窗口: `2026-06-24T00:00:00Z` onward; cities: `Austin, BuenosAires, Busan, Chicago, Denver, LA, Manila, Miami, Philadelphia, Shanghai, Singapore, Tokyo`.
- 口径: 盘口微结构只用 raw timing logs；未计算真实成交/PnL；NO-side 不用 `1 - YES ask` 推 EV。
- 限制: forecast/hash 事件由 30 分钟 paper snapshots 重构；还没有逐源 `forecast_state_first_seen_utc`。

## 结论

有一丝机会，但还只够进入 shadow 取证：盘口变化确实集中在整点/半点附近，目标城市平均约 41.5% 的 book-change rows 落在本地 :00/:30 +/-5m。本窗口里 observation 关联大反应 rows=142，forecast/hash 关联大反应 rows=19；主导形态更像 observation/update-window 驱动，forecast/hash 只在 LA/Tokyo/Shanghai/Manila/Austin 等少数 current/next bracket 有弱信号。注意 forecast 事件来自 30 分钟 paper snapshot 重构，不是原生 forecast first-seen cadence；且部分价格已在 source_detect 前移动。

## City-Level Opportunity Table

| city | trigger | moves | :00/:30 rate | obs events | fc events | obs big | fc big | obs delay p50 | fc delay p50 | bracket opportunity | strength | next |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| Austin | observation | 3056 | 42.2% | 241 | 64 | 6 | 2 | 46.0 | 40.9 | yes | medium | shadow_cadence_monitor |
| BuenosAires | observation | 6145 | 41.1% | 43 | 17 | 5 | 1 | 38.5 | 122.3 | yes | low | shadow_cadence_monitor |
| Busan | observation | 9060 | 41.8% | 52 | 17 | 5 | 2 | 40.4 | 26.2 | yes | low | shadow_cadence_monitor |
| Chicago | observation | 3504 | 42.2% | 233 | 64 | 7 | 0 | 33.9 | 67.8 | yes | low | shadow_cadence_monitor |
| Denver | mixed | 4154 | 40.8% | 44 | 63 | 1 | 0 | 39.2 | 42.8 | yes | thin | keep_collecting |
| LA | mixed | 4364 | 40.0% | 231 | 67 | 2 | 2 | 34.7 | 40.9 | yes | low | shadow_cadence_monitor |
| Manila | observation | 6313 | 42.7% | 43 | 16 | 15 | 2 | 33.0 | 33.9 | yes | medium | shadow_cadence_monitor |
| Miami | observation | 3862 | 41.2% | 231 | 65 | 36 | 3 | 29.6 | 39.6 | yes | medium | shadow_cadence_monitor |
| Philadelphia | unclear | 0 | NA | 0 | 0 | 0 | 0 | NA | NA | no_clear | thin | keep_collecting |
| Shanghai | observation | 10613 | 41.7% | 84 | 16 | 18 | 2 | 23.2 | 26.2 | yes | medium | shadow_cadence_monitor |
| Singapore | observation | 8780 | 41.8% | 87 | 16 | 19 | 2 | 23.3 | 26.2 | yes | medium | shadow_cadence_monitor |
| Tokyo | observation | 9385 | 41.1% | 84 | 16 | 28 | 3 | 20.6 | 26.2 | yes | medium | shadow_cadence_monitor |

## Bracket-Level Repricing Table

| city | event | bracket_relative | rows | pre | +30s | +90s | +180s | +300s | yes ask p50 | no ask p50 | direction |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Austin | forecast | current_or_forecast_peak | 64 | 0.000 | -0.035 | -0.040 | -0.040 | -0.025 | 0.610 | 0.500 | BUY_NO watch with real NO ask |
| Austin | observation | current_or_forecast_peak | 241 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |
| Austin | forecast | next1 | 64 | 0.000 | NA | NA | NA | NA | 0.610 | 0.500 | no clear direction |
| Austin | observation | next1 | 241 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |
| Austin | forecast | next2 | 64 | NA | NA | NA | NA | NA | NA | NA | no clear direction |
| Austin | observation | next2 | 241 | 0.000 | 0.000 | 0.001 | 0.001 | 0.001 | 0.001 | 0.998 | no clear direction |
| Austin | forecast | prev_or_cross | 64 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.999 | 0.010 | no clear direction |
| Austin | observation | prev_or_cross | 241 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.997 | no clear direction |
| BuenosAires | forecast | current_or_forecast_peak | 17 | -0.014 | 0.000 | 0.000 | 0.000 | 0.000 | 0.330 | 0.700 | no clear direction |
| BuenosAires | observation | current_or_forecast_peak | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.986 | no clear direction |
| BuenosAires | forecast | next1 | 17 | 0.000 | NA | NA | NA | NA | 0.530 | 0.480 | no clear direction |
| BuenosAires | observation | next1 | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.006 | 0.966 | no clear direction |
| BuenosAires | forecast | next2 | 17 | NA | NA | NA | NA | NA | 0.530 | 0.480 | no clear direction |
| BuenosAires | observation | next2 | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.029 | 0.926 | no clear direction |
| BuenosAires | forecast | prev_or_cross | 17 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.058 | 0.980 | no clear direction |
| BuenosAires | observation | prev_or_cross | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |
| Busan | forecast | current_or_forecast_peak | 17 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.330 | 0.640 | no clear direction |
| Busan | observation | current_or_forecast_peak | 52 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.840 | no clear direction |
| Busan | forecast | next1 | 17 | 0.000 | 0.000 | 0.025 | 0.015 | 0.000 | 0.523 | 0.570 | no clear direction |
| Busan | observation | next1 | 52 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.014 | 0.760 | no clear direction |
| Busan | forecast | next2 | 17 | 0.000 | -0.005 | -0.030 | 0.000 | 0.000 | 0.523 | 0.596 | no clear direction |
| Busan | observation | next2 | 52 | 0.000 | 0.005 | 0.005 | 0.005 | 0.005 | 0.150 | 0.860 | no clear direction |
| Busan | forecast | prev_or_cross | 17 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.150 | 0.860 | no clear direction |
| Busan | observation | prev_or_cross | 52 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.999 | no clear direction |
| Chicago | forecast | current_or_forecast_peak | 64 | NA | NA | NA | NA | NA | NA | NA | no clear direction |
| Chicago | observation | current_or_forecast_peak | 233 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.984 | no clear direction |
| Chicago | forecast | next1 | 64 | NA | NA | NA | NA | NA | NA | NA | no clear direction |
| Chicago | observation | next1 | 233 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.997 | no clear direction |
| Chicago | forecast | next2 | 64 | NA | NA | NA | NA | NA | NA | NA | no clear direction |
| Chicago | observation | next2 | 233 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |
| Chicago | forecast | prev_or_cross | 64 | 0.000 | NA | NA | NA | NA | 0.100 | 0.849 | no clear direction |
| Chicago | observation | prev_or_cross | 233 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.997 | no clear direction |
| Denver | forecast | current_or_forecast_peak | 63 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.230 | 0.800 | no clear direction |
| Denver | observation | current_or_forecast_peak | 44 | 0.000 | 0.000 | 0.000 | -0.001 | -0.001 | 0.005 | 0.998 | no clear direction |
| Denver | forecast | next1 | 63 | 0.000 | 0.001 | 0.001 | 0.001 | 0.001 | 0.999 | 0.010 | no clear direction |
| Denver | observation | next1 | 44 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.004 | 0.998 | no clear direction |
| Denver | forecast | next2 | 63 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.018 | 0.800 | no clear direction |
| Denver | observation | next2 | 44 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.009 | 0.998 | no clear direction |
| Denver | forecast | prev_or_cross | 63 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.988 | no clear direction |
| Denver | observation | prev_or_cross | 44 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.005 | 0.997 | no clear direction |
| LA | forecast | current_or_forecast_peak | 67 | 0.000 | 0.004 | 0.003 | 0.002 | 0.005 | 0.710 | 0.320 | no clear direction |
| LA | observation | current_or_forecast_peak | 231 | 0.000 | 0.004 | 0.005 | 0.005 | 0.005 | 0.001 | 0.999 | no clear direction |
| LA | forecast | next1 | 67 | 0.000 | -0.078 | -0.078 | -0.078 | -0.078 | 0.710 | 0.320 | BUY_NO watch with real NO ask |
| LA | observation | next1 | 231 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.008 | 0.999 | no clear direction |
| LA | forecast | next2 | 67 | 0.000 | NA | NA | NA | NA | 0.144 | 0.969 | no clear direction |
| LA | observation | next2 | 231 | 0.000 | -0.078 | -0.078 | -0.078 | -0.078 | 0.008 | 0.998 | BUY_NO watch with real NO ask |
| LA | forecast | prev_or_cross | 67 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.041 | 0.984 | no clear direction |
| LA | observation | prev_or_cross | 231 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.002 | 0.999 | no clear direction |
| Manila | forecast | current_or_forecast_peak | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.500 | 0.530 | no clear direction |
| Manila | observation | current_or_forecast_peak | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.672 | no clear direction |
| Manila | forecast | next1 | 16 | 0.005 | 0.025 | 0.025 | 0.015 | 0.015 | 0.510 | 0.600 | no clear direction |
| Manila | observation | next1 | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.020 | 0.720 | no clear direction |
| Manila | forecast | next2 | 16 | 0.000 | -0.015 | -0.015 | -0.040 | 0.000 | 0.340 | 0.750 | BUY_NO watch with real NO ask |
| Manila | observation | next2 | 43 | 0.000 | -0.015 | -0.015 | -0.015 | -0.015 | 0.126 | 0.870 | no clear direction |
| Manila | forecast | prev_or_cross | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.027 | 0.870 | no clear direction |
| Manila | observation | prev_or_cross | 43 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |
| Miami | forecast | current_or_forecast_peak | 65 | 0.000 | 0.000 | 0.000 | 0.005 | 0.005 | 0.340 | 0.420 | no clear direction |
| Miami | observation | current_or_forecast_peak | 231 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.010 | 0.975 | no clear direction |
| Miami | forecast | next1 | 65 | 0.000 | 0.035 | 0.035 | 0.021 | 0.020 | 0.590 | 0.460 | no clear direction |
| Miami | observation | next1 | 231 | 0.000 | 0.010 | 0.010 | 0.005 | 0.010 | 0.001 | 0.550 | no clear direction |
| Miami | forecast | next2 | 65 | 0.000 | 0.001 | 0.001 | 0.001 | 0.001 | 0.480 | 0.780 | no clear direction |
| Miami | observation | next2 | 231 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.670 | no clear direction |
| Miami | forecast | prev_or_cross | 65 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.096 | 0.979 | no clear direction |
| Miami | observation | prev_or_cross | 231 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.990 | no clear direction |
| Shanghai | forecast | current_or_forecast_peak | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.560 | 0.450 | no clear direction |
| Shanghai | observation | current_or_forecast_peak | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.002 | 0.390 | no clear direction |
| Shanghai | forecast | next1 | 16 | 0.000 | 0.000 | 0.010 | -0.050 | -0.010 | 0.240 | 0.830 | BUY_NO watch with real NO ask |
| Shanghai | observation | next1 | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.021 | 0.820 | no clear direction |
| Shanghai | forecast | next2 | 16 | 0.000 | 0.010 | 0.000 | -0.010 | -0.010 | 0.050 | 0.830 | no clear direction |
| Shanghai | observation | next2 | 84 | 0.000 | 0.009 | 0.009 | 0.009 | 0.009 | 0.240 | 0.740 | no clear direction |
| Shanghai | forecast | prev_or_cross | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.020 | 0.810 | no clear direction |
| Shanghai | observation | prev_or_cross | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.997 | no clear direction |
| Singapore | forecast | current_or_forecast_peak | 16 | 0.000 | -0.005 | 0.000 | 0.000 | 0.000 | 0.480 | 0.810 | no clear direction |
| Singapore | observation | current_or_forecast_peak | 87 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.750 | no clear direction |
| Singapore | forecast | next1 | 16 | 0.000 | NA | NA | NA | NA | 0.320 | 0.810 | no clear direction |
| Singapore | observation | next1 | 87 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.003 | 0.820 | no clear direction |
| Singapore | forecast | next2 | 16 | NA | NA | NA | NA | NA | 0.320 | 0.800 | no clear direction |
| Singapore | observation | next2 | 87 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.010 | 0.888 | no clear direction |
| Singapore | forecast | prev_or_cross | 16 | 0.000 | 0.000 | 0.010 | 0.000 | 0.030 | 0.010 | 0.996 | no clear direction |
| Singapore | observation | prev_or_cross | 87 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | NA | no clear direction |
| Tokyo | forecast | current_or_forecast_peak | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.520 | 0.490 | no clear direction |
| Tokyo | observation | current_or_forecast_peak | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.490 | no clear direction |
| Tokyo | forecast | next1 | 16 | 0.000 | -0.021 | -0.071 | -0.075 | -0.080 | 0.390 | 0.620 | BUY_NO watch with real NO ask |
| Tokyo | observation | next1 | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.530 | 0.460 | no clear direction |
| Tokyo | forecast | next2 | 16 | NA | NA | NA | NA | NA | NA | NA | no clear direction |
| Tokyo | observation | next2 | 84 | 0.000 | -0.155 | -0.155 | -0.155 | -0.155 | 0.270 | 0.750 | BUY_NO watch with real NO ask |
| Tokyo | forecast | prev_or_cross | 16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.985 | no clear direction |
| Tokyo | observation | prev_or_cross | 84 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.001 | 0.998 | no clear direction |

## Next Action

- Continue as zero-notional shadow only: forecast hash jump + observation report trigger + book move confirmation.
- Add a dedicated cadence monitor that logs forecast_state_first_seen_utc per city/source instead of reconstructing from 30-minute snapshots.
- For NO expressions, capture real NO ask/depth at trigger time; do not infer NO from YES.
- Keep city hot-window polling around local :00/:30 for Busan, Singapore, Shanghai, Tokyo, Austin/Denver/Chicago where evidence is not thin.
