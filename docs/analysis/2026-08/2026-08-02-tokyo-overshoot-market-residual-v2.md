# Tokyo overshoot market residual v2

## 数据快照

- observed_at_utc：`2026-08-10T10:25:40Z`；当前生产 manifest 的 canonical DB route 为同 inode healthy，
  但全局 health 仍因非 Tokyo observation coverage 与旧 canonical-refresh LaunchAgent last-exit 非零而为
  `CRITICAL`。本轮不读 canonical DB，不发布 live PnL，只使用 Tokyo raw first-seen/book 与 detached settlement。
- PIT 输入：`/Volumes/jrs/weather_data_feed_service_runtime/output/live_cross_observations` 与
  `tokyo_current_break_active_ladder_shadow/active_bracket_books`；冻结 artifact training_end=`2026-07-15`。
- primary forward slice：`2026-08-04..08-09`，6 target dates，理论 288 个十分钟 checkpoint，捕获 287，
  同刻 current-bracket 双边盘口可评分 255；8 月 3 日仅捕获 15/48，单列 coverage-gap，不混入主结论。
- settlement：8 月 3–8 日使用 raw `pm_history` near-binary winner；8 月 9 日 `pm_history` 尚缺，使用
  IEM RJTT 48 条完整本地日报文得到 33°C，只标 `late_backfill_label_only_not_first_seen`。missing settlement=0，
  但 8 月 9 日证据等级低一层。8 月 10 日截至 18:25 CST 已有 48/48 个 10:00–17:50 JST source/book
  checkpoint，文件仍在增长且事件未结算，故不进入 accuracy、Brier、胜率或 ROI。
- 本轮是 zero-notional research replay：actual fills=0；盈亏均为每个选中信号 taker 买 5 shares、按官方
  Weather fee curve 持有到结算的 counterfactual，不是实盘 PnL。

## 结论

截至 2026-08-10 的当前结论：8 月 4–9 日 frozen forward checkpoint proper score 未胜 market，
current-NO 仍 0 触发；机械双边 first-per-bracket 为 9 笔 3 胜、fee 后 ROI `-16.48%`。
因此 v2 只保留 zero-notional 数据链，`significance/baseline/forward=FAIL`，不具备 live 资格。
下文早期阶段结论按时间保留，最新复评见末节。

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

盈亏列采用 research replay：在该 checkpoint 以 NO ask taker 固定买 5 shares，entry fee 使用官方
Weather 曲线，持有至结算；`fee 后 PnL = 5 × (NO payout - NO ask - entry fee/share)`。它不是 actual fill。

- **正式 v2 总计**：要求 `P(NO) - NO ask - fee >= 2%`，固定 5 shares；0 笔，成本 `$0`，
  fee 后总 PnL **`$0`**。
- 只按模型方向 `P(NO) >= 50%` 买入的诊断：26 笔、26 胜，成本 `$120.27220`，fee 后 PnL
  `+$9.72780`，ROI `+8.09%`。这不是 market-residual 策略，而且包含大量 99c
  的极低收益重复买入。
- 无条件在 44 个可评分 checkpoint 各买 5 shares（包括模型明确不买的行，并会对同一 bracket
  重复加仓）：26 胜 18 负，成本 `$128.59870`，fee 后 PnL `+$1.40130`，ROI `+1.09%`。

下表只是从完整 44 行中选出的路径锚点，**没有过滤 99c 行**；完整逐 checkpoint 数字在 CSV：

| JMA JST | 决策 JST | current | JMA °C | market mid | NO ask | v2 P(NO) | fee 后 edge | v2 会买？ | 最终 NO | 强制买 5 shares fee 后 PnL |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 10:30 | 10:44 | 31 | 30.8 | 99.65% | 99.9% | 99.52% | -0.385% | 否 | 赢 | +$0.00475 |
| 12:40 | 12:48 | 32 | 32.4 | 93.35% | 94.7% | 90.14% | -4.814% | 否 | 赢 | +$0.25245 |
| 13:40 | 13:47 | 33 | 34.2 | 95.00% | 98.0% | 95.19% | -2.903% | 否 | 赢 | +$0.09510 |
| 14:10 | 14:17 | 34 | 33.6 | 57.50% | 60.0% | 51.18% | -10.023% | 否 | 赢 | +$1.94000 |
| 14:50 | 14:57 | 34 | 34.3 | 77.50% | 79.0% | 65.85% | -13.982% | 否 | 赢 | +$1.00850 |
| 15:00 | 15:08 | 35 | 34.7 | 17.50% | 21.0% | 14.13% | -7.704% | 否 | 输 | -$1.09150 |
| 15:30 | 15:37 | 35 | 35.3 | 33.50% | 35.0% | 28.82% | -7.321% | 否 | 输 | -$1.80685 |
| 16:40 | 16:52 | 35 | 34.3 | 1.10% | 2.0% | 0.40% | -1.695% | 否 | 输 | -$0.10490 |
| 17:20 | 17:27 | 35 | 33.3 | 0.15% | 0.2% | 0.15% | -0.056% | 否 | 输 | -$0.01050 |

### 下午负 PnL 诊断

15:00 之后的 18 行负 PnL 不是 18 个模型错误，而是“无视模型、强制买 current NO”的反事实：

1. 15:00 时 official running max 已进入 35 档；当日最终也正好结算 35，所以此后的 `35 NO`
   payout 都是 0。exact-bracket 语义下，只有最终升到 36 或更高，35 NO 才赢。
2. v2 在这 18 行的 `P(35 NO)` 全部低于 50%，且 fee 后 edge 全为负，因此正式策略一笔都不会买。
   15:00–17:50 的模型方向判断实际是 18/18 正确。
3. 15:30 JMA 打到 `35.3°C` 时 market NO midpoint 短暂升到 33.5%、模型升到 28.8%，反映的是
   “还会不会 overshoot 35 档”的风险上升；JMA AMeDAS 小数温度不是 official settlement cross，最终
   official 没进入 36 档。模型仍判断不应买 35 NO，这次判断正确。
4. proper score 也支持这一点：15:00 后 v2 Brier `0.00826`，优于同 rows market `0.01277`。
   当天真正的模型弱点反而在 15:00 前：26 个最终会赢的 NO 上，v2 Brier `0.03246`，差于 market
   `0.02343`，尤其 14:00–14:50 对 34 NO 的概率偏低；这是 under-confidence，不是下午误判。

完整 48 行：`generated/tokyo_overshoot_intraday_replay_v2/checkpoints.csv`；摘要：
`generated/tokyo_overshoot_intraday_replay_v2/summary.json`。

```bash
.venv/bin/python scripts/analysis/market_structure_edge/replay_tokyo_overshoot_intraday_v2.py
```

## 2026-08-02 forward 与持续零信号诊断

### 数据完整性

8 月 2 日不能按“完整一天”发布：目标窗口理论应有 48 个十分钟 JMA checkpoint，raw exact collector
只有 24 个，覆盖率 50%。缺口为 `12:00..15:50 JST`；11:50 后 producer identity 从
repo SHA `41806c87` 变为 `c2047752`，16:00 才恢复新 observation，属于 collector/deployment
中断，不是策略筛除，也不能 late backfill 后冒充 exact first-seen。

旧 replay 还会把 11:50 observation 错配到 16:08 的 stale book。现已给 source first-seen→book
增加 900 秒 PIT 配对上限；该行改记 `timely_book_capture_gap`，不再评分。修复后的 evidence funnel：

```text
48 expected checkpoints
→ 24 exact first-seen observations
→ 23 timely current-bracket books
→ 22 two-sided scored rows
→ 0 current-NO signals
```

受影响窗口是 8 月 2 日 12:00–15:50 JST 共 24 个缺失 checkpoint；没有 paper intent、order、fill，
所以执行影响为 0 笔，但该窗口不得进入 exact-forward 概率或 ROI 分母。完整可用行见
`generated/tokyo_overshoot_intraday_replay_v2_20260802/checkpoints.csv`。

### 8 月 2 日结果

- final official bracket = 33；22 行可评分，v2 Brier `0.000381`，market Brier `0.000291`，
  v2 仍未胜 market。
- current-NO fee 后最大 edge 只有 `-0.282%`，最小 `-1.952%`；2% 门槛 0 笔，拿掉门槛仍
  没有正 fee-adjusted edge。
- 最接近入场的是 10:00：market NO midpoint 96.45%、ask 97.0%、v2 P(NO) 96.86%；模型相对
  midpoint 只增加 0.41%，不足以覆盖 0.55% half-spread、fee，更不用说额外 2% margin。
- 单纯按 `P(NO)>=50%` 固定 5 shares 会买 11 次、11 胜，但成本 `$54.26795`、fee 后只赚
  `+$0.73205`；这是大量 97–99.8c 的重复低收益方向票，不是 market residual alpha。

### 一直 0 信号是不是正常

**单日 0 信号正常，但当前 v2 连续为 0 已是结构性问题，不能再解释成健康的“耐心等待”。**

冻结 7/16–7/30 的 238 行、8/1 的 44 行和 8/2 的 22 个可用行合计 304 个 checkpoint，
current-NO taker 表达没有一笔 fee-adjusted 正 edge。原因不是单独的 2% threshold：v2 以 market
midpoint logit 为固定 prior，ridge correction 很小，而交易必须跨 ask spread 与 Weather fee；模型只要
没有产生足够大的、方向正确的 market residual，就天然不会触发。

双边表达也不是直接修复：若把 8/1 对称扩成 current-YES，2% edge 会触发 4 笔，只有 2 胜，固定
5 shares fee 后 `-$1.3420`；8/2 仍为 0 笔。因此不能靠降低门槛或机械开放 YES 制造交易。

当前结论应收紧为：probability challenger 保留 collector，但 `current-NO taker v2` 交易表达
`rejected_for_expression`；significance=NA，baseline=FAIL，forward=FAIL，不改 live。下一版必须先在
完整 exact-forward rows 上用更有独立信息量的天气/path innovation 打败同 rows market，再重新评估
双边 executable edge，而不是继续调低 2% threshold。

## 2026-08-03 当前状态

截至北京时间 `2026-08-03 00:26`（东京 `01:26 JST`），东京 8 月 3 日 active 日内窗口尚未开始，
还没有 `2026-08-03.jsonl` JMA/book checkpoint，故不存在可发布的 8/3 胜率、edge 或 PnL。collector
健康状态只能说明进程已恢复，不能提前把 8/2 或 future 数据当 8/3 first-seen。

## 2026-08-04..08-09 frozen forward（8 月 10 日复评）

### 根因修正与影响半径

旧 replay 用 official observation journal 的最后一条 `running_max_c` 充当最终标签；journal shard 缺失时，
会把不完整盘中最高值冒充 settlement。8 月 3 日因此曾被错标成 26°C，而 raw pm_history 与完整 RJTT
METAR 的最终档均为 27°C。修复后：PIT current anchor 优先读取 book 在当时保存的
`capture_anchor_values.official`；最终 label 只读 pm_history，缺失时必须显式提供 label-only late backfill，
且绝不进入特征或 first-seen clock。

污染窗口为本轮曾生成过的 8 月 3 日旧 summary：15 个 scored checkpoint 的 label 与 Brier/PnL 全部受影响；
没有 candidate、intent、order 或 fill，生产影响为 0 笔。修复前 Brier `0.37625` vs market `0.42337` 的
“模型优于市场”结论作废；修复后为 `0.09059` vs `0.06537`，实际是模型更差。8 月 4–8 日旧 final bracket
与 pm_history 一致；8 月 9 日从一开始就按 late-backfill label-only 分层。

### 概率质量

主分母只取覆盖完整的 8 月 4–9 日：287/288 exact source checkpoints，255 行有同刻双边盘口。

| grain | rows / dates | v2 Brier | market Brier | v2-market | date-block 95% CI | accuracy v2 / market |
|---|---:|---:|---:|---:|---:|---:|
| 每十分钟 checkpoint | 255 / 6 | 0.07673 | 0.06897 | +0.00777 | [-0.00541, +0.02340] | 91.76% / 92.94% |
| 每次新 current bracket 首行 | 21 / 6 | 0.03989 | 0.04554 | -0.00565 | [-0.01186, +0.00017] | 100.00% / 95.24% |

checkpoint 主口径下模型比 market 差约 11.3%，logloss 也是 `0.26924` vs `0.23792`；不能称为
market residual alpha。state-entry 点估反而略优，说明“刚升入新档时的路径信息”可能比每十分钟重复预测更有价值，
但 21 rows/6 dates 且 CI 仍跨 0，只能继续积累，不能拿 100% accuracy 升级策略。

为避免 91.76% 被重复、近定局 checkpoint 抬高，另固定 market midpoint uncertainty slice：

| 去重/难例口径 | rows | v2 accuracy | market accuracy | 解释 |
|---|---:|---:|---:|---|
| market midpoint 10%–90% | 96 | 79/96 = 82.29% | 82/96 = 85.42% | 去掉两端近定局行后的主难例诊断 |
| market midpoint 20%–80% | 70 | 59/70 = 84.29% | 62/70 = 88.57% | 更窄 uncertainty band，结论同号 |
| 每个 date×current bracket 首行 | 21 | 21/21 = 100.00% | 20/21 = 95.24% | 去掉十分钟重复，但样本极小、CI未过门 |
| 最终选中交易 | 9 | 模型方向 8/9 = 88.89% | 市场方向 8/9 = 88.89% | 交易实际仅3/9获胜，见下文 |

原 255 行也不是单纯 NO class imbalance：最终停在 current（YES）118 行，v2 答对 117；最终继续
overshoot（NO）137 行，v2 只答对 117，弱于 market 的 122。更诚实的 headline 因此是
**hard-checkpoint accuracy 82.29%（market 85.42%）**，而不是 91.76%。

逐日 checkpoint Brier delta（负数才是模型优于 market）：

| target_date | delta | 判定 |
|---|---:|---|
| 08-04 | +0.02048 | 模型更差 |
| 08-05 | -0.01212 | 模型更好 |
| 08-06 | +0.04182 | 模型明显更差 |
| 08-07 | -0.00277 | 模型略好 |
| 08-08 | -0.00601 | 模型更好 |
| 08-09 | +0.00509 | 模型更差 |

### 交易表达

固定此前规则：`edge_after_fee >= 2%`，每个 `target_date × current_bracket × side` 只取首次信号，
5 shares、taker ask、官方 entry fee、持有至结算。

- current-NO：**0 笔**。这仍是结构性无 edge，不是漏跑。
- 对称开放 current-YES：9 笔、3 胜 6 负，投入 `$17.9598`，fee 后 PnL **`-$2.9598`**，
  ROI **`-16.48%`**；target-date block bootstrap 95% CI `[-69.30%, +24.95%]`。
- 分日 PnL：08-04 `-$1.1943`、08-05 `+$1.9150`、08-06 `-$2.8244`、
  08-07 `-$0.0497`、08-08 `+$1.2031`、08-09 `-$2.0095`。

91.8% checkpoint accuracy 与 3/9 策略胜率并不矛盾：多数 checkpoint 是 market 与模型都判断很容易的
current-NO；真正入选的是最不确定、模型相对盘口最激进的 current-YES 尾部。8 月 6 日 15:07 的 30 YES
就是主要错误之一：模型给 54.37%、ask 50c，但最终升到 31；这类边界 residual 的校准还不够稳定。

因此结论为：`current-NO expression rejected`；机械双边 YES 同样未通过。state-entry probability 方向保留为
下一轮研究假设，但不改当前 zero-notional 行为。

```text
significance=FAIL（概率与交易 CI 均跨 0）
baseline=FAIL（checkpoint proper score 未胜同 rows market）
forward=FAIL（6 dates，双边交易 -16.48%，且低于 10 active-day 门槛）
conclusion=inconclusive / not live eligible
```

耐久产物：

- `generated/tokyo_overshoot_intraday_replay_v2_forward_20260804_20260809/aggregate_summary.json`
- `generated/tokyo_overshoot_intraday_replay_v2_forward_20260804_20260809/selected_trades.csv`
- 含 8 月 3 日 coverage-gap 的诊断分母保存在对应 `20260803_20260809` aggregate，不用于主结论。

## 2026-08-10 第一性原理重构：market-confirmed previous-NO

### 为什么整体正确率尚可，选中交易却很差

`91.76%` 是 255 个十分钟 checkpoint 上用 0.5 阈值计算的分类准确率，包含大量重复且接近定局的状态；
去掉 market midpoint 两端的容易样本后，模型在 10%–90% 难例上的准确率只有 `82.29%`，还低于同分母
market 的 `85.42%`。旧 selector 又专门挑 `P_model-P_market` 最大的分歧尾部，而不是挑模型最有把握的结果，
因此放大了 residual 估计噪声（winner's curse）。最后，current-YES 是 exact-bracket 表达：即使当前档已经出现，
后续多升一档它仍会输。三件事叠加，便会出现“模型方向 8/9 看似正确、交易却只有 3/9 获胜”。

这不是继续调 edge threshold 可以解决的问题。旧 current-YES/current-NO residual expression 保持
`rejected_for_expression`；Tokyo 重新拆成两个独立问题：

1. terminal full-ladder probability head：继续要求 weather/path 模型在同分母 PIT Brier/logloss 上胜过 market，
   当前尚未通过；
2. source-event execution head：JMA first-seen `.7°C` cross 只作为物理触发，买被跨过档位的 previous-NO；
   market 只作为 source-basis false-cross veto，不把下一份 METAR confirmation 概率冒充最终结算概率。

### 数据与模型刷新

历史 JMA/METAR 路径已刷新到 `2026-08-09`：JMA `119,784` rows、METAR `40,766` rows，形成
`117,410` feature rows / `829` dates；event-safe v2 为 `3,419` first-cross events / `753` dates。
这些长历史数据用于天气路径与 source-basis 研究，但其时钟仍明确标记为
`historical_observation_clock_not_first_seen`，不能冒充 exact first-seen forward。下载器同时改为复用本年 IEM cache、
只补缺失尾部并重试瞬时网络错误，避免每次重训重复下载整年文件。

对应 artifact：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/tokyo_jma_multivariate_path_refresh_20260810_v2/summary.json`
  (`sha256=ebc50e720c75bb21a71b3ff19d4ee68547818a4dd4403a30b82d81da90f5fd6d`)
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/tokyo_jma_multivariate_path_v2_refresh_20260810/summary.json`
  (`sha256=f5b42ead0873c925cfc23a88a82588daba87d879fb3e767a903c17eabfe5d5be`)

### 固定策略与结果

固定策略 `first_margin_ge_0p7_market_consensus`：每个 `target_date × previous bracket` 仅取首次真实 JMA
`.7°C` cross；读取该 event 之后保存的 previous-NO ask，要求 `0.80 <= ask <= 0.97` 且顶档至少 5 shares；
固定买 5 shares、按官方 entry fee、持有至结算。`0.80` 是 round-number structural arm，不是 q-confirm
天气概率 threshold，也不是 untouched 数据前预注册的最优点；外部物理事件与市场必须同向，用来避免拿 source cross
单独对赌 terminal basis。本段所有 ask sensitivity 都明确是 retrospective diagnosis。

| 窗口 | trades / target dates | 胜负 | cost | fee 后 PnL | ROI |
|---|---:|---:|---:|---:|---:|
| 2026-07-09..07-30 development | 7 / 7 | 7 / 0 | $32.3270 | +$2.6731 | +8.27% |
| 2026-08-01..08-09 retrospective new window | 4 / 3 | 4 / 0 | $18.7654 | +$1.2346 | +6.58% |
| 合并固定规则 | 11 / 10 | 11 / 0 | $51.0924 | +$3.9076 | +7.65% |

完整 signal funnel 为 64 个首次 date×bracket `.7` signals，其中 60 个已有 settlement、57 个方向正确；
可执行 market-consensus 子集为上表 11 笔。单用 T13 为 7 笔 6 胜、ROI `-0.19%`；单用 T3 为 8 笔
6 胜、ROI `-6.33%`，因此“固定等某一期 METAR”不是盈利机制。market veto 排除了三个已知可执行 false cross：
`7/26 ask=.77`、`7/29 ask=.119`、`8/05 ask=.65`。

合并结果的 date-block bootstrap ROI 95% CI 为 `[+5.96%, +9.74%]`，但这不能单独证明 alpha：平均成交
ask 为 `0.9255`，含 fee 平均 breakeven win probability 为 `92.90%`，而 11/11 的 Wilson 胜率下界只有
`74.12%`。更重要的是，8/1–9 数据已在形成规则时被查看过，所以它是 retrospective validation，不是 untouched
frozen forward。正确结论是：这是 Tokyo 当前唯一跑出正 fee ROI 的候选方向，但仍是
`point-profitable / unconfirmed / zero-notional only`，不能升 live。

为避免把单个 cutpoint 包装成局部最优，固定 round-number ask sensitivity 同时保留：下限 `.70/.75` 都是
12笔11胜、ROI仅 `+0.02%`；`.80/.85` 都是11笔全胜、ROI `+7.65%`；`.90/.93/.95` 分别为
9/7/3笔全胜、ROI `+6.54%/+5.74%/+4.30%`。正收益并非只存在于一个小数点，但主要跳变恰好来自
排除 ask `.77` 的7/26败单，因此仍存在明显的 selection risk；这正是必须冻结向前而不能继续调阈值的原因。

耐久结果：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/tokyo_market_confirmed_previous_no_20260810_v2/summary.json`
  (`sha256=c6bc5aa53b2196bb8762fb38f7cb86cdf868f9aae70d16bf765ec3f7f8014296`)
- 同目录 `market_consensus_threshold_sensitivity.csv`
  (`sha256=15b26dfae73b00d7165f4325331ed8be743d15401aac3c5c97fa6d317b75d973`)
- 复现入口仍为共享 runner `research_tokyo_jma_multivariate_market_v1.py --trigger-events ...
  --prior-trigger-join ... --target-shares 5 --consensus-min-ask 0.80`，没有复制 Tokyo v2/v3 一次性入口。
- 同目录 `phase_policy_trades.csv` 保存全部逐笔结果，固定 shares=5。

### 冻结后动作

从 `2026-08-11`（或规则冻结后的下一完整 settled target date）开始，只做 zero-notional forward；规则、ask
区间、shares 与 fee 口径全部冻结，至少积累 30 个新 settled target dates。同步采集 event 前 full ladder 与
event 后 `0/15/30/60s` book，用来区分“market 已吸收后的高胜率 carry”与真正可执行的 first-seen repricing。
只有新 forward 同时满足 probability/market baseline、fee-adjusted expression 和 stability 三门，才重新讨论 live。

本轮没有创建 candidate/intent/order/fill，也没有修改任何 live runner 或生产行为。8 月 10 日因 market-books
health 未通过且尚未 settlement，不进入任何准确率或 ROI 分母。

## 2026-08-11 二次升温 / re-cross PIT 审计

本节只回答一个问题：在 `31 NO` 经历首次 JMA `.7°C` cross、急跌、随后二次升温时，当前 Tokyo
特征和两个 zero-notional probability head 能否在第二次 cross 前识别风险。`.7°C` 只是 JMA source
diagnostic，不是 settlement truth；全程只用 collector-exact first-seen、当时已写入的 decision bundle 和 raw book。

结论分两层：**特征层看见了，当前概率头没有在 8 月 11 日 pre-cross 阶段识别成可用的 `31 NO` 概率。**

| JMA obs JST / first-seen JST | temp | 路径状态 | v7 P(31 NO) | overshoot v2 P(31 NO) | market NO mid | fee 后最大 edge |
|---|---:|---|---:|---:|---:|---:|
| 13:00 / 13:07:07 | 28.2 | 首次高点后 trough，距 32.3 低 4.1°C | 未评分 | 未评分 | 未评分 | 未评分 |
| 13:10 / 13:17:10 | 30.5 | +2.3°C/10m，30m slope +3.6°C/h，回收跌幅 56.1% | 3.24% | 4.78% | 7.50% | -4.63c |
| 13:20 / 13:27:05 | 30.7 | 连续第 2 个回升点，30m slope +3.8°C/h，回收 61.0% | 5.81% | 2.58% | 7.00% | -4.64c |
| 13:30 / 13:37:04 | 32.0 | 第二次 `.7` cross 已发生 | 99.90% | 99.41% | 99.80% | 约 0 / 负 |

因此 13:30 的高概率只是事后 confirmation，不是模型提前抓到二次升温。13:10 与 13:20 两个真正可提前行动的
checkpoint，两个模型都低于同期 market，fee 后 edge 全为负，也没有 selected candidate。

盘口时钟支持“外部参与者拿到更快真实报文”的推断，但尚不能锁定具体 feed：13:30:25 JST 时 `31 NO`
仍约 `.07/.09`；13:31:26 bid 到 `.10`，13:34:07 bid 到 `.53`，13:35:12 已 one-sided `.99`。
本地 JMA second-cross first-seen 是 13:37:04，比 `.99` 晚 112.3 秒；本地 routine METAR 更晚到 13:40。
这说明市场在我们 first-seen 前已获得或推断出新信息，不能证明是哪家数据源。

根因不是特征完全缺失，而是状态表达和 runtime anchor 不足：

- v5 feature row 已有 `delta10`、30/60m slope、warming-run、pullback；但没有 episode trough、rise-from-trough、
  recovery fraction、slope reversal、cross count 等“第二热波”状态。8 月 11 日的 +2.3°C/10m 已进入特征，
  weather-only break probability仍只有约 13.9%，说明现有 head 没学会这种深 pullback 后快速恢复。
- compact overshoot v2 只看 boundary distance、60m slope、距首次 strict high 时间和剩余时段；13:20 的
  60m slope 仍为 -1.6°C/h、首次高点已过去 60 分钟，所以它把明显的短周期回升继续解释成 fade。
- 13:00 trough 时 active ladder 只围绕 JMA rounded anchor `28` 抓书，没有保留 held/official bracket `31`，
  因而没有 `31 NO` holding checkpoint。对持仓退出/反转研究，这是明确的 observability gap。

对 2026-07-21..08-11 exact collector 全窗做相同 episode census，只有 **2 个**同时具备二次 cross 与
pre-cross 模型证据的 episode：8 月 5 日 `29`（两个模型在 re-cross 前均高于 50%，但 market 已给 95%，
fee 后仍无 edge）和 8 月 11 日 `31`（两个模型最高仅 5.81% / 4.78%）。即按最宽松的 50% 识别口径也只是
1/2；两次都没有正 fee edge。样本太少，不能发布“二次升温准确率”，但足以否定“当前模型已经稳定识别该流程”。

下一模型合同应是独立 conditional-reheat head：固定 held/official bracket，在每个 JMA first-seen 都评分
`P(final official Tmax > bracket | first cross → pullback → current recovery)`；补 episode trough/recovery、
短长 slope reversal、cross count、forecast peak/remaining heat，以及真实可用的 JMA rain/wind transition。
market velocity 只作同分母 baseline/joint challenger，不能让 market anchor 吞掉 weather innovation。
在拥有更多正 episode 前只记录 zero-notional telemetry，不改 live runner。

可复现入口与耐久产物：

- `scripts/analysis/market_structure_edge/audit_tokyo_second_reheat_pit_v1.py`
- `generated/tokyo_second_reheat_pit_audit_v1/{summary.json,checkpoints.csv,exact_episode_census.csv}`

```bash
.venv/bin/python scripts/analysis/market_structure_edge/audit_tokyo_second_reheat_pit_v1.py \
  --target-date 2026-08-11 --bracket 31 \
  --scan-start 2026-07-21 --scan-end 2026-08-11 \
  --output-dir docs/analysis/2026-08/generated/tokyo_second_reheat_pit_audit_v1
```
