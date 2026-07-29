# Weather 盈利钱包事件级组合深拆

目标：估计外部钱包在完整 `city × target_date` 温度 ladder 上表达的经济 payoff，
而不是把每个 exact bracket 的单腿 BUY/SELL 当成独立策略。

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | Polymarket Data API `activity` / Weather leaderboard；Gamma API event + 全 ladder metadata |
| 数据快照时间 | 2026-07-29 10:17 北京时间 / 2026-07-29 02:17 UTC |
| 记录行数 | 5 钱包；最新 wallet activity 18,979 行，其中 temperature 16,696 行 |
| 事件级复核 | 95 个完整 city-day event；每钱包 16–20 个跨期 event、16–20 个独立 target_date |
| unsettled 占比 | 跨期样本从 closed-event slug 发现后重新抓完整 activity，settled-discovered；最新样本含未结算事件，只作机制描述 |
| missing_bracket | 0；每个抽样 event 都从 Gamma 重新取完整 ladder |

`closed-positions` 只用于发现历史 event slug。它有 surviving-position bias，且无法还原
SELL，因此其 `realizedPnl` 和单 token 形态均不用于本报告结论。

## 核心结论

| 钱包 | 完整事件级策略 | 关键证据 | 不能怎样误读 |
|---|---|---|---|
| `0x496f...` | D-1 分布库存交易 / 主动再平衡 | 68% event 多档；68% 买入成本为 NO；95% event 主动卖出；SELL proceeds / BUY cost 95%；78% 成本在 target_date 前 | 不能把近期多个 NO 当成独立“看空温度档”；多数只是动态 probability book 的腿 |
| `badatmath.` | 非均匀 Tmax 分布 + cheap-tail convexity | 90% event 多档，平均 4.6 档；73% YES；65% event 同 condition 买过 YES+NO；主要持有、不频繁退出 | 不是纯 YES 胜率策略，也不是全梯子套利；是一张有峰、有尾部的 payoff 曲线 |
| `Gptball` | 中国城市日内落点 / pass-through 交易 | 60% event 单档；70% NO；61% target-day 入场，target-day 成本 72% 在当地 14–18 点；13/20 抽样 event 是成都 | 它是最接近“单一物理判断”的地址，但仍要把同城当天少量相邻腿合并 |
| `yourthos` | Seoul source-event / 盘口库存 scalper | 100% Seoul；86% target-day；75% 多档；SELL/BUY 87%；44% event 主动调仓；成本集中 80–95¢ | 最终持仓几乎不能代表原 thesis；大量腿已经在日内卖掉 |
| `0x43cb...` | observed-floor feasible-range YES strip | 90% event 多档，平均 6.6 档；99.5% YES；95% target-day；几乎不卖；常对连续可行上方档等 shares 买入 | 不能说它“同时看好 6 个 exact 温度”；合并后常是一个 `Tmax ≥ X` 或区间数字仓 |

没有一个钱包在全 ladder 上主要做“恒定 payoff 的无风险套利”：跨期样本按所有可能
winning bracket 计算，flat-payoff event 占比为 0%、0%、0%、0%、5%。它们大多
仍承担真实天气方向风险。

## 方法：把多腿还原成一张 payoff

对一个有 `N` 个互斥温度档的 event，设每档净 YES shares 为 `Y_j`、净 NO shares
为 `N_j`。若最终第 `k` 档获胜，组合 payout 是：

```text
payoff(k) = Y_k + Σ(j ≠ k) N_j
```

所以：

- 等 shares 买相邻多个 YES，本质是一个 range digital，不是多个独立预测。
- 等 shares 买某阈值以上所有 YES，本质是 `Tmax ≥ X`。
- 多档 NO 篮子是这些档位命中的补集组合；若再配 YES/SELL，往往是动态库存。
- 同 condition 买过 YES 和 NO 不自动代表观点矛盾，可能是价格变化后的 hedge、
  roundtrip 或 negative-risk inventory conversion。

报告先按 event 合并全部 condition、outcome、BUY、SELL，再给策略标签。

## 1. `0x496f...`：跨城市的概率库存再平衡

跨期完整样本为 19 events / 19 target dates（2026-04-21 至 07-30）：

- 68.4% event 交易至少两个 condition，平均 2.79 档。
- 买入成本 YES 31.5%、NO 68.5%；不是纯 NO。
- 94.7% event 的 SELL proceeds 达到 BUY cost 的 20% 以上；总体
  `SELL proceeds / BUY cost = 95.3%`。
- 78.4% 买入发生在 target_date 前。价格以 20–80¢（57.0%）和
  80–95¢（37.2%）为主，>=95¢ 仅 0.3%。
- 组合以 `mixed ladder + rebalance` 为主；多次在同一 condition 买过两个 token。

典型 Seoul 07-30 event 同时持有 29–33°C 多档 NO，组合在不同最终档位的 payout
为 170–284；它不是“五条 NO 信号”，而是一张对 32°C 风险最敏感、其他档较平的
event payoff。

判断：这更接近 D-1 probability book / relative-value inventory trader。它先建
多档仓位，随后跟随 forecast、市场概率或盘口进行退出和迁移。收益如果真实存在，
很可能一部分来自 maker/spread/库存管理；只复制某一时刻剩余 NO 腿会拿到完全不同
的风险。

和我们的经验对应：它更像 `forecast-bounded Range RV + execution_quality`，不是
`BUY_NO side alpha`。在没有 maker/taker、queue 和逐次 PIT book 前，不应从其
Weather PnL 推导可复制天气模型。

## 2. `badatmath.`：离散分布与尾部凸性

跨期完整样本为 20 events / 20 target dates（2026-04-27 至 07-28）：

- 90% event 为多档，平均 4.6 档。
- 买入成本 YES 73.2%、NO 26.8%；65% event 在至少一个 condition 买过双 token。
- 63.6% 成本在 target_date 前，36.4% 在 target day。
- 价格分布为 20–80¢ 64.4%、5–20¢ 21.7%、80–95¢ 9.4%；不是 99¢ carry。
- 只有 10% event 属持续主动退出。总体 SELL/BUY 35.7% 主要由少数大额 unwind
  拉高，因此主形态仍偏 hold-to-resolution。

Kuala Lumpur 06-30 的完整组合覆盖 27–34+°C：低价 27–29°C YES 提供巨大尾部
convexity，30–34+°C 再用 YES/NO 填出主分布。不同最终档的 payout 为 $477–$4,832，
明显不是 underround flat book。

判断：它是在买一张非均匀的 Tmax payoff curve——主峰、相邻区间和 cheap tails
同时存在。近期看起来“几乎全买 YES”，但跨期合并后有约四分之一 NO 成本以及大量
paired inventory。最合理的解释是 forecast distribution / market residual 加
便宜尾部凸性，不是单一 current-YES 或 longshot 策略。

和我们的经验对应：最接近 `tmax_distribution_edge`、adjacent/range basket 和
low-price tail 的组合。我们的历史结果提醒：物理分布预测不等于打赢 market，
而 cheap tail 很容易由少数中奖档主导；必须检查 target-date block、top-event
concentration 和同 rows market proper score。

## 3. `Gptball`：成都为主的日内落点交易

跨期完整样本为 20 events / 16 target dates（2026-07-07 至 07-28）：

- 60% event 只交易一个 condition；平均 1.75 档，是五个地址里最方向化的。
- NO 占买入成本 70.2%，YES 29.8%。
- 61.0% 成本在 target day；其中 72.4% 集中当地 14–18 点。
- 20–80¢ 占 80.9%，>=95¢ 仅 1.4%。
- 13/20 event 是成都，其余也集中重庆、广州、青岛、深圳、台北。
- 35% event 主动退出；总体 SELL/BUY 29.2%。

典型成都事件有三种形态轮换：单档 YES、相邻少数档 YES strip，以及两档 NO
complement。07-26 的组合主要是 37°C YES，36°C 少量 hedge、38/39°C 退出腿；
合并后本质仍是“落在 37°C 附近”，不是四条独立观点。

判断：这是本组中最像真实天气判断的策略。时间和城市集中度指向 target-day
observed path、remaining heat / overshoot 和最终 exact landing，而非广泛预报
分布或做市。偏 NO 可能来自 current bracket pass-through / 排除某个落点；切换到
YES 则可能是 heat-death 后锁 current exact。

和我们的经验对应：它横跨 `current_bracket_no pass-through` 与
`current-YES persistence`，但公开交易无法判断入场时 bracket 相对 running max
是 current、d1 还是 tail。下一步必须把成都当时的 official path、forecast peak
clock、remaining heating window 和 settlement lattice PIT join 回去。

## 4. `yourthos`：Seoul 单城 source/microstructure trader

跨期完整样本为 16 events / 16 target dates（2026-06-16 至 07-28），全部 Seoul：

- 75% event 为多档，平均 2.56 档。
- YES/NO 成本为 43.3% / 56.7%，没有稳定单边。
- 85.6% 成本在 target day；target-day 成本 62.0% 在 10–14 点、
  18.8% 在 14–18 点。
- 80–95¢ 占 58.3%，20–80¢ 占 33.8%，>=95¢ 仅 5.5%。
- 43.8% event 主动调仓；总体 SELL/BUY 87.0%。

Seoul 06-24/25 两个最大 event 分别买入约 $8.6k/$7.2k，同时卖出达到买入的
103%/115%；settlement 前净 shares 接近零。若只看最终仓位，会错误认为它没策略；
实际上策略收益主要发生在日内 price move / spread / source repricing。

判断：这是 Seoul 专门化的 source-event inventory scalper，可能结合 AMOS/KMA/WU
路径和盘口变化。它不像稳定的天气方向模型，更像天气信息到达后的微结构执行。
其本月好表现也可能依赖低延迟、maker queue 和对 Seoul settlement basis 的熟悉。

和我们的经验对应：最接近 AMOS fast-source / market repricing，但必须牢记
`P(next source cross)` 不等于 `P(final WU leaves bracket)`。Busan/Seoul 经验表明，
快源即时打印、routine METAR 和最终 WU lattice 会分叉；复制成交方向而没有 source
first-seen 和 quote timing，通常只会接 adverse selection。

## 5. `0x43cb...`：把 exact brackets 合成 observed-floor threshold

跨期完整样本为 20 events / 20 target dates（2025-12-29 至 2026-07-28）：

- 90% event 为多档，平均 6.6 档。
- 99.5% 买入成本是 YES；几乎不买 NO、不卖出，SELL/BUY 仅 0.3%。
- 95.0% 成本在 target day；target-day 成本主要在 10–18 点。
- 20–80¢ 占 65.7%，80–95¢ 占 17.7%，>=95¢ 仅 0.7%。
- 18/20 event 是多档 YES strip，不是多个独立单腿。

最清楚的例子：

- Chengdu 04-06 共 11 档。每档先有约 48 shares；22°C 约 280 shares，
  23–26+°C 约 300 shares。合并后等价于一个小的 all-ladder base，加一笔主要押
  `Tmax ≥ 22°C` 的 threshold position。
- Buenos Aires 03-08 共 9 档。所有档 14 shares，26°C 以上再加到约 100 shares；
  经济表达接近 `base + 86 × 1{Tmax ≥ 26°C}`。

判断：它很可能在 target day 根据 observed maximum / physical lower bound，等
shares 买入所有仍可行的上方 exact brackets，把它们合成一个 range/threshold
digital。若 running max 已达到阈值，较低档在正常 settlement 下已不可能，剩余
strip 接近“覆盖所有可行结局”的结构篮子；盈利点可能是盘口尚未把 lower-bound
信息完整传导到每条腿。

这和普通 all-YES underround 不同。我们的全 ladder all-YES taker 回放扣 Weather
fee 后为负；这里应研究的是 `observed_floor_to_upper_tail_yes_strip`：

```text
PIT official running max 已锁定 lower bound
→ 枚举所有 settlement-feasible brackets
→ 按共同 shares 计算整篮子 taker VWAP + 每腿 fee
→ min feasible payout - executable basket cost
```

最大风险是 source/settlement basis、未原子成交、薄腿无深度和 overshoot 后重新扩展
feasible set。这个机制值得单独做 zero-notional collector，但不能从该钱包 PnL
直接升 live。

## 三类策略全貌

1. **组合概率/区间表达**：`badatmath.`、`0x43cb...`。前者是非均匀分布与凸性，
   后者是把连续上方档合成 threshold/range。
2. **日内物理落点**：`Gptball`。单城/区域、target-day 下午、少量档，最可能依赖
   running max、peak clock 和 reheat/overshoot。
3. **库存与执行**：`0x496f...`、`yourthos`。大量 BUY/SELL 后净仓很小；alpha
   更可能在 spread、source repricing 和 inventory management，不能靠跟仓复制。

## Signal / evidence funnel

```text
signal funnel（不可观测）
外部钱包完整天气 universe ? -> 私有 forecast/source state ? -> city-day selection ?
-> event payoff design ? -> filled trades（公开层只能看到最后一层）

evidence funnel
18,979 latest wallet rows
-> 16,696 temperature rows
-> client-side city-day grouping
-> Gamma full ladder
-> condition-filtered exact event activity
-> 95 usable complete events / 每钱包 16–20 独立 target dates
```

未成交机会、私有 forecast、下单 queue 和完整 maker/taker identity 不公开，因此不能
估计 signal selection rate、漏单或同分母 market residual。

## 8 环覆盖与结论等级

| 环 | 状态 |
|---|---|
| 描述性绩效 | 部分：官方 Weather leaderboard；已另报 |
| 统计推断 | 缺：本报告是机制抽样，不以抽样 PnL作显著性结论 |
| 信号判别 | 缺：私有 signal universe 不公开 |
| 概率分布 | 部分：可重建 realized payoff shape，不能取得其主观概率 |
| 执行微结构 | 部分：有公开 BUY/SELL；缺 order、queue、完整 maker/taker |
| 容量 | 缺：没有 PIT depth 和全腿原子成交证据 |
| 组合相关性 | 已按完整 city-day ladder 合并；跨城市同日相关性未估 |
| 基准/反事实 | 缺：没有同一时点 executable market baseline |

统一结论：

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive
```

不改现有 live。研究优先级为：

1. `0x43cb...` 的 `observed_floor_to_upper_tail_yes_strip`，做 PIT feasible basket
   collector 和 fee/depth/partial-fill baseline。
2. `Gptball` 的成都 target-day landing，join running max、peak clock、reheat 和
   settlement lattice。
3. `0x496f...` / `yourthos` 只在能获得 maker/queue/source-first-seen 证据后研究；
   不做跟仓策略。
4. `badatmath.` 作为 full-distribution challenger，与同 rows market probability
   做 Brier/logloss 和 tail concentration 检验。

机器可读产物：
`docs/analysis/2026-07/generated/weather_wallet_event_portfolios_v1/summary.json`
