# Live Strategy Idea Radar v0

> generated_at_utc: `2026-06-15T16:16:30Z`
> target_metric: `retail_live_strategy_candidate_radar_v0`
> Scope: direction-selection report only; no N100/live config changed; no orders placed.

## 数据快照

- Data source: latest local mirror after `scripts/ops/sync_weather_remote.sh` on 2026-06-16 Beijing time, rebuilt through `scripts/weather_dashboard/run_stack.sh`.
- `run_stack.sh` rebuilt DB/facts/gate, then failed only at frontend port `5174` still busy.
- DB: `runtime/weather.db`.
- MAX fact build: `2026-06-15T16:15:20.000141+00:00`.
- CLOB coverage gate: `gate_pass=true`, `missing_order_rows=0`, `over_order_keys=0`, `db_fill_cost_minus_fact_cost=0.0`.
- This report does not publish `live_real` PnL/ROI/rank/curve; CLOB gate is recorded only as data-integrity context.

### 强制 5 行 SQL 自检

```text
MAX(fact_built_at_utc) FROM fact_trades -> 2026-06-15T16:15:20.000141+00:00
trade_class distribution -> live_real 855, live_simulated 624, paper 2285, snapshot_replay 636
settlement_status distribution -> blank 150, settled 4250
fact_signal_candidates coverage -> rows 30140, eligible 10366, paper_ordered 3968, live_filled 348
CLOB orders with fills -> error 33 / with_fill 0, submitted 961 / with_fill 855
```

## 为什么低价 BUY_YES 没有 live 版本

低价 `BUY_YES` 不是因为“从没赚钱”失败，而是因为它不满足可复制 live 策略的最小证据结构。

最新 v1 搜索里，`2880` 个可解释规则中有 `973` 个 train 正超额；最强规则是 `<0.10 price + edge>=0.20`，整体超额 ROI `+1131.9%`，CI `[+467.4%, +2105.5%]`。但这些赢家在 holdout 没有足够触发，forward screen 通过数是 `0`。这意味着它更像一段历史上的凸性暴露，不像明天能按规则继续开的策略。

一句话：低价 YES 是表达层彩票，不是 alpha 源。它可以做 tag 或 shadow 观察，不能当 live 主线。

## 策略方向雷达

| direction | alpha source | current evidence | live blocker | action |
|---|---|---|---|---|
| low-price BUY_YES lottery | cheap convexity / rare winners | train 有大赢家；v1 forward screen `0` | holdout 无触发，无法证明可复制 | stop broad threshold search; only keep as convexity tag |
| broad side-band / adjacent / Range RV | expression relative value | 多轮点估计曾经好 | holdout/top-date/orderbook gates 反复失败 | do not reopen broad search |
| all-YES underround | model-free no-arb underround | offline confirmed; current scanner can find candidates | retail execution requires all-leg-or-none, low latency, partial-fill unwind; edge thin | keep as engineering sandbox/paper-shadow, not first live |
| forecast-quality BUY_NO | forecast reliability tag | train/holdout mildly positive in some slices | CI/top5 stress thin; quality tag over-filtering | shadow only; use reliability as sizing/tag layer |
| forecast-bounded Range RV | market distribution vs compact forecast interval | source-aware proxy rows confirmed; orderbook-native width-3 cheaper closest | strict live-standard fails active-date/top5/depth support | next research/shadow branch if seeking scalable strategy |
| station-basis | market watches wrong station/source | mechanism evidence strongest; live-prep plumbing exists | forward shadow settled too low; shadow continuity stale; candidate windows sparse | closest tiny-live path after gate repair and accumulation |

## 下一步优先级

### 1. Station-basis maker pilot path

This is the best live-oriented branch because the edge source is not “our forecast is better”; it is “market participants are using the wrong station/feed.” That is a more durable mechanism.

Current local gate after the 2026-06-16 sync:

```text
verdict = NOT_READY_ACCUMULATE_SHADOW
live_now = false
blockers = 6
```

Important blockers:

- `forward_eval_not_ready`: forward shadow has not passed thresholds.
- `shadow_continuity_not_ready`: latest shadow cycle age was about `1381` minutes.
- `yes_bucket_forward_rule_fail`: settled `<40`, ROI `<+15%`, positive day rate `<55%`.
- `no_d1_exh_forward_rule_fail`: settled `<40`; current settled sample is `1` PanamaCity row, ROI `+21.2%`.
- `pending_monitor_stale`: pending monitor age about `1380.6` minutes.
- `recent_candidates_price_or_liquidity_blocked`: recent qualified candidates were blocked by price or missing asks.

This is not a “strategy dead” failure. It is a telemetry/forward-sample failure. The right move is:

1. restore/ensure the v1 shadow loop is continuously running on N100;
2. keep live disabled;
3. accumulate settled forward entries;
4. make maker price rule explicit (`best_bid+1tick` vs current placeholder);
5. only after live-prep gate flips to deploy review, consider `$1/order`, `$10/day` pilot through `weather-strategy-deploy`.

### 2. Forecast-bounded Range RV shadow path

This is the best scalable research branch. The closest rows are width-3 compact forecast interval expressions:

- Proxy/source-aware rows can pass three gates.
- Time-aligned orderbook rows are much closer than older broad Range RV.
- Strict live-standard still fails on active dates, top5 stress, and some depth requirements.

The next version should not be another broad scanner. It should be a fixed shadow rule:

```text
forecast_bounded_w3_cheaper_default_wu_shadow_v0
grain = city + event_date basket
legs = 2-4 preferred
source = default_wu only for generic claim
pricing = time-aligned orderbook ask
action = zero-notional shadow / paper only
```

Promotion requires settled forward baskets, not just historical replay.

### 3. All-YES underround sandbox

All-YES underround is real but awkward for retail live. It needs all legs filled, partial-fill cancellation/unwind, and fast execution for a thin gross edge. Keep it useful as:

- market-structure evidence;
- orderbook/FOK engineering sandbox;
- paper-shadow forward ledger.

Do not make it the first tiny-live strategy unless the fresh paper loop accumulates settled live-equivalent baskets and the executor has all-leg-or-none safeguards.

## 发散后的结论

如果目标是“尽快找到一个真能开小钱 live 的版本”，优先级不是 low-price YES，也不是继续大网格搜索。

Current ranking:

```text
1. station-basis maker pilot path      -> closest to tiny-live, but gate still NOT_READY
2. forecast-bounded Range RV shadow    -> best scalable research/shadow path
3. all-YES underround paper sandbox    -> real offline edge, retail-live blocked
4. forecast-quality BUY_NO             -> shadow/tag only
5. low-price BUY_YES                   -> stop threshold search; tag only
```

当前动作：继续研究，但下一步应切到 `station-basis gate repair + forward shadow accumulation` 或 `forecast_bounded_w3_cheaper_default_wu_shadow_v0`，不要再围着低价 YES 调参。

## Evidence Pointers

- `docs/analysis/2026-06/2026-06-15-low-price-buy-yes-live-candidate-v1.md`
- `docs/analysis/2026-06/2026-06-15-retail-live-strategy-direction-v0.md`
- `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-live-standard-v1.md`
- `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-source-aware-v0.md`
- `docs/analysis/2026-06/2026-06-15-station-basis-strategy-master-design.md`
- `docs/analysis/2026-06/2026-06-14-station-basis-live-candidate-v1.md`
- `runtime/weather_edge_v1/station_basis_shadow_v1/live_prep_gate.json`
