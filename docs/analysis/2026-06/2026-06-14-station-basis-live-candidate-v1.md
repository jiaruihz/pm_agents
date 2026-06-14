# Station-Basis Live Candidate v1

> generated_at_utc: `2026-06-13T18:40:13.294030+00:00`
> scope: local research + zero-notional shadow only; no N100/live config changed.

## 数据快照

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- fact_trades MAX(fact_built_at_utc): `2026-06-13T18:37:50.673546+00:00`
- CLOB coverage gate: `True`; fail_reasons: ``
- CLOB coverage gate is passing; live_real reporting is no longer blocked by fill reconciliation, but this station-basis live decision is still blocked by v1 forward evidence.

### 5 行 SQL 自检

```json
{
  "fact_trades_max_built_at_utc": "2026-06-13T18:37:50.673546+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "fact_trades_by_settlement_status": [
    {
      "settlement_status": null,
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 28488,
    "eligible": 9695,
    "paper_ordered": 3686,
    "live_filled": 348
  },
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## 候选方向

`station_basis_taker_live5_h16_yes_no_d1_v1`：5 个 station-basis 城市 (Chicago/KualaLumpur/London/PanamaCity/Paris)，排除 Milan/Jakarta；YES 只在当地 16h 买官方 running bucket，且每 city-day 只记一次；NO d1 在 13-17h 衰竭后记录为 live core；live-core shadow/eval 使用 `ask<=0.90`；NO d2 只记录为 observe-only。

## Historical YES Ablation

| variant | rows | city_days | days | ROI | pos_days | total_days | daily_t |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0_yes_all_h14_16_repaired6 | 204 | 81 | 21 | +30.9% | 14 | 21 | +2.73 |
| v1_yes_all_h14_16_live5 | 151 | 62 | 21 | +63.1% | 16 | 21 | +4.21 |
| v1_yes_h16_live5 | 44 | 44 | 21 | +120.7% | 19 | 21 | +6.58 |
| v1_yes_one_cityday_last_live5 | 62 | 62 | 21 | +113.8% | 18 | 21 | +6.50 |
| negative_control_yes_milan | 53 | 19 | 19 | -72.1% | 3 | 19 | -4.19 |

## Historical NO Ablation

| variant | rows | city_days | days | ROI | pos_days | total_days | daily_t |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0_no_d1_repaired6 | 49 | 48 | 20 | +9.8% | 18 | 20 | +2.12 |
| v1_no_d1_live5 | 42 | 41 | 20 | +12.8% | 19 | 20 | +2.56 |
| v1_no_d2_live5 | 13 | 13 | 9 | +4.3% | 8 | 9 | +0.62 |
| negative_control_no_d1_milan | 7 | 7 | 7 | -9.0% | 5 | 7 | -0.53 |

## Train / Holdout Robustness

`train` is target_date < 2026-06-01; `holdout` is 2026-06-01 onward.

### YES

| variant | train_rows | train_ROI | holdout_rows | holdout_ROI | holdout_pos_days | holdout_days |
| --- | --- | --- | --- | --- | --- | --- |
| v0_yes_all_h14_16_repaired6 | 117 | +42.6% | 87 | +17.7% | 6 | 9 |
| v1_yes_all_h14_16_live5 | 82 | +88.2% | 69 | +39.2% | 6 | 9 |
| v1_yes_h16_live5 | 23 | +186.7% | 21 | +74.3% | 7 | 9 |
| v1_yes_one_cityday_last_live5 | 33 | +170.5% | 29 | +70.6% | 7 | 9 |
| negative_control_yes_milan | 35 | -64.8% | 18 | -86.3% | 1 | 7 |

### NO

| variant | train_rows | train_ROI | holdout_rows | holdout_ROI | holdout_pos_days | holdout_days |
| --- | --- | --- | --- | --- | --- | --- |
| v0_no_d1_repaired6 | 22 | +7.4% | 27 | +11.9% | 8 | 9 |
| v1_no_d1_live5 | 20 | +5.6% | 22 | +19.8% | 9 | 9 |
| v1_no_d2_live5 | 4 | +3.9% | 9 | +4.6% | 5 | 6 |
| negative_control_no_d1_milan | 2 | +27.4% | 5 | -23.5% | 3 | 5 |

### Prefix Walk-Forward YES

- orders: `32`; test_days: `14`; ROI: `+94.9%`; daily_t: `+4.97`

## Price-Band Stress

### YES h16 live5

| price_band | rows | days | ROI | win_rate | holdout_rows | holdout_ROI | holdout_win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <=0.40 | 24 | 17 | +1475.4% | +87.5% | 9 | +683.9% | +77.8% |
| 0.40-0.60 | 2 | 2 | +108.1% | +100.0% | 2 | +108.1% | +100.0% |
| 0.60-0.75 | 3 | 3 | +42.9% | +100.0% | 2 | +37.9% | +100.0% |
| 0.75-0.80 | 1 | 1 | +26.6% | +100.0% | 0 | NA | NA |
| 0.80-0.90 | 3 | 3 | +14.5% | +100.0% | 2 | +14.9% | +100.0% |
| 0.90-0.97 | 3 | 3 | +4.9% | +100.0% | 2 | +4.7% | +100.0% |
| >0.97 | 8 | 7 | +1.1% | +100.0% | 4 | +1.3% | +100.0% |

### NO d1 live5

| price_band | rows | days | ROI | win_rate | holdout_rows | holdout_ROI | holdout_win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.40-0.60 | 6 | 4 | +28.2% | +66.7% | 5 | +53.3% | +80.0% |
| 0.60-0.75 | 7 | 7 | +42.9% | +100.0% | 3 | +47.1% | +100.0% |
| 0.75-0.80 | 1 | 1 | -100.0% | +0.0% | 0 | NA | NA |
| 0.80-0.90 | 10 | 10 | +15.5% | +100.0% | 4 | +17.8% | +100.0% |
| 0.90-0.97 | 18 | 13 | +5.0% | +100.0% | 10 | +5.4% | +100.0% |

- latest v1 entry ask `0.825` falls in price band `0.80-0.90`.
- NO d1 `0.80-0.90` remains positive in historical and holdout slices; `0.90-0.97` is only marginal and should stay separately monitored before any tiny-live gate.

## Forward Shadow v0 Diagnosis

- entries: `16`; settled: `8`; pending: `8`
- by_rule: `{"no_d1_exh": {"rows": 1, "city_days": 1, "days": 1, "cities": ["PanamaCity"], "cost": 1.4244, "pnl": 0.1056, "roi": 0.07413647851727043, "daily_t": null, "positive_days": 1, "total_days": 1}, "yes_bucket": {"rows": 7, "city_days": 5, "days": 2, "cities": ["KualaLumpur", "London", "Milan", "PanamaCity", "Paris"], "cost": 35.39, "pnl": -15.39, "roi": -0.4348686069511161, "daily_t": -0.8650927487352448, "positive_days": 1, "total_days": 2}}`

v0 的 broad `yes_bucket` 已出现 early divergence：7 笔 settled ROI -43.5%，而历史 ablation 显示 Milan 是负贡献，且 16h-only / one-city-day-last 是更强表达。

## Forward Shadow v1 Current

- cycles: `95`; latest_cycle_ts_utc: `2026-06-13T18:38:24.266400+00:00`
- entries: `1`; settled: `0`; pending: `1`
- latest_entry_ts_utc: `2026-06-13T18:04:28.868339+00:00`
- latest_entry: `{"ts_utc": "2026-06-13T18:04:28.868339+00:00", "city": "PanamaCity", "target_date": "2026-06-13", "hour_local": 13, "rule": "no_d1_exh", "side": "BUY_NO", "bracket": "34°C", "ask": 0.825, "ask_size": 50.0, "shares": 10.0, "notional": 8.25, "token_id": "105521426332755775111490419398148470576645707464818087890066797733519652754417", "market_id": "2512836", "event_slug": "highest-temperature-in-panama-city-on-june-13-2026", "official_icao": "MPMG", "unit": "C", "running_max_c": 33.0, "current_temp_c": 32.0, "decline_c": 1.0, "running_value": 33, "settlement_status": "pending"}`

## Liveability Readiness Probe v1

- generated_at_utc: `2026-06-13T18:38:26.884769+00:00`
- status_counts: `{"ok_no_candidates": 1, "outside_window": 3, "ok_candidates": 1}`
- output: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/station_basis_shadow_v1/readiness.json`

## Pending Monitor v1

- generated_at_utc: `2026-06-13T18:38:29.941930+00:00`
- pending: `1`; status_counts: `{"ok": 1}`; thesis_counts: `{"alive_current_running_value": 1}`
- latest_pending: `{"entry": {"ts_utc": "2026-06-13T18:04:28.868339+00:00", "city": "PanamaCity", "target_date": "2026-06-13", "hour_local": 13, "rule": "no_d1_exh", "side": "BUY_NO", "bracket": "34°C", "ask": 0.825, "ask_size": 50.0, "shares": 10.0, "notional": 8.25, "token_id": "105521426332755775111490419398148470576645707464818087890066797733519652754417", "market_id": "2512836", "event_slug": "highest-temperature-in-panama-city-on-june-13-2026", "official_icao": "MPMG", "unit": "C", "running_max_c": 33.0, "current_temp_c": 32.0, "decline_c": 1.0, "running_value": 33, "settlement_status": "pending"}, "ts_utc": "2026-06-13T18:38:29.941930+00:00", "local_time": "2026-06-13T13:38:29.941930-05:00", "status": "ok", "dry_run_order": {"exec_day": "2026-06-13", "ts_utc": "2026-06-13T18:04:31.757100+00:00", "mode": "dry_run", "city": "PanamaCity", "target_date": "2026-06-13", "rule": "no_d1_exh", "side": "BUY_NO", "bracket": "34°C", "ask": 0.825, "requested_shares": 10.0, "shares": 6.0606, "notional": 5.0, "allow": true, "guard_reason": "ok_capped", "token_id": "105521426332755775111490419398148470576645707464818087890066797733519652754417", "event_slug": "highest-temperature-in-panama-city-on-june-13-2026", "shadow_ts_utc": "2026-06-13T18:04:28.868339+00:00", "placement": "dry_run_logged"}, "metar": {"status": "ok", "n_obs": 14, "age_min": 38.5, "running_max_c": 33.0, "current_temp_c": 32.0, "decline_c": 1.0, "last_obs_utc": "2026-06-13T18:00:00+00:00"}, "running_value": 33, "current_bracket_hit": false, "thesis_status": "alive_current_running_value", "book_status": "ok", "best_bid": {"price": 0.94, "size": 0.73}, "best_ask": {"price": 0.978, "size": 26.0}, "mid_price": 0.959, "mtm": {"shadow_bid_pnl": 1.15, "shadow_mid_pnl": 1.34, "dry_run_bid_pnl": 0.697, "dry_run_mid_pnl": 0.8121, "entry_price": 0.825, "shadow_shares": 10.0, "dry_run_shares": 6.0606}}`
- output: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/station_basis_shadow_v1/pending_monitor.json`

## Pending Monitor History v1

- rows: `7`; first: `2026-06-13T18:23:57.677004+00:00`; latest: `2026-06-13T18:38:29.941930+00:00`
- thesis_counts_total: `{"alive_current_running_value": 7}`
- output: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/station_basis_shadow_v1/pending_monitor_history.jsonl`

## Dry-Run Execution Chain v1

- exec path: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/station_basis_exec_v1`
- mode: `dry_run`; cursor_processed: `1`
- orders: `1`; allowed_orders: `1`
- kill_switch_path: `runtime/weather_edge_v1/station_basis_shadow_v1/PAUSE`
- latest_order: `{"exec_day": "2026-06-13", "ts_utc": "2026-06-13T18:04:31.757100+00:00", "mode": "dry_run", "city": "PanamaCity", "target_date": "2026-06-13", "rule": "no_d1_exh", "side": "BUY_NO", "bracket": "34°C", "ask": 0.825, "requested_shares": 10.0, "shares": 6.0606, "notional": 5.0, "allow": true, "guard_reason": "ok_capped", "token_id": "105521426332755775111490419398148470576645707464818087890066797733519652754417", "event_slug": "highest-temperature-in-panama-city-on-june-13-2026", "shadow_ts_utc": "2026-06-13T18:04:28.868339+00:00", "placement": "dry_run_logged"}`
- v1 shadow cycle now side-loads `weather_station_basis_exec_v1.py run`, so new v1 entries are risk-audited without a separate persistent process.

## Eval v1 Price-Eligible

- verdict: `NOT_READY_ACCUMULATE_SHADOW`
- entries/settled/pending: `1` / `0` / `1`
- live_core_results: `{"yes_bucket": {"summary": {"settled": 0, "all_settled": 0, "price_excluded_settled": 0, "max_live_core_ask": 0.9}, "failures": ["settled<40", "roi<+15%", "positive_day_rate<55%"], "pass": false}, "no_d1_exh": {"summary": {"settled": 0, "all_settled": 0, "price_excluded_settled": 0, "max_live_core_ask": 0.9}, "failures": ["settled<40", "roi<+3%", "positive_day_rate<55%"], "pass": false}}`

## Live-Prep Gate

- artifact: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/station_basis_shadow_v1/live_prep_gate.json`
- verdict: `NOT_READY_ACCUMULATE_SHADOW`; live_now: `False`
- blockers: `4`; passed checks: `10`
- blocker_codes: `forward_eval_not_ready, yes_bucket_forward_rule_fail, no_d1_exh_forward_rule_fail, forward_settlements_missing`

- `yes_bucket` live core: settled >=40, ROI >= +15%, positive_day_rate >=55%, continuity ready.
- `no_d1_exh` live core: settled >=40, ROI >= +3%, positive_day_rate >=55%, continuity ready.
- `no_d2_exh`: observe-only; not a live enabler/blocker until stronger forward evidence exists.
- price overlay: v1 live-core shadow/eval now requires `ask<=0.90`; latest `no_d1_exh` ask band `0.80-0.90` is acceptable for shadow; `0.90-0.97` remains marginal observe-only evidence before tiny-live sizing.
- Current v1 forward: 1 entries / 1 pending / 0 settled, so live is still blocked by forward evidence, not by historical edge.

## Verdict

- live_now: `false`
- direction: `station-basis taker` 是当前最接近 live 的方向。
- next_gate: 运行 v1 zero-notional shadow，直到每条规则 settled >=40 且符号与历史一致。
- 只有 v1 forward 过门后，才进入真实 CLOB 接线和 N100 部署；部署必须另走 `weather-strategy-deploy`。
- collection: v1 is side-loaded by the existing `weather_station_basis_shadow.py` loop on each v0 cycle/settle, and also has standalone start scripts for real-terminal use.
- sidecars: every v1 cycle refreshes readiness, dry-run execution, and pending monitor; every v1 settle refreshes pending monitor.
- execution: v1 dry-run executor consumes only `station_basis_shadow_v1/entries.jsonl` and writes only `station_basis_exec_v1/`, keeping v0/v1 cursors separate.

## Files

- JSON: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-14-station-basis-live-candidate-v1.json`
- Markdown: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-14-station-basis-live-candidate-v1.md`
- v1 shadow: `scripts/ops/weather_station_basis_shadow_v1.py`
- v1 eval: `scripts/ops/station_basis_eval_v1.py`
- v1 exec: `scripts/ops/weather_station_basis_exec_v1.py`
- v1 readiness: `scripts/ops/station_basis_v1_readiness.py`
- v1 pending monitor: `scripts/ops/station_basis_v1_pending_monitor.py`
- v1 live-prep gate: `scripts/ops/station_basis_live_prep_gate_v1.py`
