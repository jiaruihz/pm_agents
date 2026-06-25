# Current-YES Peak-YES Timing Shadow Telemetry v1

Status: research-only
Generated: 2026-06-25T15:02:20+00:00

## 一句话结论

Shadow telemetry replay materialized 1466 rows across 711 first-signal events. Holdout first-signal rows have ask ROI +1.8%; post-first tracking rows have average ask drift 0.193 and ask ROI +2.7%. This is the right forward logging shape for maker-first/timing research, not a live approval.

## 数据范围

- scored rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv`
- telemetry rows: 1466 / events 711 / dates 36

This materializes the JSONL shape for forward shadow logging. Replay-only payoff fields must not exist in live forward rows.

## Summary

| period | scope | rows | events | win | ask | maker probe | ask drift | ask ROI | maker ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | all_tracking_rows | 491 | 238 | +83.7% | 0.781 | 0.771 | 0.097 | +7.2% | +8.6% |
| train | first_signal_only | 238 | 238 | +75.2% | 0.712 | 0.702 | 0.000 | +5.6% | +7.2% |
| train | post_first_tracking | 253 | 137 | +91.7% | 0.846 | 0.836 | 0.188 | +8.4% | +9.7% |
| holdout | all_tracking_rows | 856 | 419 | +77.5% | 0.757 | 0.747 | 0.098 | +2.3% | +3.6% |
| holdout | first_signal_only | 419 | 419 | +70.9% | 0.696 | 0.686 | 0.000 | +1.8% | +3.3% |
| holdout | post_first_tracking | 437 | 231 | +83.8% | 0.816 | 0.806 | 0.193 | +2.7% | +3.9% |
| forward | all_tracking_rows | 119 | 54 | +76.5% | 0.751 | 0.741 | 0.121 | +1.8% | +3.2% |
| forward | first_signal_only | 54 | 54 | +70.4% | 0.682 | 0.672 | 0.000 | +3.1% | +4.6% |
| forward | post_first_tracking | 65 | 32 | +81.5% | 0.809 | 0.799 | 0.222 | +0.8% | +2.1% |

## Live 接入要点

- 只新增 shadow telemetry，不下单、不改变 taker/live gates。
- state key 是 `city + target_date + current_bracket`；runner 需要记住当天首次 signal。
- 每个后续 cycle 记录 ask drift、fresh bid/ask、maker probe edge，判断 later confirmation 是否只是更贵。
- replay label/PnL 字段只在研究产物里存在，live forward row 不能写这些后验字段。

## Runner 接入状态

- 本地 runner 已补 telemetry-only enrichment：`scripts/ops/weather_theta_current_yes_tiny_live.py`。
- 新增 state 文件：`runtime/weather_edge_v1/theta_current_yes_tiny_live_v1/peak_timing_shadow_state.json`。
- 新增 forward fields 前缀：`peak_timing_shadow_*`，包括 first-signal ask/edge、current ask/edge、ask drift、1c-inside maker probe edge。
- 该 enrichment 是非阻塞：异常只写 `latest_summary.json` 的 `peak_timing_shadow_status/error`，不改变 plan/order/live gate。
- 当前未部署 N100，未改实盘下单逻辑。

## Outputs

- telemetry_jsonl: `docs/analysis/2026-06/generated/current_yes_peak_yes_timing_shadow_telemetry_v1/peak_yes_timing_shadow_telemetry_v1.jsonl`
- summary: `docs/analysis/2026-06/generated/current_yes_peak_yes_timing_shadow_telemetry_v1/peak_yes_timing_shadow_telemetry_v1_summary.csv`
- schema: `docs/analysis/2026-06/generated/current_yes_peak_yes_timing_shadow_telemetry_v1/peak_yes_timing_shadow_telemetry_v1_schema.json`
- json: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-timing-shadow-telemetry-v1.json`
- markdown: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-timing-shadow-telemetry-v1.md`
