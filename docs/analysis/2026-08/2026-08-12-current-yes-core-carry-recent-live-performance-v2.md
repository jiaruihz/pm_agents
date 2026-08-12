# Core Carry 最近实盘表现与 maker 效果复盘 v2

Status: `current-reference / recent-positive / maker-operational-low-sample / no-live-change`

## 结论

最近体感上的准确度提升有数据支持，但还不能归因于新 maker，也还不能证明模型进入了一个永久更好的阶段。

- 以 `target_date=2026-08-03..2026-08-11`、已结算、每个 Core signal 合并全部真实 fills 为固定分母：33 个 signals，32 胜 1 负，准确率 `96.97%`；fee-adjusted PnL `+$24.8047`，成本 `$359.1630`，ROI `+6.91%`。
- 更早的 7/25–8/02 为 29 个 settled signals、26 胜 3 负、准确率 `89.66%`、ROI `-10.02%`。最近准确率高 `+7.31pp`，但 target-date block bootstrap 的差值 CI 为 `[-4.76pp,+17.95pp]`，尚不能确认是稳定跃迁。
- 全部当前 live 历史仍只有 62 个 settled signals、58 胜 4 负，准确率 `93.55%`；PnL `+$4.5565`、ROI `+0.81%`，target-date block ROI CI `[-7.61%,+7.58%]`。最近窗口很好，但尚未抹掉全历史的不确定性。
- 最近唯一失败是 Manila 8/07 的 5-share maker-only fill：taker 提交报错，maker `5 @ 0.83` 成交后 exact bracket 失败，PnL `-$4.15`。因此实际策略 exposure 是 32/33；有真实 taker fill 的 settled signals 则是 32/32。

当前动作：保持 Core selector、10 taker + 最多 5 maker 和单信号 15-share cap；不因本轮结果加仓或改模型。新 maker 继续收集真实 fill/adverse-selection 证据。

## 固定分母与数据完整性

- canonical DB：`/Volumes/jrs/pm_agents/runtime/weather.db`；仓库入口 `runtime/weather.db` 与物理 DB 为同 device/inode，strict manifest 与 storage identity 均通过。
- trade grain：`fact_trades` 中 `instance_id=current_yes_core_carry_tiny_live_v2`、`fill_qty>0` 的 `live_real` fills；按 `(signal_id, city, target_date)` 合并后计算准确率和组合 PnL。
- maker intent grain：Mac raw `live_orders.jsonl` 中 `child_order_role=maker` 的 root intent；只用 authenticated terminal fill / canonical fill 认定成交，不用 future touch。
- 概率：canonical 的历史 migration 曾漏读 `model_token_probability`，本报告从 raw taker order 恢复，不使用污染的 `fact_trades.model_p_yes`。
- 已执行 Mac market 增量同步与 bounded canonical refresh；CLOB fill coverage gate 为 `gate_pass=true`，DB/cache fill 差异、missing order 和 over-cap 均为 0。
- Buenos Aires 8/11 的 settlement source 仍是下单前 `closed=false` 快照，Shanghai 8/12 尚未结算；两笔都不进入 accuracy/PnL 分母。其 submitted/filled cost 分别保留为 open evidence，不冒充亏损。

## 整体与最近表现

| 窗口 | settled signals | 胜-负 | 准确率 | settled cost | fee-adjusted PnL | ROI | target-date block ROI CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 7/25–8/12 全部已结算 | 62 | 58-4 | 93.55% | $561.1609 | +$4.5565 | +0.81% | [-7.61%, +7.58%] |
| 7/25–8/02 | 29 | 26-3 | 89.66% | $201.9979 | -$20.2482 | -10.02% | [-20.31%, +7.58%] |
| 8/03–8/11 | 33 | 32-1 | 96.97% | $359.1630 | +$24.8047 | +6.91% | [+5.31%, +9.29%] |

最近 32 个 winner 平均每个 signal 贡献 `+$0.9048`；唯一 Manila loss 为 `-$4.15`，约吞掉 4.6 个平均 winner。全历史四次失败为 Chengdu `-$13.8826`、Singapore `-$12.2819`、Lucknow `-$8.5638`、Manila `-$4.15`。这仍是高价 carry 的核心风险：高准确率不等于低尾部风险。

### 执行信号上的概率质量

最近 33 个 settled signals 的 raw Core 平均概率为 `94.30%`，实际命中 `96.97%`；同刻 market mid 平均 `91.07%`，best ask 平均 `92.33%`。

| 同 33 个 executed signals | Core | market mid | Core − market |
|---|---:|---:|---:|
| Brier | 0.03046 | 0.03219 | -0.00173，CI [-0.00559, +0.00070] |
| logloss | 0.13346 | 0.15004 | -0.01658，CI [-0.03674, -0.00134] |

这对 Core 是支持性证据，但只是经过 live selector 和可执行盘口筛选后的 executed denominator，不等于全 checkpoint 的独立模型验证。

## maker 到底有没有效果

### 最近全部 maker（v2 + v3）

- 28 个真实 root maker intents，15 个成交，intent fill rate `53.57%`；不是“一直不成交”。
- 已结算 maker 为 70 shares、cost `$63.20`、PnL `+$1.80`、ROI `+2.85%`；target-date block ROI CI `[-4.10%,+13.25%]`。
- 同期 taker 为 320 settled shares、PnL `+$23.0047`、ROI `+7.77%`。maker 作为额外 5 股 exposure，把组合绝对 PnL从 `+$23.0047` 增至 `+$24.8047`，但组合 ROI 从 `7.77%` 稀释到 `6.91%`。
- 15 个 maker fills 中 14 个有同 signal taker VWAP。maker 相对这些 taker fills 共节省 `$0.8269`，即 70 paired shares 平均改善 `1.18c/share`；这只计成交价改善，未把假设 taker fee 算进来。
- 唯一 maker-only Manila loss 说明 maker 的价值不能只看便宜了几分钱：它也会新增一段 taker 没拿到的 adverse-selected exposure。

所以 maker 当前的准确表述是：**提高了绝对利润和成交价质量，但尚未提高组合 ROI，统计上也未确认独立正收益。**

### 新 staged maker v3

v3 首次加载于 `2026-08-10T14:31:12Z`。截至本报告：

- 6 个 Core entry attempts；5 个 taker 成交，Warsaw taker 报错。
- 4 个实际 maker-eligible root intents：Panama、Karachi、Buenos Aires、Shanghai；2 个成交，fill rate `50%`。
- Karachi 初始 maker 提交失败后由 repost 恢复，`5 @ 0.88` 成交；Shanghai 初始 `0.821` 未成，第一次 scheduled reprice 到 `0.852` 后 `5` 股成交。两笔都证明 event/lifecycle reprice 路径真实工作。
- 两笔相对同 signal taker 成交价共改善 `$0.265`，平均 `2.65c/share`。
- 只有 Karachi 已结算，maker PnL `+$0.60`；Shanghai 尚未结算。Panama reprice 后未成并按 TTL 撤，Buenos Aires 在 source-report deadline 前撤。
- v2 最近为 24 intents / 13 fills（54.17%），v3 为 4 / 2（50%）。四笔 v3 样本不能证明 fill rate 改善；v3 当前确认的是生命周期修复和价格改善，不是收益率晋升。

London 8/11 只有 taker、没有当时的 maker root；后来补写的 terminal-blackout 记录不算历史 maker intent。该遗漏属于已修复的 clock/lifecycle 影响窗，不能拿它虚增 v3 未成交分母。

## 可执行 insight

1. 最近准确率提升主要来自 Core signals 的实际结果，不是新 maker：v3 已结算 maker 目前只贡献 `+$0.60`。
2. maker 应继续拆成两个 KPI：`paired price improvement` 衡量执行质量，`incremental maker-leg PnL/ROI` 衡量新增 exposure。把二者混成一个“maker 有效/无效”会误判。
3. 当前目标若是放大绝对利润，maker 最近 `+$1.80` 是正贡献；若目标是提高 ROI，当前 additive 5-share maker 尚未做到。
4. 不调整参数。等 v3 再积累真实 maker-eligible intents、settled fills 与 loss/adverse-selection 后，继续按同一 intent 分母复评，不从触价或少数 winner 推断成交能力。

## 可复现口径

- signal performance：`fact_trades` → filter active instance / positive fill quantity → group by signal → settled only → sum fee-adjusted `pnl_usd_at_fill` and `cost_usd`。
- maker/taker split：join canonical `orders` by `execution_id`，以 `maker_only`、`child_order_role`、`execution_action` 判 leg；同 signal maker/taker VWAP 计算 price improvement。
- maker funnel：raw `entry_attempts.jsonl`、`live_orders.jsonl`、`maker_lifecycle_decisions.jsonl`；terminal authenticated state 和 canonical fills 双向核对。
- CI：固定 seed `20260812`，20,000 次 target-date block bootstrap；准确率前后差使用 50,000 次独立 period date-block resample。

