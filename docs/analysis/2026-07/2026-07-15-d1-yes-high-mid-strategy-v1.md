# Strategy: d1_yes_high_mid_v1（favorite 侧低估收割 · zero-notional shadow）

Status: `shadow_candidate_no_live`
Strategy ID: `d1_yes_high_mid_shadow_v1`（instance）· family `market_structure_edge.favorite_low_estimation`
Created: 2026-07-15
Source research: [market calibration curve v1](2026-07-15-market-calibration-curve-v1.md)
Runner: [scripts/ops/d1_yes_high_mid_shadow_v1.py](../../../scripts/ops/d1_yes_high_mid_shadow_v1.py)
Runtime dir: `runtime/weather_edge_v1/d1_yes_high_mid_shadow_v1/`

## 一句话

全分母市场校准曲线上唯一可能穿过 taker 摩擦的格子：当市场把「最终最高温正好比当前 running-max 档高一档」（d1 YES）定价到 mid ≥ 0.80 时，市场系统性地仍偏保守（真实兑现率高于定价），买 d1 YES taker 持有到结算，赚 favorite-longshot bias 中兄弟档彩票买家压出的 3–6c 折价。

## 冻结入场规则（v1，promotion 证据轨）

1. 触发：`d1_yes_mid >= 0.80`，其中 `d1` = running-max 上一档 exact bracket（`tail_distance == 1`，语义与回测 factory `add_state_siblings` 完全一致，runner 直接 import 复用）。
2. 入场：taker，成交价 `d1_yes_ask = 1 - d1_no_bid`；fee = `0.05*p*(1-p)`。
3. 去重：每个 `(city, target_date)` 只取**首个**满足条件的 poll（promotion 分母）；同 city-date 后续 poll 记为 telemetry 轨，不并入 promotion ROI。
4. 新鲜度 parity gate：`obs_age <= 45min`（回测触发行 99% 满足，非新 filter，是防止用比回测更旧的观测交易）。
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

满足后才以固定 5 shares taker 起 tiny-live，走执行确认与 CLOB/live gate；maker improve 版本仅 telemetry，不混 ROI。**当前不推荐任何 live。**

## 采集依赖（重要）

策略需要**全 ladder（36+ 城 × 全档）盘口**。2026-07-15 已落地：独立的 `snapshot-full` 采集 loop
（`scripts/ops/start_full_ladder_orderbook_capture.sh` → `_full_ladder_capture_loop_body.sh`，专用 tmux
`-L weather-full-ladder` socket、输出到 JRS `full_ladder_output/`、20min 一轮、经本地 market proxy），**不触碰
live data-feed**（快源/hko 头依赖的高频观测不受影响）。首轮实测覆盖 ~34 城，runner `coverage_note` 已由
`narrow_targeted_coverage` 翻为 `full_ladder`（scanned≈28）。runner 优先读 full-ladder 目录、stale 时才回退
targeted。关键坑（已解）：Polymarket gamma/CLOB 必须走本地代理 `127.0.0.1:7890`，直连在并发下被限流（curl 28
超时、只抓到 ~4 城）；tmux 写 JRS 需专用 socket（默认 server 缺外置卷写权限）。

## 血缘接入

- L2 market_structure_edge；消费 L0 observations（running max）+ L0 orderbook snapshots；不新建并行事实表。
- canonical 策略血缘：`strategy_def` key `d1_yes_high_mid`，`strategy_instance` / `weather_strategy_shadow_queue` / `weather_strategy_runtime_registry` instance `d1_yes_high_mid_shadow_v1`（execution_mode=zero_notional_shadow）。
- 零 notional：不写 `orders`/`fills`/`fact_trades`；结算标签从 `settlement_outcomes` 回填到 journal。

## 已知限制

- 整数边界 running value 的 current-bracket 归属存在轻微歧义（相邻档在整数点重叠），对零 notional telemetry 影响可忽略；d1 由 `tail_distance==1` 判定，不受影响。
- forecast remaining-heat 尚未 join（v1.1 guard 半开）。
- 发现路径含 ~50 格校准扫描的事后选择，需 fresh-forward 洗清。
