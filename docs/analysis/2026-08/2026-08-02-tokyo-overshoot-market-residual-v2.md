# Tokyo overshoot market residual v2

## 结论

`P(final Tmax > current bracket)` 与 `P(final Tmax = current bracket)` 在**当前已观测最高档**上互为补集：

```text
P(current NO) = P(final Tmax > current) = 1 - P(final Tmax = current)
```

两种交易表达因此可以互换，但模型结构并不自动等价。旧 v5 独立训练 leave-current，旧 exact-ladder
又逐档产生 YES；两者没有强制共享一个 coherent distribution，selector 还曾叠加互斥 YES。v2 将目标明确为
current-NO overshoot survival，并用同刻 current-NO midpoint logit 作固定 prior，只学习天气路径 correction。

第一轮 weather-only monotonic challenger 未通过：冻结 15 日期 checkpoint Brier `0.04265`，劣于 v5
的 `0.03639`；238 条盘口行 Brier `0.02864`，也劣于 market `0.02092`；rolling-NO 为
10 笔、2 胜、ROI `-37.51%`。该版本保留为 dormant 负例，不部署。

第二轮 market-offset v2 通过“是否值得持续 shadow”的概率门，但**没有可交易 edge**：

- 历史训练证据：`2026-04-01..07-15` 共 6661 行 / 103 dates；前 88 dates development，
  后 15 dates validation。价格是 `/prices-history` midpoint-like proxy，时钟为
  `archive_reconstructed_plus_15m`，不是 exact first-seen，也不是 orderbook。
- validation：compact ridge-20 Brier `0.06503` vs market `0.06812`；logloss `0.22744`
  vs `0.23890`。
- 冻结 collector 检验 `2026-07-16..07-30`：238 rows / 12 dates。v2 Brier
  `0.01809` vs market `0.02092`，accuracy `97.48%` vs `96.64%`；date-block Brier
  delta `-0.00283`，95% CI `[-0.00722,+0.00038]`，点估改善但尚未显著。
- rolling current-NO：支付真实 quote spread 与官方 fee 后，2% edge 信号为 `0`；甚至 fee 前
  最大 edge 也只有 `0.00186`（0.186c）。因此 0 trade 不是漏跑，而是模型没有给出足以覆盖成本的优势。

## 模型与组合语义

模型每个 JMA 10-minute checkpoint 输出：

```text
p_no = sigmoid(logit(current-NO midpoint) + compact weather correction)
p_yes_current = 1 - p_no
```

compact correction 只使用 boundary distance、60m slope、minutes since strict high、remaining-to-18h。
没有 forecast PIT archive，因此没有偷偷用当前 forecast 填历史；late backfill/issue time 也没有被标成 exact
first-seen。

组合规则为“每个 bracket 第一次正 edge”，但同一时刻只允许一个 unresolved current-NO；已经被官方路径跨过的
旧 bracket NO 才转为 locked。模型不会同时买多个互斥 exact YES。当前 v2 因无 executable edge，只记录
概率、market residual、spread、fee 与 `would_enter=false`，不制造模拟成交。

## 状态与下一道门

- artifact：`generated/tokyo_overshoot_market_residual_v2/tokyo_overshoot_market_residual_v2.joblib`
- runtime：统一 `city_probability_shadow_v2` 增加 Tokyo v2 A/B profile；`emit_paper_intents=false`，
  notional/shares/orders 均为 0，不接 plan/order/fill/exit，不改变任何 live runner。
- verdict：`forward_collecting / probability challenger / no tradable edge / not live eligible`。
- 继续积累 20 个 exact collector train dates + 10 个完全 untouched holdout dates 后复评；必须同时要求
  proper score 稳定胜 market、出现正的 fee-adjusted executable edge、并通过 date-block CI，才允许讨论 paper intent。

可复现命令：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_tokyo_overshoot_market_residual_v2.py
```
