# Low-Price YES Tail Strategy Thesis v1

Generated: 2026-07-02

## Verdict

2026-07-02 审阅与 `p_cal_v1` 复核后，当前结论改为：

```text
keep live:
  low_price_yes v1 tiny live
  BUY_YES edge>=0.20 && ask 0.05..0.20
  $1/order

do not promote:
  source_aware_v3 selector
  p_cal_v1 selector
  city-fixed selector

implemented shadow telemetry:
  source_aware_v3
  p_cal_no_city
  p_cal_city_diag
  hot_tail_pct_asof
  bias_p90_asof
  bias_mean_asof
  bracket_distance features
```

结论等级：`inconclusive` for selector replacement；`shadow_candidate_keep_collecting` 只适用于 broad v1 low-price YES sleeve 本身。

核心变化：v3 的 source-aware 结果不能再解释成 source alpha，因为 source 与 city/station 几乎共线；`p_cal_v1` 说明 station-basis 有信息，但 no-city calibrated EV 还不能稳定改善 v1。2026-07-02 已给 `$1` tiny live 与 zero-notional shadow 补 station-basis / p_cal / bracket-distance telemetry；下一步最合理的是继续收 fresh forward，并把 alpha 假说改成 **station-basis / 时区注意力 / market tail underpricing 的 convex sleeve**，不是单独的 forecast source band。

## Boundary With METAR Rich-Current Reversal

2026-07-03 之后，`rich_current_collapse_d1_yes` 不再放在本低价彩票仓主线里。它属于单独的 Head B：

```text
Head A, this document:
  forecast-tail low-price YES
  early entry
  ask 0.05..0.20
  maker-first entry + TP20 exit overlay

Head B, separate strategy family:
  METAR rich-current collapse / runway d1 YES reversal
  intraday only
  current YES still expensive while obs+forecast point one bracket higher
  taker entry + hold to settlement
```

`hotter_tail_reversal_shapes_v1` 里的 Shape A 只说明日内 3c..10c hotter pump 路径无效；它不能推翻本文件的 D-1 / early forecast-tail sleeve。Shape B4 是 Head B 的主候选，应在 `metar_reversal` 窗口继续研究。

## Strategy Map

| line | current status | core idea | result |
|---|---|---|---|
| Raw cheap YES lottery | superseded | 只买低价 YES | 宽口径不稳定，dust ticket 依赖太强 |
| v1 no-dust lottery | live tiny / shadow_candidate | `edge>=0.20 && ask 0.05..0.20`，每 city-date 最早 PIT row | 历史 457 rows ROI +24.2% fixed-$1 proxy；当前 $1 live 继续跑 |
| v2 METAR/regime tail | inconclusive | 等 intraday METAR/regime 再买 tail YES | 宽口径亏，窄切片正但 holdout 挂；只能记录 telemetry |
| v3 source-aware tail | downgraded / diagnostic tag | GFS/ECMWF 分 source 选不同 ask band | 点估漂亮，但 source 基本是 city proxy，paired excess CI 跨 0；不作 selector |
| p_cal v1 station-basis | inconclusive / telemetry | as-of station bias + model_p_yes + ask 校准 EV | broad no-city train 好但 holdout 亏；v1 no-city 不改善；city-fixed 很强但过拟合风险 |
| high-current-NO reversal | separate shadow idea | 高价 current NO 过度自信时反手 current YES | 机制有意思但样本薄，属于另一个 expression head |
| true METAR path reversal | future work | 观测路径开始推翻市场/预报共识，盘口未及时 repricing | 尚未跑出稳定策略 |

## Evidence So Far

### v1: no-dust low-price lottery

来自 `2026-07-02-low-price-yes-lottery-selector-refinement-v1`。

```text
BUY_YES
edge >= 0.20
ask 0.05..0.20
one candidate per city-date
earliest PIT decision snapshot
```

主要结果：

| window | rows | dates | avg ask | win | ROI | note |
|---|---:|---:|---:|---:|---:|---|
| historical | 457 | 50 | 0.105 | 13.6% | +28.8% under payout25 sizing / +24.2% fixed-$1 proxy | broad enough |
| recent | 226 | 19 | 0.105 | 12.8% | +22.8% under payout25 sizing | still positive |
| closed forward | 19 | 3 | 0.100 | 15.8% | +58.5% under payout25 sizing / +65.9% fixed-$1 proxy | too few dates |

Interpretation:

- `ask < 5c` 的 dust ticket 会抬 headline ROI，但实际成交和手续费更麻烦。
- `ask 5c..20c` 是更实用的 no-dust sleeve。
- 小额固定 notional 可以跑，但不能因为短 forward 漂亮就放大。

### v2: METAR/regime attempt

来自 `2026-07-02-low-price-yes-lottery-metar-regime-v2`。

这个版本测试的是：如果不提前买，而是等 intraday METAR/regime 出现后再买 tail YES，是否更好。

结果：

| selector | rows | dates | ROI | holdout | judgment |
|---|---:|---:|---:|---:|---|
| all low-price intraday tail YES | 171 | 37 | -43.9% | -36.4% | fail |
| loose regime score >= 4 | 108 | 34 | -15.8% | -53.7% | fail |
| score >= 5 | 67 | 30 | +25.7% | -30.6% | too filtered, not stable |
| `day_open_runway + late_morning + light_wind` | 40 | 27 | +26.3% | -20.6% | case label only |

Interpretation:

- 纯 METAR/regime 不是现在的 alpha。
- 手写 time/regime rule 很容易把中奖 case 包装成策略。
- 但这些字段仍然值得记录，因为未来可能解释哪些 live v1/v3 票更脆弱，或者形成真正 observation-led v4。

### v3: source-aware forecast tail

来自 `2026-07-02-low-price-yes-source-aware-tail-v3`。

规则：

```text
base denominator:
  BUY_YES
  edge >= 0.20
  ask 0.05..0.20
  earliest PIT row per city-date

source-aware expression:
  GFS   -> ask 0.05..0.15
  ECMWF -> ask 0.10..0.20

sizing for research:
  $1/order fixed cost
```

主要结果：

| strategy | hist rows | hist dates | win | avg ask | hist ROI | CI | recent | holdout 6/21-6/26 | forward 6/27-6/30 | top removed |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| baseline v1 unified 5c-20c | 457 | 50 | 13.6% | 0.105 | +24.2% | [-9.1%, +60.6%] | +20.1% | +43.9% | +65.9% | +22.3% |
| source-aware v3 | 278 | 50 | 16.9% | 0.115 | +46.1% | [+3.0%, +95.7%] | +43.0% | +55.8% | +186.6% | +45.7% |

Original v3 read:

- It improves ROI without collapsing rows to a 10-row gimmick.
- It improves win rate and top-trade-removed ROI.
- It has a positive historical date-block CI.
- It keeps the forecast-source mechanism explicit.
- Forward is promising but still only 3 target dates, so it is not size-up proof.

2026-07-02 review update:

- Source is mostly fixed by city, so this is not clean source alpha.
- Paired excess versus v1 is not significant by date-block CI.
- Removing top winners/dates makes the apparent edge fragile.
- Treat `source_aware_v3` as a shadow diagnostic tag, not as the promoted selector.

### p_cal v1: station-basis calibration attempt

来自 `2026-07-02-low-price-yes-tail-pcal-v1`。

| selector | train | holdout 6/21-6/26 | forward 6/27-6/30 | judgment |
|---|---:|---:|---:|---|
| broad no-city `p_cal_ev>=0.5` | +52.4% | -25.5% | n/a | fail |
| v1 baseline | +20.4% | +43.9% | +65.9% | keep tiny live |
| v1 no-city `p_cal_ev>=0.5` | +38.5% | +19.4% | -100.0% | not better |
| v1 city-fixed `p_cal_ev>=0.5` | +105.0% | +87.2% | +127.3% | diagnostic only |

Interpretation:

- As-of station-basis features are useful context.
- No-city calibrated EV is not yet a selector.
- City fixed effects look too good, which is exactly the overfit/city-whitelist risk fabel5 warned about.
- The next valid test is fresh forward telemetry, not another in-sample band search.

### robustness v1: concentration and complement check

来自 `2026-07-02-low-price-yes-tail-robustness-v1`。

| selector | train | holdout 6/21-6/26 | forward 6/27-6/30 | robustness read |
|---|---:|---:|---:|---|
| v1 baseline | +20.4% | +43.9% | +65.9% | broadest, still fragile to top winners |
| city-fixed `p_cal_ev>=0.5` | +105.0% | +87.2% | +127.3% | high point estimate, but raw city identity and only 8 forward rows |
| station `bias_p90_high` | +65.3% | +27.9% | +268.8% | train lift real-looking, but holdout loses to complement (+49.9%) |
| no-city `p_cal_ev>=0.5` | +38.5% | +19.4% | -100.0% | not a selector |

Interpretation:

- `station_bias_p90_high` 是最像机制的 tag，但还不是交易规则。
- `city-fixed` 不应该被简单说成“单一城市中奖”，它在 train/holdout 的 leave-one-city 压测不差；真正的问题是它用了 raw city identity，且没有 fresh forward 证明。
- 下一步研究应从“阈值回测”切换到“事前记录 telemetry + fresh forward 验证”：如果 station-basis 真是 alpha，新数据会自己证明，不该继续在 6/21-6/30 上找规则。

## Why This Could Be Alpha

The hypothesized alpha is **market underpricing of station-basis-adjusted tail probability**, not simply weather being weird and not forecast source by itself.

The mechanism:

1. Polymarket temperature brackets often price tail YES as near-lottery outcomes.
2. Forecast models sometimes still assign meaningful probability to those brackets.
3. The market may not fully price city/station/model basis, especially when the model probability says a low-probability bracket is small but real.
4. The payoff is convex: at 5c-15c, a modest hit-rate improvement can dominate many losing tickets.
5. City/station basis may matter: some city/station/model combinations repeatedly realize hotter than forecast in tail states, and the market may not price that fully.

The alpha is therefore in the interaction:

```text
as-of station-basis prior
+ model probability edge
+ market ask band
+ convex payout
+ one-shot city-date selection discipline
```

It is not currently in:

```text
cheap YES by itself
late morning by itself
humid/wind/cloud regime by itself
blindly waiting for METAR confirmation
one city/date hero wins
```

## What Is Not Yet Proven

Important caveats:

- Forward closed sample is tiny: v3 forward is 11 rows across 3 settled target dates.
- The evidence still comes from research replay, not enough fresh live-forward settlement.
- Execution may be worse than backtest if low ask queues do not fill or taker fees dominate.
- Source-aware bands were discovered after studying v1/v2, so they need fresh prospective validation.
- This is a convex sleeve: many -100% trade/day outcomes are normal. It should never be evaluated like a high win-rate carry strategy.

## Current Live / Shadow Policy

Recommended current state:

```text
keep live:
  current low-price YES v1 tiny live
  $1/order
  no size-up

shadow telemetry is implemented in `low_price_yes_lottery_tiny_live.py` and `low_price_yes_lottery_reversal_shadow_v1.py`:
  source_aware_v3=true/false
  p_cal_no_city
  p_cal_city_diag
  hot_tail_pct_asof
  bias_p90_asof
  bias_mean_asof
  bracket_distance_available
  forecast_to_bracket_low_native
  forecast_above_bracket_high_native
  source band reason:
    gfs_05_15
    ecmwf_10_20
  ask
  model_p_yes
  edge
  forecast_source
  forecast_peak_source
  city
  target_date
  bracket
  eventual settlement

do not yet:
  replace live selector
  increase notional
  turn METAR/regime into a hard gate
  use city fixed effects as live selection
```

If fresh forward later confirms v3 or p_cal, the live selector can be changed from:

```text
edge>=0.20 && ask 0.05..0.20
```

to:

```text
edge>=0.20 && ask 0.05..0.20
PLUS a pre-registered p_cal/station-basis or source-aware shadow tag
```

But that should happen only after shadow-tagged forward rows settle; no rule selected using 6/21-6/30 should count as fresh proof.

## Future Research Directions

### 1. Prospective p_cal / station-basis validation

Now that `p_cal_no_city`, `p_cal_city_diag`, station-basis fields, bracket-distance fields, and `source_aware_v3` shadow tags are recorded, measure:

- rows / dates / cities
- fill feasibility
- win rate
- avg ask
- ROI
- top-trade-removed ROI
- daily PnL distribution
- comparison versus v1 rows that fail each shadow tag

This is the most practical next step.

### 2. True METAR path reversal v4

This should be separate from v3.

Target question:

```text
Can observation path reveal that the market has underpriced a tail before books reprice?
```

Candidate features:

- current temp acceleration versus forecast path
- running max closing forecast gap faster than expected
- temp_trend_1h / temp_trend_3h
- minutes_since_running_max
- wind shift / light wind persistence
- dewpoint depression and humidity support for further heating
- cloud suppression clearing
- market ask still stale or low after new observation
- source disagreement: GFS/ECMWF spread or peak-time disagreement

This should be modeled as `P(tail YES hits | ask, forecast, observation path, source, city)` rather than hand-written late-morning rules.

### 3. City/station/model basis calibration

Use historical station-vs-forecast bias as soft sizing or prior:

- city/station/model pairs with stable hot-underforecast should tolerate higher tail exposure.
- cold-overforecast or high MAE pairs should not be hard-banned yet, but should be monitored.
- This layer should explain v3-style tags, not override v1 until it improves fresh forward results.

### 4. Execution realism

The backtests assume the selected ask is tradable. Live validation needs:

- maker versus taker fill rate
- order size at low ask
- fee impact at 5c-15c
- stale book detection
- no sub-5c dependency
- minimum shares constraints

For now `$1/order` is the right scale.

### 5. Portfolio relationship to other reversal heads

Do not mix these into one runner prematurely:

- station-basis/source-aware low-price tail YES = forecast/model-market convex sleeve
- high-current-NO reversal = overconfidence / wrong-way expression detector
- METAR path reversal = observation-led repricing lag

They may share features, but their alpha hypotheses are different.

## Working Prompt

Short operator prompt for future runs:

```text
低价尾部 YES 先不要推 v3 source-aware 或 p_cal selector。
继续 $1 v1 tiny live：
  BUY_YES edge>=0.20 && ask 0.05..0.20。
每笔已补 shadow telemetry：
  source_aware_v3
  p_cal_no_city
  p_cal_city_diag
  hot_tail_pct_asof
  bias_p90_asof
  bias_mean_asof
  bracket_distance。
当前结论：station-basis 可能是对的 alpha 方向，但 no-city p_cal 还没过 holdout/forward；city-fixed 看起来强但不能 live。
下一轮只用 fresh forward settlement 验证，不再拿 6/21-6/30 调 selector。
```

## References

- `docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v2.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-metar-regime-v2.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-source-aware-tail-v3.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-tail-research-review-v1.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-tail-pcal-v1.md`
- `docs/analysis/2026-07/2026-07-02-low-price-yes-tail-robustness-v1.md`
- `docs/analysis/2026-06/2026-06-30-historical-forecast-station-bias-v1.md`
- `docs/analysis/2026-06/2026-06-18-low-price-yes-reheat-reversal-v1.md`
