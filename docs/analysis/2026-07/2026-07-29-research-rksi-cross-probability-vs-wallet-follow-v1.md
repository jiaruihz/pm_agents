# RKSI 动态跨档概率与 yourthos 跟随可行性

Status: `research recommendation / zero-notional shadow / no live change`

## 数据快照

- analysis cutoff：2026-07-29 15:02 Asia/Shanghai
- yourthos replay：30 个 Seoul target dates，29 个已结算；1,561 条 trade activity，折叠为 1,512 个独立 transactions
- source-aligned subset：7 个有完整 AMOS first-seen 的 current-NO 首次入场日期
- canonical live diagnostic：`fast_source_prev_no_trial_v1` 共 107 个 `live_real` fills；Seoul 仅 1 个独立 target date、2 fills、20 shares，fee-adjusted realized PnL `+$2.1021`
- current Seoul production probe：AMOS persistent candidate，至少 2 个 distinct observations `>= running max +0.5°C`、最新 `>= running max +0.7°C`，`max_no_ask=0.94`
- coverage caveat：本轮 `weather_clob_fill_coverage_gate.py` 在无输出后以 code 143 结束，故上述 canonical 查询只作为定向诊断，不作为完整 live performance 发布
- production status boundary：本文不修改 runner、实例配置、资金或 live/shadow switch

## 结论

优先做自己的：

```text
新观测 first-seen
  → 更新未来 30/60 分钟 RKSI 跨档概率和 final exact 分布
  → 把同一 Seoul city-day 的整条 ladder 合并后选择一个净 EV 最大的表达
  → 只有 fee-adjusted executable edge 仍为正才进入 zero-notional shadow
```

这比继续使用固定 `x.5/x.7` hard trigger 更接近 yourthos 的真实机制，也值得研究；但目前证据只够进入
zero-notional shadow，不够新开或扩大 live。

跟随 yourthos 不适合作为独立执行策略。它可以作为模型的外部确认特征或 veto，但不能看到一笔链上 BUY 就机械复制。

## 为什么不能把“买 31 NO”写死

`31 NO` 的经济含义是“最终不结算在 31 档”，而不只是“未来 30–60 分钟会升到 32”。真正需要同时估计：

```text
p_cross_30 = P(未来 30 分钟 RKSI official/routine state 离开 31 档)
p_cross_60 = P(未来 60 分钟 RKSI official/routine state 离开 31 档)
p_final_leave = P(final WU settlement 不在 31 档)
```

前两个是 source-event hazard，最后一个才对应 `31 NO` 的最终 payout。AMOS 短时跨档后仍可能存在 source basis、
回落或 settlement lattice 差异，所以不能直接用 `p_cross_60` 当 `31 NO` 的 fair value。

同一城市的 `30 YES / 31 NO / 32 YES / upper YES` 也不能拆成独立信号。每次观测只生成一份完整 final-Tmax
distribution，再在整条 ladder 上比较：

```text
NO_edge(k)  = 1 - P(final=k) - executable_NO_ask(k) - fee - friction
YES_edge(k) =     P(final=k) - executable_YES_ask(k) - fee - friction
```

每个 Seoul city-day / source episode 最多选择一个新增表达；NegRisk 等价组合先折叠为同一 payoff 后再比较。

## 动态模型应看什么

每条 AMOS snapshot first-seen 后重算，而不是只在跳变后计算：

- RKSI preferred-runway 与 all-runway max、running max、距离下一档的 margin；
- 最近 5/15/30/60 分钟 slope、acceleration、distinct persistence、drawdown；
- observation age、距下一份 routine METAR 的时间、local solar time 与 remaining heat；
- RKSS–RKSI spatial gradient，但只作为协变量，不能把 RKSS 当 RKSI settlement proxy；
- 云、雨、风向风速、露点，以及海风/对流 regime；
- 同刻完整 ladder 的 executable ask、depth、spread 与观测到盘口的延迟。

yourthos 的数据支持“每次新观测自动重算”：7 次可精确对齐的首次 current-NO 入场中，6 次在新 AMOS snapshot
后的 30 秒内，source age 中位数约 10 秒；但只有 1 次发生在 preferred-runway persistent cross 后。因此它不像
“温度已经跳变才买”，更像连续概率更新。

## 跟随钱包为什么容易失真

1. 可见的是已经成交/上链的动作，不是其下单时的 source、盘口和模型概率；发现时 stale-book edge 可能已消失。
2. 它会在一个 city-day 内同时做多档、多 side 和 NegRisk conversion。复制单腿会改变 payoff。
3. 首笔可能只是探测单，随后快速加仓、反向或转换；其独立 transaction 间隔中位数 22 秒，峰值 1 分钟 25 笔。
4. 主要退出不是普通波段止盈：95.1% 的 SELL cash 在 `>=0.99`，大量是确认后 conversion/确定性退出；跟随 SELL
   往往已经太晚。
5. 钱包风格存在 regime change：部分日期主动 round trip，近期更多 terminal exact 持有至结算，固定复制规则不稳。

历史 burst 也没有稳定的“晚 1–5 分钟一定更贵”关系：有时盘口更贵，有时因 thesis 失败而更便宜。因此价格变化本身
不能告诉跟随者这是 confirmation 还是 adverse selection。

## 推荐的 shadow A/B

在完全相同的 Seoul observations、first-seen clock、full-ladder quote 和 target-date 分母上同时记录：

| Arm | 决策 |
|---|---|
| A | 自有 `p_final` 与同刻 executable price 决定 |
| B | 只按 yourthos 首次 event-level net expression 跟随 |
| C | 自有模型有正净 EV，且 yourthos 同方向时才确认 |
| D | 自有模型有正净 EV，但 yourthos 明确反向时 veto |
| Benchmark | 当前固定 `2×x.5 + latest x.7` trigger |

wallet arm 必须重建整个 city-day 的 event-level payoff，并保存 detection delay、原成交价、发现时真实可执行价和
price drift；不能按 token/fill 粒度记作多个独立表达。

Primary comparison：

- 同分母 probability score：logloss / Brier / calibration；
- fee-adjusted executable EV、PnL、ROI；
- source event → signal → executable quote → simulated fill 漏斗；
- 按 target_date block，至少 15 个独立 forward Seoul dates 后再决定是否晋级；
- exit 由自己的 source/final probability 驱动，绝不等待钱包 SELL。

## 当前研究动作

1. 保留现有 Seoul 小额历史授权 probe，但不因 yourthos 研究扩大 live；Seoul 当前只有 1 个独立已结算日期，
   `+$2.10` 没有统计意义。
2. 把概率版做成现有 full-ladder residual Head B 的 Seoul/RKSI shadow 分支，以 current hard trigger 为 benchmark。
3. 加 wallet watcher，但只产出 `event-level direction / detection latency / price drift` 特征，不下单。
4. forward 达到预注册分母后比较 A/B/C/D；只有自有模型在 fee、深度和 frozen forward 下稳定胜过 market 与
   hard-trigger benchmark，才讨论 live gate。
