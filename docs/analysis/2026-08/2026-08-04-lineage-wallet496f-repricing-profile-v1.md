# Wallet 496f：全 ladder 库存 / NegRisk 生命周期剖析 v1

## 结论与动作

`0x496f76bd5cf4c2819c710ae90ed1c84b2dc6fa5d` 长期赚钱，但**不属于可简化成 LMVM「单主档 + residual 收敛退出」的同型钱包**。其主要形态是：

```text
D-2/D-1 开始进入完整 ladder
→ 主要买多个 NO，夹杂 YES
→ 约 20 小时持续建仓，65% 的 city-day 边买边卖
→ 大量 maker inventory、CONVERSION / MERGE
→ 中位 8.07 小时开始卖，中位 36.04 小时完成最后一笔 SELL
→ 仍有大量余仓进入结算
```

全历史 cashflow-complete settled portfolio 为 +$13,119.23 / $322,284.30，turnover ROI 4.07%，按 target_date block bootstrap 的 95% CI 为 [3.37%, 5.08%]。这个结果证明钱包历史形态稳定赚钱，**不证明普通账户可以跟单复制**：没有 private probability、未下单机会、同时点 executable full book、未成交 maker queue 和 frozen replication forward。

动作：把它作为 `full-ladder inventory / NegRisk` 的参考与 LMVM 的 negative control；不把它纳入第一版“单主档 forecast repricing”复制策略，不改 live。若后续研究，应另立 maker/inventory shadow，不能与 LMVM residual shadow 混在同一分母。

```text
significance=PASS（仅该钱包 selected public cashflow）
baseline=NA
forward=NA
conclusion=inconclusive
```

## 数据快照与完整性

| 项目 | 结果 |
|---|---:|
| public weather activity | 2026-03-29 07:29:44 UTC → 2026-08-04 13:33:29 UTC |
| weather activity rows | 201,539 |
| unique weather transactions | 172,877（其中进入 resolved BUY profile 169,422） |
| city × target_date portfolios | 2,997 |
| metadata complete | 2,994 / 2,997 |
| cashflow-complete resolved | 2,876 |
| cashflow-complete BUY portfolios | 2,860 |
| resolved independent target_date | 94 |
| representative-case Polygon receipts | 592 / 592 |

采集按 133 个 UTC 日窗口执行；18 个饱和窗口递归拆分，绕过 Data API 5,500-row offset 上限。PnL grain 固定为 `wallet × city × target_date × complete mutually-exclusive ladder`，纳入 BUY、SELL、CONVERSION、MERGE、REDEEM 的实际 public cashflow。activity timestamp 是 fill time，不是 order-post time。

机器产物：

- [全量画像](generated/wallet496f_repricing_profile_v1/summary.json)
- [逐 city-day 画像](generated/wallet496f_repricing_profile_v1/event_profile.csv)
- [赚 / 平 / 亏 / 典型四案例](generated/wallet496f_repricing_lineages_v1_dir/cases.json)
- raw resumable snapshot：`runtime/analysis_snapshots/wallet496f_20260804`

### 双漏斗

```text
signal funnel（不可观测）:
private universe / probability → private candidate → private selection
                                                ↘ observed 2,997 city-day portfolios

evidence funnel:
201,539 public weather rows
→ 2,997 complete-ladder city-days
→ 2,994 metadata-complete
→ 2,876 cashflow-complete resolved
→ 2,860 resolved portfolios with BUY
→ 592/592 receipts in four representative lineages
```

盘口缺失、private signal 缺失是 coverage gap，不是策略筛除。没有全机会概率层，不能计算同 rows 的 Brier/logloss 或 market residual baseline。

## 长期绩效与稳定性

| 指标 | 结果 |
|---|---:|
| settled BUY cost | $322,284.30 |
| settled PnL | **+$13,119.23** |
| turnover ROI | **4.07%** |
| target-date bootstrap 95% CI | **[3.37%, 5.08%]** |
| 正收益 target_date | 93.62% |
| 正收益 BUY portfolio | 71.22% |
| 去掉最佳 5 个 target_date | **4.03% ROI** |
| 前半 47 dates | 13.47% ROI / $24,729 cost |
| 后半 47 dates | **3.25% ROI / $297,555 cost** |

后半段收益率下降，但绝大多数资金与日期都在后半段，且去掉最佳五日仍为正，因此不是少数 jackpot 撑起。另一方面，selected-fill 历史没有同分母 market baseline；CI 只能说明“这个钱包过去赚钱”，不能说明“我们复制后也赚钱”。

## 不是单主档策略

| 形态 | Wallet 496f |
|---|---:|
| BUY cash：YES / NO | **19.90% / 80.10%** |
| 只买一个 condition | **741 / 2,860 = 25.91%** |
| 买多个 condition | **74.09%** |
| 主 condition 成本占比 | 中位 **58.35%**；P25 38.61% |
| 主 selected-side 成交价 | 中位 **0.503**；P25 0.308；P75 0.780 |
| 0.60+ 主档成本 | $234,849 / $322,284 = **72.87%** |
| 每 city-day BUY transactions | 中位 23；P75 44；P90 69 |

condition 数分布为：1 档 741、2 档 501、3 档 372、4 档 368、5 档 408、6 档 275、7 档及以上 195。全量 expression 中 `mixed_yes_no` 1,836 个、`single_yes` 837、`yes_strip` 179、`no_only` 129、`conversion_only` 16。

这说明它通常不是在完整 ladder 中选一个最强 residual，而是在多个互斥 condition 上维护组合库存。主要成本在较高价格的 NO，YES 经常承担减库存、跨档表达或 NegRisk conversion 后的现金流腿；不能把单个 condition 的 BUY/SELL 单独当成整场 PnL。

单 condition portfolio 的描述性 ROI 达 16.40%，但只承载 $10,197（总成本 3.16%），包含早期小额、低价 YES 案例；多 condition 才是实际放量主体，ROI 3.63%。不能把“单档更高 ROI”事后切片成复制 gate。

## 入场与退出

### 入场相对 target_date

按每一笔 BUY fill 的真实成本归属：

| fill 日 | BUY cost share |
|---|---:|
| D-2 | 28.24% |
| D-1 | **47.98%** |
| D0 | **23.78%** |

UTC 没有 LMVM 式单一批量窗口：00–06 为 23.82%、06–12 为 32.65%、12–18 为 25.20%、18–24 为 18.32%；最大单小时 07 UTC 也只有 5.93%。因此公开路径不支持“某轮 forecast 到达后集中扫一遍”的简单解释。

### 生命周期

| 指标 | 结果 |
|---|---:|
| 有主动 SELL 的 BUY portfolio | **99.83%** |
| BUY 建仓跨度 | 中位 **20.64 小时** |
| 首 BUY → 首 SELL | 中位 **8.07 小时**；P25 3.10h；P75 15.64h |
| 首 BUY → 最后 SELL | 中位 **36.04 小时** |
| 首 SELL 早于最后 BUY | **65.07%** |
| 主 condition 首次 SELL | 中位 **16.49 小时** |
| 有主档 SELL 后卖出 ≥99% | 2,036 / 2,800 = **72.71%** |
| 完整退出主档 return | 中位 +8.21%；P25 -4.97%；P75 +22.74% |

它确实主动 SELL，但“主动 SELL”不等于短线 residual。20 小时建仓、边买边卖、36 小时最后退出，更像连续更新 ladder inventory。全量 2,997 portfolios 中，2,888 个是 `active_sell_then_settlement`；另有 579 个发生 CONVERSION、175 个 MERGE、2,494 个 REDEEM。资金管理、full-set collateral 与结算腿是收益链的一部分。

## Maker / taker

全历史 169,422 个 trade transaction 的 receipt 全解码成本过高，本报告不把不完整 RPC 结果冒充全量。四个预注册代表案例共 592/592 receipts 的钱包侧订单结果为：

| 方向 | maker cash | taker cash | maker cash share |
|---|---:|---:|---:|
| BUY | $1,149.55 | $745.98 | **60.65%** |
| SELL | $453.16 | $138.89 | **76.55%** |

这是 case-selected execution denominator，不是全历史随机样本；但它与大量 partial fills、长建仓跨度、CONVERSION/MERGE 和多档库存互相印证。至少可以排除“像 LMVM 一样主要靠 taker 抢一次短时 forecast residual”的描述。公开 receipt 仍看不到原始 post time、取消订单、queue rank 与未成交 adverse selection。

## 四个完整案例

### 1. 极端盈利：Chengdu 2026-07-25

- 成本 $835.56，PnL **+$96.55**，ROI **11.55%**；winner `41°C`。
- D-2 04:49 UTC 开始，1.65 小时后首次 SELL，持续交易到 target day；139 个 BUY transactions。
- 买 9 个 conditions，主腿是 `37 NO`：$169.36 / 193.33 shares；同时持有 36–42 多档 NO，并卖出多个 YES。
- 现金退出包含 SELL $154.84、CONVERSION $602.31、MERGE $137.08、REDEEM $37.88。
- 201/201 receipts；maker $687.59、taker $322.46。

利润来自整套多档 NO / YES / conversion inventory；只跟 `37 NO` 无法复制 portfolio cashflow。

### 2. 典型盈利：Milan 2026-07-21

- 成本 $651.12，PnL **+$44.54**，ROI **6.84%**；winner `30°C`。
- D-2 10:00 UTC 首 BUY，到约 29.1 小时后才首 SELL；持续到 target day。
- 主要买 `28 NO`、`29 NO`、`31 NO`，同时卖 `30 YES`、`29 YES`、`32 YES` 等库存；有 9 次 CONVERSION、2 次 MERGE。
- SELL $246.77、CONVERSION $422.41、MERGE $26.48；240/240 receipts。
- maker $685.07、taker $211.32，maker 明显主导。

这笔的 winner 预测并不是一个可见的单档押注；收益来自完整 ladder 的互补与库存重组。

### 3. 平：Helsinki 2026-06-17

- 成本 $66.0957，PnL **+$0.0112**，ROI **0.017%**；winner `16°C`。
- 买 `18 YES`、`19 YES`、`20 YES`，约 9.08 小时后开始卖，三腿基本退出。
- `18 YES` +$0.437，被 `19/20 YES` 的 -$0.426 基本抵消。
- 63/63 receipts；maker $37.72、taker $94.74。

这是清晰的多档概率带/库存净额案例；某一腿赚钱不能代表组合赚钱。

### 4. 亏损：Manila 2026-07-27

- 成本 $331.56，PnL **-$91.14**，ROI **-27.49%**；winner `33°C`。
- D-2 06:40 UTC 首 BUY，2.60 小时后首 SELL，但继续交易约 46.3 小时。
- 主腿 `33 NO` 均值约 0.832；它正好是最终 winner，NO 方向错误。只卖出 44.3%，卖价约 0.263，主腿损失严重。
- 同时持有 `30 NO`、`31 NO`、`32 NO` 和小额 `29 YES`；CONVERSION $97.86、REDEEM $23.67 仍不足以弥补。
- 88/88 receipts；maker $192.33、taker $256.35。

这说明多档与 conversion 不能消除 exact winner 暴露；错误主腿仍会造成大幅亏损。

## 与 LMVM 的同分母对比

| 指标 | LMVM | Wallet 496f |
|---|---:|---:|
| resolved BUY portfolios | 310 | **2,860** |
| settled cost | $111,674 | **$322,284** |
| turnover ROI | 3.55% | **4.07%** |
| target-date CI | [1.18%, 5.76%] | **[3.37%, 5.08%]** |
| YES buy cost | **94.34%** | 19.90% |
| 单 condition | **68.71%** | 25.91% |
| 主 condition cost share median | **100%** | 58.35% |
| D-2 / D-1 / D0 BUY cost | **62.95% / 36.02% / 0.86%** | 28.24% / 47.98% / **23.78%** |
| BUY span median | **12.9 分钟** | **20.64 小时** |
| 首 BUY → 首 SELL median | **20.6 分钟** | **8.07 小时** |
| 首 BUY → last SELL median | **1.91 小时** | **36.04 小时** |
| execution evidence | BUY 93% taker（全 receipts） | 代表案例 BUY 60.65% maker |
| CONVERSION / MERGE | 非核心 | **核心现金流腿** |

两者都在 target 前交易、都会主动卖出、长期 selected cashflow 为正；除此之外，核心表达几乎相反。LMVM 更适合作为我们第一版 `forecast-update residual` 原型；496f 更适合作为 full-ladder market making / NegRisk inventory 的后续研究对象。

## 可借鉴与不可照搬

可借鉴：

- 完整 ladder 联合估值，而不是只看某一档；
- 组合级 cashflow 与 condition inventory 实时对账；
- 边买边卖的库存状态机；
- maker-first、partial-fill、CONVERSION/MERGE 的执行研究；
- 仍保留单 condition residual 作为独立 sleeve，不与库存账混算。

不可照搬：

- 看到链上某个 NO BUY 就跟单；后续 YES SELL / conversion 可能是同一组合的一部分；
- 把 8 小时或 36 小时当固定退出规则；公开数据没有其 inventory target；
- 假设 maker future touch 就能成交；queue 与 adverse selection 不公开；
- 把 4.07% 历史 ROI 当作我们的 forward alpha；没有同分母未选择 ladder 与 PIT model baseline；
- 把它压缩为“单主档 + residual=0 退出”，这会删除其主要收益和风险管理机制。

## 8 环与最终判断

| 环 | 状态 |
|---|---|
| 描述性绩效 | PASS：完整 cashflow portfolio |
| 统计推断 | PASS：target-date bootstrap；仅 selected history |
| 信号判别 | NA：private signals/unselected universe 不公开 |
| 概率评估 | NA：没有 PIT private probability |
| 执行微结构 | PARTIAL：四案例 592/592 receipts；全历史 role 未解码 |
| 容量 | NA：未成交 order/depth 不公开 |
| 组合相关性 | PARTIAL：按 target_date block；跨城市库存协方差未建模 |
| 基准/反事实 | FAIL：没有同 rows executable market / random / LMVM replication baseline |

最终判断：在 2026-03-29 至 2026-08-04 的完整 public selected-fill 分母，Wallet 496f cashflow-complete turnover ROI 为 4.07%（target-date 95% CI [3.37%, 5.08%]）；baseline=NA、forward=NA，结论 `inconclusive`。它不是第一版 LMVM 单主档 repricing 的复制候选，只作为 full-ladder inventory / NegRisk 的独立研究参考。
