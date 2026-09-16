# City Strategy Fit by Forecast Bias v1

Generated: 2026-06-30

## Verdict

需要给城市分类，但分类对象不是“城市好坏”，而是“这个城市/数据源的历史 station-vs-forecast 偏移适合哪类交易表达”。同一个城市可能适合 current-bracket NO，却不适合 forecast-capped higher NO。

Input uses the historical bias layer from `2026-06-30-historical-forecast-station-bias-v1`: `error = actual daily max - forecast daily max`.

## Regime Summary

| regime | cities | city list |
| --- | --- | --- |
| balanced_tight | 4 | Lucknow, Miami, Minneapolis, Tokyo |
| cold_overforecast_clean | 3 | Boston, Houston, Jeddah |
| cold_overforecast_noisy | 4 | Karachi, MexicoCity, NYC, PanamaCity |
| hot_underforecast_clean | 18 | Atlanta, Austin, Beijing, BuenosAires, Chengdu, Chicago, Chongqing, Dallas, KualaLumpur, Lagos, Manila, Phoenix, Seattle, Seoul, Shanghai, Shenzhen, Singapore, Wellington |
| hot_underforecast_noisy | 4 | Guangzhou, SanFrancisco, SaoPaulo, Wuhan |
| mild_or_mixed | 5 | CapeTown, Denver, Istanbul, Jakarta, TelAviv |
| two_sided_noisy | 1 | Taipei |

## Hot Underforecast Cities

这些城市/模型长期实际偏热，forecast ceiling 不能当硬上限。它们更适合研究 `runway current-bracket NO` 或 higher/hot-break YES；不适合作为 `forecast_capped_higher_no` 的干净城市池。

| city | unit | model | bias | p10 | p90 | hot% | cold% | current NO | capped NO | fade YES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Atlanta | F | gfs | 1.1528 | -0.2 | 2.5 | 56.2 | 2.5 | strong | weak | weak |
| Austin | F | gfs | 1.6201 | 0.1 | 3.4 | 68.1 | 1.4 | strong | weak | weak |
| Beijing | C | ecmwf | 1.2279 | -0.333 | 2.778 | 58.1 | 4.5 | strong | weak | weak |
| BuenosAires | C | ecmwf | 0.9777 | -0.056 | 2.167 | 48.0 | 1.4 | strong | weak | weak |
| Chengdu | C | ecmwf | 1.121 | -0.667 | 3.0835 | 52.2 | 7.0 | strong | weak | weak |
| Chicago | F | gfs | 0.8034 | -0.54 | 2.3 | 41.4 | 3.9 | strong | weak | weak |
| Chongqing | C | ecmwf | 0.7998 | -1.111 | 2.778 | 40.7 | 11.8 | strong | weak | weak |
| Dallas | F | ecmwf | 1.1087 | -1.0 | 3.45 | 48.3 | 11.2 | strong | weak | weak |
| KualaLumpur | C | ecmwf | 1.1395 | -0.278 | 2.5 | 57.0 | 3.6 | strong | weak | weak |
| Lagos | C | ecmwf | 1.5054 | 0.0 | 2.9445 | 72.0 | 4.6 | strong | weak | weak |
| Manila | C | gfs | 1.2891 | -0.139 | 2.611 | 64.0 | 3.9 | strong | weak | weak |
| Phoenix | F | gfs | 1.1161 | -0.1 | 2.4 | 55.6 | 1.6 | strong | weak | weak |
| Seattle | F | gfs | 0.7865 | -0.5 | 2.15 | 41.0 | 3.6 | strong | weak | weak |
| Seoul | C | ecmwf | 1.0911 | -0.167 | 2.361 | 52.0 | 1.7 | strong | weak | weak |
| Shanghai | C | gfs | 0.9496 | -0.444 | 2.278 | 50.7 | 5.0 | strong | weak | weak |
| Shenzhen | C | ecmwf | 0.8825 | -0.361 | 2.1945 | 43.8 | 3.4 | strong | weak | weak |
| Singapore | C | gfs | 0.8289 | -0.389 | 2.111 | 40.7 | 4.8 | strong | weak | weak |
| Wellington | C | gfs | 1.181 | 0.167 | 2.222 | 61.0 | 3.4 | strong | weak | weak |
| Guangzhou | C | gfs | 0.5786 | -1.611 | 2.6945 | 42.4 | 16.0 | medium | weak | weak_to_medium |
| SanFrancisco | F | ecmwf | 1.3185 | -1.7 | 4.1 | 59.8 | 18.0 | medium | weak | weak_to_medium |
| SaoPaulo | C | ecmwf | 0.6972 | -0.611 | 2.278 | 39.0 | 7.6 | medium | weak | weak_to_medium |
| Wuhan | C | ecmwf | 0.688 | -0.972 | 2.3055 | 44.9 | 10.1 | medium | weak | weak_to_medium |

## Cold Overforecast Cities

这些城市/模型长期 forecast 偏热，适合优先研究 capped higher NO / peak-fade YES；runway current-bracket NO 要求更强实时升温确认。

| city | unit | model | bias | p10 | p90 | hot% | cold% | current NO | capped NO | fade YES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Boston | F | gfs | -0.5096 | -3.0 | 1.8 | 19.8 | 37.7 | weak | strong | strong |
| Houston | F | gfs | -0.7775 | -2.4 | 0.8 | 8.2 | 47.8 | weak | strong | strong |
| Jeddah | C | ecmwf | -0.97 | -2.528 | 0.361 | 3.9 | 47.8 | weak | strong | strong |
| Karachi | C | ecmwf | -0.115 | -1.861 | 1.5 | 25.6 | 27.5 | weak_to_medium | medium | medium |
| MexicoCity | C | ecmwf | -0.2951 | -1.528 | 1.111 | 12.1 | 26.4 | weak_to_medium | medium | medium |
| NYC | F | gfs | -0.2712 | -1.9 | 1.4 | 15.6 | 29.7 | weak_to_medium | medium | medium |
| PanamaCity | C | gfs | -0.0702 | -2.278 | 1.889 | 25.8 | 25.8 | weak_to_medium | medium | medium |

## Balanced, Mixed, Or Noisy Cities

这些城市不应该仅靠历史 source bias 决定方向；要把实时 regime、盘口价格和执行纪律放在前面。`two_sided_noisy` 城市尤其应该先 shadow 或降 size。

| city | unit | model | bias | p10 | p90 | hot% | cold% | current NO | capped NO | fade YES |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lucknow | C | ecmwf | 0.0804 | -1.0 | 1.278 | 17.7 | 11.0 | neutral | neutral | neutral |
| Miami | F | gfs | 0.1286 | -1.3 | 1.6 | 22.4 | 15.5 | neutral | neutral | neutral |
| Minneapolis | F | gfs | 0.1371 | -1.2 | 1.55 | 21.6 | 15.2 | neutral | neutral | neutral |
| Tokyo | C | gfs | -0.0899 | -1.444 | 1.167 | 13.8 | 17.5 | neutral | neutral | neutral |
| Taipei | C | gfs | -0.0708 | -2.611 | 2.278 | 36.2 | 21.9 | shadow_only | shadow_only | shadow_only |
| CapeTown | C | ecmwf | 0.4615 | -1.0 | 1.778 | 32.0 | 10.7 | neutral_to_medium | neutral | neutral |
| Denver | F | gfs | 0.3357 | -1.1 | 1.88 | 36.5 | 11.1 | neutral_to_medium | neutral | neutral |
| Istanbul | C | ecmwf | 0.4844 | -0.5835 | 1.556 | 27.0 | 4.8 | neutral_to_medium | neutral | neutral |
| Jakarta | C | ecmwf | 0.4844 | -1.167 | 2.028 | 34.8 | 12.1 | neutral_to_medium | neutral | neutral |
| TelAviv | C | gfs | 0.4697 | -0.889 | 1.639 | 32.9 | 9.3 | neutral_to_medium | neutral | neutral |

## How To Use

- `hot_underforecast_clean`: 优先给 current-bracket NO / hot-break YES 更高 prior；capped higher NO 要明显更便宜、更大 margin。
- `cold_overforecast_clean`: 优先给 capped higher NO / current-high YES 更高 prior；current NO 只在实时 runway 很干净时考虑。
- `two_sided_noisy`: 不做城市级硬过滤，但 size 应小，必须依赖更强 live feature 和盘口 edge。
- `balanced_tight`: forecast bias 不提供明显方向，策略胜负更依赖 intraday regime 和 market price。

Boundary: this is a calibration/selection feature layer, not a live gate. Live eligibility still needs strategy-specific replay/shadow with real ask, capacity, settlement and execution rules.

## Artifact

- CSV: `docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv`
- Generated CSV is ignored by git and reproducible from the script.
