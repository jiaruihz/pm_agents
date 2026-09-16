# Weather 研究：非常规 forecast 形态完整分母复核 v2

## 结论

**不是只有成都，历史积累也没有白做。** 旧 v1 把“完整历史形态”和“clean forward 可交易证据”混在了一起；本次在所有可同时拿到 observation、current exact bracket、盘口和 settlement 的历史 feature-factory 分母上重跑。

- evidence parent：1508 city-days / 36 cities / 50 dates（2026-05-19..2026-07-08）。
- strict double-lobe：90 city-days / 34 cities / 39 dates。
- all unusual shapes：203 city-days / 35 cities / 47 dates。
- peak-clock alias：90 city-days / 26 cities / 42 dates。

但完整分母否定了“看到这种形态就直接下单”：peak-clock alias 的 current NO 与 d1 YES 都是负 ROI；strict double-lobe 也没有独立交易优势。成都应继续作为“盘中 forecast revision + future lobe + 盘口残差”的机制案例，而不是 named-shape gate。

## 数据层没有丢

- canonical fact：3753 city-days / 49 cities / 86 dates（2026-05-05..2026-07-29）；settled 到 2026-07-27。
- fixed-run D-1 backfill：3753 city-days / 49 cities / 2026-05-05..2026-07-29；GFS 3753/3753，ECMWF 3753/3753。
- 原生 intraday curve archive：1706 files / 24 capture dates（2026-07-04..2026-07-29）。
- 不能伪造的缺口：可交易 evidence feature-factory 只到 7/8；7/9–7/23 虽有部分 curve/market raw，但尚未物化成同一 checkpoint evidence rows。它是 join/feature-layer 覆盖缺口，不是历史 raw 全丢。

## 形态分布（first checkpoint / city-day）

| curve_shape | city_days | cities | dates | current_exact_losses | d1_hits | mean_current_yes_ask |
|---|---|---|---|---|---|---|
| canonical_afternoon_single | 728 | 36 | 50 | 672 | 105 | 0.08016850828729281 |
| broad_plateau | 577 | 36 | 49 | 508 | 106 | 0.125046875 |
| multi_peak_other | 72 | 31 | 33 | 60 | 17 | 0.17355555555555557 |
| late_peak_or_advection | 60 | 15 | 37 | 58 | 13 | 0.08728333333333334 |
| irregular_other | 53 | 18 | 36 | 32 | 19 | 0.4216923076923077 |
| morning_peak_afternoon_reheat | 18 | 11 | 14 | 16 | 2 | 0.11338888888888889 |

## 价格与收益

价格按同一 checkpoint 的互补报价重建：current NO ask proxy = 1-current YES bid；d1 YES ask proxy = 1-d1 NO bid；计 Weather taker fee `0.05*p*(1-p)`。历史没有保留对应 opposite-side direct depth，因此是 price-only diagnostic，不冒充真实 fill。

| sample | expression | quoted_settled_city_days | wins | win_rate | mean_price | median_price | price_q10 | price_q90 | fee_adjusted_pnl_per_share | fee_adjusted_roi | fee_adjusted_roi_ci95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all_first_checkpoint | current_no | 1295 | 1133 | 87.5% | 88.9% | 99.4% | 58.0% | 99.9% | -20.46119619999998 | -1.8% | [-0.033404489010646986, -0.004080857723763681] |
| all_first_checkpoint | d1_yes | 1486 | 260 | 17.5% | 20.0% | 10.8% | 0.6% | 51.0% | -44.93720110000001 | -14.7% | [-0.22108055491932085, -0.07531416692371572] |
| strict_double_lobe | current_no | 79 | 65 | 82.3% | 85.3% | 95.7% | 49.0% | 99.9% | -2.6482294499999983 | -3.9% | [-0.10699887244318952, 0.019153355569195247] |
| strict_double_lobe | d1_yes | 90 | 19 | 21.1% | 27.9% | 27.0% | 1.4% | 53.4% | -6.80700165 | -26.4% | [-0.5001286056247228, -0.024799640035939166] |
| all_unusual_shape | current_no | 170 | 133 | 78.2% | 78.5% | 95.0% | 28.8% | 99.9% | -1.0587344499999982 | -0.8% | [-0.06793254553986257, 0.04736023413513879] |
| all_unusual_shape | d1_yes | 198 | 51 | 25.8% | 26.4% | 23.4% | 1.0% | 57.3% | -2.6184544000000005 | -4.9% | [-0.22912515970196481, 0.11178505083954385] |
| first_peak_clock_alias | current_no | 90 | 9 | 10.0% | 11.6% | 2.7% | 0.3% | 38.2% | -1.7090038000000005 | -16.0% | [-0.5884928747276532, 0.3092169064816521] |
| first_peak_clock_alias | d1_yes | 85 | 7 | 8.2% | 10.5% | 2.0% | 0.2% | 40.0% | -2.2272023 | -24.1% | [-0.7502393488574823, 0.3928787547347971] |

## 策略结论

1. `named shape`（双峰、午夜峰、晚峰）保留为解释标签和风险 overlay，不单独交易。
2. 主特征应是连续的 boundary-relative state：未来 lobe 距 current exact 上沿、future heat area、局部峰间 valley、forecast revision、source disagreement、running-max age。
3. 真正值得继续的是“成都型 revision shock”：旧 peak-clock/hold 概率突然转乐观，但未来局部热峰仍越界，且 current NO / d1 YES 盘口没有同步。这个 interaction 需要从 7/24 起的原生 forward raw 继续 frozen collector。
4. promotion 仍要求 same-row market/core proper-score 改善、target-date block CI、direct depth 和 fee-adjusted ROI；当前结论是 feature/collector，不是独立 live 策略。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_intraday_forecast_curve_morphology_v2.py
```

- result: `docs/analysis/2026-07/2026-07-29-intraday-forecast-curve-morphology-v2.json`
- generated: `docs/analysis/2026-07/generated/intraday_forecast_curve_morphology_v2/`
- row-level distributions: `strict_double_lobe_city_days.csv`, `all_unusual_shape_city_days.csv`, `first_peak_clock_alias_city_days.csv`
