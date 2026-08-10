# Current Exact Weather + Book Fixed-Offset Residual v1

- Status: `current_research_correction`
- Verdict: `reject_static_weather_level_residual`
- Script: `scripts/analysis/reheat_risk/research_current_exact_weather_book_offset_v1.py`
- Artifact: `docs/analysis/2026-07/generated/current_exact_weather_book_offset_v1/summary.json`

## 数据快照

- 主输入：`intraday_weather_regime_state_rows.csv`，mtime `2026-07-09 11:37 CST`；14,368 个 city-date-hour states、36 城、50 个 target dates，覆盖 `2026-05-19..2026-07-08`。
- settlement label 缺失 57/14,368（0.40%）；该 CSV 没有 `settlement_status`，因此 explicit `missing_bracket=NA`，不把缺 label 擅自归因成 missing bracket。
- 同时具备 exact-current label 和同 snapshot current YES/NO market probability 的固定分母：11,246 states / 48 dates；key duplicates=0。
- model-selection 窗：8,468 states / 33 dates，`<2026-06-21`；frozen forward：2,778 states / 15 dates，`2026-06-21..2026-07-07`。
- canonical 新鲜度旁证：`fact_signal_candidates` snapshot 到 `2026-07-16 13:58Z`、`fact_trades` fill 到 `2026-07-17 06:46Z`、settlement 到 `2026-07-15`。本报告不是 live PnL，不触发 fact rebuild。
- fresh full-ladder collector 现有 71 个 complete snapshots；最新 `2026-07-17 07:12Z` 有 738 rungs、47 城、91 city-date、718 rungs 双侧 direct book、fallback=0，但对应 7/17–18 尚未结算，不能混进历史 forward。

## 纠正后的直接结论

用户指出得对：上一版 `current YES mid>0.98 maker` 是盘口微结构 hypothesis，不是天气 alpha，而且历史真实 queue fill=0。它不再作为本问题的推荐答案。

这次按正确目标重跑后，结论也很明确：

> **现有静态天气/path + 同刻盘口，没有找到第二条可执行策略。** 天气 path 能强烈预测“是否继续升温”，但这些一阶信息已经被 market price 吸收；在固定宽分母上学习的二阶 residual，forward 显著输给原始盘口。

因此现在不能再从 pre-peak、warming、fade 或某个价格区间挑一个小切片包装成策略。真正干净的新方向只能是 **weather information innovation 到达后，盘口尚未完全 repricing 的 event-time residual**；目前数据只够继续 collector，不够宣布策略。

## 目标与模型约束

物理目标：

```text
P(final Tmax 正好停在 current exact bracket
  | PIT weather path + local ladder book)
- same-snapshot market probability
```

不是 touch label：已经打印 current 温度不代表 current YES 安全；后续 overshoot 一档就输。

模型约束：

- market logit 系数固定为 1，只允许天气/路径学习强收缩 correction；不会先把强 market baseline 重拟合坏掉。
- weather path：forecast gap、forecast peak 前/后时钟、1h/3h trend、从 running max 回落幅度、距高点时间、local hour，全部为连续输入。
- book 增量：current/d1/d2/tail 的同 snapshot midpoint、spread、size geometry。
- 禁止 city、价格带、weather regime、事后 outcome slice。
- train 内比较 11 个 `feature set × ridge penalty` 候选；只按 pre-6/21 expanding-OOF date-equal logloss 选一次，forward 不调参。
- probability 先对同 rows raw market 比 logloss/Brier；交易层才比较 current YES/NO direct ask + 官方 fee。

历史 atlas 没有完整 `weather_state_v3` 的 rain、cloud layer、wind direction、solar geometry、future-only curve 与 cadence 字段；缺失不填 0、不用未来 archive 回灌。因此这次是严格 path core 的 retrospective test，不冒充完整 v3 forward。

## 天气特征有用，但不是盘口 residual

现有宽分母机制证据很强：

- sustained 1h+3h warming 的 future-break rate 约 77.7%。
- mature cooling/fade 的 future-break rate 约 1.5%。
- `forecast_peak_2h_plus_ahead` 的 current NO pass-through 约 86.4%；peak passed 2h+ 只约 12.1%。

这说明天气特征确实在预测物理结果。问题在于同刻盘口也知道这些状态：此前全量 current YES、d1 NO、d2 NO taker 分别约 -5.6%、-4.0%、-2.9%，sustained-warming current NO 仍约 -8.1%。预测天气不等于打败盘口。

## 双漏斗

Signal funnel：

| stage | unit | rows | dates |
|---|---|---:|---:|
| atlas state universe | city-date-hour state | 14,368 | 50 |
| exact-current label + same-snapshot market | paired state | 11,246 | 48 |
| pre-forward model selection | paired state | 8,468 | 33 |
| frozen forward | paired state | 2,778 | 15 |
| model fee-adjusted edge > 0 | state | 517 | 15 |
| first signal per city-day | city-day signal | 241 | 15 |

Evidence funnel：

| stage | unit | rows | dates |
|---|---|---:|---:|
| PIT weather/path core | forward state | 2,778 | 15 |
| direct current YES/NO ask + settlement | first signal | 241 | 15 |
| symmetric 5-share depth verified | planned order | **0** | 0 |
| actual fills | fill | **0** | 0 |

历史 atlas 缺 current YES ask size，所以 5-share depth 是 coverage gap，不是策略筛除。交易回放甚至在这个更宽松的 price-only 口径下都失败。

## Proper score：train 略好，forward 反转

train 内选出的 primary 是 `weather_path_offset / ridge=0.03`：6,107 OOF rows / 23 dates，date-equal logloss delta `-0.00109`，只是很薄的改善。加入 local ladder book 后，最佳 train delta 只有 `-0.00053`，没有显示额外 book 增量。

冻结 forward：

| model | rows / dates | logloss | vs raw market | Brier vs market | better dates |
|---|---:|---:|---:|---:|---:|
| raw market | 2,778 / 15 | **0.19738** | baseline | baseline | — |
| market calibration only | 2,778 / 15 | 0.19905 | +0.00166 `[-0.00001,+0.00361]` | +0.00074 | 4/15 |
| **selected weather path offset** | **2,778 / 15** | **0.20552** | **+0.00814 `[+0.00193,+0.01509]`** | **+0.00374 `[+0.00117,+0.00665]`** | **3/15** |

正值表示 challenger 更差。weather residual 不只是“没显著赢”，而是在 forward 上显著破坏了 market probability。

## 可执行表达：不设价格带仍亏

执行规则没有 `0.98`、城市、时段或天气 hard filter：每个 forward state 计算

```text
YES edge = p_stop - YES ask - fee
NO edge  = 1 - p_stop - NO ask - fee
```

选较高且大于 0 的一边，每 city-day 只取首次。结果：

- 517 个 positive-edge state / 15 dates。
- 241 个 first city-day signals / 15 dates / 36 城；YES 218、NO 23。
- 模型声称平均 edge `+1.79c/share`；同 rows market baseline edge `-1.94c/share`。
- 实际 settlement win rate 38.2%，ROI **-11.81%**，date-block CI `[-23.72%,+0.05%]`。
- 每单 5 planned shares 的总反事实 PnL **-$61.63**；尚未计额外滑点，也没有假设 maker 填单。

这正是为什么 probability gate 必须先于 selected ROI：模型制造了看似正 EV 的 residual，但 forward calibration 是错的。

## 其他天气切片为什么不拿来救结果

`pre_peak current YES` 在一个 1,378-state holdout 里曾有 52 signals / 8 dates、ROI +3.2%，但同规则训练窗 119 signals / 10 dates、ROI -2.5%，方向翻转。warming、plateau、fade 等大分母 path bins 相对 market 的 residual CI 也均跨 0。

因此不把 pre-peak 或 warming 改成 eligibility gate；那会再次回到“筛出几个看起来不错的样本”。

## 真正值得做的下一条：Weather Innovation Repricing v1

这是下一轮研究设计，不是当前已确认策略：

```text
event = first-seen official observation / source observation / forecast run hash

weather innovation = P_after(outcome | weather_state_v3)
                   - P_before(outcome | prior state)

market response    = market_after - market_before
residual            = P_after - fresh executable ask - fee
```

冻结规则：

1. grain 是 first-seen `(city, target_date, source, event_ts, prior_state)`，不是 hourly price bucket。
2. 同时保存 event 前后完整 exact-bracket ladder、direct bid/ask/depth、forecast curve hash 和 `weather_state_v3`。
3. 概率 head 用 stop/next-rung/overshoot survival，先在全 event denominator proper score 上打赢 pre-event market 与 post-event market。
4. 只有 score 过关后，才在全 ladder 自动选择 `p_win - direct ask - fee` 最大的一档；5-share depth，taker-only，短 event TTL，无 maker、无价格带、无城市 blacklist。
5. 至少 12 个完整 settled forward dates；selected、blocked、未成交全部保留。

为什么研究 innovation 而不是 level：静态 warming/peak/cloud 状态已经被 market 吸收；可产生 residual 的只可能是**新信息到达与盘口反应之间的时间差**。这也与 fast-source 少量正结果的因果方向一致，但去掉了“source print=确定结算”的错误假设。

当前 blocker：full ladder 从 7/15 才连续完整，`weather_state_v3` prospective event frame 尚未积满，只有 1 个已结算 collector target date。因此动作是补齐 collector/feature-frame lineage，不输出 ROI、不启动 live。

## 八环与 three gates

| 环 | 状态 |
|---|---|
| label / exact semantics | PASS |
| PIT path core | PASS；完整 v3 PARTIAL |
| probability calibration | FAIL：forward 输 raw market |
| action mapping | PASS：双边 direct ask 自动选边，无 filter stack |
| execution microstructure | PARTIAL：有 ask，无对称 5-share depth |
| capacity | FAIL |
| uncertainty / date block | PASS |
| market baseline / forward | FAIL |

```text
significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive_no_static_weather_book_strategy
live_action=none
next=weather_innovation_repricing_collector_only
```

## 证据入口

- [Temperature context feature layer](../../WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md)
- [Temperature path mechanism decomposition](2026-07-05-temperature-path-mechanism-decomposition-v1.md)
- [Tmax distribution v3](2026-07-13-tmax-distribution-v3.md)
- [Tmax clean feature restoration](2026-07-12-tmax-clean-feature-restoration-v1.md)
- [Strategy search reset](2026-07-14-strategy-search-reset-v1.md)
- [Previous maker hypothesis](2026-07-17-current-exact-favorite-maker-v1.md)
