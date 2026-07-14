# Weather Climate Feature Review v1

> 2026-07-14; opportunity-grain model audit; zero notional; no live change.

## 结论

现有特征层的 thermal path 骨架是合理且有物理判别力的，但还不能证明有交易增量。雨没有进入历史 atlas；云只有粗粒度 METAR sky code；风向、观测 age/cadence 也没有进入这批历史状态。它们目前适合作为连续 probability features，不适合作为下雨/多云 hard filter。

数据覆盖 `2026-05-19..2026-07-08`，50 dates、36 城、14368 state rows。模型同分母为 train 8468 rows，forward 2778 rows / 15 dates。

## 数据完整性自检

- canonical fact_signal_candidates: 54054 rows，2026-05-05..2026-07-15，built_at=2026-07-14T07:58:43.800304+00:00。
- canonical settlement_outcomes: 32958 rows，2026-05-04..2026-07-12。
- derived atlas: 14368 rows；label coverage=99.6%；paired YES/NO ask model rows=11246。
- 严格排除 `final_max_native`、`remaining_heat_native`、`future_break_*`、payoff/ROI 等后验字段；split 只按 target_date，train 严格早于 forward。
- 本轮不发布 live_real PnL，因此不调用 fill coverage gate；执行结果是 opportunity replay，不是实际 fill。

## Forward proper-score ablation

delta 为 challenger - raw market；负值才是改善。CI 按 target_date block bootstrap。

| model | date-equal logloss | delta vs market | 95% CI | Brier | AUC |
| --- | ---: | ---: | ---: | ---: | ---: |
| market_raw | 0.1974 | +0.0000 | [+0.0000, +0.0000] | 0.0598 | 0.9750 |
| market_calibrated | 0.1990 | +0.0016 | [+0.0000, +0.0034] | 0.0604 | 0.9750 |
| thermal_only | 0.4703 | +0.2729 | [+0.2121, +0.3469] | 0.1548 | 0.9052 |
| market_plus_thermal | 0.2183 | +0.0209 | [+0.0064, +0.0408] | 0.0688 | 0.9738 |
| market_plus_thermal_suppression | 0.2142 | +0.0168 | [+0.0036, +0.0356] | 0.0673 | 0.9742 |
| market_plus_all_regimes | 0.2124 | +0.0150 | [+0.0033, +0.0288] | 0.0667 | 0.9743 |
| market75_thermal25 | 0.2008 | +0.0034 | [+0.0003, +0.0074] | 0.0612 | 0.9749 |
| market75_thermal_suppression25 | 0.1999 | +0.0025 | [-0.0004, +0.0062] | 0.0609 | 0.9751 |
| market75_all_regimes25 | 0.1992 | +0.0018 | [-0.0009, +0.0049] | 0.0606 | 0.9752 |

## BUY current-NO diagnostic

固定规则：forward 上仅当模型 `p_no > current_no_ask + fee` 且 ask depth>=5 才买；没有调 edge threshold。

| probability | rows | dates | cities | avg edge | ROI | 95% CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| market_raw | 0 | 0 | 0 | NA | NA | [NA, NA] |
| market_calibrated | 0 | 0 | 0 | NA | NA | [NA, NA] |
| thermal_only | 1124 | 15 | 36 | +8.48% | -17.14% | [-33.87%, -2.67%] |
| market_plus_thermal | 111 | 13 | 31 | +2.39% | -11.81% | [-26.65%, +7.80%] |
| market_plus_thermal_suppression | 182 | 13 | 30 | +2.06% | -12.09% | [-30.22%, +13.86%] |
| market_plus_all_regimes | 254 | 14 | 33 | +2.44% | -6.21% | [-29.36%, +18.10%] |
| market75_thermal25 | 11 | 4 | 5 | +1.07% | +7.75% | [-100.00%, +29.71%] |
| market75_thermal_suppression25 | 20 | 9 | 9 | +0.72% | -1.77% | [-45.51%, +72.45%] |
| market75_all_regimes25 | 33 | 11 | 18 | +0.69% | -7.23% | [-58.13%, +46.96%] |

## Price-move × weather pattern audit

只看同一 bracket 且 midpoint 变化至少 2c：全量 3786 rows / 48 dates；forward 844 rows / 15 dates。`market_move_weather_interactions` 显式加入 move×温度趋势、forecast runway、云、湿度、风速交互；不含 city identity。

| model | date-equal logloss | delta vs market | 95% CI | AUC | NO EV rows | NO ROI | ROI CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| market_raw | 0.2762 | +0.0000 | [+0.0000, +0.0000] | 0.9378 | 0 | NA | [NA, NA] |
| market_plus_move | 0.2838 | +0.0076 | [+0.0021, +0.0141] | 0.9378 | 38 | -100.00% | [-100.00%, -100.00%] |
| market_move_thermal | 0.3026 | +0.0264 | [+0.0015, +0.0627] | 0.9366 | 52 | -17.11% | [-59.43%, +24.41%] |
| market_move_weather_interactions | 0.3068 | +0.0306 | [+0.0039, +0.0623] | 0.9340 | 145 | -6.17% | [-37.68%, +21.14%] |
| market75_move_weather25 | 0.2799 | +0.0038 | [-0.0021, +0.0105] | 0.9384 | 36 | +8.58% | [-54.93%, +116.30%] |

## Feature review

| family | verdict | evidence / problem |
| --- | --- | --- |
| temperature path | keep | 1h/3h trend、decline、minutes since max、forecast runway 物理方向清楚，历史覆盖约 88%–100%；此前机制研究也显示 sustained warming 对 future break 有判别力。 |
| market price | primary prior | raw market 是必须打败的基准；天气模型不能脱离盘口独立定价。 |
| cloud | partial | `sky_cover_code` 只有粗等级且覆盖约 75%；没有 cloud base、分层云量、变化率的统一 live contract。 |
| rain / convection | missing | 历史 atlas 没有 `precip_state`、雨强、雷暴或 radar；当前 `humid_convective_risk` 只是 RH proxy，不能称为下雨特征。 |
| wind / marine flow | partial | wind speed 覆盖高，但历史 atlas 无 wind direction；手工 onshore sector 只能做先验，未校准前不能定方向。 |
| observation freshness | missing in atlas | shared builder 已有 age/cadence 字段，但当前历史 atlas 没有，旧路径状态把 3 分钟和 50 分钟前观测近似等同。 |
| solar clock | weak proxy | `solar_window` 是固定 local-hour 桶，不是真实 solar elevation/sunset/day length，跨纬度季节会漂。 |
| heating_done_score_v1 | diagnostic only | 手工加权、相关特征重复计分、clip 0..1；不是校准概率，不能直接拿来算 EV。 |
| composite regimes | diagnostics only | 多个阈值标签叠加容易碎片化；应保留连续字段，让模型学习 residual，不用 composite string 作策略。 |

## Verdict

- significance=FAIL：没有天气 challenger 的 forward delta-logloss CI 全部低于 0；多数点估反而更差。
- baseline=FAIL：raw market probability 仍是最佳基准。
- forward=FAIL：固定 2026-06-21+；没有从 forward 反选阈值，但天气增量未复现。
- conclusion=inconclusive：thermal path 作为 shared context 保留；气候模式不能升级为 selector。
- live：不改。先补 precipitation、wind direction、freshness、solar geometry 的 PIT capture，再用相同 ablation 验证；若 market+weather 仍不胜 market，就不把气候模式升级为交易 selector。

一句话：在 2026-06-21+ forward，最保守的 market75+all-regimes25 相对 raw market 的 date-equal logloss delta 为 +0.0018（95% CI [-0.0009,+0.0049]），前瞻 FAIL，结论等级 inconclusive。

## 8 环覆盖

- covered：特征覆盖、date-block 统计推断、信号判别、概率评分、market baseline、target-date 相关性。
- partial：执行只重放 current-NO ask+5-share depth；容量只到 top ask 5 shares。
- missing：真实 maker/taker fill、YES ask depth、组合资金占用；因此不作 live 结论。

Structured artifact: `docs/analysis/2026-07/generated/weather_climate_feature_review_v1/summary.json`.
