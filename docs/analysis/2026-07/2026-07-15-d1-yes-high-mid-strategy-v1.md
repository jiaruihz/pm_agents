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
- `remaining_heat_native <= 1.3`：12 次亏损 11 次是 overshoot 跳过 d1，亏损日预报剩余热量中位 1.67 vs 盈利日 1.11。（需 forecast_enrichment join，runner 当前记 `v11_remaining_heat_ok=null` 待接入。）

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
- forecast remaining-heat 尚未 join（v1.1 guard 半开）。
- 发现路径含 ~50 格校准扫描的事后选择，需 fresh-forward 洗清。

## 2026-07-16 current→d1 语义修复影响账

污染窗口 `2026-07-15T07:37:29Z`–`2026-07-16T00:19:11Z`。旧 shadow promotion 共 10 个 city-day，修复后 6 个受影响：

- Chongqing、Lucknow、Paris、London、BuenosAires：当时 ladder 缺 current bracket，旧逻辑仍凭 raw distance 造出 d1；正确口径为无有效 d1，不应入分母/下单。
- Chicago：93.92°F round 为 94，旧逻辑把 `94-95` 同时当 current 和 d1；正确 d1 是 `96-97`。
- Beijing、Helsinki、Madrid、Jeddah 的 current→d1 身份不变；但 Jeddah d1=`38+` 按当前资金政策仍只 shadow。

影响金额 `$0`：该窗口 runner 为 zero-notional shadow。逐条重放证据见 [generated audit](generated/d1_yes_high_mid_shadow_semantics_audit_v1.json)；后续研究使用旧 journal 时须剔除上述 6 行或按 corrected fields 重建。
