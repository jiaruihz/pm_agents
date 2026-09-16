# Strategy: d1_yes_high_mid_v1（favorite 侧低估收割 · Taipei shadow / 其他城市 tiny-live）

Status: `user_authorized_tiny_live_with_taipei_shadow`
Strategy ID: `d1_yes_high_mid_live_v1`（active instance；历史 shadow instance 保留）· family `market_structure_edge.favorite_low_estimation`
Created: 2026-07-15
Source research: [market calibration curve v1](2026-07-15-market-calibration-curve-v1.md)
Runner: [scripts/ops/d1_yes_high_mid_shadow_v1.py](../../../scripts/ops/d1_yes_high_mid_shadow_v1.py)
Runtime dir: `runtime/weather_edge_v1/d1_yes_high_mid_live_v1/`

## 当前执行政策（2026-07-16 用户明确授权）

- Taipei：继续 zero-notional shadow。
- 其他城市：首个合格 poll 固定 5 shares BUY YES tiny-live；每天最多 10 笔 / $50 cost。
- 资金动作前必须重新读取该 YES token 的实时 CLOB：fresh mid 仍需 ≥0.80、top ask 深度 ≥5；按 fresh top ask 提交 taker limit。
- `X+` open-upper、current 缺失、current=d1、ladder 中间档缺失、YES/NO 同 outcome 报价不互补等语义/执行异常只记 shadow，不下单。这些是 exact-bracket 身份和可成交性边界，不是城市/天气 alpha filter。
- 研究 promotion gate 尚未满足；本次 tiny-live 是用户在知悉证据仍属 inconclusive 后的显式资金决策，不改写研究 verdict。

## 一句话

全分母市场校准曲线上唯一可能穿过 taker 摩擦的格子：当市场把「最终最高温正好比当前 running-max 档高一档」（d1 YES）定价到 mid ≥ 0.80 时，市场系统性地仍偏保守（真实兑现率高于定价），买 d1 YES taker 持有到结算，赚 favorite-longshot bias 中兄弟档彩票买家压出的 3–6c 折价。

## 冻结入场规则（v1，promotion 证据轨）

1. 触发：`d1_yes_mid >= 0.80`，其中先按结算 half-up rounding 锚定 current bracket，再取 ladder 中紧邻的上一档 bounded exact bracket；缺 current / 缺中间档时 fail closed。
2. 入场：taker，成交价 `d1_yes_ask = 1 - d1_no_bid`；fee = `0.05*p*(1-p)`。
3. 去重：每个 `(city, target_date)` 只取**首个**满足条件的 poll（promotion 分母）；同 city-date 后续 poll 记为 telemetry 轨，不并入 promotion ROI。
4. 回测本身没有 obs-age filter；live 只将 `obs_age > 120min` 视为 feed stall 并 fail closed，同时记录 `obs_age_in_backtest_band <= 61min` 供 forward 分层。
5. **无城市 / 时段 / 天气附加 filter**（刻意保持单条件；市场敢定价到 0.80 本身已完成物理筛选）。
6. 持有到结算，无止盈止损。

## 预注册次级假设（v1.1，与 v1 并行 shadow，forward 裁决，不回改 v1）

来自 v1 触发行的事后诊断（见 source research 补节），只作并行记录：
- `d1_yes_ask <= 0.95`：edge 集中在 0.85–0.95（+3~6%），>0.95 转负（-0.9%，残差 < 摩擦）。
- 旧草案写过 `remaining_heat_native <= 1.3`，现已撤回：该字段实际是
  `final_max_native - running_native`，使用结算后的最终高温，属于 label leakage，不能作为 live 特征。
  合法替代量是 PIT `forecast_gap_to_running_native`、forecast peak clock 和模型尾部分布；当前历史同分母上
  这些字段对 overshoot 的区分度很弱，继续只做 telemetry，不加 v1.1 hard gate。

## 证据（回测，同分母，fee-adjusted）

- 全部触发 296 rows / 48 dates：ROI +2.18% CI[+0.04%,+4.47%]。
- 诚实分母（first row / city-date）218 rows：ROI +2.24% CI[-0.64%,+5.19%]（**去重 CI 跨 0**）。
- forward ≥ 2026-06-21 15 dates：ROI +6.62% CI[+3.47%,+9.89%]（train 仅 +0.73%）。
- 广度 33 城，24/27 个 n≥3 城为正；亏损集中 plateau/overshoot 城（Taipei/Madrid/Lucknow/Wellington/Amsterdam）。

## Verdict / gate

Contract: significance=MARGINAL(all-rows CI>0, dedup CI 跨 0); baseline=同价 taker 全表为负、本格子唯一正; forward=15 dates CI>0 但短; conclusion=`inconclusive_positive_signal_shadow_only`。

**Promotion gate（升 tiny-live 前，全部满足才讨论）**：
- ≥10 个 fresh-forward 独立结算日（本 runner 持久保存起始 2026-07-15）；
- 绝对 ROI CI 与同价 baseline excess CI 均 > 0；
- NO bid 深度证据：5-share 可成交无穿档（runner 已记 `d1_no_bid_size` / `d1_no_depth_bid_5c`）；
- 幽灵报价审计：触发时 book 报价在下单时刻仍可成交（per-poll telemetry 轨对比）。

原研究规则是满足后才讨论固定 5 shares tiny-live；截至 2026-07-16 gate 仍未自然通过。用户已显式授权上述拆分 tiny-live，故执行状态升级，但研究结论仍为 inconclusive，不能把用户授权写成统计 promotion pass。

## 采集依赖（重要）

策略需要**全 ladder（36+ 城 × 全档）盘口**。2026-07-15 已落地：独立的 `snapshot-full` 采集 loop
（`scripts/ops/start_full_ladder_orderbook_capture.sh` → `_full_ladder_capture_loop_body.sh`，专用 tmux
`-L weather-full-ladder` socket、输出到 JRS `full_ladder_output/`、完成后间隔 20min、单轮 orderbook budget
900s、经本地 market proxy），**不触碰 live data-feed**（快源/hko 头依赖的高频观测不受影响）。runner 只读取
已有同名 `paper_snapshots/snapshot_*.json` 完成标记的 full-ladder 文件，避免消费采集中逐行追加的半成品；完整
覆盖必须在完整文件内达到 `book_cities_for_target_dates>=36` 才标 `full_ladder`，不足时标
`full_ladder_partial`；`cities_scanned_with_book` / `cities_with_usable_d1_quote` 另报实时观测交集和可用 d1 quote，
不把已近结算而无非 binary book 的城市误记成采集失败。full-ladder 完成文件超过 40min 时才回退 targeted。
关键坑（已解）：Polymarket gamma/CLOB 必须走本地代理 `127.0.0.1:7890`，
直连在并发下被限流（curl 28 超时、只抓到 ~4 城）；tmux 写 JRS 需专用 socket（默认 server 缺外置卷写权限）。

## 血缘接入

- L2 market_structure_edge；消费 L0 observations（running max）+ L0 orderbook snapshots；不新建并行事实表。
- canonical 策略血缘：`strategy_def` key `d1_yes_high_mid`；active instance `d1_yes_high_mid_live_v1`，historical instance `d1_yes_high_mid_shadow_v1` 保留为 superseded-for-now。
- Taipei / execution-blocked 行只写 shadow journal；真实订单写 `runtime/weather_edge_v1/d1_yes_high_mid_live_v1/live_orders.jsonl`，后续由 canonical rebuild 接入 order→fill→settlement。

## 已知限制

- 2026-07-16 已修复旧 runner 先按 raw running 算 tail distance 的错误：例如 93.92°F 会结算 round 到 94，`94-95` 必须是 current 而不是 d1。旧 shadow 污染窗口和逐条影响另做重放记录；无真实资金影响。
- forecast remaining-heat v1.1 已撤回；runner 中旧名 `v11_remaining_heat_ok` 仅为未启用的 telemetry 占位，不得接入
  `remaining_heat_native`；后续若实现必须改成明确的 PIT forecast ceiling/tail 字段。
- 发现路径含 ~50 格校准扫描的事后选择，需 fresh-forward 洗清。

## 2026-07-19 Wuhan overshoot 事前特征复盘

Wuhan 在 13:39 local 入场：running max 30°C，买 31°C YES；最终最高 33°C。入场可见的常规
forecast/TAF 没有报出 33°C：固定 ECMWF ceiling 31.22°C，九模型最高同为 31.22°C，TAF TX30；
因此这次不是已有 forecast threshold 本可挡住而 runner 漏用了，而是预报尾部整体低估。

真正可见的软风险只有 peak clock：入场距预报 15:00 peak-window 结束仍约 81 分钟，属于 active
heating window，不是 post-peak fade。历史同分母 218 个 first signals（11 次 overshoot）上，PIT
特征没有形成可上线分离：`forecast_gap_to_running_native` AUC 0.514，peak delta AUC 0.465，
decision hour AUC 0.506；peak-ahead overshoot 6/93（6.45%）对 peak-passed 3/68（4.41%），差异不足以
支持 hard filter。市场在约 14:27 把 31 YES 快速压低、同时抬高 32 YES，早于 15:00 的 32°C
official print，属于更强的**入场后** warning candidate；需用历史 book replay 做同分母 exit shadow，不能据单例改 live。

## 2026-07-19 observation fallback running-max 事故

影响窗口：`2026-07-18T19:33:45Z`–`2026-07-19T10:17:30Z`。主 observation source
偶发失败时，`aviationweather_cache_csv` 往往只有最新一条 METAR；旧 cache builder 却用这一个点重新计算
当日 running max，导致已经打印过的最高温向下倒退。历史 jsonl 回放发现 179 个污染 cache rows、57 个
city-days、40 城；这是 source history continuity bug，不是天气特征或市场 alpha。

d1 promotion 受影响 5 个 city-days，其中 Taipei 仍为零资金 shadow；其余 4 个产生真实订单：

| city | 错误记录 current→d1 | 正确 current→d1 | 正确 d1 mid | 实际成交 |
|---|---:|---:|---:|---:|
| Singapore | 31→32 | 32→33 | 0.0070 | 5 @ 0.999 |
| Beijing | 32→33 | 33→34 | 0.0065 | 10 @ canonical avg 0.995 |
| Busan | 28→29 | 29→30 | 0.0015 | 5 @ 0.999 |
| Chongqing | 34→35 | 35→36 | 0.0040 | 5 @ 0.999 |
| Taipei | 34→35 | 35→36 | — | zero-notional shadow |

正确口径下四个 live city-days 的 d1 mid 均远低于 0.80，反事实为**全部不下单**；污染造成 25 filled
shares / `$24.935` canonical fill cost。Singapore、Busan、Chongqing maker 均 0 fill 后取消；Beijing maker 5 shares
成交。已提交生产修复 `f81a3efb`：同 station + local-date 的 running max 在 source failover/截断历史间保持
单调，保留此前最高点和时间戳，并记录 `history_continuity_status=merged_previous_running_max`；定向测试 5 passed。
修复后首轮生产 cache `2026-07-19T10:33:32Z` 已实际触发 4 次 continuity merge，证明生效。

这 25 shares 是事故持仓，不得计入 d1 策略 promotion PnL；结算/退出后 canonical rebuild 必须按该污染窗口
和四个 opportunity IDs 分层。未获单独资金指令前不自动平仓。

2026-07-20 完整加固生产 commits `cc109b02`、`e915a312`：fetch error 复用行现在显式标
`reused_after_fetch_error` 并重算 age；该状态仍可作为下一轮 continuity merge 的可信历史；d1 live runner
额外持久化 station-day running-max 不变量，任何倒退均 fail closed + journal；prod health 直接检查实际
observation cache 和近 30 分钟 history；runner 同时要求 cache generated age 不超过 10 分钟，避免单循环
collector 卡住时冻结旧 age。production runner 在 0 trigger / 0 plan 窗口重启，首轮 cache age `1.628m`、
40 城 invariant、0 violation、0 新单；production/develop 定向回归分别 `66/66`、`70/70`。

结果核对必须分两层：Polymarket closed event 显示事故实际买入的 current brackets——Singapore `32`、
Beijing `33`、Busan `29`、Chongqing `35`——均 YES；canonical fills 为 25 shares、cost `$24.935`、fees
`$0.00096`，待 settlement bridge 落库后对应 payout profit `$0.06404`。但修正后的真正 d1
`33/34/30/36` 均 NO；由于入场时这四档 mid 仅 `0.0070/0.0065/0.0015/0.0040`，正确策略决策仍是四笔
全部不下单，而不是换档后继续买入。这次偶然盈利不得计入 d1 promotion alpha。

## 2026-07-16 current→d1 语义修复影响账

污染窗口 `2026-07-15T07:37:29Z`–`2026-07-16T00:19:11Z`。旧 shadow promotion 共 10 个 city-day，修复后 6 个受影响：

- Chongqing、Lucknow、Paris、London、BuenosAires：当时 ladder 缺 current bracket，旧逻辑仍凭 raw distance 造出 d1；正确口径为无有效 d1，不应入分母/下单。
- Chicago：93.92°F round 为 94，旧逻辑把 `94-95` 同时当 current 和 d1；正确 d1 是 `96-97`。
- Beijing、Helsinki、Madrid、Jeddah 的 current→d1 身份不变；但 Jeddah d1=`38+` 按当前资金政策仍只 shadow。

影响金额 `$0`：该窗口 runner 为 zero-notional shadow。逐条重放证据见 [generated audit](generated/d1_yes_high_mid_shadow_semantics_audit_v1.json)；后续研究使用旧 journal 时须剔除上述 6 行或按 corrected fields 重建。
