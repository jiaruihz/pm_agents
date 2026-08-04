# LMVM forecast innovation 历史回测 v2

> generated_at_utc: `2026-08-04T15:22:21.145182+00:00`  
> research-only；没有 production instance、plan、order 或 fill 写入。

## 固定问题

每次 D-2/D-1 forecast first-seen 改版，在完整 exact-bracket ladder 中只买一档 YES：
`argmax[(ΔPmodel)−(ΔPmarket)]`。同 snapshot ask 入场，固定 horizon 的首个可用 bid 退出，
entry/exit 都扣官方 Weather taker fee。静态 residual、forecast mode、market favorite 是同 rows 对照。

- snapshot files: 6,357
- forecast update events: 12,413
- complete paired ladders: 12,237
- blocked unpaired ladders: 176
- chronological cutoff: `2026-07-04`（前 2/3 development，后 1/3 late holdout；不是事前盲测）

## 旧 forecast probability 本身

| metric | model | same-row market | model-market | target-date 95% CI |
|---|---:|---:|---:|---:|
| Brier | 0.085398 | 0.068673 | +0.016725 | [+0.015093, +0.018369] |
| logloss | 2.701873 | 1.414771 | +1.287102 | [+1.091192, +1.504523] |

## `ΔPmodel−ΔPmarket` 可执行 markout

| period | lead | horizon | covered/signals | turnover ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|
| all_history | D-1 | 30m | 8831/11936 | -15.26% | [-15.99%, -14.67%] |
| all_history | D-1 | 60m | 9882/11936 | -14.94% | [-15.55%, -14.42%] |
| all_history | D-1 | 120m | 8681/11936 | -14.62% | [-15.23%, -14.11%] |
| all_history | D-2 | 30m | 240/301 | -18.34% | [-21.17%, -15.85%] |
| all_history | D-2 | 60m | 284/301 | -16.39% | [-18.79%, -14.22%] |
| all_history | D-2 | 120m | 271/301 | -14.94% | [-17.77%, -12.58%] |
| development | D-1 | 30m | 6343/8743 | -15.75% | [-16.68%, -15.01%] |
| development | D-1 | 60m | 7281/8743 | -15.36% | [-16.13%, -14.71%] |
| development | D-1 | 120m | 6485/8743 | -14.94% | [-15.72%, -14.30%] |
| development | D-2 | 30m | 240/301 | -18.34% | [-21.17%, -15.85%] |
| development | D-2 | 60m | 284/301 | -16.39% | [-18.79%, -14.22%] |
| development | D-2 | 120m | 271/301 | -14.94% | [-17.77%, -12.58%] |
| late_holdout | D-1 | 30m | 2488/3193 | -13.95% | [-14.43%, -13.53%] |
| late_holdout | D-1 | 60m | 2601/3193 | -13.74% | [-14.22%, -13.30%] |
| late_holdout | D-1 | 120m | 2196/3193 | -13.67% | [-14.23%, -13.15%] |

## late holdout 相对同 rows 对照

| lead | horizon | control | paired events | ROI delta | target-date 95% CI |
|---|---:|---|---:|---:|---:|
| D-1 | 30m | residual_argmax | 2488 | +1.97% | [+1.31%, +2.57%] |
| D-1 | 30m | forecast_mode | 2488 | -1.48% | [-1.91%, -1.07%] |
| D-1 | 30m | market_favorite | 2488 | -4.24% | [-4.73%, -3.81%] |
| D-1 | 60m | residual_argmax | 2601 | +1.47% | [+0.76%, +2.23%] |
| D-1 | 60m | forecast_mode | 2601 | -1.35% | [-1.82%, -0.86%] |
| D-1 | 60m | market_favorite | 2601 | -3.95% | [-4.54%, -3.35%] |
| D-1 | 120m | residual_argmax | 2196 | +1.55% | [+0.71%, +2.53%] |
| D-1 | 120m | forecast_mode | 2196 | -1.13% | [-1.78%, -0.38%] |
| D-1 | 120m | market_favorite | 2196 | -3.60% | [-4.42%, -2.73%] |

## spread / maker 条件诊断

D-1 late holdout 的 median spread 约 `0.02`。60m 时只有 `7.84%` 的 future bid
高于 entry ask；即使先忽略 fee，ask→future bid 的 quote ROI 仍为 `-7.72%`，
双边 taker fee 后为 `-13.74%`。所以失败不只是 fee，主要是没有足够的方向性上涨来跨 spread。

若把入场价反事实改成当时 best bid，并**假设每一笔都能 maker 成交**：

- 全部 D-1 late holdout 的 60m bid→future bid、完全不计 fee，仅 `+0.35%`；
- 只付 exit taker fee 后即转负；
- development 分位冻结后的最高 innovation decile（score `>=0.228825`）在 late holdout
  有 252 个 covered rows，60m 无 fee bid→bid 为 `+2.14%`，只付 exit taker fee为 `-1.34%`；
- decile 并非严格单调，且 archive 没有我们的 queue position / maker fill，不能把这组
  conditional quote return 当作可实现 ROI。

因此剩下的可研究点不是“预测后 taker 买入”，而是很窄的 maker/microstructure 假设：
高 innovation 时是否能在两边都拿到 maker fill。它需要 forward queue/fill ledger，历史快照无法证明。

D-2 的 301 个 paired update 全部落在 development 时段，late holdout 没有 D-2 证据；
不能把 D-1 的结论外推成已验证的 D-2 策略。

## 边界

- 该 archive 足够回测 forecast innovation 与 taker ask→future bid repricing。
- 它不含我们自己的真实 maker queue position；`bid touched` 不能当 maker fill，因此 maker 成交率仍需独立 forward paper/quote ledger。
- `model_prob` 是 capture-time telemetry，能做 PIT replay；但旧概率体系的某些历史研究是 market-anchored bucket model，不能与这里的 raw full-ladder telemetry 混称同一个模型。
