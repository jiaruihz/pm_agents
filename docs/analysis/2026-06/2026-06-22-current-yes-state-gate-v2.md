# Current-YES state gate v2 telemetry research

Target metric: 用 split current-YES `peak_forming_micro` forward telemetry 验证状态层从“刚摸高”改成“post-observation hold / hazard downtrend”后，是否出现比旧 peak 信号更可靠的方向。

## Data Snapshot

- Window: target_date `2026-06-18`..`2026-06-22`; settled labels currently available through `2026-06-21`.
- Evidence layer: `forward_telemetry.jsonl` signal telemetry + `settlement_outcomes`; this is not `live_real` fill PnL.
- Row grain: one deduped signal epoch = `city + target_date + bracket + token_id + running_max_obs_utc`.
- Price modes: `snapshot` = snapshot ask; `fresh` = fresh ask/limit when present; `live_like` = only current runner `planned` rows.
- Telemetry synced after fixing split runtime sync; peak latest summary generated_at `2026-06-22T15:41:45+00:00`, live_enabled `False`.
- run_stack.sh rebuilt fact tables before this report; DB and CLOB coverage gate were usable.
- CLOB coverage gate: `gate_pass=True`.

## 5-line Self-check

```json
{
  "fact_signal_candidates": {
    "max_ts": "2026-06-22T15:00:41Z",
    "min_ts": "2026-05-05T15:27:41Z",
    "rows": 35445
  },
  "fact_trades": {
    "max_ts": "2026-06-11T09:59:21+00:00",
    "min_ts": "2026-05-05T15:27:41Z",
    "rows": 4400
  },
  "live_real": {
    "cost_usd": 2261.969673,
    "pnl_usd": -80.440799,
    "rows": 855,
    "settled_rows": 830
  },
  "settlement": [
    {
      "rows": 150,
      "settlement_status": ""
    },
    {
      "rows": 4250,
      "settlement_status": "settled"
    }
  ],
  "trade_class": [
    {
      "rows": 2285,
      "trade_class": "paper"
    },
    {
      "rows": 855,
      "trade_class": "live_real"
    },
    {
      "rows": 636,
      "trade_class": "snapshot_replay"
    },
    {
      "rows": 624,
      "trade_class": "live_simulated"
    }
  ]
}
```

## Funnel

- Raw peak telemetry rows in window after old peak profile pass: 2438.
- Deduped signal epochs: 151.
- Settled deduped signal epochs: 124.
- Unsettled/pending signal epochs: 27.
- Decision status counts after dedupe: `{'planned': 23, 'strategy_signal_cap': 31, 'fresh_ask_exceeds_cushion': 90, 'fresh_edge_below_required': 7}`.

## Main Variants

| variant | price | kept | settled | dates | W-L | win | avg price | ROI | PnL per $1 | date bootstrap 95% ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `old_peak_snapshot_price` | snapshot | 151 | 124 | 4 | 98-26 | +79.0% | +75.9% | +4.2% | $+5.21 | [-12.3%, +33.6%] |
| `old_peak_fresh_proxy` | fresh | 151 | 124 | 4 | 98-26 | +79.0% | +81.7% | -5.1% | $-6.34 | [-20.7%, +23.5%] |
| `old_peak_live_planned` | live_like | 23 | 18 | 4 | 11-7 | +61.1% | +69.6% | -14.0% | $-2.52 | [-30.1%, +33.8%] |
| `last_max_gap_v0` | fresh | 0 | 0 | 0 | 0-0 | NA | NA | NA | $+0.00 | NA |
| `first_touch_plateau_v2` | fresh | 31 | 31 | 3 | 25-6 | +80.6% | +81.7% | -2.9% | $-0.91 | [-29.9%, +20.6%] |
| `first_touch_after_forecast_peak` | fresh | 6 | 6 | 3 | 3-3 | +50.0% | +73.6% | -33.4% | $-2.00 | [-100.0%, +52.7%] |
| `first_touch_dtmp3_le_2f` | fresh | 10 | 10 | 2 | 8-2 | +80.0% | +80.7% | -1.1% | $-0.11 | [-23.4%, +8.4%] |
| `first_touch_after_peak_dtmp3_le_2f` | fresh | 2 | 2 | 2 | 1-1 | +50.0% | +75.5% | -36.7% | $-0.73 | [-100.0%, +26.6%] |
| `first_touch_no_reheat_le_0_9f` | fresh | 0 | 0 | 0 | 0-0 | NA | NA | NA | $+0.00 | NA |
| `dtmp3_le_2f_only` | fresh | 53 | 40 | 3 | 31-9 | +77.5% | +81.8% | -8.1% | $-3.25 | [-27.2%, +28.0%] |
| `forecast_peak_passed_only` | fresh | 40 | 36 | 3 | 22-14 | +61.1% | +77.6% | -23.9% | $-8.62 | [-31.9%, +52.7%] |

## Grid Search

Exploratory only.  The window has too few settled dates for promotion; this is used to choose what to keep tracking next.

| variant | kept | settled | dates | W-L | win | ROI | CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `grid_first_touch_forecast_delta_le_2` | 18 | 18 | 3 | 13-5 | +72.2% | -10.8% | [-29.9%, +20.6%] |
| `grid_first_touch_forecast_delta_le_1` | 11 | 11 | 3 | 8-3 | +72.7% | -8.1% | [-33.1%, +29.8%] |
| `grid_first_touch_forecast_delta_le_0.5` | 9 | 9 | 3 | 6-3 | +66.7% | -19.3% | [-70.9%, +29.8%] |
| `grid_first_touch_dtmp3_le_4` | 15 | 15 | 2 | 11-4 | +73.3% | -11.9% | [-29.9%, -2.9%] |
| `grid_first_touch_dtmp3_le_2` | 10 | 10 | 2 | 8-2 | +80.0% | -1.1% | [-23.4%, +8.4%] |
| `grid_first_touch_dtmp3_le_3` | 10 | 10 | 2 | 8-2 | +80.0% | -1.1% | [-23.4%, +8.4%] |

## Finding

- `old_peak_live_planned` is the closest live-action proxy: after adding 2026-06-21 settlement it is 18 settled signals, 11-7, ROI -14.0%, and the date bootstrap CI still crosses 0.  That is a clear no-live result.
- `last_max_gap_v0` passes zero rows because live telemetry stores the last observation equal to the running max, not the first touch.  This confirms the old cadence field is structurally wrong for plateau detection.
- `first_touch_plateau_v2` is the right state semantics to log, but it is not a tradable hard gate yet: 31 settled signals, ROI -2.9%, CI crosses 0.
- Adding `first_touch` as a hard gate sample-starves the slice and does not rescue expectancy.  The previous best-looking hazard/downtrend feature `d_tmpf_3h <= 2F` also failed after 2026-06-21 settled: 40 settled signals, 31-9, ROI -8.1%, CI crosses 0.
- `forecast_peak_passed_only` and `first_touch_after_forecast_peak` are too blunt here; they cut sample and still do not create a reliable live-grade edge.

## Recommendation

Do not replace the old `minutes_since_running_max >= 10` live gate with a new peak-forming hard gate yet.  Keep `peak_forming_micro` real live disabled and leave fade live unchanged.

```text
peak_state_v2_next_step =
  telemetry/research only
  + log first_touch_plateau fields
  + log hazard/downtrend features such as d_tmpf_3h
  + collect more settled forward dates before any shadow trading rule
```

Shadow verdict: telemetry-only, not a new executable shadow rule.  Live verdict: no live change.

Contract verdict:

```text
significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive
```

## Examples

### d_tmpf_3h <= 2F kept settled examples

| created | date | city | bracket | status | win | price | PnL/$1 | peak_delta | d_tmpf_3h |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| 2026-06-19T10:55:21+00:00 | 2026-06-19 | TelAviv | 29 | planned | True | 0.740 | $+0.35 | 1.5 | 1.8 |
| 2026-06-19T10:55:21+00:00 | 2026-06-19 | Istanbul | 24 | planned | True | 0.740 | $+0.35 | 0.5 | 1.8 |
| 2026-06-19T11:57:58+00:00 | 2026-06-19 | TelAviv | 29 | strategy_signal_cap | True | 0.880 | $+0.14 | 2.5 | 1.8 |
| 2026-06-20T03:33:32+00:00 | 2026-06-20 | Wellington | 17 | fresh_ask_exceeds_cushion | True | 0.890 | $+0.12 | -2.4499999999999993 | 0.0 |
| 2026-06-20T03:44:10+00:00 | 2026-06-20 | Wellington | 17 | fresh_ask_exceeds_cushion | True | 0.880 | $+0.14 | -2.2666666666666675 | 0.0 |
| 2026-06-20T04:13:43+00:00 | 2026-06-20 | Wellington | 17 | fresh_ask_exceeds_cushion | True | 0.930 | $+0.08 | -1.783333333333335 | 0.0 |
| 2026-06-20T04:41:18+00:00 | 2026-06-20 | Wellington | 17 | strategy_signal_cap | True | 0.905 | $+0.10 | -1.3166666666666664 | 0.0 |
| 2026-06-20T06:00:46+00:00 | 2026-06-20 | Taipei | 35 | fresh_ask_exceeds_cushion | True | 0.660 | $+0.52 | 1.0 | 1.8 |
| 2026-06-20T06:14:30+00:00 | 2026-06-20 | Busan | 27 | strategy_signal_cap | False | 0.720 | $-1.00 | -0.7666666666666675 | 1.8 |
| 2026-06-20T06:14:30+00:00 | 2026-06-20 | Taipei | 35 | fresh_ask_exceeds_cushion | True | 0.860 | $+0.16 | 1.2333333333333325 | 0.0 |
| 2026-06-20T07:03:16+00:00 | 2026-06-20 | Tokyo | 24 | fresh_ask_exceeds_cushion | True | 0.970 | $+0.03 | 3.0500000000000007 | 1.8 |
| 2026-06-20T07:13:53+00:00 | 2026-06-20 | Busan | 28 | fresh_ask_exceeds_cushion | True | 0.960 | $+0.04 | 0.216666666666665 | 1.8 |
| 2026-06-20T08:45:21+00:00 | 2026-06-20 | Karachi | 34 | fresh_ask_exceeds_cushion | False | 0.880 | $-1.00 | 1.75 | 1.8 |
| 2026-06-20T11:11:21+00:00 | 2026-06-20 | Jeddah | 37 | planned | True | 0.720 | $+0.39 | 1.1833333333333336 | 1.8 |
| 2026-06-20T12:11:18+00:00 | 2026-06-20 | Jeddah | 37 | strategy_signal_cap | True | 0.725 | $+0.38 | 2.1833333333333336 | 0.0 |
| 2026-06-20T14:05:03+00:00 | 2026-06-20 | Helsinki | 24 | strategy_signal_cap | True | 0.835 | $+0.20 | 2.083333333333332 | 0.0 |
| 2026-06-20T14:10:22+00:00 | 2026-06-20 | Amsterdam | 25 | fresh_ask_exceeds_cushion | True | 0.835 | $+0.20 | 3.166666666666668 | 0.0 |
| 2026-06-20T15:32:53+00:00 | 2026-06-20 | Helsinki | 24 | strategy_signal_cap | True | 0.915 | $+0.09 | 1.533333333333335 | 0.0 |
| 2026-06-20T17:45:44+00:00 | 2026-06-20 | Paris | 35 | strategy_signal_cap | True | 0.766 | $+0.30 | 3.75 | 0.0 |
| 2026-06-20T18:12:22+00:00 | 2026-06-20 | SaoPaulo | 24 | strategy_signal_cap | True | 0.865 | $+0.16 | 2.1999999999999993 | 0.0 |
| 2026-06-20T21:01:56+00:00 | 2026-06-20 | Chicago | 76-77 | fresh_ask_exceeds_cushion | True | 0.850 | $+0.18 | 0.01666666666666572 | 1.9799999999999962 |
| 2026-06-21T02:11:06+00:00 | 2026-06-21 | Wuhan | 28 | fresh_edge_below_required | False | 0.760 | $-1.00 | -0.8166666666666664 | 1.8 |
| 2026-06-21T05:14:11+00:00 | 2026-06-21 | Shanghai | 27 | fresh_ask_exceeds_cushion | True | 0.968 | $+0.03 | 0.2333333333333325 | 1.8 |
| 2026-06-21T06:12:19+00:00 | 2026-06-21 | Chongqing | 27 | strategy_signal_cap | True | 0.790 | $+0.27 | -1.8000000000000007 | 1.8 |
| 2026-06-21T06:12:20+00:00 | 2026-06-21 | Manila | 36 | fresh_ask_exceeds_cushion | True | 0.940 | $+0.06 | -0.8000000000000007 | 1.8 |
| 2026-06-21T06:45:25+00:00 | 2026-06-21 | KualaLumpur | 32 | fresh_ask_exceeds_cushion | False | 0.992 | $-1.00 | -2.25 | 0.0 |
| 2026-06-21T07:17:42+00:00 | 2026-06-21 | Singapore | 31 | fresh_ask_exceeds_cushion | False | 0.780 | $-1.00 | 1.2833333333333332 | 0.0 |
| 2026-06-21T09:37:17+00:00 | 2026-06-21 | Lucknow | 39 | planned | False | 0.002 | $-1.00 | 1.1166666666666671 | 1.8 |
| 2026-06-21T12:01:47+00:00 | 2026-06-21 | Helsinki | 26 | fresh_ask_exceeds_cushion | False | 0.740 | $-1.00 | -1.9833333333333325 | 1.8 |
| 2026-06-21T12:34:14+00:00 | 2026-06-21 | Helsinki | 26 | strategy_signal_cap | False | 0.710 | $-1.00 | -1.4333333333333336 | 1.8 |

### d_tmpf_3h > 2F or missing rejected losing examples

| created | date | city | bracket | status | win | price | reasons | d_tmpf_3h |
|---|---|---|---|---|---:|---:|---|---:|
| 2026-06-20T05:19:23+00:00 | 2026-06-20 | Busan | 27 | fresh_ask_exceeds_cushion | False | 0.780 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-20T06:03:57+00:00 | 2026-06-20 | KualaLumpur | 31 | planned | False | 0.630 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T07:00:02+00:00 | 2026-06-20 | Wuhan | 31 | planned | False | 0.190 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T07:45:41+00:00 | 2026-06-20 | Karachi | 34 | fresh_ask_exceeds_cushion | False | 0.630 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T08:30:34+00:00 | 2026-06-20 | Lucknow | 40 | planned | False | 0.730 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T08:45:21+00:00 | 2026-06-20 | Lucknow | 40 | strategy_signal_cap | False | 0.750 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T17:02:05+00:00 | 2026-06-20 | BuenosAires | 15 | fresh_ask_exceeds_cushion | False | 0.650 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-20T17:29:44+00:00 | 2026-06-20 | BuenosAires | 15 | strategy_signal_cap | False | 0.640 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-21T05:14:11+00:00 | 2026-06-21 | Taipei | 37 | planned | False | 0.720 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-21T06:35:45+00:00 | 2026-06-21 | KualaLumpur | 32 | fresh_ask_exceeds_cushion | False | 0.829 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-21T07:12:19+00:00 | 2026-06-21 | Karachi | 34 | planned | False | 0.610 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-21T08:18:47+00:00 | 2026-06-21 | Karachi | 34 | strategy_signal_cap | False | 0.600 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-21T14:01:17+00:00 | 2026-06-21 | Ankara | 25 | fresh_ask_exceeds_cushion | False | 0.920 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-21T14:45:51+00:00 | 2026-06-21 | Paris | 36 | planned | False | 0.720 | d_tmpf_3h_gt_2_or_missing | 3.6 |
| 2026-06-21T17:09:04+00:00 | 2026-06-21 | Miami | 92-93 | fresh_ask_exceeds_cushion | False | 0.910 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-21T17:19:09+00:00 | 2026-06-21 | SaoPaulo | 22 | fresh_ask_exceeds_cushion | False | 0.860 | d_tmpf_3h_gt_2_or_missing | None |
| 2026-06-21T18:08:27+00:00 | 2026-06-21 | Miami | 92-93 | fresh_ask_exceeds_cushion | False | 0.830 | d_tmpf_3h_gt_2_or_missing | 3.9599999999999986 |
