# Low-Price YES Executable Policy Replay v1

Status: snapshot
Date: 2026-07-09
Strategy family: `forecast_tail_low_price_yes` / HeadA
Superseded by: [2026-07-09-low-price-yes-cushion-long-window-v1.md](2026-07-09-low-price-yes-cushion-long-window-v1.md)

## 结论

`max_taker_cushion=0.01` 在 7/1-7/7 的真实 runner journal 上过紧；把它放到 `0.05` 在 fresh-book 可重放分母里没有拆坏现有链路，并且会吃到 Tel Aviv 7/06 与 Shanghai 7/07 这类 1c cushion 卡掉的 winner。2026-07-09 已把 runner/default launcher/Mac stack 默认值统一改为 `0.05`。

2026-07-09 后续长窗复查发现 `0.05` 有后验选择风险，当前 live 默认已降到 `0.03`；本报告只保留 7/1-7/7 runner-journal 同链路证据，不再作为当前配置依据。

这不是新的 alpha 证明，只是执行配置 A/B：分母只有 runner 实际 fetch 到 fresh book 的状态行；`dist<=0` 分支由于 live runner 在 book fetch 前就 block，不能用同一链路反事实成交，仍只能 shadow 采集。

## 数据与链路

- window: `2026-07-01`..`2026-07-07` target_date，settled fact rows only
- runner journal: `runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/shadow_decisions.jsonl`
- settlement/fill DB: `runtime/weather.db`
- replay semantics: chronological journal scan; once a city-date-bracket-condition is selected under a cushion, later same-key rows are simulated duplicates
- executable price: `fresh_best_ask` from runner-fetched book, not stale opportunity ask
- fee: Weather official taker fee `0.05 * price * (1-price)` per share
- entry condition replayed: `fresh_best_ask <= min(0.20, snapshot_ask + cushion)` and `model_p_yes - fresh_ask - fee >= 0.15`

## Max Taker Cushion Sweep

| cushion | selected | dates | cities | wins | win rate | avg ask | per-share ROI | per-share pnl | score-tier ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 21 | 6 | 16 | 2 | 9.5% | 0.091 | 0.3% | 0.005 | 5.1% |
| 0.02 | 23 | 6 | 17 | 2 | 8.7% | 0.098 | -14.7% | -0.344 | -14.3% |
| 0.03 | 24 | 6 | 17 | 3 | 12.5% | 0.102 | 17.6% | 0.449 | 17.2% |
| 0.05 | 25 | 6 | 17 | 4 | 16.0% | 0.106 | 44.9% | 1.240 | 50.2% |
| 0.08 | 26 | 6 | 18 | 4 | 15.4% | 0.105 | 39.7% | 1.137 | 44.9% |

解读：`0.05` 是这组 replay 里的干净候选；`0.08` 开始额外吃进 loser，点估回落。样本太小，不能说 5c 是最优参数，只能说 1c 在当前 live 微结构下明显过紧，5c 更符合“snapshot ask 之后 book 小幅重定价仍允许成交”的执行意图。

## 0.05 相对 0.01 的新增行

| target_date | city | bracket | original blocker | snapshot ask | fresh ask | final | pnl/share |
|---|---|---|---|---:|---:|---:|---:|
| 2026-07-03 | Atlanta | 98-99 | fresh_ask_exceeds_cushion_or_band | 0.085 | 0.100 | 0 | -0.105 |
| 2026-07-04 | Shanghai | 34 | fresh_ask_exceeds_cushion_or_band | 0.135 | 0.160 | 0 | -0.167 |
| 2026-07-06 | TelAviv | 33 | fresh_ask_exceeds_cushion_or_band | 0.080 | 0.108 | 1 | 0.887 |
| 2026-07-07 | Shanghai | 34 | fresh_ask_exceeds_cushion_or_band | 0.120 | 0.170 | 1 | 0.823 |

## Dist Blocker Boundary

`dist<=0` 不能和 max_taker 一样做 executable replay：当前 runner 在 dist blocker 处直接 return，历史 journal 没有 token/book/fresh ask。下面只保留 snapshot-price 诊断，不能作为恢复 live 的证据。

| blocker | rows | dates | cities | wins | win rate | avg ask | snapshot ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| dist_eq0_forecast_boundary_tail_v1 | 1 | 1 | 1 | 0 | 0.0% | 0.105 | -100.0% |
| dist_lt0_cold_or_inside_forecast_tail_v1 | 16 | 3 | 13 | 4 | 25.0% | 0.104 | 130.6% |

动作：dist 不是马上放开；下一步应该改 runner telemetry，让 dist-blocked 行也在 shadow-only 路径 fetch/log fresh book，但仍不下单。这样以后才能把 dist 的机会层表现和可执行表现放到同一个 replay 链路里。

## Live Fill 对照

- low-price YES fact_trades rows in window: `23` grouped ticket/policy rows
- live fill 只作对账，不反推 selector；selector replay 以上面的 runner journal 为准。

## Blocker Funnel Snapshot

| blocker | journal count |
|---|---:|
| sim_duplicate_after_selected | 4296 |
| dist_lt0_cold_or_inside_forecast_tail_v1 | 2089 |
| decision_snapshot_too_stale | 760 |
| no_settled_fact_match | 624 |
| fresh_book_fetch_failed | 237 |
| dist_eq0_forecast_boundary_tail_v1 | 233 |
| missing_yes_token_id | 136 |
| fresh_ask_exceeds_replayed_cushion_or_fee_edge | 51 |
| selected | 25 |

## Verdict

```text
max_taker_cushion_0p05: shadow/live-config candidate
  significance=NA (execution A/B micro-window, not alpha backtest)
  baseline=PASS for same-journal comparison vs 0.01 in 7/1..7/7 fresh-book rows
  forward=NA until post-change fills accumulate
  action=runner/default launcher/Mac stack default switched from 0.01 to 0.05 on 2026-07-09; monitor as execution config, not alpha

dist_le0_restore: not approved
  reason=current runner did not fetch book before blocking; executable replay missing
  action=add shadow fresh-book logging before any live restore
```
