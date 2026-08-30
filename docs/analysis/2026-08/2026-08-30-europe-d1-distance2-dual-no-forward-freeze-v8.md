# Europe D-1 distance=2 dual-NO：combined forward freeze v8

## 数据快照

- 数据源：frozen candidate journal、zero-notional collector journal、historical blind v7 sensitivity、closed
  `pm_history` raw 与 canonical settlement snapshot；机器评分见文末 artifact。
- 评分快照时间：`2026-08-30T07:00:03.946838Z`；canonical DB
  `/Volumes/jrs/pm_agents/runtime/weather.db`，device=`16777244`、inode=`54444`。
- 主分母：105 个 strict-forward city-day baskets / 29 target dates；全部已结算，unsettled=`0/105`，
  missing_bracket=`0`。historical 84 snapshots 经 grain audit 后只保留 47 个 first-city-day sensitivity rows，
  不进入 headline。
- 当前只读复核时 `db_route=healthy`、JRS context healthy；production 全局 health 另有 dashboard checkout/
  observation-cache critical，与冻结 artifact 无关。本轮没有 refresh/rebuild，也不发布任何 live PnL。

## 结论与冻结动作

冻结并降级 `europe_d1_distance2_dual_no_carry_v8`。

- 状态：**`FROZEN_INCONCLUSIVE / HEADLINE_GRAIN_CORRECTED / NOT LIVE-READY`**。
- 固定 10 城：Amsterdam、Ankara、Helsinki、Istanbul、London、Madrid、Milan、Munich、Paris、Warsaw。
- 规则：每个 city-day 只取 D-1 当地 12:00–24:00 内第一份 PIT 完整 ladder；买距低端和高端各两档的两个 exact-bracket NO，各 50%；首份 ladder 任一腿不可执行就保留 blocker，不换更晚报价。
- 执行：按 NO ask、官方 weather taker fee，持有到 settlement；weather features 只记 telemetry，不参与 eligibility。
- 本次冻结的是规则、严格 forward 分母、候选 SHA、评分口径和“未确认”结论；不部署、不启新 shadow、不下单，orders/fills/notional=`0/0/$0`。

原 `189 baskets / 37 dates / +1.44%` headline 已撤回：其中 historical 84 rows 是 snapshot grain，只有
47 个 unique city-days，37 个 city-days 被重复计入第二份 snapshot，违反“每 city-day 只取第一份 ladder”。
严格 forward 主分母为 105 baskets / 29 dates，ask+fee ROI=`+0.81%`，bootstrap 95% CI
`[-0.46%, +1.93%]`；额外每 basket `+1.0¢` 后 ROI=`-0.20%`。因此它目前不能再称为“回测不错”，只能
称为尚未确认的正点估方向。

## 与原 D-1 early repricing 的核心差异

| | 原 early repricing | 本次 dual-NO carry |
|---|---|---|
| alpha 假设 | forecast revision 后盘口短期反应慢 | 市场长期高估距两端两档的联合尾部命中率 |
| 入场 | revision/event 触发 | 当地 D-1 第一份完整 ladder，定时固定分母 |
| 表达 | 单腿 YES/NO，押短期方向 | 低侧与高侧两个 NO 各半，押两端都不命中 |
| 退出 | 30/60/90m 卖 bid | 持有到 settlement |
| 主要摩擦 | ask→bid spread + 两次 fee | 一次 ask + entry fee |
| 当前证据 | realistic taker round-trip 为负；不冻结为正策略 | 三个严格 forward 窗点估为正，但 strict-forward CI 跨 0 且不耐 +1¢ |

直观地说：原方向是在猜“接下来一小时价格往哪走”，本次是在尝试卖保险——同时给低尾和高尾收取
可能过高的风险溢价，并接受偶尔一侧命中造成半篮子亏损。

## 固定分母结果

所有 ROI 都是 normalized basket 的 ask + 官方 fee、hold-to-settlement；CI 按 target_date block bootstrap。

| window | baskets | dates | ROI | 95% CI | positive dates | 说明 |
|---|---:|---:|---:|---:|---:|---|
| historical first-city-day sensitivity | 47 | 8 | +0.47% | [-2.97%, +5.00%] | 62.5% | 2026-07-16..23；缺完整 rung/PIT contract，不进 primary |
| zero-notional collected forward | 24 | 10 | +1.69% | [+0.95%, +2.74%] | 100.0% | 2026-07-30..08-09；排除 7/29 bootstrap partial |
| recovery validation | 43 | 10 | +0.67% | [-1.78%, +2.67%] | 90.0% | 2026-08-10..19 prespecified PIT replay |
| locked forward | 38 | 9 | +0.42% | [-1.78%, +2.04%] | 88.9% | 2026-08-20..28；包含 Madrid 8/27 真实 loss |
| **strict forward primary** | **105** | **29** | **+0.81%** | **[-0.46%, +1.93%]** | **93.1%** | 三个同 city-day grain forward 窗合并 |
| grain-aligned context | 152 | 37 | +0.71% | [-0.70%, +2.24%] | 86.5% | strict forward + 47-row历史敏感性；不作 primary |

historical blind 原 84 snapshots 中有 37 个重复 city-day；只留每个 city-day 最早 snapshot 后，机械双腿
ROI 从 `+2.28%` 降为 `+0.47%`，同 snapshot-key 的 market-only 为 `+0.41%`。该 CSV 还缺完整 rung manifest
与 PIT-scorable 标志，所以 47 rows 只能作敏感性。在具有新式完整两腿市场概率的 105 strict-forward baskets 上，
市场本身预期 ask+fee ROI 为 `-0.56%`，
allocation-weighted 实际联合尾部命中率仅比市场隐含概率低 `0.40pp`，target-date CI
`[-2.16pp, +2.42pp]`。因此同 rows 还不能证明“尾部保险偏贵”；当前正收益是值得继续用新日期检验的
经验现象，不能把机制解释当成已经成立。

## 执行、容量与压力

- strict-forward PnL=`+0.8392` normalized units，cost=`103.1608`；这不是美元实盘 PnL，也不是 29 天
  账户复利收益。
- 新式 depth evidence 覆盖 105/105 baskets：两腿最小 ask depth 的 min/q10/median=`2.51/6.41/52.86`
  shares；104/105 至少 5 shares，90/105 至少 10 shares。历史 sensitivity 不伪造已归档时缺失的
  双腿 depth 字段。
- 统一执行价格压力：额外 +0.1¢ / +0.5¢ / +1.0¢ 后 strict-forward ROI=`+0.71% / +0.30% / -0.20%`。
- 观察到 joint tail hit 2/105；Wilson 95% 上界压力 ROI=`-1.62%`。因此尾部风险上界仍是 live gate
  blocker，不能只看平均 ROI。
- 48 个 first-ladder paired-unexecutable blocker 原样保留；7 个 7/29 bootstrap-partial 可评分 baskets
  只单列，不进入 strict-forward primary。

## 改进方向：market tail-risk overlay

在不改城市、时间、distance、两腿配比和结算表达的前提下，新增一个连续风险变量：

```text
p_tail_market = 0.5 × p_market(low distance-2 exact)
              + 0.5 × p_market(high distance-2 exact)
```

唯一假设是：入场市场已经识别出部分高尾部风险；只做 `p_tail_market <= threshold` 的 basket，可能在保留 carry
的同时减少单次约 40% 的尾部损失。阈值只用 `collected_forward + recovery_validation` 的 67 baskets / 20 dates
选择；完整披露 13 个 threshold 比较，以“官方 entry fee 后再加每 basket 1¢”的 development ROI 为目标，选出
`threshold=10%`。`locked_forward` 不参与选阈值；runner 会显式拒绝重复 `(city, target_date)` grain。

| policy | development baskets/dates | development ROI | locked baskets/dates | locked ROI | locked +1¢ ROI | locked PnL |
|---|---:|---:|---:|---:|---:|---:|
| 原 mechanical | 67 / 20 | +1.03% | 38 / 9 | +0.42% | -0.59% | +0.1583 |
| market tail ≤10% | 65 / 20 | +1.47% | 37 / 9 | +1.43% | +0.42% | +0.5228 |

locked paired ROI delta=`+1.01pp`，target-date bootstrap CI=`[0.00pp, +2.90pp]`；PnL delta=`+0.364565`，
CI=`[0.000000, +1.093695]`。下界仍为 0，significance **FAIL**。而且这个 locked window 已经在 incumbent
研究中看过，只能称 chronological holdout，不能重写成 untouched forward。

阈值在 105-row context 中只排除 3 个 basket：Helsinki 8/14 `-0.403525`、Munich 8/16 `+0.140328`、
Madrid 8/27 `-0.364565`；102 个保留 basket 的 context ROI=`+1.46%`、+1¢ stress=`+0.44%`。这个总数混合了
development 与已看过 holdout，只作机制读数，不升 headline。尤其 development 的 10% 与 12.5% 之间会跨过
Helsinki loss，说明阈值仍由一个稀有事件强烈驱动。

当前动作是把 `p_tail_market<=10%` 封存为**未来 zero-notional candidate spec**，从下一批新 settled dates 开始
原样记录 A/B；本轮不启动 collector/shadow、不改 production、不下单。三门仍是
`significance=FAIL / market-residual=FAIL_INCONCLUSIVE / clean-forward=FAIL / conclusion=inconclusive`。

### 退出与训练的 readiness

- 入场 market probability / ask / depth 对 105/105 完整，可支持上述简单风险路由。
- 结构化 forecast telemetry 只在早期 collector 的 24/105 strict candidates 直接随 candidate 保存；不能拿这
  24 条、且只有少数 tail labels 的 slice 训练复杂 weather model 后外推到 105。
- raw market-book batches 的 15/30/60m direct-NO exit replay 已完成，细节见下一节；禁止用 future mid、
  max bid touch、YES complement 或缺 depth 的报价替代真实 taker exit。

## 可执行 exit replay v2.1

退出严格使用 `weather_orderbook_capture_v3`：entry 后 15/30/60 分钟起，固定 30 分钟闭窗口内取第一份
`collector_exact_response_clock`、双腿同 `request_batch_capture_id` 的 direct `outcome=no` bid；每腿卖
0.5 share，完整 walk bid ladder 并逐 level 扣官方 fee。文件名只按 Asia/Shanghai 做 bounded routing，资格由
raw exact UTC response/available clock 决定。105 baskets / 29 dates 是 opportunity denominator；15/30/60m
分别覆盖 92/86/88 baskets，三个 horizon 共同覆盖 82 baskets / 19 dates。早期无 v3 exact-clock 的 13 个
basket 以及窗口内缺完整双腿的行保留 blocker，不补 mid 或 settlement fallback。

### 机械固定退出：否决

在 all-horizon common coverage 上只用 development 的 54 baskets / 12 dates 选 15/30/60m；三个 horizon 的
exit-minus-hold ROI delta 分别为 `-1.96pp / -1.91pp / -1.79pp`，因此“最好”的 60m 仍为负：

| split | baskets / dates | hold ROI | 60m exit ROI | exit-minus-hold | 95% CI |
|---|---:|---:|---:|---:|---:|
| development | 54 / 12 | +0.79% | -1.00% | -1.79pp | [-3.45pp, +0.24pp] |
| locked（此前已观察） | 28 / 7 | -0.46% | -1.29% | -0.83pp | [-2.32pp, +1.21pp] |

固定时间全平仓会把原本的 carry 让给 bid/ask spread 和第二次 fee；这个改进方向在当前可执行证据上**否决**。

### 选择性止损：弱正点估，未确认

第二个 challenger 不机械退出：在 15/30/60m 依次观察 full-basket direct-NO liquidation value；只有它相对
entry direct-NO bid 的净值恶化达到 development 所选阈值才立即退出，否则继续持有到 settlement。完整披露
9 个阈值，development 选出 `0.005 normalized USD/basket`（0.5¢）：

| split | triggers | hold ROI | stop-policy ROI | stop-minus-hold | 95% CI |
|---|---:|---:|---:|---:|---:|
| development | 8 / 54 | +0.79% | +0.85% | +0.06pp | [-1.26pp, +1.87pp] |
| locked（此前已观察） | 4 / 28 | -0.46% | +0.18% | +0.64pp | [-0.68pp, +2.29pp] |

它确实在 +15m 把 Helsinki 8/14 从 `-0.403525` 缩到 `-0.019915`，也把 Madrid 8/27 从
`-0.364565` 缩到 `-0.046827`；但同时提前卖掉 10 个最终盈利 basket，包括 Munich 8/16 从
`+0.140328` 变成 `-0.026162`。因此点估改善主要由两个已知 tail loss 驱动，CI 均跨 0，locked 也不是
clean forward。

与既有 `p_tail_market<=10%` 叠加后，development 选出的 stop 阈值为 10¢、实际 0 次触发；风险筛选已经排除
Helsinki/Madrid 两个 tail loss，stop 没有额外增厚，二者不是独立 alpha。额外固定 `min entry ask depth>=10`
诊断在 development 把 ROI 从 `+1.03%` 提到 `+1.46%`，但 locked 反而从 `+0.42%` 降到 `+0.26%`，不冻结
为新 gate。当前只把 0.5¢ selective stop 保留为未来 zero-notional 观测项，不部署；主候选仍是更简单的
`p_tail_market<=10%` entry filter。

## 典型 case 完整复核

统一链路是：D-1 当地 12:00 后第一份 PIT 完整 ladder → 低端 inward index=2 与高端 inward index=2
各选一个 exact NO → 两腿各 50% → NO ask 加官方 fee → 持有到 closed `pm_history` settlement。weather
forecast 只记 telemetry，不决定是否触发；四个可执行 case 为 `would_shadow_entry`，被阻断 case 保留原始
blocker，实际 plan/order/fill 均不存在。

### Case A：中位数普通盈利——Helsinki 2026-08-09

- 决策：2026-08-08 09:03:28 UTC / Helsinki 12:03 / 北京 17:03；第一份合格 ladder，book as-of
  09:03:30 UTC。
- 低腿 bracket 20 NO：ask `0.987`，fee `0.00064155`，单腿成本 `0.98764155`，depth `77.02`。
- 高腿 bracket 26 NO：ask `0.999`，fee `0.00004995`，单腿成本 `0.99904995`，depth `145.92`。
- basket 成本=`0.5×0.98764155 + 0.5×0.99904995 = 0.99334575`。
- closed raw 中 bracket 20/26 的 final YES 都是 0，因此两个 NO 都兑付；basket payout=`1.0`，
  PnL=`+0.00665425`，ROI=`+0.67%`。这接近 strict-forward 单 basket 的中位表现。

### Case B：少数较大盈利——Munich 2026-08-16

- 决策：2026-08-15 10:00:16 UTC / Munich 12:00 / 北京 18:00。
- 低腿 bracket 30 NO：ask `0.71`，fee `0.010295`，成本 `0.720295`，depth `5`；高腿 bracket 36
  NO：ask `0.999`，fee `0.00004995`，成本 `0.99904995`，depth `764.2`。
- basket 成本=`0.859672475`；closed raw 中两个 bracket 的 final YES 都是 0，payout=`1.0`。
- PnL=`+0.140327525`，ROI=`+16.32%`。策略的正收益主要靠这种“某一侧 NO 没那么贵、但最后仍没命中”的
  case，而不是每天稳定赚 16%。

### Case C：尾部命中亏损——Madrid 2026-08-27

- 决策：2026-08-26 10:04:55 UTC / Madrid 12:04 / 北京 18:04。
- 低腿 bracket 23 NO 成本=`0.72 + 0.01008 = 0.73008`；高腿 bracket 29 NO 成本
  `0.999 + 0.00004995 = 0.99904995`；basket 成本=`0.864564975`。
- closed raw 中 bracket 23 final YES=`1`、bracket 29 final YES=`0`：低腿 NO 归零，高腿 NO 兑付，
  所以 basket payout 只有 `0.5`。
- PnL=`-0.364564975`，ROI=`-42.17%`。一个这种 loss 约吃掉 55 个 Case A 规模的普通盈利。

### Case D：赢了但几乎没赚——Madrid 2026-08-28

- 两腿 bracket 24/30 的 NO ask 都是 `0.999`，各自 fee=`0.00004995`；basket 成本=`0.99904995`。
- 两个 final YES 都是 0，payout=`1.0`，但 PnL 只有 `+0.00095005`，ROI=`+0.095%`。
- 这解释了为什么 93.1% 日期为正却只有 `+0.81%` 总 ROI：大量“正确”是用接近 1 元买 NO，赔率极薄。

### Case E：盘口不足，不触发——Munich 2026-08-10

- 当地 12:03 第一份 ladder 的低腿 bracket 32 虽有 NO ask `0.95`，但 ask depth 只有 `0.01 share`，低于
  1-share executable contract；高腿可执行也不能补救。
- 结果为 `paired_book_unexecutable`：不产生 basket、不换用更晚报价、不产生 plan/order/fill/PnL。

严格 105 baskets 中，103 个无尾部命中合计赚 `+1.607274275`；2 个尾部命中合计亏
`-0.76808995`，净额只剩 `+0.839184325`。这就是高胜率、低总 ROI 的完整算术。

## Settlement 修复与影响半径

Madrid 2026-08-27 的旧 pm_history 在 market 未关闭时写入：23/24 档分别为
`0.985/0.012 missing_bracket`。刷新后的 closed raw SHA256 为
`571269219d7a48fd44e85acb27280f9c9f4380267a8d21fbb6393c73e8709c9a`，23 档最终为 winner。

canonical 表是 append-only，因此保留旧行，并新增两条受限 `manual_backfill` correction；不 UPDATE、
不 DELETE。影响只有一条策略 candidate：Madrid 8/27 bracket 23 从 missing blocker 变为 loss，candidate
cost=`0.864565`、PnL=`-0.364565`。修复后：

- locked-forward ROI：`+1.43% → +0.42%`，下降 `1.01pp`；
- 8/10..28 replay ROI：`+1.02% → +0.55%`，下降 `0.47pp`；
- orders/fills/live decisions 影响均为 0。

## Signal / evidence funnel

- frozen replay：114 个 city-days / 19 dates；81 paired executable、33 paired unexecutable；candidate
  SHA 在读取 settlement 前锁定。
- zero-notional collector：46 个 city-days / 12 raw dates；31 paired executable，其中 7 个 bootstrap partial，
  24 个进入 primary。
- historical blind sensitivity：原 84 snapshots / 8 dates 经审计只剩 47 first-city-day rows，排除 37 个
  duplicate snapshots；文件 SHA 锁定，但缺完整 rung/PIT contract，不进入 primary。
- strict-forward primary：24 + 43 + 38 = 105 baskets / 29 dates；10 个固定城市。8/10..28 replay 仅 6 城有
  scoreable candidate 是 raw coverage gap，不是事后城市筛选。
- actual order / fill / notional：`0 / 0 / $0`。

## 三门与研究冻结状态

- fixed-rule / chronological consistency：**PASS only for strict forward**。三个 forward 窗 point ROI 全正；
  historical duplicate-snapshot 污染已排除。
- same-row market mechanism：**FAIL / INCONCLUSIVE on 105 modern rows**。allocation-weighted market tail
  probability overestimate=`+0.40pp`，CI 跨 0。
- execution stress：**FAIL at +1.0¢**。strict-forward ROI=`-0.20%`；locked 单窗在 +0.5¢ 时已略负。
- significance：**FAIL**。strict-forward 95% CI=`[-0.46%, +1.93%]`；locked CI 也跨 0。
- conservative tail bound：**FAIL**。Wilson upper-tail stress 为负。
- live admission：**FAIL / NOT REQUESTED**。无真实 fill、queue、美元容量与新 prospective tail-loss 验证。

改进 entry overlay 的后段点估和 +1¢ stress 有改善，但 paired CI 下界为 0，且没有 clean new-date forward；
0.5¢ selective stop 也只把 locked common-coverage 点估增厚 `+0.64pp`，CI 跨 0。两者都不改变上述
incumbent 三门结论；机械 fixed exit 已否决，entry filter 与 selective stop 仅作为冻结的未来候选规则。

因此，“整体冻结”的含义改为：停止在这批数据上继续调 city/filter/threshold，保留本规则为
`frozen inconclusive direction`，撤回“当前 D-1 最优/回测不错”的说法。原 early-repricing taker 方向仍保留为
dormant/negative execution evidence。下一次只用新 settled target dates评分，不能回头优化本窗口。

## V1 frozen deployment（2026-08-31）

- frozen policy：10 个固定欧洲城市、当地 D-1 12:00–24:00 第一份完整且同 clock 的 ladder、低/高端
  distance=2 exact NO 各 50%，`joint_market_probability <= 0.10`（inclusive），weather features 仅作
  telemetry，first-lock 后不重算 eligibility。
- runtime：`europe_d1_distance2_dual_no_shadow_v1`；执行模式为 `zero_notional_shadow`，没有 plan/order/venue
  路径。formal forward start 保持 `2026-08-30T16:42:11Z`。
- immutable strategy release：`0ed49db4602ab7410207070dc0e03da6a11d6c57`；controller desired-state、health
  contract 与 observation-cache scope 修复位于后续 control-plane commits `5f1fd26f`、`39786b66`。
- 生产验收：strict manifest 为 warning-only，release checkout clean、HEAD 与 pin 一致、operational venv binding
  healthy；controller 中本 runtime healthy。首轮 current snapshot 为 10 candidates、10 paired-clock complete、
  6 paired executable/market-tail eligible、9 forecast telemetry；`actual orders/fills/notional = 0/0/$0`。
- append-only 边界：目录内旧 first-lock rows 原样保留并带旧 `repo_sha`；hardened V1 forward 评分只接受
  `repo_sha=0ed49db4602ab7410207070dc0e03da6a11d6c57` 的新锁，不清 state、不回填、不把旧 rows 混入分母。
- V2 不在本次部署范围；退出、更多过滤与训练特征继续保持研究候选，等待 V1 新 settled target dates 后另开冻结。

## 数据快照与证据封印

- canonical DB：`/Volumes/jrs/pm_agents/runtime/weather.db`，device=`16777244`，inode=`54444`。
- config SHA256：`3b6fb0ba8737bdd7063f7a8a6f26df69fdb54e87bfc69c26b18fb3f65d1f4631`。
- frozen candidate SHA256：`0fee3d9cf7ce2744362a9ff2206165735c15c6135dd0414aa581f3ecb189b06a`。
- historical blind v7 SHA256：`5d6e8a2638a5e882d80a769cf7c87605f2178514289cccdd47155af25c8d95e7`。
- zero-notional candidate journal SHA256：`aa4f061690f35af1fb140d282d49ce111ca7fc366e1b49323d4a0745670ceac5`。
- final score summary SHA256：`5e883664ff823ccc7b6683a1378f16f24c70dbb131690d9a6c95506579965a0d`。
- scored rows SHA256：`5bc00da9920f1da830ca92a6b85c2a5ff250e5b108535c9d0b7cf1b94625f1f0`。
- artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/europe_d1_distance2_dual_no_forward_v8/frozen_20260830T033214Z/score_grain_fixed_v8/`。
- market tail-risk overlay summary SHA256：`08194e6202bf8b92e4fcf8c554c74bcc5e5d34d992942a92651df9745e599c21`；
  research record SHA256：`d046c05e21036f934533cd7456d6c6a31abc65bd8cf780f6396baa123a5aa73b`；artifact：
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/europe_d1_distance2_dual_no_forward_v8/frozen_20260830T033214Z/risk_overlay_market_tail_v1/`。
- exit replay v2.1 summary SHA256：`30132de00c129f242620ba331d6b3b6999407d0d12591c3d9bd4ad25ff7905cf`；
  research record SHA256：`08d42c5c4c7a1373e88da8efa18ca5a7d14e5e803878639e88f5422410f0276d`；
  raw candidate-condition identity：`40bb6d38d3d95d15d3d69284117520cd7b11500d25ff4b99c67349d250702192`；
  artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/europe_d1_distance2_dual_no_forward_v8/frozen_20260830T033214Z/exit_replay_v2_1/`。
- `exit_replay_v2/` 的数值与 v2.1 相同，但 research record 使用了不合规的本机绝对 locator，validation FAIL；
  该目录保留为失败证据并由 v2.1 supersede，不作为 canonical 结论来源。
- `score_combined_forward_v8_primary_allocation_fixed/` 已 superseded：修正了 allocation，但仍把同一 historical
  city-day 的第二 snapshot 重复计入。更早的 `score_combined_forward_v8_primary/` 还同时含 allocation diagnostic bug。

## 证据环覆盖

- 描述性绩效：覆盖；概率不确定性：target-date bootstrap 覆盖。
- 分母/相关性：105 个 strict-forward rows 固定 city-day first-ladder；历史 raw 84 snapshots 只保留 47 个
  first-city-day sensitivity rows；同 snapshot-key baseline 与 target-date block 覆盖。
- 概率质量：105 个 modern rows 覆盖；历史 47-row sensitivity（raw 84 snapshots）不补造 joint market probability。
- 执行/成交质量：entry ask、官方 fee、depth 与 +0.1/+0.5/+1.0¢ stress 覆盖；exit direct-NO full bid
  ladder、逐 level fee、exact response clock 与双腿 same-batch 在 82-basket common coverage 上覆盖；真实
  fill/queue 未覆盖。
- 稳定性：三个 strict-forward 时间窗、historical sensitivity、positive-date rate、bootstrap partial 隔离覆盖。
- 容量：1/5/10-share depth 可见；美元规模、冲击成本和真实 queue 未覆盖。
- 尾部：实际 hit、Wilson upper stress 覆盖；更多新 tail-loss 日期仍缺。
- 改进 overlay：entry risk 105/105 覆盖；13-threshold multiplicity、paired delta、+1¢ stress 覆盖；exit
  9-threshold multiplicity、105×3 opportunity denominator、coverage blocker、paired delta 与 case path 覆盖；
  clean new-date forward、forecast-model 全分母、真实 exit fill/queue 未覆盖。
