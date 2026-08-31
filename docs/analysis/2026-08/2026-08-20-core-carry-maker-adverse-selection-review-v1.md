# 绩效+血缘复盘:core carry maker 腿 adverse selection 全审计与 maker-vs-taker replay

> 窗口:2026-07-25 — 2026-08-18(settled 截至 8/18 build;Warsaw 8/18 用户确认已输,Amsterdam 8/18 未结算)
> 策略身份:`current_yes_core_carry_tiny_live_v2` / config `..._v3_split_10_taker_5_staged_5_pullback_rearm_v6`(8/13 前为 v1/v2 单 maker)
> evidence layer:canonical DB + Mac raw runtime + 链上成交(data-api.polymarket.com trades)+ weather feed 原始报文
> 动作状态:低价带止血已于 8/20 部署(commit `93d3d5a8`/`05b9d1c8`,release pin 已更新);其余结论均为 no-live-change

## 结论与动作

1. **maker 腿全史净负贡献**。settled+Warsaw 口径 PnL ≈ **-$12.46**(v1 -8.92 / v2 +2.54 / staged +1.27 / pullback +0.90 / Warsaw -8.25),同期 taker 腿 +$10.73。win/loss 侧分解:maker win 侧 +$12.49(134 股)vs loss 侧 -$24.95(20 股),loss/win PnL 比 2.0(taker 为 0.74)。paired 价格改善 1.5c/股(全期合计仅 +$2.75),远小于单次亏损 maker fill(-$4.0~-8.25)。
2. **全部 5 笔亏损 maker fill 都是"闪吃型"**:链上核实的真实成交时刻显示,对手在挂单后 11 秒~9 分钟内一笔卖压精确扫掉我们的 top bid;不存在可观察的"盘口渐崩前兆"。因此 **`best_bid 跌破挂价即撤单`类规则在逻辑上不可能救 maker 第一口**(maker 单本身是 top bid,bid 跌破挂价 ⟺ 已被吃),只能救同 signal 的兄弟单(全史仅 Singapore #2 一笔,-$4.0)。
3. **撤单 buffer 无可信设置点**:对手相对 obs 报文时刻的提前量为 5.3~19.2 分钟(Warsaw 最快 5.3、Singapore 14.1、Chengdu 17.6、Manila 19.2)。obs−8min 只覆盖 1/5 亏损型;obs−15min 净效果≈0(误伤 +8.18 vs 规避 -8.0);obs−20min 规避 -16.7 但砍掉 26/32 笔正确成交。**obs−固定提前量形态与对手宽分布不匹配,不采用。**
4. **低价带切分与止血**:亏损 fill 4/5 挂价 ∈[0.80,0.83](Chengdu @0.91 例外)。低价带全口径 6W(+$3.23)/5L(-$20.4);高价带 26W(+$9.27)/1L(-$4.55)。8/20 已部署 `low_price_band_halt_min_posted_price=0.84`:挂价 <0.84 fail closed,entry_attempts 以 `low_price_band_halt_shadow_only` 继续记录 would-be 价格(shadow 不断)。事后切分标注:0.83 阈值为观察所得,需 forward 验证;回滚=参数置 0 重部署。
5. **maker-vs-taker replay(用户假设检验)**:73 个 settled taker signal(69W/4L=94.5%),在 signal 触发时刻挂 maker(窗口至 obs−90s,prints 口径 fill 判定)的 EV 全部低于 taker:taker 0.0261/股 vs ask−1c 0.0246、mid 0.0223、ask−2c 0.0199、bid 0.0182。瓶颈不是准确率而是 **fill rate(28.8%~41.1%)**:错过的正期望 signal(taker +0.026/股)远大于每股 12.6~16.5c 的毛价格改善。条件于 fill 的胜率 95-97% vs 全体 94.5%(样本 20-30,无显著差异)。**"先算概率直接挂 maker 替代等待式 taker"在当前 fill rate 下不成立;要翻盘需要把挂单窗口前移(更早挂单、更长窗口),而 checkpoint 级评分历史未存,是数据缺口。**
6. **blackout 机制正确、不修**:8/18 三个"正确但未挂 maker"的 signal(Wellington/Taipei/Karachi)全部落在"下一份报文 obs 已过、我们未 ingest(管道延迟 3~15 分钟)"的黑暗窗口,clock 合同正确 fail closed;rearm 的 `checkpoint_not_eligible` 拒绝同样正确(市场已 price in,不追高)。
7. **后续**:tape/结构 shadow 采集(hot-token 触发式,详见附录设计)+ 0.84 下限 forward 验证 + A/B 跑满 30 intents/10 dates 契约(复评需计入"双 sleeve 同崩"维度:Warsaw 两个 sleeve 同秒被吃,单 signal adverse 敞口 10 股)。

```text
significance=NA; baseline=PASS(taker同分母paired); forward=FAIL/NA; conclusion=maker腿为负贡献execution probe,止血已部署,机制完善shadow-first
```

## 数据快照

| 项目 | 值 |
|---|---|
| DB | `/Volumes/jrs/pm_agents/runtime/weather.db`(device 16777244/inode 54444,与 manifest 一致) |
| `fact_built_at_utc` | 2026-08-18T14:38:26Z(manifest 无 critical,db_route healthy) |
| live signals / fills | 80 signals;taker 74 settled fills(610 股)+50 未结;maker 36 settled fills(154.16 股)+20 未结(Warsaw/Amsterdam 各 10) |
| maker 触发率 | 69 signals 挂过 maker(raw live_orders 去重)/32 成交 ≈ 46% |
| 未结算 | 8/18 四城 9 fills(Wellington/Taipei/Karachi taker;Warsaw 20 股;Amsterdam 20 股) |
| CLOB coverage gate | 未发布 live_real 新口径数字,沿用 8/12 报告 gate 状态 |
| 链上证据 | data-api trades 按 condition 查询:Warsaw/Chengdu/Manila/Singapore 亏损窗口 + 全部 73 signal 窗口的卖方 prints |

## Warsaw 8/18 血缘(链上核实版)

canonical `fill_ts` 是 **cancel 流程 order-lookup 的观察时刻**,不是成交时刻;Warsaw 滞后 4 分钟。真实时间线(venue book 快照 + 链上 trades):

```text
12:30 obs 20.0°C(首进 20 档)/ ingest 12:35:53
12:42:33 第一波卖压:SELL 12.19@0.833,吃掉 0.84/0.83 档 → 挂单前 book 已被打薄
12:46:36 signal(p=0.879;ask 被 12:42 卖压压到 0.85 → edge +0.023 转正触发,进场即接刀)
12:46:43-47 taker 10@0.85;staged 0.82 / pullback 0.83 挂出,即成全场 top bid
12:54:40 第二波卖压:BUY No 16.18@0.18 = SELL Yes@0.82,一笔扫掉 0.83×5+0.82×5+0.81×6.18
         ← 两个 maker 真实成交时刻,早于 cancel 死线(12:58:30)3 分 50 秒
12:58:30 pre-data-update cancel 发出;12:58:45 order-lookup 发现 MATCHED(=canonical fill_ts)
13:00 obs 21.0°C 确认升档 / ingest 13:03:23;对手(Drab-Catalyst)13:00:39 以 0.14 平掉 No
```

对手提前量 5.3 分钟(obs 前);对 early case 的链上核实:Chengdu 09:42:21(提前 17.6m)、Manila 05:40:49(挂单 11 秒后,提前 19.2m)、Singapore 06:45:55(提前 14.1m)。

## 撤单 buffer 敏感性(为什么没有好设置点)

| 撤单线 | 规避亏损 | 误伤正确成交 | 净 |
|---|---|---|---|
| obs−5min | 0(Warsaw 5.3>5) | 3 笔 +0.96 | −0.96 |
| obs−8min | Warsaw −8.25 | 6 笔 +1.76 | ≈+6.5(仅覆盖 1/5 型) |
| obs−15min | Singapore×2 −8.0 | 23 笔 +8.18 | ≈−0.2 |
| obs−20min | −16.7+Warsaw | 26 笔 +10.18 | ≈+6.5,砍 80% 成交量 |

## maker-vs-taker replay 明细

分母:73 个 settled taker signal(8/18 build);挂单时点=signal 触发;窗口=触发→obs−90s;fill 判定=窗口内存在卖方 print(Yes SELL 或 BUY No 等价)≤ 挂价;毛口径不含 fee(taker fee 已含于 fact_trades 实际成交价)。

| 变体 | 均价 | fill% | 每股 PnL(必成交假设) | 每股 EV(fill 加权) | 5股 EV/signal |
|---|---|---|---|---|---|
| taker 实际 | 0.919 | 100% | +0.0261 | +0.0261 | 0.130 |
| ask−1c | 0.793 | 41.1% | +0.1525 | +0.0246 | 0.123 |
| mid | 0.775 | 37.0% | +0.1700 | +0.0223 | 0.112 |
| ask−2c | 0.783 | 28.8% | +0.1625 | +0.0199 | 0.100 |
| bid | 0.755 | 34.2% | +0.1907 | +0.0182 | 0.091 |

打平条件:ask−1c 口径需 fill rate ≈ 88%(实际 41%)。4 个 loss 中 3 个(Singapore/Busan/Lucknow)窗口内 prints 未低至任何挂价(不 fill),仅 Chengdu 全档 fill(min print 0.10,崩到底)——亏损 signal 一旦 fill 即被扫穿,与 live 观察一致。

### 补充:early-maker replay("p 一算出来就挂,不等价格触线")

数据修正:`pre_live_scores.jsonl` 共 4708 行 = 92 触发行 + **4616 个未触发 checkpoint 评分行**(每新报文 checkpoint 算完 p 即写行,含盘口)——此前"checkpoint 级评分未存"的说法有误,early 假设可全量回测。规则:posted = min(p−0.01, ask−0.01),窗口 = 评分时刻→obs−90s;label 用 feed METAR 当地日最终 running_max 落档(与 canonical 结算交叉验证一致率 95.1%,8 个分歧全为官方源边界差、方向一致偏保守)。

分母:1772 个 checkpoint(27 天,日均 65.6),win rate 90.1%,平均挂价 0.942(59% 落在 0.95+ 带);prints 抽样 369 行(320 condition-day)判定 fill:

| 价带 | n | fill% | win|fill | win|未fill | 实际 EV/股 |
|---|---|---|---|---|---|
| 0.80-0.85 | 132 | 45.5% | **68%** | 88% | **-0.0645** |
| 0.85-0.90 | 70 | 44.3% | 80% | 89% | -0.0321 |
| 0.90-0.95 | 40 | 35.0% | 78% | 88% | -0.0494 |
| 0.95+ | 90 | 10.0% | 100%(9笔) | 96% | +0.0029 |
| 全部 | 369 | 37.1% | 75.2% | 91.4% | **-0.0351** |

**结论:early maker 实际 EV -0.0351/股 vs taker +0.0261,每股差 6c;必成交口径同样 -0.037。** 两层机制:(a) 必成交口径下,post-only 挂价被当时 ask 锁死(提前挂时市场多在 0.99,挂 0.98 赢 2c/输 98c),且价格触发线被绕过后胜率从 94.5% 稀释到 90.1%——**价格触发线不是摩擦而是有效过滤器**;(b) 实际口径下,fill 的 37% 条件胜率仅 75.2%(对手选择性卖给你),未成交的 91.4% 才是好单。早期 168 行子集的 +0.04 为 selection bias,全量后消失。唯一非负的是 0.95+ 带(EV≈0,fill 10%)。

## staged vs pullback A/B(8/13–8/18 初步,未满契约)

staged 5/8 成交(62.5%,均 0.902)vs pullback 3/9(33%,均 0.87);同 signal 内 staged 比 pullback 高的 1c 恰为成交/不成交分界(Manila 8/15、Wellington 8/17)。settled PnL staged +1.271 / pullback +0.90;加 Warsaw 后 -2.83 / -3.25。A/B 期间 staged 爬价改价落地 0 次(never_down+1-tick drift+保队列约束);全史 82 次 reprice(v1/v2 follow_best_bid)成交率 7.3%,均为防守性跟价。追价收益上界(6 天未成交单全赢、全部追到)≈ +$1.6,对照 Warsaw 单次 maker -8.25。

## 2026-08-22 Shanghai 复发与机制处置

Shanghai `30 YES` 的冻结 Core 信号于 `06:51:36Z` 触发，model p=`0.90441`，当时 book=`0.85/0.87`。canonical/raw 对账确认三笔真实 fill：taker `10 @0.87`（`06:51:45Z`，fee `$0.05655`）、staged maker `5 @0.86`（`06:54:24Z`）、pullback maker `5 @0.85`（`06:59:54Z`），合计 **20 shares、principal `$17.25`**。`06:58:23Z` book 已降至 `0.78/0.85`，撤单于 `06:58:44Z` 才触发：staged 已成交，pullback 正在首触成交竞态中。该 market 当前 canonical 为 `missing_bracket`，因此这里只报未结算 exposure，不把它写成 realized loss。

这次不是 `<0.84` 漏网，而是两个独立 maker arm 同时拥有加仓权：resting maker 的首次被吃本身就是 adverse-selection 事件，基于后续 quote 的 cancel 无法保护第一口；两个 arm 又把同一错误状态的 exposure 从 10 taker 放大到 20。**撤销 Core maker live authority只能作为立即止血候选，不是执行机制的最终答案，也不解决成交覆盖与价格改善。** 最终方向应是把第二 sleeve 从固定 maker 改造成 event-time execution router：根据 first-positive 的 weather-driven/quote-driven 来源、触发前后主动流、full-ladder 上方质量迁移和 quote resilience，在 `passive maker / 30–120s confirm 后执行 / next-bracket hedge / skip` 间选择；所有模式共享最多 5-share incremental budget，不能叠加。

影响半径（target_date `>=2026-08-13`、dual-maker policy）：15 条 maker fill、71.78 shares、principal `$64.3588`；其中 staged 8 条/36.78 shares/`$33.1588`，pullback 7 条/35 shares/`$31.20`。按 maker-shadow-only 反事实，这 15 条均不会发生；Shanghai 本次会从 20 shares 降为 10 shares，少暴露 **10 shares / `$8.55` principal**。fill coverage gate 通过：DB/raw `1534/1534`，两侧 orphan=0、missing order=0、fill-cost delta=0。

纯止血实现已冻结为 commit `d81ab1e33058a4f107a24d7c6c80efe45e92119c`（config `..._maker_shadow_only_v7`），focused tests **74 passed**，但它只作为 containment fallback 保留，**不视为机制完成且当前不部署**。当前 production SHA `24099162...` 仍是 `10 taker + 5 staged + 5 pullback`。下一研究动作是补齐 candidate-stage pre-trigger tape，再按 GPT Pro 的 `candidate → core → confirm/hedge` 路由做 P2 固定 A/B。

## Tape/结构 shadow 采集设计(用户确认的约束:非全量、分档+触发式)

- 订阅范围:仅 core carry hot token(signal 触发→撤单/结算窗口内的 current bracket);复用 market_books 的 `capture_priority=hot` 机制,同 collector 增加 trades channel 记录。
- 频率:事件驱动(WS push),非轮询;无 hot token 时零订阅。预计量级:每 token 每小时几十行 prints,全天 < 几 MB(对照:lifecycle 每 20s 全天 181MB)。
- 记录字段:print 价/量/方向(Yes SELL 与 BUY No 归一到 Yes 卖方)、ts、token、当时我们挂价(如有)。
- 同步快照挂单结构:挂价下方 2-3c 支撑深度、近 10 分钟卖压量(Warsaw 前例:挂单时下方 0.78→0.54 悬崖 + 12:42 刚被 12 股卖单打薄)。
- 目的:积累"卖压/结构特征→是否被吃×是否正确"配对,作为未来撤单/挂单条件与"更早挂单"回测的证据底座(当前 checkpoint 级评分历史未存,是 replay 数据缺口)。

## 证据与指针

- 链上核实脚本与中间产物:`/tmp/cc_replay_*.json`(会话级);正式复跑以本文口径重放 canonical+data-api。
- 止血 commit:`pm_agents_core_carry_prod` `93d3d5a8`(halt)+`05b9d1c8`(fixtures);release pin `05b9d1c8`,进程 8/20 11:53 CST 重启加载;测试 49 passed;今日暂无低价带挂单意图(0 条 halt 记录)。
- 相关 registry 行:core carry 8/12 recent live、8/12 event-rescore+maker forward v1(30 intents/10 dates 契约继续)。
