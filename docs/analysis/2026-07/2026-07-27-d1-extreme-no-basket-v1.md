# D-1 Extreme NO Basket v1

> 2026-07-27；research replay；zero notional；不改 live。

## 数据快照

- 数据源：`runtime/weather.db` canonical Tmax v2 ladder + `settlement_outcomes`。
- DB mtime UTC：`2026-07-27T11:14:26.511879921+00:00`。
- 记录：835,031 rung rows；646 settled policy-baskets；unsettled=0，missing_bracket=0。

## 结论

固定在目标日前一天，分别取当地 12:00–18:00、18:00–24:00 区间内首个可执行 full-ladder snapshot；每个 city-day 等份买最低档 NO 和最高档 NO，direct ask 入场、官方 Weather taker fee、持有到结算。

| slice | baskets | dates | tail hit | break-even tail | market tail | avg cost | PnL | ROI (95% CI) | excess vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | 338 | 12 | +2.07% | +1.88% | +2.30% | 1.9812 | $-0.647 | -0.10% [-0.78%, +0.47%] | +0.11% [-0.57%, +0.68%] |
| D-1_18_24_first | 308 | 12 | +2.60% | +2.18% | +2.68% | 1.9782 | $-1.281 | -0.21% [-0.94%, +0.43%] | +0.04% [-0.72%, +0.65%] |

裁决：`inconclusive`。这等于同时做空两个最外侧挂牌 condition；在 ladder 恰好覆盖且互斥完备时，可改写成“一份 $1 回款 + 中间所有档位 YES”。市场已把两端低命中率计入 NO 价格，实际收益只剩 tail calibration residual，且还要付两腿 spread/fee。

## 时间稳定性（描述性，不冒充预注册 forward）

| slice | baskets | dates | tail hit | break-even tail | market tail | avg cost | PnL | ROI (95% CI) | excess vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first|early_half | 197 | 6 | +3.55% | +2.57% | +3.02% | 1.9743 | $-1.935 | -0.50% [-1.40%, +0.38%] | -0.27% [-1.18%, +0.61%] |
| D-1_12_18_first|recent_half | 141 | 6 | +0.00% | +0.91% | +1.28% | 1.9909 | $+1.288 | +0.46% [+0.21%, +0.67%] | +0.65% [+0.34%, +0.86%] |
| D-1_18_24_first|early_half | 198 | 6 | +3.54% | +2.72% | +3.23% | 1.9728 | $-1.621 | -0.41% [-1.47%, +0.50%] | -0.15% [-1.29%, +0.84%] |
| D-1_18_24_first|recent_half | 110 | 6 | +0.91% | +1.22% | +1.69% | 1.9878 | $+0.340 | +0.16% [-0.07%, +0.41%] | +0.40% [+0.21%, +0.64%] |

本次规则未在样本开始前冻结，因此 early/recent half 只作 chronology check，不能把 recent half 写成正式 frozen forward。

## Signal / Evidence Funnel

Signal funnel（snapshot/city-day）:

- raw universe：所有 complete + `pit_verified_capture` + settled ladder。
- mechanism candidate：D-1 snapshot。
- first city-day signal：每个固定当地时段首个可执行 snapshot。
- policy selection：不使用 forecast、价格阈值、城市筛选或事后 winner。

Evidence funnel（snapshot）:

- `raw_rung_rows` = 835,031
- `complete_pit_settled_snapshots` = 84,874
- `both_extreme_labels_available_snapshots` = 83,047
- `d1_snapshots` = 49,291
- `d1_both_extremes_executable_snapshots` = 13,780
- `d1_full_ladder_80_snapshots` = 13,771

## Tail-hit Losses

| policy | date | city | low | high | cost | payout | pnl |
|---|---|---|---|---|---:|---:|---:|
| D-1_12_18_first | 2026-07-05 | Jeddah | 29 | 39+ | 1.4009 | 1.0 | -0.4009 |
| D-1_12_18_first | 2026-07-06 | Amsterdam | 18 | 26+ | 1.9800 | 1.0 | -0.9800 |
| D-1_12_18_first | 2026-07-16 | Jeddah | 32 | 38+ | 1.5714 | 1.0 | -0.5714 |
| D-1_12_18_first | 2026-07-16 | Shenzhen | 27 | 35+ | 1.9952 | 1.0 | -0.9952 |
| D-1_12_18_first | 2026-07-17 | Chengdu | 29 | 39+ | 1.9762 | 1.0 | -0.9762 |
| D-1_12_18_first | 2026-07-17 | LA | 73 | 86-87 | 1.9971 | 1.0 | -0.9971 |
| D-1_12_18_first | 2026-07-17 | Lucknow | 26 | 35+ | 1.7487 | 1.0 | -0.7487 |
| D-1_18_24_first | 2026-07-05 | Jeddah | 29 | 39+ | 1.5215 | 1.0 | -0.5215 |
| D-1_18_24_first | 2026-07-06 | Amsterdam | 18 | 26+ | 1.9724 | 1.0 | -0.9724 |
| D-1_18_24_first | 2026-07-16 | Jeddah | 33 | 38+ | 1.6200 | 1.0 | -0.6200 |
| D-1_18_24_first | 2026-07-16 | Shenzhen | 27 | 35+ | 1.9924 | 1.0 | -0.9924 |
| D-1_18_24_first | 2026-07-17 | Chengdu | 29 | 39+ | 1.9810 | 1.0 | -0.9810 |
| D-1_18_24_first | 2026-07-17 | LA | 73 | 86-87 | 1.9981 | 1.0 | -0.9981 |
| D-1_18_24_first | 2026-07-17 | Lucknow | 29 | 35+ | 1.8167 | 1.0 | -0.8167 |
| D-1_18_24_first | 2026-07-18 | London | 21 | 29 | 1.9924 | 1.0 | -0.9924 |

## 口径与 Gate

- unit：basket / city-day；`trade_class=research_replay`，不是 actual fill。
- exact bracket：最低/最高指当时 ladder 的两个最外侧挂牌 condition；仅当问题文本明确写 `or below` / `or higher` 时才是 open-ended。命中其中一端时该腿 NO 归零，另一腿兑付 $1；落在其他档或挂牌范围外时两腿都兑付。
- capacity：每腿仅要求 top ask size >=1 share；未测试更大 size。
- significance：以 target_date block bootstrap 95% CI。
- baseline：同一 snapshot 归一化 full-ladder YES midpoint 的两端概率质量。
- forward：`NA`；chronological half 非预注册 frozen forward。
- conclusion：`inconclusive`；不启动 shadow、不改 live。

Artifacts:

- `scripts/analysis/market_structure_edge/research_d1_extreme_no_basket_v1.py`
- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/summary.json`
- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/selected_baskets.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/tail_hit_losses.csv`
