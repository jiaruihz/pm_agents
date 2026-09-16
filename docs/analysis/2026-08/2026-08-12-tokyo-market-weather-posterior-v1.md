# Tokyo market-weather posterior v1（数据区间修正版）

## 结论

旧版“只有 3 个 strict exact dates、50/50 posterior 打败 market”的结论已撤回。它不是 Tokyo
只有 3 天数据，而是 adapter 错把只在新 WCIR bundle 中完整存在的 `book_association` 当成了原始数据分母。
改为直接按 raw JMA first-seen 与 raw active-bracket book 做因果 join 后，2026-08-01..11 共得到
`554 rows / 11 settled target dates`，而不是 `152 rows / 3 dates`。

在修正后的 11 日 frozen forward 上，冻结 v5 weather-only 明显弱于 market；此前选定的 50/50
market-weather blend 也没有打败 market。其 5-share fee 后回放虽仍为正，但只有
`30 笔 / 20 胜 / PnL +$1.8183 / ROI +1.85%`，target-date bootstrap 95% CI
`[-20.61%, +18.79%]`，不能称为正收益模型。该候选降级为 `inconclusive`，不再作为 Tokyo
shadow 升级候选；live、plan、order、fill 均未改变。

## 为什么旧版只剩 3 天

旧 adapter 同时要求：v7 bundle、NO side、`book_association.probability_status=two_sided_midpoint`、
bundle decision clock 距 first-seen 不超过 30 秒。这个条件只在 8/7、8/10、8/11 的新格式行上完整成立：

1. 8/2–8/6 的迁移 bundle 把迁移时刻放进 `decision_ts_utc`，且没有 `book_association`；原始
   JMA first-seen 与原始 book 仍在，不能拿迁移时刻当交易时钟。
2. 8/8 bundle 只保留 interval-censored 状态；原始 book 中存在后续 causal two-sided quote。
3. 8/9 没有 v7 bundle，但 raw JMA 与 raw book 都完整；官方 observation snapshot 有缺口，book
   仍保存了当时的 official bracket anchor，因此只按“anchor-only、其余 METAR 特征 missing”评分。
4. 8/1 是旧 schema，没有显式 `pit_lineage_class`，但 `source_first_seen=fetched=local_detect` 且
   raw/payload hash 齐全；保留为 `collector_exact_legacy_hash_verified`，不把 issue time 或 late backfill
   冒充 first-seen。

修复时还发现两个独立的 interval bug：Tokyo target date 横跨两个 UTC physical shards，不能只读
同名 UTC 目录；current-bracket 跨版本稳定 anchor 是 `reference_market_value`，不是曾在 8/1 偏一档、
8/11 为空的 `metar_running_max_market_value`。两项都已加入 regression test。

## 清洁后的逐日 coverage

模型窗口固定为 Tokyo 06:00–18:00；每个 JMA observation 只保留真实最早 first-seen，取 first-seen
后 180 秒内最早 causal two-sided current-official-bracket book。盘口缺失、one-sided、超过 180 秒均记为
evidence gap，不当作策略过滤。

| target date | exact obs | 06–18 obs | current book | causal ≤180s | two-sided/scored/settled | 备注 |
|---|---:|---:|---:|---:|---:|---|
| 8/1 | 97 | 72 | 63 | 63 | 53 | 83 条旧 schema hash-verified exact |
| 8/2 | 73 | 48 | 48 | 47 | 45 | source 仅覆盖窗口后 48 个 observation |
| 8/3 | 40 | 39 | 39 | 39 | 39 | source 明显缺早段 |
| 8/4 | 97 | 72 | 72 | 72 | 52 | 20 个 one-sided gap |
| 8/5 | 97 | 72 | 72 | 72 | 57 | 15 个 one-sided gap |
| 8/6 | 73 | 72 | 58 | 54 | 41 | 14 个 current-book gap、4 个 latency gap |
| 8/7 | 97 | 72 | 71 | 71 | 64 | 7 个 one-sided gap |
| 8/8 | 97 | 72 | 72 | 72 | 53 | 19 个 one-sided gap |
| 8/9 | 97 | 72 | 72 | 72 | 59 | 59 行为 PIT book-anchor-only，官方 snapshot 缺口显式保留 |
| 8/10 | 97 | 72 | 72 | 71 | 57 | 1 个 latency、14 个 one-sided gap |
| 8/11 | 97 | 72 | 72 | 72 | 34 | 38 个 one-sided gap |
| **合计** | — | **735** | **711** | **705** | **554 / 11日** | 554 行均有 settlement 与 5-share ask depth |

完整机器可读矩阵在
`tokyo_market_posterior/raw_exact_plus_archive_20260811_v5/coverage_by_target_date.csv`。

## 修正后的同分母概率结果

开发仍只用 2026-07-16..28 的 `57 rows / 7 dates` archive-reconstructed +15m 数据选择固定
weather weight `0.5`；8/1..11 的 554 rows 不参与选择，只作 frozen forward。三者在同一 rows、
同一 settlement label 上按 target date 等权：

| 模型 | Brier | logloss | accuracy | ΔBrier vs market (95% CI) | ΔLL vs market (95% CI) |
|---|---:|---:|---:|---:|---:|
| market | **0.07473** | **0.24058** | **90.99%** | — | — |
| frozen v5 weather-only | 0.13864 | 0.43943 | 80.23% | +0.06391 `[+0.01239,+0.13213]` | +0.19885 `[+0.06340,+0.39451]` |
| frozen market-weather 50/50 | 0.09063 | 0.27956 | 87.20% | +0.01590 `[-0.00572,+0.03926]` | +0.03898 `[-0.01166,+0.09655]` |

正 delta 表示比 market 更差。weather-only 的 CI 全正，说明它在这个 forward 明确过度自信；50/50
blend 的点估也更差且 CI 跨 0。主要问题在 midday：market/blend Brier 为 `0.11346/0.15196`；
morning 全部 label 都是 NO winner，不能用 100% accuracy 当 alpha。

旧 3 日切片恰好只保留 8/7、8/10、8/11，而完整逐日结果显示 blend 只在 8/10、8/11 明显改善，
8/1–8/5、8/7–8/9 多数更差。格式迁移造成的非随机缺日放大了后两天，因此旧 3 日结论属于
coverage selection bias，不是可靠 forward 证据。

## 5-share fee 后回放

固定表达不变：每个 `target_date × bracket` 首次 `posterior > 5-share ask VWAP + official fee`
时 BUY NO，不加事后时段、价格或 edge gate。

| 指标 | 修正前 3 日 | 修正后 11 日 |
|---|---:|---:|
| trades | 9 | 30 |
| wins | 7 | 20 |
| win rate | 77.78% | 66.67% |
| cash cost | $31.7822 | $98.1817 |
| fee-adjusted PnL | +$3.2178 | +$1.8183 |
| ROI | +10.12% | +1.85% |
| target-date ROI 95% CI | [-100.00%, +47.97%] | **[-20.61%, +18.79%]** |

逐日 PnL 中 8/2 `-$4.2841`、8/4 `-$1.8444`、8/10 `-$4.2336`；8/11 `+$7.1340`
贡献了大部分正收益。点估为正来自少数日期波动，且 proper score 已输给 market，不能作为交易依据。

## 影响半径与当前动作

- 污染窗口：旧报告与 registry 中所有 `152 rows / 3 dates`、Brier/logloss 优于 market、ROI +10.12%
  的 Tokyo posterior 结论。
- 决策影响：这是 research/zero-notional；没有 TradeIntent、plan、order、fill，也没有 live 行为变化，
  因此错误结论没有产生真实订单影响。
- `significance=FAIL`：ROI CI 跨 0。
- `baseline=FAIL`：50/50 blend 的 proper score 点估输给 market；weather-only 显著输给 market。
- `forward=PASS_COVERAGE / FAIL_ALPHA`：11 个 frozen dates 已覆盖，但 alpha 不成立。
- `conclusion=inconclusive`：停止把固定 `.5` blend 当 shadow_candidate；raw market 保持零模型。

下一轮模型研究只能把这 11 日锁为未调参 holdout，开发新的“market offset + source innovation”模型；
不能再根据 8/1..11 的错例加时段/价格 hard filter，也不能从正的 selected-trade PnL 倒推模型有效。

## 产物

- materializer：`weather_model_evaluation/tokyo_market_prior_adapter.py`
- cleaned input：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tokyo_market_posterior/raw_exact_plus_archive_20260811_v5/`
- corrected result：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tokyo_market_posterior/posterior_frozen_20260811_v9/`
- 旧 v7 artifact 保留作影响审计，不再是当前结论。
