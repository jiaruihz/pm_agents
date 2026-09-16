# Low-Price YES (HeadA) Live Audit v1

> generated_at_utc: `2026-07-10T14:20:00Z`
> scope: HeadA `forecast_quality.low_price_yes_lottery` 链路审计：runner 代码 + canonical fact 血缘 +
> tiny-live forward（7/02–7/09 结算窗）。**本轮不改任何 runner/config/order 行为**。
> 姊妹篇：[tmax 链路审计](2026-07-10-tmax-model-lineage-audit-v1.md)。
> 样本量警告：live forward 只有 50 笔去重结算（4 个中奖市场 / 6 fills）、blocked 各组 13–23 行，以下所有"命中率"结论是方向性证据，不是显著性结论。
>
> **v1.1 更正（2026-07-11）**：v1 初稿 F2 的 −13.2% ROI 把窗口截在 7/02–7/08 fill，切掉了 7/09 两个 Seattle 中奖单，把一个正边写成了负边。全窗去重后真账是 **净已实现 +$6.35 / 约 +24% ROI on settled（50 结算 / 4 中奖市场 / 命中 12%）**。6 个中奖 fill 已逐一对 `clob_fills.jsonl` 核实全部真实成交（非 synthetic）。F1 双计对**彩票这条**仅 5 行、全在 7/02、且全是输单——方向是**低估利润**，不是虚增；系统级双计仍真实且近期（最近 7/09）需修。下方 F1/F2 已按此更正。

## 结论（先给动作）

| # | 发现 | 性质 | 建议动作 |
|---|---|---|---|
| F1 | **fact_trades 成交重复入账**（全 live_real 83 组、5/16–7/09、约 $258 双计成本；彩票仅 5 行/全 7/02/全输单→低估利润，不影响盈利结论） | canonical 层 bug，根因已定位 | 修 fill 摄入去重；重跑受影响 PnL；gate 加双计检查 |
| F2 | 全窗去重后 **净 +$6.35 / ~+24% ROI**（50 结算 / 命中 12%），但 `model_p_yes` 均值 0.38 vs 实际命中 12% 仍高估 ~3x + p 分箱反向校准 | 策略有薄正边；概率高估使 sizing 不可信 | 保留策略；p_yes 只作排序/telemetry，不驱动 score-tier sizing |
| F3 | **每一道 gate 挡下的票命中率都高于实际买进的票**（买进 8.7%；cushion 挡 16.7%；dist<0 挡 25%；stale 挡 28.6%） | 选票机制系统性反选 | 印证 book-state/adverse-selection 假说的 live 首读；重审 dist 硬 filter |
| F4 | p_yes 最长 6 小时陈旧、下单前不重估 thesis，只 gate 市场价格漂移 | 结构性 adverse selection | fresh-book 阶段加 thesis 重估（新 forecast run / 实时 obs） |
| F5 | 已证伪：`MAX(event_date)` 日窗滚动并未饿死 first-eligible 机会 | 假说测试，阴性 | 无需动作，留观察 |

---

## F1. fact_trades 成交重复入账（canonical 层，跨策略）

**现象**：lottery tiny-live 7/02 的全部 5 笔订单在 `fact_trades` 各出现两行——
同 `order_id`、同 `fill_ts_utc`（到秒）、同 `fill_qty`、同 `cost_usd`，但 **fill_id 不同**。
扩大到全部 `trade_class='live_real'`：**83 组重复**，日期跨 5/16–7/09（昨天仍在发生），
涉及 theta / mid_price / regime_routed / lottery / tmax / value_d1 全部策略，双计成本合计约 **$258**。
重灾日：6/18 theta 15 组（$74）、6/20 t2 theta 18 组（$71）。

**根因（已定位到代码）**：fill 有两条摄入路径——

1. 真实路径：`runtime/weather_edge_v1/clob_fills.jsonl`（该 Ankara 订单在 raw journal 里只有 **1 条**，
   fill_id `0c7501b6…`）；
2. 合成路径：[strategy_runtime_orders.py:302-327](../../../weather_dashboard/legacy_migration/strategy_runtime_orders.py#L302)
   `_runtime_fill_from_order` 对每个 `exchange_response.place.status=matched` 的 order
   **无条件**从 order journal 合成一条 fill，`fill_id = make_fill_id(execution_id=…)`
   （[调用处 :437](../../../weather_dashboard/legacy_migration/strategy_runtime_orders.py#L437) 无任何
   "journal 里已有该 order 的真实 fill 就跳过"的防重）。

两条路径 fill_id 生成方式不同 → 主键去重失效 → 两边都覆盖到的 order 全部双计。
第二条 fill_id `a20980ad…` 在所有 raw fills 文件（含 gate_repaired / rebuilt）中都不存在，纯属重建时合成。

**影响**：
- 任何用 `SUM(fact_trades.cost_usd / pnl_usd_at_fill)` 得出的 live_real 数字都可能被抬高
  （亏损侧被双计时亏损被夸大——lottery 一线 ROI 从表面 −30.0% 修正为去重后 **−13.2%**）。
- **现有 5 行 SQL 自检和 `weather_clob_fill_coverage_gate.py` 检查的是覆盖缺失，不检查重复**——
  这个方向的 gate 缺口需要补。

**修复方向**：fill 以 `clob_fills.jsonl` 为单一事实源；`_runtime_fill_from_order`
只在该 order_id 在 fills journal 中无记录时作为 fallback；dedupe 键改为
`(order_id, filled_at_utc, filled_shares, filled_price)` 而不是 fill_id。修复后重跑
受影响窗口的 fact rebuild 并重发历史 PnL 口径。

## F2. live forward：薄正边，但概率高估 + 反向校准（sizing 不可信）

去重后**全窗**（7/02–7/09 fill，50 笔已结算）：

```text
settled=50  wins(fills)=6 / 中奖市场=4  hit=12%  cost≈$26.4  net_pnl=+$6.35  ROI≈+24%
avg fill price ≈ 0.10     avg model_p_yes = 0.384
```

**先纠 v1 初稿的错**：v1 把窗口截在 7/08，漏掉 7/09 两个 Seattle 中奖单（净 +$10.56），
才得出 −13.2%。**全窗真账是正的**——12% 命中率 × 平均约 10c 进场、赢单赔 ~$1/share，EV 为正，
这是条**薄但真实的边**，不是"按公允价买彩票付摩擦"。结论从"证伪"更正为"保留"。

但**概率校准依然坏**：模型给平均 10c 的票标 **38.4% 胜率**，实际命中 12%（高估约 3x）。

更刺眼的是同窗 candidates（selector band 内、7/02+ 已结算，n=54）按 p_yes 分箱的命中率**单调反向**：

```text
p∈[0.2,0.3): n=24  hit 29.2%
p∈[0.3,0.4): n=14  hit 14.3%
p∈[0.4,0.5): n= 9  hit 11.1%
p>=0.5     : n= 3  hit  0%
```

模型越自信越错。而 `edge >= 0.20` 选票天然偏向高 p_yes 行 → **selector 在优先装载模型最过度自信的票**。
7/02 首日 5 单全灭（NYC `104-105` 标 p=0.50 这种面上就荒谬的票）正是 ECMWF 静默 fallback GFS
污染窗（fills 的 forecast_source 全是 `open_meteo_live_gfs`），但 7/03+ 修复后的票 p_yes 均值仍 ~0.39 vs 命中 ~12%，
**高估不能全归咎污染窗**。

这与 [alpha-decomposition](2026-07-06-low-price-yes-alpha-decomposition-v1.md) 的
"score 有排序力但同票数 A/B 未打赢 dist>0" 一致，且 forward 上排序力翻转为负。
**score-tier sizing（按 score 放大 1.2x/1.5x）建立在这个 p 排序上，在反向校准坐实前不应继续依赖。**

## F3. 每一道 gate 都在反选（book-state 假说的 live 首读）

把 runner decision journal（`shadow_decisions.jsonl`）按 `(candidate_id, status, blocker)` 去重后
join canonical `bracket_hit`（只算已结算）：

```text
decision                                    n    hit rate
planned（实际买进）                          23    8.7%
blocked: fresh_ask_exceeds_cushion_or_band  18   16.7%
blocked: dist_lt0_cold_or_inside            16   25.0%
blocked: decision_snapshot_too_stale        14   28.6%
blocked: missing_yes_token_id               13   15.4%
```

n 都很小，但**方向在四个独立 gate 上完全一致：被挡下的票都比买进的票命中更高**。逐条解读：

1. **cushion（16.7% vs 8.7%）**：fresh ask 比 snapshot ask 涨超 1c 就拒绝——但市场涨价本身就是信息
   （尾部真在发展时 ask 会快速 reprice）。拒绝追价 = 只留下市场不相信的票。这是
   [heada-book-state 假说]（train 边际集中在 book missing/宽 spread 行，feasible 行 ROI≈0）
   在 live forward 的第一次方向性确认：**能便宜成交的票恰恰是市场愿意便宜卖给你的票**。
   注意：hit rate ≠ ROI（cushion 挡下的票若追价会买贵），此处只证明方向，完整裁决要按追价成本重算 EV。
2. **dist_lt0（25.0%）**：这个硬 filter 是 7/04 从 train 切片（-10.6% train ROI）加的，
   forward 却在挡 25% 命中、约 8c 的票（若真如此 EV 极高）。n=16 太小不能下结论，但这正是
   CLAUDE.md 警告的"单次阈值实验加硬限制"模式——**建议把 dist_lt0 降回 telemetry-only 或安排同分母 forward A/B 裁决**，
   而不是继续当机制边界。
3. **snapshot_too_stale（28.6%）**：被新鲜度挡掉的票赢得最多，说明 fact 重建 cadence 在信号还有效时
   就让它过期了——**管道刷新频率在丢赢家**，值得查 fact_refresh loop 的实际间隔 vs 6h 上限的匹配。

## F4. 结构性 adverse selection：thesis 不重估，只 gate 价格

[validate_candidate](../../../scripts/ops/low_price_yes_lottery_tiny_live.py#L1255) 的门序：
dist → dedupe → snapshot age（**上限 6 小时**）→ token → fresh book → cushion → depth → fee edge。
`p_yes` 全程用 fact 行里的旧值，**下单一刻不重估 thesis**：6 小时内若新 forecast run 已把
forecast_max 砍下来（hot-tail 论点已死），只要盘口没涨价照买。与 F3-1 合成完整的逆选择结构：

```text
尾部真发展 → ask 上涨 → cushion 拒绝（错过赢家）
尾部已死   → ask 不动/阴跌 → 6h 旧 thesis 照常买入（收下输家）
```

**建议**：fresh-book 通过后加一步 thesis 重估——至少检查决策时刻的最新 forecast_max（或实时 obs 路径状态）
仍支持 hot-tail，不支持则显式 block（`thesis_invalidated_fresh_forecast`）。这是"修根因"而不是加 gate：
它把过期信号显式失败掉，而不是靠价格 proxy 猜。

## F5. 已证伪：日窗滚动不饿死机会（诚实记录阴性结果）

`effective_min_event_date = MAX(event_date)`（[:422](../../../scripts/ops/low_price_yes_lottery_tiny_live.py#L422)）
会在 UTC 13–17 点（领先时区次日候选一出现）滚到明天，理论上会拒绝当天所有后续候选。
用 7/02–7/09 每天的滚动时刻对 canonical 表回放：**8 天里 0 个 first-eligible city-day 出现在滚动之后**
（该信号本来就是早盘信号，13Z 前全部首现完毕）。主口径无损失；残余风险只剩
"滚动前被临时 block 的票失去当日重试机会"，量级小，留观察不动。

## 次要观察

- **score sizing 特征缺失静默用 train 中位数填**（[score_dist_sizing_features:1042](../../../scripts/ops/low_price_yes_lottery_tiny_live.py#L1042)）：
  与 tmax 审计 B2 同模式；至少在 telemetry 里记 per-feature 缺失率。
- **maker tick 硬编码 0.001**（[maker_price_for_buy:1134](../../../scripts/ops/low_price_yes_lottery_tiny_live.py#L1134)）：
  live 订单有成交说明当前市场接受，但换 tick=0.01 的市场会出无效价，建议从 book/market 元数据读。
- 7/04 出现过 min_event_date 7/05→7/04 回跳（fact 重建后 MAX 变化），说明日窗有抖动；对选择无实害，仅记录。
- signal→order 1:多（29 signals / 41 orders）来自 maker lifecycle cancel/repost，符合设计。
- 去重（signal_id + natural key）、depth walk、fee gate、maker-first 结构本身干净，比 tmax runner 成熟。

## 与既有研究结论的对账

- **book-state 假说**（[数据审计+边界 v1](2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md)）：
  F3-1 是它的第一份 live forward 方向性证据，等 forward 样本再厚一点可以正式裁决。
- **dist 分支**（[dist-branch v1](2026-07-04-low-price-yes-dist-branch-v1.md)）：F3-2 forward 方向与 train 相反，
  建议裁决升级为显式 A/B 而不是默认保留硬 filter。
- **score×dist sizing**（[score-dist sizing v1](2026-07-06-low-price-yes-score-dist-sizing-v1.md)）：
  回放 +49.5% 的前提是 p/score 排序有效；F2 显示 forward 排序反转，sizing overlay 的证据基础被削弱。
- **7/02 污染窗**：registry 已要求 forward gate 剔除/分层，本审计确认 7/02 的 5 单（去重后）全灭且
  全部带 GFS fallback 源，**建议发布任何 HeadA forward 数字时把 7/02 单列**。

## 建议落地顺序

1. **F1**：修 fact 重建 fill 去重（单一事实源 + fallback 才合成），重跑受污染窗口，
   fill coverage gate 加重复检查。这是所有策略共用的地基，优先级最高。
2. **F2**：任何依赖 model_p_yes 点值的东西（edge 门、score sizing）先降级为排序/telemetry，
   等重校准（分 source、剔污染窗、加校准曲线监控）后再恢复。
3. **F4**：下单前 thesis 重估（fresh forecast / obs），显式失败替代价格 proxy。
4. **F3-2**：dist_lt0 从硬 filter 降回 telemetry 或开同分母 forward A/B。
5. 次要项按顺手修：sizing 缺失率 telemetry、tick 从元数据读。
