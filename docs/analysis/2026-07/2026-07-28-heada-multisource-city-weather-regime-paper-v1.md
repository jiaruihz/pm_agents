# HeadA 多源 × 城市 × 次日天气型机制论文 v1

Generated: 2026-07-28T09:11:08+00:00

## 摘要

是的，现阶段更准确的说法是：**历史筛选条件和最近 frozen shadow 出现了明显 domain shift，很多漂亮切片不能判断是稳定机制还是偶然。** 加入多源、城市 archetype 和次日云雨预报后，结论不是“找到一个可以立刻禁 GFS 的条件”，而是把问题拆成了三层：

1. **source 名字不是根因。** 历史 GFS 16/111，而当前 1/30；Beta-Binomial 后验预测当前应有约
   4.5 个赢家，
   观察到不超过 1 个的概率为
   6.8%。
   这是异常低，但仍不是“永不命中”。
2. **post-hoc 多源共识不能解释 GFS 崩盘。** 7-model replay 中，当前 GFS 有
   20 张属于 ≥75% 模型都认为 ticket 在热侧，仍只有 1 中；
   历史同桶是 84/14，ROI
   +34.0%。
   所以不是“GFS 一家报热、其他模型都不支持”这么简单。
3. **次日天气型给出机制线索，但没有稳定 filter。** 当前 GFS 唯一赢家来自 forecast rain/convective
   桶（4/1）；非雨桶 26/0。
   但样本太薄，0/26 的单侧 95% 命中率上界仍是
   10.9%，而历史同类天气里已有赢家，不能筛掉。

论文式裁决：`mechanism_plausible / selector_inconclusive / no_live_change`。

## 1. 研究问题与可证伪假说

目标量：固定 HeadA `dist>0` exact-bracket YES ticket 的命中概率与 fee-adjusted taker ROI。

- H1（单源异常）：最近 GFS 差，是因为 GFS 与其他模型分歧，且 GFS 单独把 ticket 判成热尾。
- H2（城市构成）：最近 GFS 差，是固定 GFS 城市池/气候 archetype 的样本构成变化。
- H3（次日天气型）：云、雨、对流、风和日较差改变“热尾是否发生”及 exact bracket 落点。
- H4（不可约 shift）：控制 ask、距离、城市、多源与天气后，最近 GFS 仍显著低于历史映射。

H1 在 post-hoc replay 层不受支持；由于缺 decision-time alternate-source vintage，不能声称因果上完全证伪。
H2/H3 只能解释一部分；H4 仍保留。

## 2. 数据、分母与证据等级

| 层 | 分母 | 覆盖 | 可用于 live gate? |
|---|---:|---:|---|
| historical HeadA | 333 tickets / 53 target dates / 2026-05-06..06-30 | settlement 100% | 只作 train / historical baseline |
| frozen shadow | 84 tickets / 8 target dates / 2026-07-16,19..25 | settlement + fresh ask 100% | forward 仍太短 |
| decision-time assigned hourly curve | 当前 84 | 84/84 | 可作 PIT feature evidence |
| 7-model daily replay | 历史 + 当前 | 当前 82/84 | **不可**；无精确 decision-version timestamp |
| historical hourly weather replay | 历史 333 | 328/333 | **不可**；post-hoc mechanism label |

主执行口径保持 fresh ask、5 shares、官方 Weather taker fee。历史 333 沿用原 price-tier
6/8/10 shares，所以跨窗口 ROI 只比较方向；概率/胜率使用同一 ticket denominator。

### Signal / evidence funnel

- signal funnel：历史 333 + frozen shadow 84，都是 first city-date hot-tail ticket。
- evidence funnel：当前 84 的 fresh quote / settlement / assigned PIT curve 完整；
  alternate-source decision-time curve 不完整，因此 7-model结果降级为 post-hoc 机制诊断。
- actual fill：当前为 zero-notional shadow，0 fills；本文不发布 `live_real`。

## 3. 描述性结果：历史与当前 source shift

| window | source | rows | dates | wins | win | ask | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_shadow | ecmwf | 54 | 8 | 11 | 20.4% | 11.4% | 70.8% |
| current_shadow | gfs | 30 | 8 | 1 | 3.3% | 11.9% | -73.2% |
| historical | ecmwf | 222 | 52 | 34 | 15.3% | 10.4% | 51.7% |
| historical | gfs | 111 | 47 | 16 | 14.4% | 10.3% | 21.3% |

| source | hist_rows | hist_wins | current_rows | current_wins | posterior_expected_current_wins | predictive_95_low | predictive_95_high | tail_probability_in_observed_direction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf | 222 | 34 | 54 | 11 | 8.438 | 3 | 15 | 23.5% |
| gfs | 111 | 16 | 30 | 1 | 4.513 | 1 | 9 | 6.8% |

解释：GFS 当前结果处在历史后验预测的低尾，但不是概率为零的事件。ECMWF 当前 11/54 则略高于历史期望，
没有达到可以把 source 当因果 treatment 的条件，因为 source 由 city 固定路由。

## 4. 多源共识：否定“GFS 单独报错”假说

`hot share` 定义为 7 个独立核心全球模型中 forecast Tmax 低于 ticket lower bound 的比例；
它只回答“各模型是否认为这是热侧 ticket”，不等于 exact-bracket 概率。

| window | source | consensus_bucket | rows | wins | win | ask | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_shadow | ecmwf | missing | 2 | 1 | 50.0% | 8.0% | 497.6% |
| current_shadow | ecmwf | mixed_hot_29_71 | 18 | 6 | 33.3% | 12.2% | 162.1% |
| current_shadow | ecmwf | strong_hot_75_100 | 25 | 3 | 12.0% | 11.0% | 4.4% |
| current_shadow | ecmwf | weak_hot_0_25 | 9 | 1 | 11.1% | 11.8% | -9.9% |
| current_shadow | gfs | mixed_hot_29_71 | 10 | 0 | 0.0% | 12.0% | -100.0% |
| current_shadow | gfs | strong_hot_75_100 | 20 | 1 | 5.0% | 11.8% | -59.5% |
| historical | ecmwf | missing | 5 | 0 | 0.0% | 8.1% | -100.0% |
| historical | ecmwf | mixed_hot_29_71 | 88 | 13 | 14.8% | 10.9% | 40.1% |
| historical | ecmwf | strong_hot_75_100 | 100 | 21 | 21.0% | 10.4% | 106.7% |
| historical | ecmwf | weak_hot_0_25 | 29 | 0 | 0.0% | 9.6% | -100.0% |
| historical | gfs | mixed_hot_29_71 | 16 | 1 | 6.2% | 9.2% | -46.9% |
| historical | gfs | strong_hot_75_100 | 84 | 14 | 16.7% | 10.7% | 34.0% |
| historical | gfs | weak_hot_0_25 | 11 | 1 | 9.1% | 9.5% | -3.1% |

最重要的反直觉是：

- 历史强热共识通常优于弱共识，说明共识对“热尾是否发生”有信息；
- 当前 GFS 强共识仍是 1/20，说明最近失败不是单模型 outlier；
- ECMWF 当前 mixed 是 6/18，strong 反而 3/25。对 exact bracket 来说，越强的热尾共识也可能增加
  overshoot，而不是单调增加某一张彩票命中率。

当前 miss direction：

| source | consensus_bucket | miss_direction | rows |
| --- | --- | --- | --- |
| ecmwf | missing | settled_above_ticket | 1 |
| ecmwf | missing | ticket_won | 1 |
| ecmwf | mixed_hot_29_71 | settled_above_ticket | 12 |
| ecmwf | mixed_hot_29_71 | ticket_won | 6 |
| ecmwf | strong_hot_75_100 | settled_above_ticket | 2 |
| ecmwf | strong_hot_75_100 | settled_below_ticket | 20 |
| ecmwf | strong_hot_75_100 | ticket_won | 3 |
| ecmwf | weak_hot_0_25 | settled_above_ticket | 8 |
| ecmwf | weak_hot_0_25 | ticket_won | 1 |
| gfs | mixed_hot_29_71 | settled_above_ticket | 6 |
| gfs | mixed_hot_29_71 | settled_below_ticket | 4 |
| gfs | strong_hot_75_100 | settled_above_ticket | 1 |
| gfs | strong_hot_75_100 | settled_below_ticket | 18 |
| gfs | strong_hot_75_100 | ticket_won | 1 |

所以多源的正确表达不是 `all models hot => 买这张 exact ticket`，而应是：

```text
P(reach hot tail | multi-source state)
× P(land in exact bracket | reached hot tail, dispersion, city bias, weather regime)
```

严格证据边界：这些 7-model forecast 是 historical-forecast archive replay，不是每张票 decision timestamp
冻结的 alternate-source vintage。因此它足以推翻“现有数据已经证明 GFS 独自报热”的说法，
但不足以证明当时盘口时刻其他模型一定也报热。

## 5. 次日晴 / 云 / 雨：有机制，不足以筛

当前天气型在决策时按 assigned-source PIT hourly curve、forecast peak ±2h 固定分类；
历史表使用同定义的 post-hoc hourly forecast replay，只检查机制方向，不冒充 PIT：

- rain/convective：peak-window precipitation probability ≥50%;
- cloud-suppressed：非雨且 peak-window mean cloud ≥70%;
- mixed-cloud：35%..70%;
- clear-low-cloud：<35%。

| window | source | weather_regime | rows | wins | win | ask | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_shadow | ecmwf | clear_low_cloud | 15 | 2 | 13.3% | 10.2% | 25.3% |
| current_shadow | ecmwf | cloud_suppressed | 15 | 4 | 26.7% | 12.7% | 101.7% |
| current_shadow | ecmwf | mixed_cloud | 9 | 2 | 22.2% | 9.3% | 129.2% |
| current_shadow | ecmwf | rain_convective | 15 | 3 | 20.0% | 12.7% | 50.9% |
| current_shadow | gfs | clear_low_cloud | 16 | 0 | 0.0% | 11.6% | -100.0% |
| current_shadow | gfs | cloud_suppressed | 7 | 0 | 0.0% | 13.4% | -100.0% |
| current_shadow | gfs | mixed_cloud | 3 | 0 | 0.0% | 8.3% | -100.0% |
| current_shadow | gfs | rain_convective | 4 | 1 | 25.0% | 13.2% | 81.0% |
| historical | ecmwf | clear_low_cloud | 72 | 9 | 12.5% | 10.2% | 32.4% |
| historical | ecmwf | cloud_suppressed | 51 | 7 | 13.7% | 10.6% | 43.3% |
| historical | ecmwf | missing | 5 | 0 | 0.0% | 8.1% | -100.0% |
| historical | ecmwf | mixed_cloud | 40 | 3 | 7.5% | 10.0% | -24.3% |
| historical | ecmwf | rain_convective | 54 | 15 | 27.8% | 11.2% | 138.1% |
| historical | gfs | clear_low_cloud | 32 | 3 | 9.4% | 10.3% | -10.7% |
| historical | gfs | cloud_suppressed | 33 | 5 | 15.2% | 10.5% | 14.7% |
| historical | gfs | mixed_cloud | 26 | 4 | 15.4% | 11.1% | 26.3% |
| historical | gfs | rain_convective | 20 | 4 | 20.0% | 9.2% | 88.4% |

当前 GFS 的 1 个赢家确实在 rain/convective，clear 0/16、mixed 0/3、cloud-suppressed 0/7。
但这更像 **天气 regime 改变 forecast error direction / exact landing**，不是“雨天才会中”：
历史 replay 中非雨桶存在赢家，而且当前每桶 target-date block 太少。

物理解释：

- clear / low-cloud 通常扩大可加热窗口；对 hot-tail“能否到达”有利，但也提高 overshoot 下一档的风险；
- cloud-suppressed 降低 reach 概率，但若 assigned forecast 本来过热，反而可能把最终 Tmax 拉回 ticket；
- convective/rain 同时带来辐射抑制、阵前增温、风向突变，结果取决于发生时钟相对 forecast peak，而非晴/雨二元标签；
- 因此分类只适合进入连续概率模型，不能直接做 hard gate。

## 6. 城市 archetype：构成效应存在，但 source 与 city 不可分

为避免手工按赢家给城市贴标签，A1..A4 是仅用历史气候/地理连续量做的无监督聚类：
`|latitude|、历史日最高温均值/波动、7-model spread`。没有使用 ticket win/ROI。

| city_archetype | city_n | abs_lat | mean_tmax_f | sd_tmax_f | ensemble_spread_f | cities |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | 12 | 15.749 | 92.102 | 3.721 | 6.149 | Guangzhou, Jakarta, Karachi, KualaLumpur, Lagos, Lucknow, Manila, Miami, PanamaCity, Shenzhen, Singapore, Taipei |
| A2 | 18 | 32.533 | 78.769 | 5.481 | 5.304 | Atlanta, Austin, Beijing, BuenosAires, Busan, CapeTown, Chengdu, Chongqing, Dallas, Houston, Istanbul, MexicoCity, SaoPaulo, Shanghai, TelAviv, Tokyo, Wellington, Wuhan |
| A3 | 4 | 32.678 | 78.654 | 4.308 | 12.682 | Jeddah, LA, SanFrancisco, Seoul |
| A4 | 14 | 47.524 | 75.773 | 10.467 | 4.903 | Amsterdam, Ankara, Chicago, Denver, Helsinki, London, Madrid, Milan, Moscow, Munich, NYC, Paris, Seattle, Warsaw |

| window | source | city_archetype | rows | wins | win | ask | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_shadow | ecmwf | A1 | 7 | 2 | 28.6% | 14.1% | 93.8% |
| current_shadow | ecmwf | A2 | 21 | 4 | 19.0% | 10.5% | 73.0% |
| current_shadow | ecmwf | A3 | 6 | 2 | 33.3% | 11.7% | 172.5% |
| current_shadow | ecmwf | A4 | 18 | 2 | 11.1% | 11.7% | -8.8% |
| current_shadow | ecmwf | missing | 2 | 1 | 50.0% | 8.0% | 497.6% |
| current_shadow | gfs | A1 | 5 | 1 | 20.0% | 13.6% | 41.0% |
| current_shadow | gfs | A2 | 18 | 0 | 0.0% | 11.6% | -100.0% |
| current_shadow | gfs | A3 | 2 | 0 | 0.0% | 10.0% | -100.0% |
| current_shadow | gfs | A4 | 5 | 0 | 0.0% | 12.0% | -100.0% |
| historical | ecmwf | A1 | 33 | 3 | 9.1% | 11.3% | -15.8% |
| historical | ecmwf | A2 | 56 | 10 | 17.9% | 10.0% | 81.7% |
| historical | ecmwf | A3 | 20 | 2 | 10.0% | 10.1% | -6.5% |
| historical | ecmwf | A4 | 108 | 19 | 17.6% | 10.6% | 74.8% |
| historical | ecmwf | missing | 5 | 0 | 0.0% | 8.1% | -100.0% |
| historical | gfs | A1 | 25 | 6 | 24.0% | 9.4% | 116.6% |
| historical | gfs | A2 | 59 | 8 | 13.6% | 10.4% | 13.8% |
| historical | gfs | A4 | 27 | 2 | 7.4% | 11.2% | -31.3% |

城市层最大限制是 positivity：很多城市只固定走 GFS 或 ECMWF，缺少同一 city-date 的随机 source A/B。
因此 `source coefficient` 同时携带城市、纬度、气候、市场关注和历史 bias，不能解释为 ECMWF 因果优于 GFS。

## 7. 概率模型与 market baseline

训练固定为历史 ≤2026-06-20；先测 historical recent 6/21..30，再测当前 84。模型是 L2 logistic，
不扫阈值。`market_raw` 直接用 fresh ask 作为概率基准。

| eval_window | model | rows | wins | observed_rate | mean_probability | Brier | logloss |
| --- | --- | --- | --- | --- | --- | --- | --- |
| historical_recent | market_raw | 58 | 10 | 17.2% | 9.9% | 0.139 | 0.446 |
| current_shadow | market_raw | 84 | 12 | 14.3% | 11.6% | 0.118 | 0.387 |
| historical_recent | market_source_distance | 58 | 10 | 17.2% | 13.2% | 0.134 | 0.436 |
| current_shadow | market_source_distance | 84 | 12 | 14.3% | 16.0% | 0.114 | 0.375 |
| historical_recent | market_source_city | 58 | 10 | 17.2% | 13.3% | 0.139 | 0.452 |
| current_shadow | market_source_city | 84 | 12 | 14.3% | 16.2% | 0.115 | 0.379 |
| historical_recent | market_source_city_multisource_weather | 58 | 10 | 17.2% | 12.9% | 0.148 | 0.487 |
| current_shadow | market_source_city_multisource_weather | 84 | 12 | 14.3% | 15.8% | 0.124 | 0.402 |

| model | source | rows | wins | expected_wins | observed_rate | expected_rate |
| --- | --- | --- | --- | --- | --- | --- |
| market_source_city | ecmwf | 54 | 11 | 9.112 | 20.4% | 16.9% |
| market_source_city | gfs | 30 | 1 | 4.497 | 3.3% | 15.0% |
| market_source_city_multisource_weather | ecmwf | 54 | 11 | 9.319 | 20.4% | 17.3% |
| market_source_city_multisource_weather | gfs | 30 | 1 | 3.925 | 3.3% | 13.1% |
| market_source_distance | ecmwf | 54 | 11 | 8.998 | 20.4% | 16.7% |
| market_source_distance | gfs | 30 | 1 | 4.477 | 3.3% | 14.9% |

full mechanism model 的 current `observed - expected`，按 target_date block bootstrap：

| source | observed_minus_expected | ci_low | ci_high |
| --- | --- | --- | --- |
| ecmwf | 3.1% | -7.8% | 20.5% |
| gfs | -9.8% | -13.9% | -5.0% |

如果 source/city/weather 模型不能在 historical recent 和当前同时打败 market 的 Brier/logloss，
它只能解释 shift，不能用于重新定价。当前结论正是如此：加入大量机制特征没有形成稳定的 market residual alpha。
GFS residual 为负且 block CI 不跨 0，但这个模型本身没有在 historical recent 打败 market，
所以它证明“历史映射解释不了当前 GFS”，不证明存在可交易的新 gate。

## 8. “完全不会中”的筛选反证

| rule | hist_rows | hist_wins | hist_roi | current_rows | current_wins | current_roi | current_zero_win_95_upper | hard_filter_falsified_by_history |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gfs_clear_low_cloud | 32 | 3 | -10.7% | 16 | 0 | -100.0% | 17.1% | True |
| gfs_non_rain | 91 | 12 | 10.0% | 26 | 0 | -100.0% | 10.9% | True |
| gfs_strong_hot_consensus | 84 | 14 | 34.0% | 20 | 1 | -59.5% |  | True |
| weak_hot_consensus_any_source | 40 | 1 | -74.3% | 9 | 1 | -9.9% |  | True |
| assigned_colder_than_consensus_gt1f | 54 | 4 | -18.3% | 10 | 2 | 61.1% |  | True |

任何当前 0-win 桶，只要历史同机制已有 winner，就已经否定“完全不会中”；样本 0/n 的上界也远不等于零。
因此这些条件最多做 frozen `lottery-risk / no-size-up` telemetry，不能删票。

## 9. 讨论：这次多源带来的新理解

1. **把 source effect 改写成 forecast-state effect。** 真正可迁移的对象不是 GFS/ECMWF 名字，而是
   assigned-vs-consensus 偏差、ensemble spread、各模型 hot vote，以及每城 rolling bias。
2. **把 hot-tail 与 exact landing 分开。** 多源共识较擅长判断会不会进入热尾；彩票收益取决于进入后落在哪一档。
   强共识既可能提高 reach，也可能提高 overshoot。
3. **天气型是 error-direction moderator。** 云雨不是静态筛选器，而是改变 forecast bias、剩余加热窗口和分布宽度的变量。
4. **城市是层级效应，不是黑名单。** 应做 hierarchical city/source calibration 或 partial pooling；
   逐城 ROI 会被一两个彩票 winner 主导。
5. **最近 GFS 崩盘仍有不可约 residual。** 7-model strong-hot 也救不了，城市/价格/距离也不足以解释；
   需要继续收真正 PIT alternate forecasts，才能区分 collector/version shift、季节 regime shift 与纯随机低尾。

## 10. 局限、可证伪的下一步与裁决

- 当前只有 8 个 target-date blocks；所有 current weather 交互都不具备独立 forward。
- 多源 daily/hourly backfill 没有精确 decision-version timestamp，只能 post-hoc。
- 当前 curves 对 assigned source 完整，但 alternate source 不完整；这正是旧报告 `any_source` 样本过薄的根因。
- source×city 缺 overlap，不能做因果 source treatment。
- 本轮候选切片是 mechanism diagnostics，没有做多重检验后的 selector promotion。
- canonical metadata 没有可靠 elevation；若你说的 “Altas” 指 altitude，本轮只测了纬度/历史气候 archetype，
  没有伪造海拔结论。

下一步已冻结为：

```text
每张 HeadA candidate 在 decision timestamp 同步记录
7-model Tmax + peak-window cloud/precip/wind + forecast vintage
→ 先预测 reach-hot-tail
→ 再预测 exact-bracket conditional landing
→ raw market ask 为基准
→ target_date block、至少 20 个新日期后 frozen forward
```

最终裁决：

```text
significance = PASS only for current GFS-vs-ECMWF descriptive gap
historical baseline = FAIL for permanent source ban
multi-source mechanism = H1 unsupported in post-hoc replay; decision-time causal test unavailable
weather/city interaction = plausible but underpowered
probability baseline = market not beaten robustly
conclusion = inconclusive; keep all as continuous shadow telemetry; no live selector change
```

## 产物

- evaluator: `scripts/analysis/forecast_quality/research_heada_multisource_city_regime_paper_v1.py`
- structured summary: `generated/heada_multisource_city_regime_v1/summary.json`
- row-level data: `historical_enriched.csv`, `current_enriched.csv`
- diagnostics: `source_period.csv`, `source_consensus.csv`, `source_weather.csv`,
  `source_archetype.csv`, `probability_scores.csv`, `filter_falsification.csv`
- figure: `mechanism_panels.png`
