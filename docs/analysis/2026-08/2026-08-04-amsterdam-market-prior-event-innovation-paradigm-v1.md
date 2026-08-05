# Amsterdam market-prior event-innovation paradigm v1

Status: `research prototype / historical holdout point-pass / non-executable / frozen-forward required`

## 结论

V7 不是“天气模型彻底失败”，而是被错误地当成了交易 fair price。当前通用 selector 直接使用
`edge = p_weather - executable_cost`；这会把 weather 与接近确定性的 market 巨大分歧解释成超级机会。

新的主范式是：**market 作 prior，KNMI 只提供 first-seen information innovation；模型学习 market log-odds 应被修正多少。**
CrossNO 保留为稀疏 event benchmark，V7 保留为 weather feature/head；不再让 V7 standalone probability 直接决定交易。

## 为什么原连接方式有问题

1. V7 artifact 的 freeze metadata 已明确写着 `weather probability artifact only; market residual remains separate`，但 WCIR
   selector 仍直接比较 weather probability 与含费成本。
2. 2026-07 historical holdout 的同 checkpoint price reference 上，weather-only binary logloss `0.16660`，差于 market
   `0.16005`；full-ladder ordinal logloss 更是比 market 高 `+0.10648`，date-block CI `[+0.00171,+0.21732]`。
3. 8 月 3 日晚间 V7 连续给 `P(31 NO wins)=29.5%..37.2%`，market 约 `0.15%`。最终 31°C NO 输；这不是“31%事件不能输”，
   但暴露了尾部概率不能无收缩覆盖成熟盘口。
4. 这批 Amsterdam decision bundle 的 `pit_provenance` 实际为 `archive_reconstruction`，information event 仍来自
   `legacy_city_probability_shadow`；因此它是 forward-like shadow plumbing，不应称作已完成的 clean live PIT 证据。
5. bundle 只保留模型输出概率和 lineage，没有持久化完整 raw feature vector，导致极端概率不能从单条 candidate 直接审计。

## 第一个 market-prior prototype

固定输入为 11,800 个 timestamped price-reference checkpoints / 112 dates；它没有 bid/ask/depth，证据等级仅为
`historical_timestamped_price_reference_non_executable`。

- train：2026-04-03..06-30，8,858 rows / 84 dates。
- historical holdout：2026-07-01..07-29，2,942 rows / 28 dates。
- label：`P(final Tmax leaves current bracket)`，与 current-bracket NO 同义。
- 公式：

```text
logit(p_post) = logit(p_market)
              + intercept
              + beta * (logit(p_weather) - logit(p_market))
```

target-date-equal train 加固定 L2 后，`intercept=-0.0170`、`beta=0.0641`。也就是说，在历史可比窗口里，模型只愿意
吸收约 6.4% 的 weather-vs-market log-odds 分歧，而不是把 weather 当完整 fair price。

| July holdout | Brier | Logloss | Δ Brier vs market | Δ Logloss vs market |
|---|---:|---:|---:|---:|
| market | 0.04926 | 0.16005 | — | — |
| weather-only | 0.05072 | 0.16660 | +0.00146 | +0.00654 |
| linear 25% weather blend | 0.04631 | 0.15235 | -0.00294，CI跨0 | -0.00771，CI跨0 |
| regularized market-prior offset | 0.04803 | 0.15674 | -0.00123，95% CI [-0.00253,-0.00016] | -0.00332，95% CI [-0.00644,-0.00082] |

这是 historical holdout 的 point-pass，不是 clean forward confirmation：L2 参数在本轮没有事前冻结，且 price history
不是可执行 orderbook。

### 8 月 3 日反事实

- market：`0.15%`
- V7 weather：`31.276%`
- 含 fee cost：`0.20998%`
- 原 selector edge：约 `+31.07 percentage points`，因此 selected。
- market-prior posterior：`0.21256%`
- posterior edge：约 `+0.00258 percentage points`，低于现有 2% edge threshold，因此不会生成交易。

这不是为单个坏例子追加 hard guard；它是由 84 个 train dates 拟合的 market-prior 结构，并在 28 个 historical holdout
dates 上按 proper score 复核。

## 新交易架构

```text
pre-event full ladder
  -> coherent market prior
KNMI first-seen innovation
  -> ta/tx delta, slope, acceleration, persistence, source-to-official basis
  -> forecast peak clock / remaining heat / cloud-rain-wind-solar state
market prior + learned event likelihood ratio
  -> post-event full-ladder posterior
  -> uncertainty-aware executable residual
```

分开两个可交易目标：

1. `next-routine markout head`：在 `:10/:40` KNMI first-seen 后预测盘口到 `+30/+120/+300s/next METAR` 的 repricing，允许短持有退出；
2. `EOD settlement offset head`：预测最终 exact-bracket，但 market 必须作为 prior，不能用 standalone weather probability 直接下单。

真正的 alpha target 是 `new information not yet in price`。因此模型输入必须区分：

- `pre_event_book_snapshot_id`：事件前 prior；
- `feature_book_snapshot_id`：模型读取盘口；
- `execution_book_snapshot_id`：实际可买成本；
- `source_event_id` 和 `event_innovation`：本次 KNMI 相对上一 checkpoint 新增了什么。

## 下一步冻结实验

1. 继续采 `pre-event/t0/+15/+30/+60/+120/+300s/next-METAR` 完整 ladder，不只保存 selected rows。
2. 在所有 KNMI first-seen event 上建立 `Δlogit market` markout label；`:10/:40` 为 primary，其他 slot 是固定 diagnostics。
3. 同 rows 比较 market-only、V7 weather-only、market-prior offset、event-innovation offset；训练 grain 按 target-date 和 event 等权。
4. 参数和模型选择在历史 train/validation 完成后冻结；下一段 untouched forward 只评分，不调参。
5. probability 层打败 market 后，再按 fresh ask/VWAP、官方 fee、退出成本与 target-date bootstrap 报信号、胜率、PnL、ROI。

当前动作：不改 live；V7 继续 zero-notional weather telemetry，但 standalone `p_weather-cost` 只作旧表达 lineage。下一版本应输出
market-prior posterior 和 markout head，再进入新的 frozen-forward shadow。

## 可复跑产物

- `scripts/analysis/forecast_quality/research_amsterdam_market_prior_offset_v1.py`
- `docs/analysis/2026-08/generated/amsterdam_market_prior_offset_v1/summary.json`
- `docs/analysis/2026-08/generated/amsterdam_market_prior_offset_v1/holdout_predictions.csv.gz`
