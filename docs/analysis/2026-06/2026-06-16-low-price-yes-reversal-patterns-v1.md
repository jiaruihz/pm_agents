# Low-Price YES Reversal Patterns v1

> generated_at_utc: `2026-06-15T16:41:12.382845+00:00`
> target_metric: `low_price_yes_reversal_pattern_v1`
> Scope: opportunity-layer convexity sleeve research only; no N100/live config changed; no orders placed.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于强制自检和 CLOB gate 状态。
- DB last_modified_utc: `2026-06-15T16:31:16.772910+00:00`
- MAX(fact_built_at_utc): `2026-06-15T16:31:04.483297+00:00`
- CLOB gate: `gate_pass=True`; fail_reasons=`[]`
- 收益数字为机会层 `counterfactual_pnl`，不是 live fill PnL。
- train: `2026-05-06` -> `2026-05-30` (22 event_dates)
- holdout: `2026-06-01` -> `2026-06-10` (10 event_dates)

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-15T16:31:04.483297+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "n": 855
    },
    {
      "trade_class": "live_simulated",
      "n": 624
    },
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": null,
      "n": 150
    },
    {
      "settlement_status": "settled",
      "n": 4250
    }
  ],
  "candidate_coverage": [
    {
      "rows": 30140,
      "eligible": 10366,
      "paper_ordered": 3968,
      "live_filled": 348
    }
  ],
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

### Funnel

| step | rows | active_dates | cities | drop | retained_from_full | note |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| fact_signal_candidates | 30140 | 43 | 49 | NA | +100.0% | full opportunity |
| BUY_YES | 13431 | 43 | 49 | -16709 (-55.4%) | +44.6% | side only |
| BUY_YES with decision price | 7252 | 42 | 49 | -6179 (-46.0%) | +24.1% | decision price present |
| low-price BUY_YES <0.25 | 6350 | 42 | 49 | -902 (-12.4%) | +21.1% | convexity universe before settlement |
| settled/evaluable low-price BUY_YES | 497 | 32 | 48 | -5853 (-92.2%) | +1.6% | can score reversal |
| reversal hits | 51 | 21 | 32 | -446 (-89.7%) | +0.2% | final_yes=1 |

## Target Metric

`low_price_yes_reversal_pattern_v1` = 在 full-opportunity 分母上，观察 `BUY_YES`、`decision_entry_price < 0.25`、settled/evaluable 的机会中，哪些事前标签更容易发生 `final_yes=1` 的反转命中。这里不追求 90% 胜率；核心是小预算右尾暴露是否有可解释触发条件。

Base low-price YES: rows `497`, active_dates `32`, cities `48`, hits `51`, hit_rate `+10.3%`, ROI `+17.3%`.

## 反转集中在哪些城市

| city | rows | active_dates | hits | hit_rate | pnl | roi | avg_price |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Madrid | 9 | 8 | 4 | +44.4% | +26.47 | +1957.5% | 0.150 |
| Miami | 23 | 16 | 5 | +21.7% | +20.66 | +704.2% | 0.128 |
| Busan | 9 | 7 | 3 | +33.3% | +19.43 | +1836.9% | 0.117 |
| Chengdu | 16 | 4 | 2 | +12.5% | +9.83 | +967.5% | 0.064 |
| Moscow | 8 | 4 | 2 | +25.0% | +9.77 | +955.0% | 0.128 |
| Warsaw | 13 | 8 | 2 | +15.4% | +8.66 | +762.9% | 0.087 |
| Guangzhou | 4 | 2 | 1 | +25.0% | +7.72 | +3386.0% | 0.057 |
| Jeddah | 9 | 4 | 2 | +22.2% | +6.47 | +478.2% | 0.150 |
| Singapore | 4 | 4 | 1 | +25.0% | +5.81 | +1383.8% | 0.105 |
| Wellington | 5 | 4 | 1 | +20.0% | +5.55 | +1244.7% | 0.089 |
| LA | 15 | 11 | 2 | +13.3% | +5.54 | +383.1% | 0.096 |
| Denver | 3 | 2 | 1 | +33.3% | +5.28 | +1116.4% | 0.158 |
| Atlanta | 10 | 9 | 2 | +20.0% | +4.60 | +298.3% | 0.154 |
| Milan | 10 | 5 | 1 | +10.0% | +3.65 | +574.8% | 0.064 |
| Shenzhen | 6 | 5 | 1 | +16.7% | +3.45 | +526.7% | 0.109 |

## 反转集中在哪些日期

| event_date | rows | cities | hits | hit_rate | pnl | roi | hit_cities |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-05-30 | 52 | 32 | 8 | +15.4% | +35.64 | +803.4% | Chengdu, Jeddah, London, Miami, Moscow, SaoPaulo, Shenzhen, Warsaw |
| 2026-05-14 | 62 | 33 | 9 | +14.5% | +27.41 | +438.0% | Atlanta, Busan, Guangzhou, Jeddah, Madrid, Miami, Milan, NYC |
| 2026-05-13 | 43 | 26 | 7 | +16.3% | +20.59 | +416.9% | Amsterdam, BuenosAires, Chicago, Helsinki, LA, Miami, NYC |
| 2026-05-23 | 4 | 4 | 2 | +50.0% | +12.10 | +1531.6% | Busan, Houston |
| 2026-05-15 | 23 | 15 | 3 | +13.0% | +9.69 | +476.7% | Busan, Miami, Tokyo |
| 2026-06-07 | 1 | 1 | 1 | +100.0% | +7.75 | +3444.4% | London |
| 2026-05-16 | 6 | 6 | 2 | +33.3% | +7.00 | +538.5% | Atlanta, Wuhan |
| 2026-05-20 | 12 | 6 | 2 | +16.7% | +6.18 | +447.2% | Ankara, Moscow |
| 2026-06-01 | 2 | 2 | 1 | +50.0% | +5.80 | +1381.0% | London |
| 2026-06-03 | 2 | 2 | 1 | +50.0% | +5.80 | +1381.0% | Munich |
| 2026-05-07 | 19 | 11 | 2 | +10.5% | +5.08 | +340.5% | Madrid, Shanghai |
| 2026-05-26 | 8 | 4 | 1 | +12.5% | +2.65 | +360.5% | Miami |
| 2026-06-04 | 4 | 4 | 1 | +25.0% | +1.95 | +242.2% | Madrid |
| 2026-06-02 | 1 | 1 | 0 | +0.0% | -1.15 | -1000.0% |  |
| 2026-05-22 | 6 | 4 | 1 | +16.7% | -1.35 | -118.9% | Beijing |

## 事前标签切片

### Price bucket

| price_bucket | rows | active_dates | hits | hit_rate | pnl | roi | avg_edge |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.15-0.20 | 71 | 21 | 16 | +22.5% | +37.04 | +301.3% | 0.151 |
| 0.00-0.05 | 177 | 17 | 7 | +4.0% | +30.93 | +791.7% | 0.178 |
| 0.05-0.10 | 90 | 17 | 6 | +6.7% | -4.60 | -71.1% | 0.199 |
| 0.20-0.25 | 82 | 30 | 16 | +19.5% | -21.86 | -120.2% | 0.159 |
| 0.10-0.15 | 77 | 18 | 6 | +7.8% | -32.87 | -353.9% | 0.180 |

### Edge bucket

| edge_bucket | rows | active_dates | hits | hit_rate | pnl | roi | avg_price |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| >=0.20 | 175 | 24 | 26 | +14.9% | +91.86 | +546.3% | 0.096 |
| 0.00-0.05 | 67 | 23 | 6 | +9.0% | -16.54 | -216.1% | 0.114 |
| 0.05-0.10 | 59 | 19 | 5 | +8.5% | -18.06 | -265.4% | 0.115 |
| 0.10-0.20 | 196 | 24 | 14 | +7.1% | -48.61 | -257.7% | 0.096 |

### Model probability bucket

| model_p_bucket | rows | active_dates | hits | hit_rate | pnl | roi | avg_price |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| >=0.35 | 127 | 28 | 26 | +20.5% | +73.69 | +395.5% | 0.147 |
| 0.00-0.05 | 10 | 6 | 0 | +0.0% | -1.11 | -1000.0% | 0.011 |
| 0.05-0.10 | 19 | 13 | 0 | +0.0% | -5.90 | -1000.0% | 0.031 |
| 0.10-0.20 | 122 | 22 | 5 | +4.1% | -8.49 | -145.2% | 0.048 |
| 0.20-0.35 | 219 | 25 | 20 | +9.1% | -49.53 | -198.5% | 0.114 |

### Hour bucket

| hour_bucket | rows | active_dates | hits | hit_rate | pnl | roi | avg_price |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| T-22-24 | 490 | 32 | 51 | +10.4% | +15.15 | +30.6% | 0.101 |
| T-24-28 | 7 | 5 | 0 | +0.0% | -6.51 | -1000.0% | 0.093 |

### Forecast source / model

| forecast_source | model_version | rows | active_dates | hits | hit_rate | pnl | roi |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| open_meteo_live_gfs | gfs | 207 | 29 | 21 | +10.1% | +11.55 | +58.2% |
| open_meteo_live_ecmwf | ecmwf | 290 | 27 | 30 | +10.3% | -2.90 | -9.6% |

## 小仓位试探规则

| rule | rows | dates | hits | hit_rate | ROI | train ROI | holdout rows | holdout ROI | top3 date removed ROI | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `all_low_yes` | 497 | 32 | 51 | +10.3% | +17.3% | +10.5% | 18 | +103.4% | -217.4% | All settled/evaluable low-price BUY_YES |
| `price_lt_010` | 267 | 18 | 13 | +4.9% | +254.0% | +254.0% | 0 | NA | -150.9% | price < 0.10 |
| `edge_ge_020` | 175 | 24 | 26 | +14.9% | +546.3% | +604.4% | 9 | +78.2% | +62.3% | edge >= 0.20 |
| `price_lt_010_edge_ge_020` | 103 | 15 | 10 | +9.7% | +1181.7% | +1181.7% | 0 | NA | +388.7% | price < 0.10 and edge >= 0.20 |
| `model_p_ge_020` | 346 | 31 | 46 | +13.3% | +55.4% | +43.4% | 16 | +201.2% | -269.5% | model_p_yes >= 0.20 |
| `model_p_ge_035` | 127 | 28 | 26 | +20.5% | +395.5% | +426.7% | 12 | +195.2% | -205.3% | model_p_yes >= 0.35 |
| `edge_ge_020_model_p_ge_035` | 102 | 24 | 20 | +19.6% | +513.3% | +584.4% | 9 | +78.2% | -151.5% | edge >= 0.20 and model_p_yes >= 0.35 |
| `price_015_020_edge_ge_020` | 21 | 11 | 8 | +38.1% | +1202.9% | +1314.5% | 1 | -1000.0% | -564.3% | 0.15 <= price < 0.20 and edge >= 0.20 |
| `spread_le_003` | 180 | 19 | 18 | +10.0% | +59.6% | +2.0% | 15 | +326.7% | -240.5% | yes_spread <= 0.03 |
| `city_train_positive_top12` | 133 | 26 | 27 | +20.3% | +850.2% | +1007.3% | 8 | -390.2% | +245.2% | train-positive cities top12: Miami, Busan, Madrid, Moscow, Warsaw, Chengdu, LA, Jeddah |
| `convexity_probe_v1` | 95 | 21 | 17 | +17.9% | +1247.0% | +1442.6% | 5 | -14.8% | +452.6% | city_train_positive_top12 and price <0.10 or edge>=0.20, one share budget candidate |

## 当前 zero-notional shadow 候选

下面只是一份按当前 BJ 日期、`edge>=0.20`、每 city-date 取 `edge/price` 最高一条的观察清单；不下单，不代表 live 建议。

| event_date | city | bracket | price | edge | model_p_yes | spread | depth_5c | snapshot_ts |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-06-16 | Beijing | 30 | 0.0355 | 0.2434 | 0.2789 | 0.0150 | 90.6 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Chengdu | 29 | 0.0470 | 0.2009 | 0.2479 | 0.0060 | 149.7 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Manila | 36 | 0.0650 | 0.2280 | 0.2930 | 0.0600 | 87.8 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Shanghai | 30+ | 0.0030 | 0.2035 | 0.2065 | 0.0020 | 1508.0 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Singapore | 30 | 0.2450 | 0.2057 | 0.4507 | 0.0200 | 262.4 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Taipei | 28 | 0.0800 | 0.2017 | 0.2817 | 0.0200 | 160.8 | 2026-06-15T15:00:49Z |
| 2026-06-16 | Tokyo | 24 | 0.0275 | 0.2211 | 0.2486 | 0.0100 | 799.7 | 2026-06-15T14:00:53Z |

## 8 环覆盖自检

- 1 描述性绩效切片: covered，机会层 low-price BUY_YES reversal。
- 2 统计推断: partial，本轮重点是模式发现；未把它声明为 confirmed live 策略。
- 3 信号判别: partial，用 price/edge/model/source/hour/city/date 标签找反转集中区。
- 4 概率分布评估: partial，使用 `model_p_yes` 分桶但不重训校准模型。
- 5 执行微结构: partial，报告 spread/depth 标签；未额外 raw orderbook join。
- 6 容量: partial，小仓位 sleeve，默认 `$1` 单笔级别。
- 7 组合相关性: covered by event_date/city/date concentration。
- 8 基准/反事实: partial，使用同 universe ROI/hit-rate 对照；不是 live PnL。

## 结论

这个方向目前的意思是：低价 YES 不适合按高胜率策略评估，应该按小预算 convexity sleeve 评估。全体 settled/evaluable low-price YES 的 hit_rate 是 `+10.3%`，ROI `+17.3%`；利润高度集中在少数城市和日期。

反转更多出现的地方不是一个单独万能阈值，而是 city/date + 低价/高 edge 标签的组合。本轮最适合继续 shadow 的规则是 `edge_ge_020`：edge >= 0.20。它 overall ROI `+546.3%`，holdout rows `9`，holdout ROI `+78.2%`。

当前动作：不要直接 live；可以建 zero-notional shadow / paper journal。试探方式应是 `$1/order`、每日/每周固定预算、每 city-date 最多 1 单，连续记录未来 20-40 个触发后再判断 tiny-live。

## 产物

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-16-low-price-yes-reversal-patterns-v1.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-16-low-price-yes-reversal-patterns-v1.md`
