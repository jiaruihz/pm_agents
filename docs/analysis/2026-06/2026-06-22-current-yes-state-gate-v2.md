# Current-YES state gate v2 telemetry research

Target metric: 用 split current-YES `peak_forming_micro` forward telemetry 验证状态层从“刚摸高”改成“post-observation hold / hazard downtrend”后，是否出现比旧 peak 信号更可靠的方向。

## Data Snapshot

- Window: target_date `2026-06-18`..`2026-06-22`; settled labels currently available through `2026-06-20`.
- Evidence layer: `forward_telemetry.jsonl` signal telemetry + `settlement_outcomes`; this is not `live_real` fill PnL.
- Row grain: one deduped signal epoch = `city + target_date + bracket + token_id + running_max_obs_utc`.
- Price modes: `snapshot` = snapshot ask; `fresh` = fresh ask/limit when present; `live_like` = only current runner `planned` rows.
- Telemetry synced after fixing split runtime sync; peak latest summary generated_at `2026-06-21T16:16:54+00:00`, live_enabled `False`.
- `run_stack.sh` rebuilt fact tables, then exited non-zero because frontend port 5174 stayed busy; DB and CLOB coverage gate were still usable.
- CLOB coverage gate: `gate_pass=True`.

## 5-line Self-check

```json
{
  "fact_signal_candidates": {
    "max_ts": "2026-06-21T16:00:50Z",
    "min_ts": "2026-05-05T15:27:41Z",
    "rows": 34830
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

- Raw peak telemetry rows in window after old peak profile pass: 1821.
- Deduped signal epochs: 107.
- Settled deduped signal epochs: 75.
- Unsettled/pending signal epochs: 32.
- Decision status counts after dedupe: `{'planned': 16, 'strategy_signal_cap': 31, 'fresh_ask_exceeds_cushion': 57, 'fresh_edge_below_required': 3}`.

## Main Variants

| variant | price | kept | settled | dates | W-L | win | avg price | ROI | PnL per $1 | date bootstrap 95% ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `old_peak_snapshot_price` | snapshot | 107 | 75 | 3 | 65-10 | +86.7% | +75.4% | +15.5% | $+11.65 | [+13.1%, +34.1%] |
| `old_peak_fresh_proxy` | fresh | 107 | 75 | 3 | 65-10 | +86.7% | +81.1% | +5.8% | $+4.35 | [+3.4%, +35.1%] |
| `old_peak_live_planned` | live_like | 16 | 11 | 3 | 8-3 | +72.7% | +70.1% | +0.1% | $+0.01 | [-12.5%, +35.0%] |
| `last_max_gap_v0` | fresh | 0 | 0 | 0 | 0-0 | NA | NA | NA | $+0.00 | NA |
| `first_touch_plateau_v2` | fresh | 31 | 26 | 2 | 22-4 | +84.6% | +81.7% | +2.2% | $+0.58 | [-0.2%, +20.6%] |
| `first_touch_after_forecast_peak` | fresh | 6 | 4 | 2 | 1-3 | +25.0% | +69.9% | -61.8% | $-2.47 | [-100.0%, +52.7%] |
| `first_touch_dtmp3_le_2f` | fresh | 10 | 7 | 1 | 6-1 | +85.7% | +79.1% | +8.4% | $+0.59 | NA |
| `first_touch_after_peak_dtmp3_le_2f` | fresh | 2 | 1 | 1 | 0-1 | +0.0% | +72.0% | -100.0% | $-1.00 | NA |
| `first_touch_no_reheat_le_0_9f` | fresh | 0 | 0 | 0 | 0-0 | NA | NA | NA | $+0.00 | NA |
| `dtmp3_le_2f_only` | fresh | 37 | 21 | 2 | 19-2 | +90.5% | +83.5% | +9.2% | $+1.92 | [+6.0%, +28.0%] |
| `forecast_peak_passed_only` | fresh | 32 | 16 | 2 | 11-5 | +68.8% | +74.7% | -14.1% | $-2.25 | [-18.5%, +52.7%] |

## Grid Search

Exploratory only.  The window has too few settled dates for promotion; this is used to choose what to shadow next.

| variant | kept | settled | dates | W-L | win | ROI | CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `grid_first_touch_forecast_delta_le_2` | 18 | 13 | 2 | 10-3 | +76.9% | -3.4% | [-10.7%, +20.6%] |
| `grid_first_touch_forecast_delta_le_1` | 11 | 8 | 2 | 5-3 | +62.5% | -17.4% | [-33.1%, +29.8%] |
| `grid_first_touch_dtmp3_le_4` | 15 | 10 | 1 | 8-2 | +80.0% | -2.9% | NA |

## Finding

- `old_peak_live_planned` is the closest live-action proxy: only 11 settled planned signals, 8-3, ROI about flat.  That is not enough to restore peak live.
- `last_max_gap_v0` passes zero rows because live telemetry stores the last observation equal to the running max, not the first touch.  This confirms the old cadence field is structurally wrong for plateau detection.
- `first_touch_plateau_v2` is the right state semantics to log, but on the current settled window it does not improve enough by itself.
- Adding `first_touch` as a hard gate currently sample-starves the best slice.  The stronger current direction is the hazard/downtrend feature `d_tmpf_3h <= 2F` without requiring first-touch as a hard pass.
- `forecast_peak_passed_only` and `first_touch_after_forecast_peak` are too blunt here; they cut sample and still do not create a reliable live-grade edge.

## Recommendation

Keep `peak_forming_micro` real live disabled.  Implement the next signal-layer candidate as shadow-only:

```text
peak_state_v2_shadow_candidate =
  old peak profile price/model gates
  + hazard_downtrend: d_tmpf_3h <= 2F
  + log first_touch_plateau fields for audit/model features
  + existing price/model edge gates
```

Do not require `forecast_peak_delta <= 0` or `first_touch_plateau == true` as hard gates yet; keep both as features / LLM preflight inputs because they are noisy and sample-starving in this slice.

Contract verdict:

```text
significance=FAIL
baseline=PARTIAL
forward=FAIL
conclusion=shadow_candidate
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
