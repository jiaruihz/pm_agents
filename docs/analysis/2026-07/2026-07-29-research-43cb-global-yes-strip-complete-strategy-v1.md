# 0x43cb：全球 YES-strip 完整策略复盘

## 数据快照

- wallet：`0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df`
- snapshot：2026-07-29 11:16:12 UTC；leaderboard 11:10:40 UTC
- public wallet：Polymarket Data API activity / positions / closed-positions；
  最新层分析最初使用 5,500 rows，现已另行补齐全公开历史
- complete ladder / settlement：Gamma event markets；所有 bracket 按
  `city × target_date` 合并为一个互斥 event
- PIT weather：本地 global observation archive，2026-07-28..29 共
  80 个 city-date source states
- 最新层：3,401 条 temperature activity、3,291 条 TRADE、50 个完整 events、
  2 个 target dates；39 events 已结算
- 历史精确抽样：从 2,931 条 closed-token discovery rows 发现 2,902 events，
  均匀抽 30 个独立 target dates（2025-12-29..2026-07-28），每个 event 重新按
  condition 拉取完整 activity；missing bracket = 0
- unsettled：最新 50 events 中 11 个在快照时未结算，占 22%；只作结构描述
- 全历史补采：从账户最早公开记录 2025-10-28 13:31:03 UTC 至
  2026-07-29 12:12:49 UTC，共 275 个 UTC 日窗口；饱和窗口递归拆分，
  1,118 次 activity 请求、18 个 split nodes、最大深度 2
- 全账户公开 activity 去重后 353,096 rows；其中 weather 212,340 rows、
  TRADE 209,409 rows、REDEEM 2,907 rows；覆盖 2,989 个 weather events、
  18,392 个 conditions、209,591 个 transactions
- supporting snapshot：15,461 条逐 market position、2,931 条 closed-position；
  Gamma 取回 2,987/2,989 个完整 events、32,545 个 bracket markets
- Gamma 唯一缺口是 Austin / Dallas 2026-07-09 两个已下架 event；其 56 条 activity
  和 14 个已交易 conditions 已保留，但不能把已交易档位误写成完整 ladder
- 官方 leaderboard 用于长周期账户 PnL；不是账户本金 ROI，也不能替代
  opportunity denominator

复现：

- [专用 YES-strip replay](../../../scripts/analysis/wallet_weather/research_wallet_yes_strip_replay_v1.py)
- [PIT replay JSON](generated/wallet_43cb_strategy_v1/yes_strip_replay.json)
- [完整 event portfolios](generated/wallet_43cb_strategy_v1/event_portfolios.json)
- [城市集中度](generated/wallet_43cb_strategy_v1/city_preferences.json)
- [leaderboard 快照](generated/wallet_43cb_strategy_v1/leaderboard.json)
- [全历史 manifest](generated/wallet_43cb_full_history_v1/manifest.json)
- [全历史 weather activity](generated/wallet_43cb_full_history_v1/weather_activity.jsonl.gz)
- [全 event metadata](generated/wallet_43cb_full_history_v1/event_metadata.jsonl.gz)
- [可续跑采集脚本](../../../scripts/analysis/wallet_weather/collect_external_wallet_weather_history_v1.py)

全历史采集完整性验收：

- 275/275 日窗口 `complete=true`，逐文件解压、row count 和未压缩 SHA256 均通过；
- 5 个汇总 artifacts 的文件大小与压缩文件 SHA256 全通过；
- 跨日边界去重 8 rows：353,104 → 353,096；
- 公开 API 不提供 unfilled/cancelled orders、私有信号、maker 意图或原始挂单时间，
  因此“全历史”严格指公开 activity 与可查询的 supporting endpoints。

## 结论

这个账户不是单城专家，也不是把大量独立 YES 随机撒出去。合并同城同日完整
ladder 后，它的主策略是：

> **在 target day 买一段连续 exact-bracket YES strip：先铺接近等 shares 的
> bounded-range 底仓，再对中心落点加权，并主要持有到 settlement。**

它更像一台全球统一的 `Tmax distribution / feasible-support basket` 机器：

```text
当前观测下限 + forecast support + market distribution
  → 确定一段连续可行温度带
  → 每档铺共同 shares
  → 对最可能的中心档追加 shares
  → 分批 maker/taker 完成
  → 基本持有到结算
```

因此：

- 不是“同时看好 5 个互斥温度”；
- 不是纯 observed-floor 以上全买；
- 不是全 ladder underround；
- 不是传统双边 market maker；
- 也不是 99¢ carry。

## 城市与市场偏好

它没有城市专精：

| 指标 | 结果 |
|---|---:|
| effective city count | 23.73 |
| top-1 city | Busan 8.96% |
| top-3 city cost share | 25.73% |
| 中国大陆 | 28.07% |
| Korea | 10.50% |
| United States | 11.36% |
| 其他地区 | 46.61% |

最新样本覆盖 48 个城市。前列为 Busan、Wellington、Shanghai、Chengdu、
Tokyo、Wuhan，但没有哪个城市能单独解释策略。它更可能使用统一的全球
METAR/WU station mapping、forecast distribution 和 ladder executor，不需要为
每个城市都有私有机场数据。

## 完整表达：连续区间底仓 + 中心档加权

最新 50 个完整 events：

- 49/50（98%）是连续 YES strip；
- 平均交易 4.52 个 brackets；
- 平均覆盖 ladder 的 41.09%；
- 主动 rebalance 仅 2%；
- YES BUY cost 100%。

历史 30 个独立 target dates 的精确抽样也高度一致：

- 29/30 为多档；
- 平均 5.97 档；
- YES BUY cost 99.53%；
- 27 个普通 YES strip，1 个带主动 rebalance 的 YES strip；
- target-day BUY cost 97.94%。

所以这不是最近两天临时换风格，而是至少从 2025-12 延续至今的主机制。

### 两层仓位

第一层是共同 shares 的 bounded range：

```text
q × YES(k_low) + ... + q × YES(k_high)
```

只要 winner 落在区间内，底仓 payout 至少为 `q`；落在区间外则归零。

第二层是在中心 bracket 上追加 shares，形成非均匀离散分布。最新 50 events 中：

- 净 YES shares 的 event 内 CV 中位数 0.188；
- 38% events 的 CV `<=0.25`，接近等 shares range；
- 其余 events 中心加权明显，更像 forecast distribution。

只有 18/50 events 的“总 basket cost / 最小共同 shares”小于 1，
且只占 17.39% BUY cost。说明纯 range-underround 只是底层组件，主要资金仍在
承担中心落点风险。

## 典型完整 event

### Shanghai 7/29：接近纯 bounded-range

- 买 `35/36/37/38 YES`；
- 每档约 414 shares；
- 总成本 `$404.23`；
- winner = `35°C`，payout 约 `$413.53`；
- event PnL `+$9.30`。

这接近“35–38 任一档均小幅盈利，区间外全亏”的 range digital。不能把四腿拆成
四个天气预测。

### Jeddah 7/29：宽 support strip

- 买 `37–43 YES`；
- 每档约 355–381 shares；
- 总成本 `$234.12`；
- 在该 strip 内的最小 payout 约 `$354.82`。

它用显著折价买一个宽可行区间，但仍承担 `<37` 或 `44+` 的全损风险。快照时
尚未结算，所以不把理论下界写成 realized profit。

### Busan 7/29：底仓上重押中心

- 连续买 `35–40 YES`；
- shares 从 14 到约 1,333 不等；
- 重仓集中在 `38/39/40`，其中 `39 YES`约 1,299 shares；
- winner = `39°C`；
- cost `$1,235.42`，PnL `+$104.28`。

这里共同 range 底仓很小，利润主要来自中心分布判断，不是 underround。

### Wuhan 7/28：上方 support + modal overweight

- 买 `31/32/33/34+ YES`；
- `32/33/34+`约 653 shares，31 仅约 173；
- winner = `33°C`；
- cost `$557.94`，PnL `+$98.66`。

经济含义是“最终大概率在 32 以上、中心靠近 33”，不是四个独立 YES。

## 入场条件

### 时间

最新截断样本：

- 100% BUY cost 在 target day；
- 当地 10–14 时占 38.46%；
- 14–18 时占 51.30%；
- 合计 89.76% 集中在当地 10–18 时。

价格分布：

| 价格 | BUY cost share |
|---|---:|
| 20–80¢ | 47.72% |
| 80–95¢ | 32.91% |
| 5–20¢ | 7.92% |
| `>=95¢` | 8.33% |
| 其余 | 3.12% |

它主要在日内分布已经收窄、但还没完全确定时买篮子，不是靠 99¢ carry。

### 与 running max 的关系

对齐到交易前 public observation 的 BUY cost：

| bracket 相对当时 running max | cost share |
|---|---:|
| 高于 running max | 44.46% |
| 包含 running max | 32.67% |
| 低于 running max | 6.13% |
| 无 PIT source coverage | 16.73% |

从 event 首单的完整 strip 看，38 个有观测覆盖的 events 中：

- 58.13%资金对应的整段 strip 全在 running max 上方；
- 31.53%资金的 strip 包含 current bracket；
- 11.07%资金的 strip 还含 running max 下方 bracket。

所以“已经观测到 X，买 X 以上全部 YES”只解释一部分。多数资本在首单时买的是
尚未触达的 forecast support，说明 forecast/market distribution 比 observed floor
更核心。

### 是否抢快源

- source age 中位数 33.78 分钟；
- `<=10m` 为 6.14%；
- `<=30m` 为 34.05%。

这不像 yourthos 的 AMOS 秒级 source-event。它可能使用统一 METAR，但不依赖每条
新观测后的极低延迟；主要优势更可能来自全 ladder 定价、forecast support 和执行。

## 执行、卖出与自动化

最新 50 events：

- 1,303 个独立 transaction hashes；
- 69.35% 的独立 transaction 间隔 `<=2s`；
- 74.73% `<=10s`；
- 峰值 43 tx/min；
- 每 event 中位 18 个独立 transactions，最高 103 个；
- BUY 持续时间中位数 94.6 分钟，只有 20% 在 5 分钟内一次完成。

同一时间覆盖的 public `/trades` all-vs-taker 对比中，约 27.48% rows 推断为
maker。maker/taker 是 endpoint 差分推断，不是订单级直接 flag，但说明它很可能：

```text
先按目标 shares 挂/吃多腿
→ 在一两个小时里逐步补齐 strip
→ 部分 maker 累积，部分 taker 完成
```

SELL trade share 仅 0.67%，event-level active rebalance 仅 2%。所以它不是双边
做市；更准确是 **maker-assisted basket accumulator**，退出基本依赖 settlement。

执行层确定是 bot。全球两日 50 events、秒内批量 transaction、相同目标 shares
无法靠人工网页完成。人工仍可能只负责设置当日预算、模型版本或启停。

## 主要风险与失败形态

1. **区间外全损**：London 7/28 只买 `30/31 YES`，winner 为 `29°C`，
   `$14.43` 全损。
2. **区间内仍可能亏**：Tel Aviv 7/28 winner 在 strip 内，但该档 payout
   低于全篮子成本，event 仍亏 `$10.60`。共同 shares 底仓之外的中心加权会改变
   每个 winner 的实际 PnL。
3. **未原子成交**：一段 strip 分 18–100 多个 transactions、持续约 1.5 小时，
   任何腿缺深度或价格移动都会改变 payoff。
4. **source basis**：global WU station 的取整、修订、2°F range 与机场 METAR
   并不完全同义。
5. **相关性**：同日全球多城共享 forecast/model error；50 events 不是 50 个独立日。

## 盈利是否成立

官方 WEATHER leaderboard：

| 周期 | PnL | volume | PnL / volume |
|---|---:|---:|---:|
| ALL | `+$31,581.99` | `$3,076,110.09` | 1.03% |
| MONTH | `+$5,836.64` | `$619,034.89` | 0.94% |
| WEEK | `+$984.66` | `$94,215.84` | 1.05% |

这个比率跨周期稳定在约 1%，但它是 PnL/重复周转，不是资本 ROI。

最新精确 event sample：

- 39 个已结算 events；
- 只有 2 个 target dates；
- BUY cost `$5,794.93`；
- PnL `+$411.94`；
- turnover ROI +7.11%；
- 37/39 winner 落在所买 strip 内；
- 区间外两笔合计亏 `$15.02`。

按两个日期 bootstrap 得到的正区间没有推断意义：独立日期远低于 contract 的
最低门槛，而且这是 selected wallet fills，不是全机会分母。

正式结论：

```text
significance=FAIL_LOW_SAMPLE
baseline=NA
forward=NA
conclusion=inconclusive
```

在最新 2026-07-28..29 的 39 个 settled events / 2 个 target dates 上，该钱包
YES-strip 相对零收益的 turnover ROI 为 +7.11%；同分母 market baseline 缺失，
forward NA，结论 `inconclusive`。

## 信号漏斗与证据漏斗

### 信号漏斗

```text
全球 temperature universe（不可见）
→ 私有 forecast / market-support 候选（不可见）
→ target-day city-event selection（公开成交后可见）
→ 连续 strip + modal overweight（可重建）
→ filled basket（最新 50 events）
```

缺少未交易 city-days，因此不能估 signal selection rate。

### 证据漏斗

| 层 | 覆盖 |
|---|---:|
| latest wallet activity | 5,500 rows，API 截断 |
| temperature activity | 3,401 rows |
| complete events | 50 / 2 target dates |
| PIT observation | 80 city-date source states |
| PIT-aligned BUY rows | 1,075 |
| settled events | 39 |
| exact historical sample | 30 events / 30 dates |
| pre-trade full-depth atomic basket | 缺 |
| opportunity denominator | 缺 |

## 8 环覆盖自检

| 环 | 覆盖 | 结论 |
|---|---|---|
| 1 描述性绩效 | 已覆盖 | leaderboard + exact recent events |
| 2 统计推断 | 部分 | 仅 2 个 recent dates，低样本失败 |
| 3 信号判别 | 部分 | 可重建 support strip，无法算 IC/AUC |
| 4 概率分布 | 部分 | 能还原 payoff，不能取得其主观概率 |
| 5 执行微结构 | 部分 | 有成交/maker推断；缺 order/queue/full depth |
| 6 容量 | 缺 | 多腿深度和未原子成交未建模 |
| 7 组合相关性 | 部分 | event 已合并；同日全球相关性未估 |
| 8 基准/反事实 | 缺 | 无同 rows executable market baseline |

## 对我们的可执行研究方向

这个机制比单城快源更值得迁移，但只能先做 zero-notional：

1. 每个 PIT checkpoint 生成完整 exact-bracket distribution；
2. 枚举连续 strip，分别计算：

```text
minimum payoff inside band
basket ask + official fee + leg slippage
probability winner outside band
center-overweight marginal EV
```

3. 固定同一分母比较四臂：
   - market-only support strip；
   - public observed-floor strip；
   - forecast-support strip；
   - wallet action overlay；
4. 把“共同 shares range 底仓”和“中心档加权”分开做 A/B，避免中心命中掩盖
   range 本身无 edge；
5. maker arm 建 queue、fill probability 和 adverse selection，taker arm按整篮子
   真实 depth/fee；
6. 保存漏腿后的实际 payoff，不假设多腿原子成交；
7. 至少 15 个 frozen-forward target dates，按 target_date block，再判断能否
   稳定胜过同刻 market distribution。

当前动作：保留为 `zero-notional research`，不跟单、不改 live。
