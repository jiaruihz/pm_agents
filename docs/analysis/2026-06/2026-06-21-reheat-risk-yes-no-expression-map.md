# Reheat-Risk YES/NO 表达地图

日期：2026-06-21
状态：`historical architecture reference / superseded_for_decision_use`

> ⚠️ “共享物理底座、不同交易 label/表达头”的架构原则仍有效；本文列出的策略
> 状态、晋级判断和历史数字已被 7 月 canonical/PIT/fee/forward 审计取代。当前状态
> 读 `WEATHER_STRATEGY_REGISTRY.md`，current-YES/hazard 结论读
> `analysis/reheat_risk.md` 顶部 correction。不要据此恢复旧 live 或把多个表达强行
> 合并。

## 一句话

`current YES`、`higher NO carry`、`low-price YES reheat reversal` 是同一个日内问题的三种交易表达：

```text
今天已经出现一个 running max 之后，后面还会不会再升温、升到哪一档？
```

它们应该共用数据层和 reheat 风险信号，但不能共用同一个交易 label、同一个 edge 阈值、同一个 PnL。

## 同一个底层问题，三个表达头

```text
observed weather + market snapshot
  -> shared reheat state/features
  -> shared reheat risk distribution
  -> expression head
       current YES
       higher NO carry
       low-price higher YES reversal
  -> execution / portfolio / journal
```

人话：

- 如果判断“不会再升温”，可以买当前档 YES。
- 如果判断“更高档不会被打穿”，可以买更高档 NO。
- 如果判断“还会二次升温”，可以买更高档低价 YES，吃凸性。

这三个不是互相替代，而是同一物理判断的不同下注方式。

## 哪些层应该共用

| 层 | 是否共用 | 说明 |
|---|---|---|
| observed path | 共用 | current temp、running max、decline、minutes since max |
| weather state | 共用 | tmpf/dwpf/RH/wind/sky、温湿趋势、气压/forecast peak 等 |
| market snapshot | 共用 | current YES、d1/d2 NO、target YES sibling quote |
| source/settlement truth | 共用 | source profile、official winning bracket、settlement_outcomes |
| reheat risk features | 共用 | 判断“还会不会升温”的输入是一套 |
| market-as-prob baseline | 共用 | 所有头都要证明相对市场价有增量 |

维护原则：这些东西属于 `reheat_feature_factory_v1` / future feature factory，不应由每个策略私自 materialize。

## 哪些层必须分开

| 层 | 必须分开 | 原因 |
|---|---|---|
| label | 是 | current YES 看 `current_bracket_holds`，低价 YES 看 `target_yes_wins`，NO 看 higher bracket miss/hit |
| model head | 是 | 同一 reheat signal 可以喂不同 payoff，但输出概率不是同一个数 |
| edge | 是 | YES long、NO carry、低价凸性的赔率结构不同 |
| threshold | 是 | `ask<=0.25` 对低价 YES 有意义，对 current YES 没意义 |
| journal / PnL | 是 | 三个头风险暴露不同，不能混成一个收益 |
| live/shadow gate | 是 | current YES 可 tiny-live，不代表低价 YES 也能 live |

## 三个策略头怎么理解

### current YES / no-reheat

交易：买 current running-max bracket 的 YES。

问题：

```text
当前 running max 所在 bracket 会不会守到结算？
```

典型 label：

```text
current_bracket_holds / current_yes_wins
```

它需要确认“升温窗口已经关闭”。这条是现在 current-YES 文档的主题。

### higher NO carry

交易：买 running max 上方 d1/d2 bracket 的 NO。

问题：

```text
更高档是否仍被市场高估，实际不会被打穿？
```

典型 label：

```text
d1_hit / d2_hit / skip_over_d1
```

它和 current YES 都属于 no-reheat thesis，但 payoff 不完全相同。NO 可以在某些 skip-over 状态下表现不同，所以要和 current YES 做 paired comparison。

### low-price YES reheat reversal

交易：买 running max 上方 d1/d2 target bracket 的低价 YES。

问题：

```text
今天是否会二次升温并命中这个更高 target bracket？
```

典型 label：

```text
target_yes_wins / target_hit
```

这是 current YES 的反方向表达：不是赌“封顶”，而是赌“反转再升温”。它可以复用温湿趋势、forecast peak、minutes since max 等 reheat signal，但必须有单独的 `p_target_yes_wins` 模型头。

## 当前版本状态

| 表达头 | 当前状态 | 读哪个文档 |
|---|---|---|
| current YES | historical tiny-live / fragile；当前状态读 registry/controller | `../reheat_risk.md` |
| higher NO carry | shadow telemetry only | `2026-06-16-higher-no-carry-expression-selector-v1.md` |
| low-price YES reheat reversal | research / runner ready locally / forward shadow blocked | `2026-06-18-low-price-yes-reheat-shadow-v1.md` |
| trend-signal reheat reversal | planned v2 research | `2026-06-19-reheat-tail-mechanism-feature-gap-v1.md` future direction B |

## 对低价 YES/NO 反转的设计结论

低价 YES 反转不应该再按“便宜彩票”单独研究。它应该是 reheat-risk 共享底座上的反买头：

```text
p_target_yes_wins
  = forecast prior
    * p_reheat_to_target_context
```

下一版应把 P0 里实证有效的温湿趋势显式接入：

```text
temp_trend_3h_f / d_tmpf_3h
d_relh_3h 或 RH trend
target distance
minutes_since_running_max
forecast peak delta
target YES ask
```

并对照：

1. v1 旧规则：`d1/d2 + adjusted_edge>=0.08 + ask<=0.25`。
2. market ask 当概率。
3. 同状态 current YES / d1 NO / d2 NO。

如果温湿趋势没有带来 holdout 增量，就不改 shadow。若有增量，也只能先 zero-notional shadow，不直接 live。

## 关键纪律

1. 共用 feature factory，不共用 PnL。
2. 共用 reheat signal，不共用 label。
3. 所有表达头都要打赢 market baseline。
4. 同一 city/date/bracket proposition 要做组合层去重，避免 current YES 和低价/NO 头互相冲突。
5. low-price YES 任何 forward 结论前，先修 fresh observed/reheat feature 生产链路；当前 0 row journal 不能解释成“没信号”。

## 入口关系

```text
current-YES 专文
  docs/analysis/reheat_risk.md

本表达地图
  docs/analysis/2026-06/2026-06-21-reheat-risk-yes-no-expression-map.md

低价 YES shadow 状态
  docs/analysis/2026-06/2026-06-18-low-price-yes-reheat-shadow-v1.md

共享 reheat 入口
  docs/analysis/reheat_risk.md
```
