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
- deployment validation：`2026-08-02T00:36:37Z` 从开发 checkout 手工执行一次 `once`，写入 2 条
  Tokyo v2 evaluation；其 `repo_dirty_tracked=true` 只表示验证进程身份，不是 first-seen/feature/label
  污染，且 `would_enter=false`、0 intent/0 order。正式 tmux PID 随后的 runtime identity 为 clean
  production SHA。独立 checkout 缺默认 `.venv` 曾导致首次重启立即退出；启动器现改为在 kill 旧 session
  前校验 `PYTHON_BIN`，避免同类短暂中断。
- verdict：`forward_collecting / probability challenger / no tradable edge / not live eligible`。
- 继续积累 20 个 exact collector train dates + 10 个完全 untouched holdout dates 后复评；必须同时要求
  proper score 稳定胜 market、出现正的 fee-adjusted executable edge、并通过 date-block CI，才允许讨论 paper intent。

可复现命令：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_tokyo_overshoot_market_residual_v2.py
```

## 2026-08-01 连续日内 PIT 重放（口径纠正）

此前把“每个 date×bracket 首次正 edge”selector 表拿来解释日内运行，是错误的展示粒度；它只是一张
压缩后的交易表，不代表模型每天只预测一次。`07-28` 只是该 selector 的最后入选日期，也不是 JMA 或盘口
数据截止日。8 月 1 日发生在 v2 的 8 月 2 日部署之前，因此这里使用冻结至 `2026-07-15` 的 artifact，
对 8 月 1 日 raw first-seen 与 raw book 做只读 counterfactual replay。

正确分母为 8 月 1 日 `10:00..17:50 JST` 的每个十分钟 JMA observation：

- signal funnel：48 个 JMA first-seen checkpoint → 48 行全部保留 → 44 行同期 current-bracket 双边盘口
  可评分 → fee 后 `edge >= 2%` 为 0 行。
- evidence funnel：47 行存在当时官方 current-bracket book；其中 3 行（10:00/10:10/10:20）盘口单边，
  无 midpoint；13:50 的 collector 只抓到 `34|35|36`，而当时官方 anchor 仍为 33，是 1 行明确
  `anchor_capture_gap`，不能伪装成策略过滤。
- 44 个可评分 checkpoint 的 fee 后 edge 范围为 `-16.519c .. -0.056c`；拿掉已知的 0.5c
  market-logit floor 后仍然 0 触发。因此 8 月 1 日的 0 触发是“模型概率没有超过同期 NO ask + fee”，
  不是模型没运行，也不是到下午才运行。
- 当日最终 official bracket 为 35。current bracket 在 15:00 前为 31→32→33→34，买这些档的 NO
  事后都赢；15:00 后 current bracket 已为 35，买 35 NO 事后都输。v2 和 market 在 0.5 分类阈值上
  都是 44/44，但这只是方向分类；proper score 上 v2 Brier `0.02256`，反而弱于 market `0.01907`，
  不能把“100% 分类正确”解释为 alpha。

关键路径（概率均为 current-bracket NO）：

| JMA 时刻 JST | 实际决策时刻 JST | current | JMA °C | market mid | NO ask | v2 P(NO) | fee 后 edge | 最终 NO |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 10:30 | 10:44 | 31 | 30.8 | 99.65% | 99.9% | 99.52% | -0.385% | 赢 |
| 12:40 | 12:48 | 32 | 32.4 | 93.35% | 94.7% | 90.14% | -4.814% | 赢 |
| 13:40 | 13:47 | 33 | 34.2 | 95.00% | 98.0% | 95.19% | -2.903% | 赢 |
| 14:10 | 14:17 | 34 | 33.6 | 57.50% | 60.0% | 51.18% | -10.023% | 赢 |
| 14:50 | 14:57 | 34 | 34.3 | 77.50% | 79.0% | 65.85% | -13.982% | 赢 |
| 15:00 | 15:08 | 35 | 34.7 | 17.50% | 21.0% | 14.13% | -7.704% | 输 |
| 15:30 | 15:37 | 35 | 35.3 | 33.50% | 35.0% | 28.82% | -7.321% | 输 |
| 16:40 | 16:52 | 35 | 34.3 | 1.10% | 2.0% | 0.40% | -1.695% | 输 |
| 17:20 | 17:27 | 35 | 33.3 | 0.15% | 0.2% | 0.15% | -0.056% | 输 |

完整 48 行：`generated/tokyo_overshoot_intraday_replay_v2/checkpoints.csv`；摘要：
`generated/tokyo_overshoot_intraday_replay_v2/summary.json`。

```bash
.venv/bin/python scripts/analysis/market_structure_edge/replay_tokyo_overshoot_intraday_v2.py
```
