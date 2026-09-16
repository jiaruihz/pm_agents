# reheat-risk Exhaustion Source-Aware Restart v1

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `runtime/weather.db` self-check + historical reheat-risk v0 artifacts under `docs/analysis/2026-06/generated/m3_exhaustion_no_v0/`.
- DB fact built at: `2026-06-15T14:43:25.893487+00:00`.
- CLOB gate: `gate_pass=True`, `missing_order_rows=0`, `over_order_keys=0`.
- `fact_trades` by class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- `fact_trades` by settlement: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`.
- `fact_signal_candidates`: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`.
- CLOB order/fill join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.
- Note: `run_stack.sh` completed DB/fact/gate rebuild but failed at frontend startup because port 5174 stayed busy; this report uses the rebuilt DB and artifacts, not the frontend.

## Target Metric

`exhaustion_incremental_alpha` = whether a decision-time "will it heat up again" gate improves BUY_NO outcomes relative to the same source bucket's clock-only NO baseline.

Row grain: one selected row is the first qualifying orderbook quote for one `city + target_date + bracket`. This is opportunity research, not live fills.

## Funnel

- Raw tail-NO quote rows: `11800` from `2026-05-20` to `2026-06-09`.
- Quote groups: `{'whitelist': 10275, 'repaired': 1525}`.
- V1 selected rules: `7`.
- Source buckets: `default_wu_control` is the clean theta control; `station_basis_repaired6` is the old repaired six station-basis cities.

## Physical Layer

The physical premise is real. In 13-17h observations, default-WU cities move from P(jump>=1) 40.0% when decline is `<0.5C` to 1.7% when decline is `>=2C`; station-basis repaired cities move from 40.4% to 1.4%. The strategy question is whether that public fact is mispriced.

## Results

| rule | rows | city-days | active dates | ROI | excess vs baseline | excess CI | daily t | train ROI | holdout ROI |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| `default_wu_baseline_h15_17_d1` | 665 | 429 | 21 | -5.6% | +0.0% | [+0.0%, +0.0%] | -4.27 | -4.9% | -6.7% |
| `default_wu_exhaustion_h13_17_decline1_d1` | 277 | 180 | 21 | -4.3% | +1.4% | [-2.3%, +5.1%] | -2.74 | -5.9% | -1.9% |
| `default_wu_exhaustion_h13_17_decline2_d1` | 54 | 37 | 20 | -5.6% | +0.0% | [-10.2%, +8.9%] | -1.20 | -10.1% | -2.9% |
| `station_basis_baseline_h15_17_d1` | 110 | 76 | 21 | +6.5% | +0.0% | [+0.0%, +0.0%] | +1.60 | +13.8% | -1.1% |
| `station_basis_exhaustion_h13_17_decline1_d1` | 71 | 48 | 20 | +7.5% | +0.9% | [-6.8%, +8.9%] | +2.37 | +6.6% | +8.0% |
| `station_basis_exhaustion_h13_17_decline1_d2` | 23 | 16 | 10 | +4.1% | -2.4% | [-10.5%, +5.0%] | +1.05 | +3.7% | +4.2% |
| `station_basis_anti_fresh_h13_17_d1` | 201 | 88 | 21 | -1.0% | -7.5% | [-14.5%, -0.6%] | -0.38 | -3.0% | +2.0% |


## Interpretation

Generic/default-WU theta remains closed. The main v1 clean-theta rule (`default_wu_exhaustion_h13_17_decline1_d1`) produced 277 selected rows, ROI -4.3%, and excess vs its same-group clock baseline +1.4% with CI [-2.3%, +5.1%]. That is not a tradable edge.

Station-basis exhaustion is different. The main basis rule (`station_basis_exhaustion_h13_17_decline1_d1`) produced 71 selected rows, ROI +7.5%, 18/20 positive active dates, and daily t +2.37. The alpha source is still station/source basis; exhaustion is a risk gate that tells us when the basis can be expressed with less "still heating" tail risk.

## Verdict

significance=FAIL for generic theta / PARTIAL for station-basis mechanism
baseline=FAIL for generic theta / PASS only because station-basis is the baseline source of alpha
forward=NA here; use station-basis shadow/live-prep gate for forward evidence
conclusion=inconclusive for live action

Action: restart the research as `basis x exhaustion`, not as standalone NO low-insurance/theta. The next useful work is to improve the "will it heat up again" model as a sizing/filter layer inside station-basis shadow, while keeping default-WU generic NO theta off.
