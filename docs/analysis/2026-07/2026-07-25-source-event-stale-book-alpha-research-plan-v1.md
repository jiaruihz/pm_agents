# Source-Event Stale-Book Alpha 研究计划 v1

Status: `preregistered research work order / Helsinki phase-1 / zero-notional / no live change`

## 1. 决策

第一阶段只研究 `Helsinki/FMI`，不一开始铺很多城市。

理由不是它历史命中率最高，而是它当前同时满足：

- exact `1°C` market，expression 语义简单；
- FMI first-seen 链路已稳定采集，样本覆盖 `2026-07-08..22` 共 15 天；
- first-seen p50/p90 为 `2.8/6.4min`，相对下一份 routine METAR 的中位 lead 为
  `10.5min`；
- 18 个 persistent events 全部对应最终离开旧 bracket，但已有 1 个 terminal false
  city-day，足以提醒模型把 FMI 当概率特征而不是 settlement latch；
- 不需要先解决美国 `2°F range` expression handler、Tokyo alternate-sensor rounding、
  Ankara native-F lattice 或 Seoul/Busan/Singapore cross-station/terminal-overshoot。

明确 caveat：已有 Helsinki persistent correct events 中只有 `2/15` 在旧 archive 上满足
fresh executable 定义。这里“数据较完整”指 source→routine→settlement 链完整，不代表
交易机会很多；t0 direct-book coverage/edge 是否存在正是 phase-1 要裁决的 blocker。

第二阶段只预留两个复制城市：

| 阶段 | 城市 | 当前证据 | 进入条件 |
| --- | --- | --- | --- |
| P1 | Helsinki/FMI | 15d；persistent settled `18/18`；exact 1°C | 立即做 zero-notional historical + frozen forward |
| P2 | SanFrancisco/NOAA MADIS | 15d；persistent settled `17/17`；median lead 5.2m | Helsinki 通过概率门，且 2°F range handler parity 完成 |
| P2 | Chicago/NOAA MADIS | 15d；persistent settled `9/9`；median lead 3.3m | 同上，只作跨城复制，不重选规则 |

`Atlanta` 固定作为 terminal false-cross negative control，不进入 phase-1 交易候选。
`Austin/Dallas/Houston` 暂不进入，因为 source switch 后缺 concurrent AWC label。
`Tokyo/Seoul/Busan/Singapore/Ankara` 保留现有 collector，不用于本轮主实验。

## 2. 研究目标

目标固定为：

> 在 FMI observation first-seen 后、routine METAR/WU/settlement 结果仍未知时，估计
> `P(final leaves prior exact bracket | PIT source/path state)`，检验该概率相对同一时点
> market probability 是否有正 residual，以及该 residual 是否在盘口随后重定价前可执行。

本研究不是验证“FMI 比 METAR 快”本身。只有同时满足以下链条才算 alpha：

```text
source first-seen 有新增信息
→ 同 rows proper score 胜 market
→ 盘口随后向该方向重定价
→ first-seen 时可按真实 ask/depth 成交
→ fee-adjusted frozen-forward ROI 为正
```

## 3. Frozen grain、universe 与 label

### State grain

```text
(city, target_date, source,
 observation_ts_utc, first_seen_ts_utc,
 prior_official_observation_ts_utc,
 prior_official_running_max_native,
 prior_market_bracket,
 ladder_snapshot_signature)
```

同一 source observation 的重复 polling 只保留 first-seen，不扩大样本。

### Probability universe

- Helsinki 当地日所有 distinct FMI observations；
- first-seen 时 settlement 未知、market 尚未关闭；
- 有 prior routine official state，能确认 prior exact bracket；
- 不按是否 cross、价格、未来 persistent、最终输赢或是否有 ask 缩小概率分母；
- source/book/settlement 缺失分别进入 evidence coverage，不属于策略筛除。

### Primary label

```text
y_leave_prior = 1
    iff final Polymarket/WU winning bracket != prior exact bracket
```

辅助 label：

- `next_routine_leaves_prior`：只作 source→routine precision/latency 诊断；
- `terminal_false_cross`：source 进入更高档，但 WU/settlement 仍停在 prior bracket；
- `repricing_15s/30s/60s/120s/300s`：source first-seen 后同一 expression 的 PIT 盘口变化。

后到的 METAR/WU/settlement 只能作 label，禁止回填成 first-seen 特征。

## 4. Primary expression

Primary 只研究：

```text
BUY prior-bracket NO
```

因为 source 信息的直接命题是“最终是否离开 prior exact bracket”。不把 source cross
误写成“新 bracket YES 会赢”；后续继续升温会使新 exact YES 失败。

交易 edge：

```text
p_leave_prior
    - five_share_prior_NO_ask_VWAP
    - official_weather_taker_fee
```

每个 `(city, target_date, prior bracket)` 只允许首次正 EV signal。10-share 只作容量
复核。第一轮不研究 maker，不设 `max ask`、margin、persistent 或 local-hour hard gate；
这些状态作为连续特征和预注册 ablation。

## 5. PIT 特征与模型

### Market prior

- prior-bracket NO midpoint / logit；
- 完整 ladder normalized probability；
- spread、5/10-share ask VWAP、depth；
- source 前 60 秒的 quote age 和上一次盘口。

### Source state

- FMI raw native value/unit；
- 相对 prior native settlement boundary 的连续 margin；
- source observation age、first-seen lag、expected cadence；
- single-print / repeated-distinct-observation count；
- source 与最近 routine METAR 的 level/basis；
- 是否 Atlanta-type provisional/terminal-false risk state。

### Path context

- strict-new-high age；
- 1h/3h slope、acceleration、pullback；
- forecast peak clock、remaining heating window；
- dewpoint depression、wind/mixing、cloud/precip transition。

模型按固定顺序打擂：

1. `M0 market_raw`；
2. `M1 market_calibrated`；
3. `M2 market + source margin/basis`；
4. `M3 market + source + compact path`；
5. `M4 shallow nonlinear challenger`，只在 train dates 内选正则。

每次 ablation 固定 rows、labels、quotes。第一阶段不加 city categorical，因为只有一个
城市；不从失败事件反推 AND gate。

## 6. Book-repricing collector contract

每个 distinct FMI first-seen event 必须保存：

```text
t-60s last known ladder
t0 first fresh ladder after first-seen
t+15s / +30s / +60s / +120s / +300s ladder
next routine official first-seen
final settlement
```

每个 checkpoint 保存完整 sibling ladder 的 bid/ask/depth、quote timestamp 和 fetch
result。若 t0 取不到 fresh book，记 `book_coverage_gap`，不能用稍后价格冒充 t0。

同时记录三个预注册 arm，但不下单：

- `immediate_score`：第一条 FMI first-seen；
- `persistent_score`：第二个 distinct observation 仍支持；
- `routine_score`：下一份 routine official first-seen，仅作不可提前获得的 timing upper
  bound，不参与 primary alpha。

比较 immediate 与 persistent 时必须使用各自真实 decision-time book，禁止用 future
persistence 选择后回填 initial ask。

## 7. 统计验证

### Historical discovery

- 现有 `2026-07-08..22` 只用于建立数据 parity、模型和 feature specification；
- expanding OOF，至少 7 个 train target dates 后逐日测试；
- 每个 target date 总权重为 1；
- 所有参数、imputation、entry rule 在 forward 前冻结。

### Frozen forward

从 artifact freeze 后首个完整 Helsinki target date 开始，不调参数。第一次正式裁决
至少需要：

- 15 个独立 settled target dates；
- 30 个 settled distinct source events；
- 至少 20 个有 t0 fresh 5-share ask 的 primary strategy signals；
- probability denominator、book coverage 和 settlement coverage 完整报告。

### Primary probability metrics

同 rows 比较 candidate 与 market：

```text
binary logloss(y_leave_prior)
Brier(y_leave_prior)
calibration slope/intercept
```

按 target-date block bootstrap；logloss 和 Brier delta 的 95% CI 上界都 `<0` 才通过。

### Repricing metric

对每个 event 计算：

```text
signed_reprice_30s = direction * (mid_30s - mid_t0)
signed_reprice_120s = direction * (mid_120s - mid_t0)
```

其中 direction 对 prior-NO 为正方向。至少一个预注册 horizon 的 date-block CI 下界
`>0`，且 terminal-false events 单列，才证明 source 确实领先盘口，而不只是预测最终
天气。

### Trading metric

- 5-share taker official-fee-adjusted ROI；
- 10-share capacity；
- first positive-EV per prior bracket；
- front/back、leave-one-date-out；
- 去掉最大两日和最大两笔 tail loss；
- terminal-false 与 correct events 的 executable/fill 分母分开。

5-share ROI 的 target-date CI 下界必须 `>0`；selected ROI 为正但 proper score 未胜
market，仍保持 `inconclusive`。

## 8. 双漏斗

### Signal funnel

```text
all distinct FMI first-seen observations
→ prior official bracket 可定义
→ model scoreable
→ source residual
→ first positive-EV prior-NO signal
```

每层报告 observations、city-days、target dates。

### Evidence funnel

```text
PIT FMI source
→ t0 fresh full ladder
→ 5/10-share executable prior-NO
→ next routine official
→ WU/Polymarket settlement
→ shadow/order/fill
```

盘口、结算或 source 缺失只算 coverage gap。

## 9. 晋升和停止条件

| 结果 | 结论 | 动作 |
| --- | --- | --- |
| source model 不胜 market | `rejected_for_current_expression` | 停止 Helsinki prior-NO，不加 filter |
| proper score 胜，但 30/120s 不重定价 | `weather_information_not_tradeable` | 保留 source feature，不做 stale-book strategy |
| repricing 存在，但 fee ROI 不过 | `edge_consumed_by_execution` | 研究执行，不启动 live |
| historical + frozen forward 三层全过 | `shadow_candidate` | 进入 SanFrancisco/Chicago 复制 |
| Helsinki + 至少一个复制城市过门 | `promotion_candidate` | 另行请求 tiny-live；不自动部署 |

任何 tiny-live 都必须重新走 deploy 确认，初始只允许 5-share taker、每日/城市上限和
独立 pause。扩大 size 需要另一个独立 forward 阶段。

## 10. 预期产物

- `scripts/analysis/market_structure_edge/research_source_event_stale_book_alpha_v1.py`
- source/book/settlement parity JSON
- complete-event state rows
- OOF probability predictions
- 15/30/60/120/300s repricing table
- 5/10-share fee replay
- frozen model artifact 与 zero-notional runner contract
- final Markdown report、registry 与 docs index 更新

共享 first-seen/source-basis 字段进入 canonical source-event/opportunity layer；不新建
平行策略事实表。本计划不改变现有 collector、runner、city pool 或 live 行为。
