# Weather 候选钱包剩余四人全历史与典型链路 v1

> 钱包：`HighTempTation` / `badatmath` / `MidYes56b` / `jjavi`
> 快照截止：2026-07-30 17:02:41 UTC（北京时间 2026-07-31 01:02:41）
> 主 grain：`wallet × city × target_date × complete mutually-exclusive ladder`
> 研究边界：外部公开钱包；private signal / plan、未成交订单、原始 post time、cancel 和 queue rank 不可见

## 结论与建议

剩余四人中，**`jjavi` 最值得作为完整机制主参考**；`badatmath` 适合学习分布和尾部 sizing，`MidYes56b` 只适合学习 target-day 路径换档，`HighTempTation` 只能作为 terminal latency 上限。

复制优先级：

1. `jjavi`：D-1/D0 概率分布 + 相邻档轮动，长期 ROI 19.93%，去掉最佳五日仍有 11.17%；执行复杂，但时间窗口以小时计。
2. `badatmath`：长期 ROI 6.08%，适合研究宽分布和 cheap-tail convexity；中位 40 笔 BUY、21 个 session，不能照搬执行。
3. `MidYes56b`：target-day mid-price selector；最近 30 日强，但长期 CI 跨零、收益集中，暂不作为主线。
4. `HighTempTation`：98.8% 资金在 target day，首买到首卖中位约 11 秒，96.0% SELL proceeds 在 99¢ 以上；公开跟随不可执行。

推荐研究动作是：

```text
jjavi/badatmath 的 pre-target probability distribution
→ settlement-facing target-day path update
→ previous/current/next bracket inventory rotation
→ direct SELL / complement+MERGE / hold 的统一退出器
```

只做同分母 zero-notional shadow；不改 live。

## 数据快照与完整性

| 钱包 | public activity 覆盖 | weather activity | event metadata | metadata missing |
|---|---|---:|---:|---:|
| `HighTempTation` | 2026-03-05 → 2026-07-30 | 4,410 | 1,007 | 0 |
| `badatmath` | 2026-01-10 → 2026-07-30 | 256,738 | 4,118 | 114 |
| `MidYes56b` | 2026-02-10 → 2026-07-30 | 14,514 | 560 | 3 |
| `jjavi` | 2026-05-17 → 2026-07-30 | 23,765 | 440 | 2 |
| 合计 | — | **299,427** | **6,125** | **119** |

四个 immutable JRS snapshot 均为 `snapshot=20260730T170241Z`。每个 UTC 日窗口都低于 API 5,500-row 上限，饱和窗口递归拆分；`badatmath` 的 closed-position discovery 在 10,000 行截断，但 PnL 使用完整 256,738 行 weather activity，不使用 closed-position endpoint 相加。

长期绩效纳入 5,813 个 cashflow-complete resolved city-day。典型链路共 16 个案例、1,984 个 trade fill、1,983 个 distinct transaction；1,983/1,983 receipt 找到。1,969 个 fill 可按 CLOB V2 解出 wallet-signed order role；`MidYes56b` 2026-04-22 的 15 个旧合约 fill receipt 完整，但不是当前 V2 topic，角色留作 unclassified。

机器可读结果：

- [四钱包统一绩效](generated/weather_wallet_remaining_summary_20260731_v1/comparison.json)
- [HighTempTation cases](generated/weather_wallet_remaining_lineages_20260731_v1/hightemptation.json)
- [badatmath cases](generated/weather_wallet_remaining_lineages_20260731_v1/badatmath.json)
- [MidYes56b cases](generated/weather_wallet_remaining_lineages_20260731_v1/midyes56b.json)
- [jjavi cases](generated/weather_wallet_remaining_lineages_20260731_v1/jjavi.json)

## 长期盈亏、ROI 与稳健性

主 ROI：

```text
Σ(SELL + REDEEM + MERGE - BUY - SPLIT) / ΣBUY cost
```

平均日 ROI 先把同钱包同一 `target_date` 的全部城市合并，再对日期等权平均；它不是资本回报率，决策仍以资金加权长期 ROI 为主。

| 钱包 | resolved city-day | 日期 | BUY cost | PnL | 长期 ROI | 平均日 ROI | 中位日 ROI | 95% target-date CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `jjavi` | 418 | 71 | $141,804 | **+$28,265.85** | **19.93%** | 25.64% | 12.06% | **[10.52%, 30.85%]** |
| `HighTempTation` | 1,004 | 73 | $516,877 | **+$49,945.46** | 9.66% | 9.31% | 9.15% | **[8.08%, 11.16%]** |
| `MidYes56b` | 544 | 122 | $191,438 | +$13,268.52 | 6.93% | 6.14% | 5.89% | **[-0.28%, 14.30%]** |
| `badatmath` | 3,847 | 90 | $749,743 | **+$45,561.20** | 6.08% | 8.33% | 6.35% | **[1.50%, 11.32%]** |

| 钱包 | 最近 30 日期 ROI | 去掉最佳五日 | 后半段 ROI | 最大 target-date 回撤 | YES cost | target-day cost | SELL event |
|---|---:|---:|---:|---:|---:|---:|---:|
| `jjavi` | 16.69% | **11.17%** | 15.46% | -$1,666 | 84.5% | 55.0% | 57.5% |
| `HighTempTation` | 10.11% | **8.64%** | 10.35% | -$1,339 | 4.1% | **98.8%** | **99.9%** |
| `MidYes56b` | **14.00%** | 1.15% | 6.01% | -$5,304 | 82.5% | 93.9% | 61.3% |
| `badatmath` | 4.08% | 2.46% | 5.11% | **-$18,311** | 72.1% | 43.9% | 25.3% |

历史正收益不等于可复制 alpha。四者都缺 opportunity universe、同一 PIT market baseline 和 frozen replication forward：

```text
HighTempTation: significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
badatmath:      significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
MidYes56b:      significance=FAIL, baseline=NA, forward=NA, conclusion=inconclusive
jjavi:          significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
```

## HighTempTation：terminal latency / near-binary liquidation

长期形态：

```text
target day 已接近结果确定
→ 买入即将变成 near-binary 的 NO/YES
→ 数秒到数分钟后在 0.99 附近 SELL
→ 很少依赖最终 REDEEM
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Taipei 7/22 | 36°C | +$1,594.85 / 36.26% | 4 maker + 10 taker orders；36 fills | 15:30 买 34 NO、35 YES；本地 archive 15:38 才 first-see 35°C，随后卖出；16:00 买 35 NO、36 YES，数分钟内再卖。四腿全部归零库存 |
| 典型盈利：Taipei 6/12 | 27°C | +$54.13 / 4.27% | 2 maker + 4 taker；11 fills | 25 NO、26 NO 各自买入后在 0.99 附近卖出；主要赚 near-binary spread，不靠 winner token |
| 接近平：Wellington 7/28 | 13°C | +$0.45 / 0.87% | 2 taker fills | 12 NO 以约 0.9806 买入，6 秒后约 0.9891 卖出；绝对收益几乎全被薄 edge/fee 限制 |
| 亏损：Kuala Lumpur 6/19 | 31°C | -$1,571.95 / -89.56% | 1 maker + 3 taker；4 fills | 早期 30 NO round trip 小赚；16:33 重仓 31 NO，同时买 33 YES，winner 停在 31，两腿同时输 |

Taipei 7/22 最像 latency edge：forecast 仍约 34.44°C，但 RCSS 随后打印 35/36°C；钱包的买卖发生在 first-seen 周围数分钟内。我们不能从 public fill 判断它使用的私有源，但可以确认公开跟单已经太晚。

**可借鉴**：terminal state classifier、source-event 到 near-binary book 的延迟上限、结果确定后的 inventory liquidation。

**不值得复制**：数秒级买卖、99¢ carry、公开 activity 跟单。错误一次如 Kuala Lumpur，会吞掉大量薄利交易。

## badatmath：宽概率分布、cheap-tail convexity 与 maker inventory

长期形态：

```text
D-2/D-1 在大量 bracket 铺非均匀 YES distribution
→ 反复 maker partial fill，中心档重仓、远端档低成本 convexity
→ winner repricing 后 SELL；部分 YES/NO 用 MERGE 回收
→ 未进入支持集的 winner 会造成整张分布大亏
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Shanghai 6/30 | 28°C | +$4,419.55 / 181.17% | 271 maker + 11 taker orders；685 fills | D-2 起铺 21–30°C 分布；28 YES 用 $351 买 5,898 股，最终卖回 $5,888.93；其他档和 MERGE 成本被 winner repricing 覆盖 |
| 典型盈利：Chengdu 7/19 | 37°C | +$223.96 / 46.51% | 30 maker + 71 taker；122 fills | forecast 最大买单时约 37.44°C，source/winner 为 37；37 YES 成本 $169.27，卖回 $668.21；36/35/38 尾腿亏损后仍净赚 |
| 接近平：Austin 6/10 | 90–91°F | $0 / 0% | 38 maker orders；50 fills | 同时积累 90–95°F 多档 YES/NO，执行 6 次 MERGE 和 1 次 REDEEM；完整 event 现金流恰好归零，说明部分 activity 是 inventory/conversion，不是方向 signal |
| 亏损：Chengdu 6/30 | 30°C or higher | -$1,495.22 / -100% | 254 maker orders；538 fills | 买 20–29°C 多档 YES，并买 30+ NO；winner 正好是 30+，支持集未覆盖 winner 且反向 NO 同时输；无主动退出 |

这组正反案例说明它的 alpha 候选不是“多买几个便宜 YES”，而是**分布支持集与每档 sizing**。Shanghai 的 winner convexity 和 Chengdu 的 omitted-tail failure 是同一机制的两面。大量 maker fill 只降低显性成本，不能修复分布错位。

**可借鉴**：输出完整 `P(bracket)`、中心/尾部份额、winner 是否落在支持集、分布随 forecast revision 的变化。

**不值得复制**：40 笔 BUY、21 个 session 的长期中位执行；最大日期回撤 -$18.3k，且最新 30 日 ROI 已降到 4.08%。

## MidYes56b：target-day mid-price selector 与相邻档轮动

长期形态：

```text
target day 观察 running path / peak clock
→ 在 current/adjacent YES 和 next-bracket NO 之间集中选择
→ 1–2 小时内主动 SELL 或留少量 winner
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Dallas 7/28 | 98–99°F | +$3,075.09 / 147.08% | 20 maker + 50 taker orders；110 fills | D-1 先买 100–101 YES；D0 转为重仓 98–99 YES，并买 102–103 NO。forecast 仍约 102.8°F、快源 max 约 100.4°F，但 settlement 停在 98–99 |
| 典型盈利：Milan 6/03 | 27°C | +$180.16 / 30.16% | 4 maker + 13 taker；26 fills | 买 27 YES、28 NO 和 cheap 26 YES；27 YES 与 28 NO 随路径升到 near-binary 后卖出，26 YES 尾腿归零 |
| 接近平：Paris 4/22 | 19°C | +$0.12 / 1.69% | 15 receipts；旧合约 role unclassified | 买 19 YES、18 YES/NO；依次卖出 18 NO 与 19 YES，剩余小库存把利润磨到近零 |
| 亏损：Dallas 6/04 | 86–87°F | -$1,885.57 / -61.32% | 13 maker + 45 taker；78 fills | 主仓 84–85 YES 成本 $1,970.59；同时持有 86–87 YES+NO。winner 为 86–87，YES 兑付仍无法覆盖错误 84–85 主仓和 complement 成本 |

**可借鉴**：target-day `current YES + next NO` 的组合表达、peak-clock/path 更新、错误旧档主动退出。

**不值得复制**：整个钱包。长期 CI 跨零，去掉最佳五日只剩 1.15%；Dallas 的正负案例表明集中 selector 容易形成单日大波动。

## jjavi：欧洲 forecast distribution + D0 path rotation

长期形态：

```text
D-1 建立有中心和尾部的 YES strip
→ D0 随 forecast/source path 把质量移到相邻 bracket
→ 部分旧档 SELL，中心档继续持有或结算
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：London 6/23 | 32°C or below | +$2,804.35 / 202.06% | 16 maker + 19 taker orders；186 fills | D-2 起铺 32-or-below 到 37 的 YES strip；低端 winner 4,710 股成本仅 $74.92，后来卖回 $3,657.70；高温尾腿损失被 convex winner 覆盖 |
| 典型盈利：Milan 7/25 | 30°C | +$885.32 / 45.02% | 12 taker fills | D-1 从 28-or-below 转 29，再卖出旧档并买 30/31；source max/winner 为 30，30 YES 留到结算，路径轮动方向正确 |
| 接近平：Paris 7/07 | 33°C | +$2.65 / 0.52% | 7 maker + 13 taker；76 fills | 铺 31-or-below 到 36 的 YES strip；卖掉 35 和部分 33，winner 33 兑付，但其余尾腿几乎吃掉全部收益 |
| 亏损：Milan 7/26 | 31°C | -$984.43 / -100% | 5 maker + 10 taker；33 fills | PIT forecast 约 27.72°C，买入 25-or-below 到 28 的低温 YES strip；source/winner 最终到 31，未覆盖 winner、没有 SELL，整张 strip 归零 |

`jjavi` 是四人里最好的完整参考，因为长期 ROI、去极值 ROI、后半段和回撤同时最好；London/Milan 正反案例又直接展示了 distribution convexity 的收益和遗漏 winner support 的风险。

**可借鉴**：欧洲城市级 calibrated distribution、D-1 strip、D0 相邻档质量转移、尾部预算上限。

**不应直接照抄**：中位 20 笔 BUY、7 个 session；必须先证明 probability distribution 在相同 PIT rows 上打败 market，再决定是否需要动态调仓。

## 跨钱包共性与我们的落地方式

### 真正可学习的四层

1. `prior distribution`：`jjavi` / `badatmath` 都不是单档猜赢家，而是构造有中心与尾部的 payoff curve。
2. `path update`：`MidYes56b` 和 WeatherHK2 更重视 target-day current/adjacent bracket 换档。
3. `execution`：maker 能获得 convex cheap-tail，但 badatmath Chengdu 证明 maker fill 也会放大 adverse selection。
4. `exit`：HighTempTation 把 near-binary SELL 做到极端；WeatherHK2 还增加 complement+MERGE。我们的退出器应统一比较 SELL、MERGE、hold。

### 必须保留的 negative controls

- omitted winner support：badatmath Chengdu 6/30、jjavi Milan 7/26；
- concentrated wrong current bracket：MidYes56b Dallas 6/04；
- terminal false path：HighTempTation Kuala Lumpur 6/19；
- churn/no edge：HighTempTation Wellington、jjavi Paris、MidYes56b Paris。

### 推荐 shadow

固定同一组 city-day/PIT rows，比：

```text
market-implied distribution
vs static calibrated forecast distribution
vs distribution + settlement-facing path update
vs distribution + path update + executable exit router
```

主指标先用 logloss/Brier 和同分母 market delta；交易层才用 official fee-adjusted executable ROI。maker 分支必须记录 post time、queue proxy、missed fills、fill probability 和 adverse selection。四个外部钱包的 selected fills 只能作为机制案例和 sizing prior，不能充当我们的 opportunity denominator。

## 双漏斗与证据边界

```text
signal funnel:
4 shortlisted wallets
→ 299,427 complete weather activity rows
→ 6,125 event slugs（其中 119 个 metadata coverage gap）
→ 5,813 cashflow-complete resolved city-day portfolios
→ 16 representative cases

evidence funnel:
public fill / conversion cashflow
→ Gamma complete ladder and winner
→ resolved zero-current-value cross-check
→ 1,983 Polygon receipts
→ 1,969 CLOB V2 wallet-signed fills + 15 legacy unclassified fills
→ PIT forecast/source where archive exists
```

公开数据能确认 order hash、maker/taker role、partial fill、transaction、完整 event cashflow 和 winner；不能确认钱包的 private probability、原始限价单 post time、未成交/撤单、queue rank、当时完整 depth 或未被钱包选择的机会。

最终判断：四个钱包的历史 selected-fill PnL 为正，但没有同分母 market baseline 或 frozen replication forward，全部保持 `inconclusive`；动作是机制拆解与 zero-notional shadow，不改 live。
