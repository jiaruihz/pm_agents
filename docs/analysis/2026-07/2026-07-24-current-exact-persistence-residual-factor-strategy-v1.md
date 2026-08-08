# Current Exact Persistence Residual：因子与策略方向 v1

Status: `shadow_candidate / user-authorized tiny-live evidence probe / not confirmed`

## 结论与下一步

下一阶段只保留一个主因子：

> **Current Exact Persistence Residual**：市场低估“当前 exact bracket 最终留存”的概率残差。

对应交易表达继续使用已经冻结的 **Current-YES Residual Carry v2**。不再给 D1
叠加 regime filter，也不启动新的 D1 exact sleeve。D1、stall、exact-d1、overshoot
继续作为同一个 terminal-lattice 的风险分解、模型标签和负对照，但当前没有证据表明
D1 本身存在可交易的独立 residual。

这不是把所有研究合成一个 selector。当前可交易候选只有 current YES；generic
current/D1 router 已经在 proper score 和 frozen forward 上失败。下一步的核心任务是让
冻结的 current-only 因子积累真正独立的 forward 分母，并判断它是否稳定打赢 market。

## 统一后的研究判断

| 研究方向 | 已完成证据 | 当前判断 | 在新策略中的位置 |
| --- | --- | --- | --- |
| D1 high-mid | 历史 first signal 218 city-day / 48 dates，fee ROI +2.24%，CI 跨 0；clean live 随后明显反转，最终 clean settled ROI -26.47% | live 已暂停；历史窄格子没有跨 regime 稳定性 | dormant 负对照，不交易 |
| D1 overshoot hazard | complete ladder 3,938 OOF states / 18 dates；market+path、market+path+regime 均劣于 raw market | hazard 分解正确，但路径/regime 没产生 residual | terminal-lattice 标签与风险诊断 |
| D1 exact landing v2 | 宽分母 1,131 OOF rows / 689 city-days / 33 dates；最佳 market calibration 的 logloss Δ +0.0069，CI 全在 0 上方 | 当前物理因子不能打赢 market | 不建 D1 sleeve |
| exact router | 完整 ladder residual 模型未打赢 market；frozen forward 曾路由 9 笔低价 current YES，9/9 亏损 | generic 多表达 selector 被否决 | 保留为 negative control |
| Current-YES Carry v2 | 5-share full-ladder 136 city-days / 30 dates / 29 cities，130 胜 6 负，fee ROI +4.67%，date-block CI [+0.91%, +8.02%] | 最强候选，但 proper-score 增量 CI 尚未完全低于 0，且带有 discovery/post-selection | 冻结 forward；tiny-live 只作 evidence probe |

这里最重要的区分是：

- `d1 exact` 是终局分布中的一个状态，不等于一个已经成立的因子；
- `stall/overshoot` 是 current 或 d1 表达的尾部风险来源，不应直接变成 hard filter；
- 当前唯一有正交易证据的方向，是 market prior 之上对 `current exact holds` 的连续概率修正。

## 因子定义

令：

- `Y_hold = 1`：最终最高温落在当前 exact bracket；
- `p_mkt`：同一 PIT checkpoint 的 current YES market midpoint；
- `p_hold`：只使用 PIT 可得的市场与物理状态估计出的 `P(Y_hold=1)`；
- `C_5`：真实 5-share ask ladder VWAP 加官方 fee 的每股成本。

因子使用连续 log-odds residual：

```text
F_persist = logit(p_hold) - logit(p_mkt)
```

实际交易 edge 使用：

```text
EV_5 = p_hold - C_5
```

`F_persist` 回答“物理路径使留存概率相对市场上调或下调多少”，`EV_5` 回答“这个
概率修正能否覆盖当前可成交成本”。价格因此既是模型 prior，也是交易成本。

> 2026-08-08 勘误：下句“建模支持域”的表述已被新 forward 审计取代。模型训练的
> market-mid support 实际为 0.011–0.9895；0.80 只是当时冻结的 live 表达/资金授权边界。
> 当前判断见 `2026-08-08-current-yes-core-carry-mid-floor-forward-v1.md`。

`mid>=0.80` 是 v2 当时冻结的表达支持域，不代表另一个价格因子；支持域内不再增加 0.95
确认门或事后价格带 hard gate。

### v1 概率头

冻结的 compact core 只保留：

- market midpoint；
- local clock 与 forecast peak clock；
- dewpoint depression；
- wind state。

物理含义是判断剩余加热窗口、干燥/蒸发状态和混合条件是否足以继续打穿当前档。
`obs_age_min` 只负责 freshness/cadence 有效性，不进入 alpha。faded、support count、
clean exhaustion、strict-new-high、remaining runway、TAF/source transition 和 regime
只保留为 telemetry 或逐个 challenger；没有同分母 proper-score 改善前不设 hard gate。

## 冻结交易表达

Current-YES Residual Carry v2 的 research alpha 保持不变：

1. 当地 13–17 点；
2. market midpoint `>=0.80`；
3. 每小时第一个 `minute>=30` 且有 fresh two-sided book 的 checkpoint；
4. 用完整 5-share ask ladder和官方 fee 计算 `EV_5`；
5. 每个 city-day 首次 `EV_5>0` 入场并锁定 city-day；
6. 不等待二次确认，不加 0.95 价格门，不加 faded/exhaustion/regime hard gate。

历史 alpha 只属于 5-share taker。当前 tiny-live 实例额外使用 `5 taker + 5 maker`
观察执行；maker 的 fill、queue、adverse selection 必须单列，不能并入历史收益或用来
证明因子。

## 为什么不把 D1 变成第二条 live 策略

从物理上，current persistence 与 d1 landing 确实是不同终局状态：

```text
current holds
    └─ current breaks
         ├─ exact d1
         └─ overshoot d2+
```

但“状态不同”不自动等于“存在两个可交易因子”。D1 宽分母 proper score、regime
模型和 exact router 都没有打赢 market；把 D1 加入策略只会引入一个尚未验证的表达。

因此当前采用“一套 terminal-lattice 研究框架、一个可交易 sleeve”：

- current persistence residual：主动验证；
- D1 reach/landing/overshoot：只做风险分解和独立 residual 搜索；
- 只有 D1 模型在宽分母 proper score、独立 forward 和 fee-adjusted execution 三层都
  打赢 market，才允许成为第二个 sleeve。

## Forward 双漏斗与当前快照

Signal funnel 的单位固定为 checkpoint / city-day：

```text
全部当地 13–17 点状态
→ 每小时首个 minute>=30 fresh-book checkpoint
→ 模型成功打分
→ 首个 EV_5>0 city-day signal
```

Evidence funnel 单列：

```text
signal
→ 5-share ask ladder 可成交
→ submitted order
→ canonical fill
→ settlement
→ fee-adjusted PnL
```

截至 `2026-07-24T11:29:21Z`，Mac tiny-live runtime 为：

- `pre_live_scores.jsonl`: 62 rows；
- `would_orders.jsonl`: 0 rows；
- `live_orders`: 0；
- `fill`: 0。

这只说明 runner 正常扫描但尚无入场证据，不能用于支持或否定策略。

## 下一轮研究计划与晋升门

### P0：积累冻结 forward，不再改模型

每个 eligible checkpoint 都保存 market、模型概率、完整特征、5/10-share ladder、
fee、source lineage、最终 label；不能只保存 selected trades。达到至少 10 个独立
target dates 且 30 个 settled strategy signals 后做第一次正式复盘。

### P1：先裁决 probability residual

在完全同分母上比较：

- raw market；
- market calibration；
- frozen Current Exact Persistence Residual v2。

使用 expanding OOF / frozen forward、target-date equal weight 和 date-block bootstrap。
只有 Brier 与 logloss 相对 market 的 delta 上界都 `<0`，才能把它称为确认的预测因子。

### P2：再裁决交易与稳定性

同一 frozen signal policy 报：

- 5-share taker full-ladder fee-adjusted ROI；
- 10-share capacity；
- front/back、leave-one-date-out、city/source/regime 切片；
- 最大单日损失和两笔最大 tail loss 后的结果；
- maker fill/queue/adverse selection，单独于 alpha。

晋升要求 5-share ROI 的 date-block CI 下界 `>0`，且不能由单日、单城或少数 tail
事件决定。若 ROI 为正但 proper score 未过，继续 tiny evidence probe，不扩 size。

### P3：只允许单因素 challenger

strict-new-high clock、remaining runway、TAF/source transition、云雨和 regime
interaction 每次只增加一组，在相同 rows 上与 frozen core 配对比较。不能把失败样本
逐条转成 AND filter。Atlanta-type terminal false cross 与 native-unit settlement
lattice 必须进入 source/challenger 审计。

### P4：D1 继续作为研究型 competing risk

维护：

```text
p_current
p_reach_d1
p_exact_d1
p_overshoot_d2plus
```

但在 current-only proper score 尚未确认前，不再训练通用交易 router。D1 新实验的
最低门槛是宽分母同分母 proper score 先胜 market；否则连 zero-notional strategy
candidate 都不升级。

## 8 环覆盖

- 数据完整性：历史报告已固定 PIT atlas/factory、market ladder 与 settlement；forward
  继续记录全 checkpoint，不能用盘口缺失冒充策略过滤。
- 模型验证：expanding OOF + frozen forward；禁止用 clean forward 调参。
- 基准：raw market、market calibration、当前 frozen core 同分母比较。
- 统计稳健性：target-date block bootstrap、front/back、leave-one-date-out。
- 经济意义：只看 official-fee-adjusted 5-share/10-share executable ladder。
- 策略逻辑：current exact persistence，不是 touch，也不是“已经到当前档就安全”。
- 实施：当前 Mac tiny-live 仅作证据 probe；本报告不改变生产参数。
- 数据需求：当前最大缺口是独立 settled forward，不是再增加 regime 或 hard filter。

## 最终策略状态

`Current Exact Persistence Residual` 定为 reheat-risk 家族下一阶段的主因子；
`Current-YES Residual Carry v2` 是唯一主动验证的交易表达。状态保持
`shadow_candidate / tiny-live evidence probe / not confirmed`。

参考：

- [Current-YES Carry v2 freeze](2026-07-24-current-yes-core-carry-no-obs-age-freeze-pre-live-v5.md)
- [Current-YES residual entry](2026-07-22-current-yes-carry-residual-entry-v2.md)
- [clean exhaustion backfill](2026-07-22-current-yes-carry-clean-exhaustion-backfill-v3.md)
- [D1 regime](2026-07-22-performance-d1-yes-high-mid-regime-v1.md)
- [D1 overshoot hazard](2026-07-23-research-d1-overshoot-hazard-v1.md)
- [D1 exact landing v2](2026-07-24-research-d1-exact-landing-v2.md)
- [exact router](2026-07-23-research-market-residual-exact-router-v1.md)
- [D1 pause and reconcile](2026-07-24-d1-live-pause-and-reconcile-v1.md)
