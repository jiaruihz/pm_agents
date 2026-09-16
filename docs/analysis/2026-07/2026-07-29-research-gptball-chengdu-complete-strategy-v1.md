# Gptball：成都完整天气策略复盘

## 数据快照

- wallet：`0xcac70909a505ed6f28b1b59a79bcc99ff22937d8`
- snapshot：2026-07-29 07:24:38 UTC；研究窗口从 2026-07-01 开始
- public wallet：Polymarket Data API activity / positions
- complete ladder / settlement：Gamma event markets；同一
  `city × target_date` 的所有互斥 brackets 合并为一个 event
- PIT weather：本地 Chengdu/ZUUU observation archive；2026-07-22..29 共 8 天
- 637 条 weather activity、610 条 TRADE、41 个完整 events、18 个 target dates
- 39/41 events 已结算，2/41 未结算或 unresolved，占 4.88%；missing bracket = 0
- Chengdu 534 条 BUY 中 417 条有交易前 PIT observation，row coverage 78.09%；
  对齐 BUY cost `$5,899.50`
- 未结算 7/29、7/30 不进入 settled ROI；公开 wallet cashflow 不与本账户
  canonical `fact_trades` 混用
- 无法观察：私有 forecast、私有/更快中国数据源、程序规则、人工审批、
  订单提交前盘口与队列位置

复现：

- [专用 replay 脚本](../../../scripts/analysis/wallet_weather/research_gptball_chengdu_replay_v1.py)
- [完整 replay JSON](generated/wallet_gptball_strategy_v1/chengdu_replay.json)
- [完整 event portfolio](generated/wallet_gptball_strategy_v1/event_portfolios.json)
- [城市集中度](generated/wallet_gptball_strategy_v1/city_preferences.json)

## 结论

Gptball 的主策略不是“买一个 bracket 等结算”，而是：

> **成都/ZUUU 日内温度路径状态交易：先用 NO 表达继续升温或排除当前档，
> 观测路径上移后，再切到 current YES 锁定 final exact。**

它同时具备三个特征：

1. **强城市专精**：86.94% BUY cost 在 Chengdu，中国大陆占 99.99%；
2. **动态换表达**：同日可由 current/upper NO 切换到 current YES；
3. **bot taker execution**：密集自动下单，但不是挂双边盘口的 market maker。

所以不能把 `30 NO`、`31 NO`、`32 YES` 拆成三套策略。它们可能只是同一个
成都日内状态机在不同时间的表达。

## 城市与市场集中偏好

| 指标 | 结果 | 含义 |
|---|---:|---|
| Chengdu BUY cost | 86.94% | 核心研究对象几乎就是 ZUUU |
| 中国大陆 BUY cost | 99.99% | 不是全球天气套利 |
| top-3 city BUY cost | 96.35% | 高度集中 |
| effective city count | 1.31 | 资金分散度接近单城 |
| Chengdu events | 18/41 | 其余城市多数是小额 probe |

非成都大额案例只有少数：Guangzhou 7/15 NO 约 `$916.56`、Wuhan 7/23 NO
约 `$203.65`、Chongqing 7/25 YES 约 `$206.44`。其余常见 `$1–10`
更像测试、校验或扩城 scouting，不能与成都主策略等权解释。

结算源标识指向 Chengdu Shuangliu / ZUUU。高度集中说明它很可能在专门维护
成都的 forecast、机场观测和日内路径经验；但公开行为无法证明它拥有空管局
私有数据。

## 入场：下午路径状态，而不是 99¢ carry

### 时间和赔率

- 72.85% BUY cost 在 target day；
- target-day 资金的 63.17% 在当地 14–18 时；
- 79.97% BUY cost 在 20–80¢；
- 80–95¢ 占 10.37%，`>=95¢` 仅 3.36%。

这不是“只买 99¢ 确定性”的 carry。它在不确定性仍很高时承担方向风险，
主要交易窗口是成都午后 running max 形成和变化阶段。

### 合并完整 event 后的方向

全账户 BUY cost 中 YES 41.33%、NO 58.67%。这个比例不能解释成“长期偏 NO”：
在 41 个完整 events 中，43.90% 交易了多个 brackets，48.78% 有主动 rebalance。
方向取决于当时路径状态。

对齐到交易前 ZUUU running max 的 417 条 Chengdu BUY：

| 相对表达 | PIT 对齐资金占比 | 完整含义 |
|---|---:|---|
| current YES | 50.77% | running max 已可能成为 final exact |
| current NO | 20.15% | 当前档仍可能被后续更高温度推翻 |
| d1 YES | 6.37% | 下一档 landing / overshoot hedge |
| d6 NO + d7 NO | 11.47% | 当时清晨/午间 running max 尚低，对绝对高档做排除或 pass-through |
| 其他 YES/NO | 11.24% | 尾部、小额探针或完整表达的辅助腿 |

`d6/d7` 只是相对当时 running max 的标签，不应独立命名为远尾策略。要结合
绝对 ladder、forecast peak 和同日后续换腿解释。

## 三天复盘：状态机如何换方向

### 7/26：running max 附近锁 current YES

- 13:58–14:51 大量买 `36/37 YES`，其中 `37 YES` cost `$1,162.16`，
  成交价约 0.175–0.734；
- 同时小额买 `39 NO` 约 `$81.25`，控制上尾；
- 最终 winner = `37°C`；
- event PnL `+$776.61`。

完整表达是“37 为主峰，39 以上尾部不成立”，不是两笔相反赌注。

### 7/27：先否定 current 29，再小额覆盖下一档

- 15:03–17:50 分 111 笔买 `29 NO`，cost `$1,188.82`，价格 0.341–0.840；
- 18 点后再买很小的 `29 YES` 和 `31 YES`；
- 最终 winner = `30°C`；
- event PnL `+$541.44`。

主判断是“29 不会成为 final exact”，但并没有重仓猜唯一落点；后续 YES 是
尾部/落点修正。只看 `31 YES` 会误以为它看 31，只看 `29 NO` 又会漏掉其
landing uncertainty。

### 7/28：最清楚的 NO → YES 状态切换

- 13:11–13:36 买 `30 NO` `$132.61`；
- 14:45–14:52 买 `31 NO` `$326.22`；
- running max 上移到 32 后，15:52–16:15 改买 `32 YES` `$512.42`，
  价格已到 0.864–0.962；
- 最终 winner = `32°C`；
- event PnL `+$200.41`。

这一日最能说明完整策略：

```text
running max 仍低、remaining heat 充足
  → 买当前/上方档 NO，表达继续跨档
新观测把 running max 推高
  → 整条分布向上重估
峰值形成、继续升温风险下降
  → 买新的 current YES，锁 final exact
```

它不是固定“升温就买 NO”，而是每次状态更新后重新选择 residual 最大的表达。

## 它是否在等观测跳变

在 417 条可对齐 Chengdu BUY 中，交易时所用最新公开 ZUUU 观测的 source age：

- 中位数 49.10 分钟；
- `<=10m` 仅 0.24%；
- `<=30m` 为 23.02%。

因此现有证据**不支持**“公开小时 METAR 一到就立刻交易”作为主规则。与
yourthos 对 AMOS 的秒级 first-seen 反应明显不同。

更合理的推断是：

- 持续维护当天 forecast peak / remaining heat / running max 状态；
- 盘口和温度路径满足 residual 条件时分批执行，而非只在 observation event 上触发；
- 可能结合云雨、风、附近站、预报更新或更快的中国本地数据源。

最后一点只能作为待验证假设：本地 replay 只有公开 ZUUU observation archive，
不能排除它看到了更高频机场 AMOS、地方气象页或其他站点。

## 卖出、换仓与结算

- 20/41 events（48.78%）有主动 rebalance；
- 但 610 条 TRADE 中只有 38 条 SELL；
- 推断 maker 仅 2/610（0.33%），其余几乎全是 taker。

典型执行是“多笔小额/中额 taker BUY + 少数大额 SELL”，不是持续做双边报价：

- 7/22：14:20 买 `41 NO @0.75`，cost `$975.53`；18:16
  以约 `0.985` 卖出，event PnL `+$287.76`；
- 7/24：前晚买 `40 NO`，当天又买 `37/38 YES`，18:42 将
  `40 NO @0.998` 卖出；最终 39，整套 event 仍亏 `$149.64`；
- 7/26：卖掉小额失败的 `38 YES`，并把 `39 NO` 在约 0.983 释放；
  主仓 `37 YES` 留到结算。

结论是“核心表达可等 settlement，辅助腿或已经接近确定性的腿主动退出”。
它会炒路径波段，但波段服务于 full-ladder state update，并不是无天气方向的
纯价格 scalping。

## 是机器还是人工

- 550 个独立 transaction hashes；
- 独立交易间隔中位数 11 秒；
- 49.00% 的间隔 `<=10s`；
- 峰值 18 tx/min。

执行层几乎可以确认是 bot。由于主入场没有紧贴公开 METAR first-seen，
无法同样高置信地证明 signal 端完全无人参与。最合理分层是：

| 层 | 判断 |
|---|---|
| execution automation | 高可信 |
| signal/risk 自动重算 | 中高可信 |
| 人工选择当天城市、regime、预算或启停 | 仍可能 |
| 传统 market maker | 否 |

## 能否赚钱

只统计已确认 winner 的 events，公开 activity cashflow 已包含钱包实际成交现金流；
ROI 分母为累计 BUY turnover，不是账户本金回报。

| 范围 | events / target dates | BUY cost | PnL | turnover ROI | target-date bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|
| 全 weather | 39 / 16 | `$11,179.87` | `+$1,845.65` | +16.51% | `[-5.62%, +35.67%]` |
| Chengdu | 16 / 16 | `$9,462.40` | `+$1,438.12` | +15.20% | `[-10.22%, +35.52%]` |

点估为正，但利润高度集中、日期只有 16 个，两个 CI 都跨 0。更重要的是：

- 没有同一时点 executable market baseline；
- 没有 opportunity denominator，无法判断它跳过的日子；
- 没有 frozen rule 的后续 forward；
- 本轮看过多个城市/表达，未做多重检验修正。

正式结论：

```text
significance=FAIL
baseline=NA
forward=NA
conclusion=inconclusive
```

在 2026-07-01..07-29，Gptball 已结算 Chengdu event 相对零收益的 turnover ROI
为 +15.20%（95% CI `[-10.22%, +35.52%]`），前瞻 NA，结论等级
`inconclusive`。

## 信号漏斗与证据漏斗

### 信号漏斗

| 层 | 数量/覆盖 |
|---|---:|
| public weather activity | 637 rows |
| TRADE | 610 rows |
| complete events | 41 |
| Chengdu events | 18 |
| Chengdu BUY | 534 rows |
| 有交易前 PIT observation | 417 rows |
| 已结算 Chengdu events | 16 |

### 证据漏斗

| 证据 | 状态 |
|---|---|
| 完整互斥 ladder | 41/41，有 |
| 公开成交 chronology / cashflow | 有 |
| settlement winner | 39/41 |
| Chengdu PIT public observation | 8/18 dates，417 BUY rows |
| 下单前 direct ask/depth | 缺 |
| 私有 forecast / 快源 | 不可见 |
| 同 rows market probability baseline | 缺 |
| frozen forward | 缺 |

## 8 环覆盖自检

| 环 | 覆盖 | 结论 |
|---|---|---|
| 1 描述性绩效 | 已覆盖 | full-event PnL、city/time/price/side |
| 2 统计推断 | 部分覆盖 | target-date bootstrap；CI 跨 0 |
| 3 信号判别 | 部分覆盖 | 可推断 state switch，无法计算 IC/AUC |
| 4 概率分布 | 缺 | 看不到其 posterior，也无 proper score |
| 5 执行微结构 | 部分覆盖 | 成交 chronology/maker 推断；缺 pre-trade book |
| 6 容量 | 缺 | 无历史 depth/queue |
| 7 组合相关性 | 部分覆盖 | 按 target_date block；城市高度集中 |
| 8 基准/反事实 | 缺 | 无同分母 executable market baseline |

缺环集中在概率、容量、baseline 和 forward，因此本报告只能解释其行为与提出
研究假设，不能据此复制为 live 策略。

## 对我们的可执行研究方向

推荐研究机制，不推荐逐笔跟单：

1. 为 Chengdu/ZUUU 建全 ladder PIT posterior，同时输出：
   `P(current invalidated in 30/60m)`、`P(reach d1+)`、`P(final=current)`；
2. state router 只在同一 city-day 内选择完整表达：
   current NO、current YES、next YES、upper NO，而不是各腿独立触发；
3. 连续特征使用 running max、strict-high age、remaining heat、forecast peak、
   升温/回落速度、云雨、风、附近站 gradient；
4. 外部 wallet action 只做 overlay：
   `无钱包`、`同向 confirmation`、`反向 placebo`、`延迟 5/15/30m` 同分母比较；
5. 每次 observation first-seen 保存 direct ask/depth，按官方 fee 回放；
6. 至少 15 个 frozen-forward target dates，再看 probability score、
   fee-adjusted residual ROI 和 target-date CI。

如果后续中国本地机场或气象快源能拿到，应与公开 ZUUU METAR 做并行
first-seen lineage；在证实领先和 source-basis 前，不能把“它可能有更快源”
直接写成策略条件。
