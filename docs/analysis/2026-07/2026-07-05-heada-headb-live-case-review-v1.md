# HeadA / HeadB Live Case Review v1（逐票血缘 + blocked 反事实）

Generated: 2026-07-05
Scope: HeadA `low_price_yes_lottery_tiny_live_v1` 全部 live 成交/被挡候选 + HeadB `metar_reversal_false_fade` shadow 触发 case 的逐票审查。
方法：五段血缘（决策→成交/被挡→路径→结算→归因），目标是"假设记分板"而非 PnL 复盘——tiny-live 的输赢是噪声，但每张票对回测假设链的检验近乎确定。

## One-Line Read

**最大的钱不在策略在管道：token-resolution 故障永久错过的 4 张已结算票里有 2 张大赢家（Shanghai 6/30 @5.5c、Paris 7/02 @6.5c，反事实 +360%，≈$25 错过盈利 vs 迄今总投入 $12.66）。执行假设暂时全绿（taker 零滑点甚至改善、maker 中位 100min 成交、零不利偏差）。cold 票占比在 7 月候选流中升到 ~60%（train 28%）——regime 在漂。HeadB 触发时 d1 top-of-book 中位只有 8 shares（≈$1.4），回放的 taker 全额成交假设不成立。**

```text
conclusion=case_review_v1_complete
live_action=plumbing 修复清单（确定性）；策略层发现全部回 pre-register，不直接改规则
```

## 0. 样本口径（先读这个再读结论）

| 材料 | 原始量 | 去重后真实量 |
|---|---:|---:|
| HeadA blocked_candidates.jsonl | 4,480 行 | **38 个唯一候选**（同候选每 cycle 重复上报，虚高 ~100 倍） |
| HeadA live fills（fact_trades） | 20 行 fill | 12 张票（7/04 起出现部分成交分段） |
| HeadA 已结算 live 票 | — | 2 张（全输，-$2.99；统计噪声） |
| HeadB state_decisions | 12,483 行 | 触发 48 行 → **3 个唯一 city-day 触发** |

**教训：任何用 blocker/journal 原始行数做的判断都会错 1-2 个数量级，必须按唯一候选去重。**

## 1. 发现 P1（最重要）：token-resolution 故障吃掉了两个大赢家

`missing_yes_token_id` 挡掉 13 个唯一候选（7/01-7/04）。区分**永久错过 vs 延迟后重试成功**：

| 状态 | 数量 | 明细 |
|---|---:|---|
| 后来重试成功（延迟非损失） | 4 | Chicago/NYC 7/02（成为 taker 成交）、Busan/Manila 7/04（成为 maker 成交） |
| 永久错过、已结算 | 4 | Tokyo 6/30 ✗、**Shanghai 6/30 @5.5c ✓赢**、**Paris 7/02 @6.5c ✓赢**、Miami 7/02 ✗ |
| 永久错过、未结算 | 5 | Busan/HongKong/Wuhan 7/03、Shanghai/HongKong 7/04 |

永久错过的已结算 4 张：**2 胜 2 负，反事实 ROI +359.8%**。按 $0.8/票算错过盈利 ≈ **+$25**，而 live 迄今总投入 $12.66。
凸性策略下管道故障的代价极端不对称：错过一个 18x 赢家 = 18 张票的成本。这 5 天里"策略选票"没有输给市场，输给了自己的 token 解析。

状态：7/04 后 runner 已带 `--token-resolution-timeout-sec 15` + `--book-failover-on-timeout`，7/04 起 missing_token 新增归零。
**遗留动作**：①"eligible 但 missing token"应触发告警而非静默 block；②token_cache 对次日市场预热。

## 2. 发现 P2：stale 门误伤是 fact-refresh 节奏问题，已自愈

`decision_snapshot_too_stale` 11 个唯一候选，集中在 7/01-02（fact 表刷新没跟上 runner 节奏），7/04 起归零（fact_refresh loop 上线后）。
已结算 7 张命中 28.6%、反事实 +166%（按 snapshot ask，偏乐观）。**修复方向是 fact_refresh 健康监控，不是放宽 6h staleness 门**——门本身逻辑正确，误伤源头是上游数据链。

## 3. 发现 P3（不易察觉）：cold 票占比在 7 月翻倍——regime 漂移

`dist_lt0` blocker（7/04 上线）每天挡 ~9-10 个唯一候选，同期提交 ~5-6 张 → **当前候选流 cold 占比 ~60%+，vs train 窗口的 28%**。
两个含义：①7/04 之前的 live 买进的反论题票比例比回测认知更高（部分解释 live 手感差）；②E1 剔除的 forward 影响比 train 估计大。
**动作**：把日度 cold-share 作为 drift 指标写进 shadow summary（确定性，低成本）。

## 4. 发现 P4：执行假设记分板——目前全绿（对 book-state 假说偏"真错价"解读）

12 张票的执行段逐票核对：

| 回测假设 | live 证据 | 判定 |
|---|---|---|
| 决策 ask 可按原价成交 | 5 张 guarded-taker 全部 0 滑点，其中 3 张**改善**（London -2c、TelAviv -3c、Ankara -0.4c） | ✅ 暂过 |
| maker 挂单能成交 | 7 张 maker 全部成交，时延中位 ~100min（4.6min~12h） | ✅ 暂过 |
| maker 无不利选择 | fill 价 = 挂价，零不利偏差；结算样本太少无法测"成交的都是死票" | ⏳ 半绿 |
| 全额瞬时成交 | **7/04 起大量部分成交**：Paris 4 段跨 9 小时、Manila 2 段跨 11 小时 | ⚠️ 见 P5 |

这是 book-state 假说（stale-quote 幻觉 vs 注意力真错价）目前唯一的 live 证据，方向偏"真错价"（挂出的 ask 真实可吃），但 n=12，继续积累。

## 5. 发现 P5：maker 队列极浅——容量约束比想象更紧

7/04 的票（4.8-15.1c）全部分段成交：$0.80 的单要 2-4 段、跨 6-11 小时吃完。这些价位的 maker 队列流量极小。
含义：①tiny-live 无碍；②**size-up 到 $5+/票时成交时延和不完整成交会显著恶化**，回测按瞬时全额的口径要加部分成交模型（策略层，pre-register 后进下一版回放）。

## 6. HeadB case（shadow 2 天，3 个真实触发）

触发 case：NYC 7/03（current 100-101 → d1 102-103 @0.16）、Lucknow 7/04（36→37 @0.29）、Helsinki 7/04（18→19 @0.16）。

| HeadB 回放假设 | live shadow 证据 | 判定 |
|---|---|---|
| 触发状态仍出现 | ~1.5 city-day/天（47 城里）——存在但稀薄，符合 trigger-hunger 预警 | ⏳ |
| taker 按 ask 全额成交 | **d1 ask top-of-book size 中位 8 shares ≈ $1.4**（均值 11）——$5 档都要吃多档 | ❌ 不成立 |
| 触发时点有可用盘口 | 48 触发行里 6 行 `orderbook_budget_exhausted`（1/3 的 NYC case 无书） | ⚠️ plumbing |
| obs 新鲜 | 触发时 obs_age 中位 19min、max 57min（公共 METAR 固有延迟） | ⚠️ 回放需用同龄 obs |

**HeadB 最硬的新事实是 top-of-book 深度**：任何下一版回放必须按档位深度模拟 taker 吃单，否则 +30.8% 的 hold ROI 是拿不到的纸面价。
三个触发的表达正确性（d1 是否命中）待 7/03-04 结算入库后回填。

## 7. 改进方向清单

**确定性修复（直接做，不需要 pre-register）：**
1. missing-token 告警 + token_cache 预热（P1 遗留）。
2. fact_refresh 健康监控接进 runtime monitor（P2）。
3. HeadB 采集预算保证触发时刻抓书（P6 的 budget_exhausted）。
4. cold-share 日度 drift 指标进 shadow summary（P3）。

**策略层（发现→pre-register→shadow，不直接改规则）：**
5. HeadB 回放 v2：按 top-of-book size/档位深度模拟 taker 入场 + 同龄 obs。
6. HeadA 回测加 maker 部分成交/时延模型（P5），为 size-up 前置。
7. blocked-cushion 反事实自动记账（目前 5 个唯一候选 n 太小，让它自己攒）。

**明确不做：**
- 不因 2 张结算票都输而动 selector（噪声）；
- 不因 stale/cushion 反事实为正而放宽任何 gate（那是修数据链不是开闸）；
- 不把 case 里人眼看出的任何"规律"直接写成 filter（20 个 case 必然能看出假规律）。

## 复核入口

- HeadA blocked：`runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/blocked_candidates.jsonl`（按 candidate_id×blocker 首见去重）
- HeadA fills：`fact_trades WHERE run_id LIKE '%low_price_yes_lottery%'` + `fills` join（20 fill 行 = 12 张票）
- 反事实标签：`fact_signal_candidates.final_yes`（≤7/02 已结算；7/03+ 待链路刷新后回填）
- HeadB：`runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1/state_decisions.jsonl`
- 去重后的 blocked 表：scratchpad `blocked_first_dedup.csv`（会话产物，复算按上述口径）
