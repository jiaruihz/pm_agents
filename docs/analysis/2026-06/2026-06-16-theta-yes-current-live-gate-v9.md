# Theta Current YES Live Gate v9

Status: snapshot
Generated: 2026-06-15T17:39:32.421757+00:00
Target metric: `current_yes_live_gate` = v8 best fixed current YES rule 能否从 shadow_candidate 升级到 live。

## 数据完整性自检

- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。
- source feature rows: 3239; active dates: 27; holdout dates: 14.
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

这条 current YES fixed rule 已经不是随便的漂亮点估：holdout 自身 ROI 和相对 d1 NO 增量都过 CI，最近半段仍为正，leave-one-date / leave-one-city 也没有翻负。

它现在可以升级成 tiny-live candidate，但不是放量策略。核心原因是容量只支持很小的 taker 试单：用 $2/order、$10/day、top-of-book 可用 notional >= $2 的执行门，历史仍有 31 行/11 天且 CI 过门；$10 或 $20 级别就样本不足。

交易动作：可以进入 tiny-live 准备，但实际改 N100 live policy 必须另走 deploy 流程；部署前不要再调参，只冻结这条规则。

## 候选规则

- model: `weather_plus_price`
- rule: `yesAsk>=0.55|decline>=0.5|h13-15|pWin>=0.5|ev>=0.05`
- holdout rows/dates/cities: 32 / 11 / 15
- YES ROI: +17.7%, CI95 [+4.6%, +28.1%]
- paired d1 NO ROI: +13.5%, YES-NO CI95 [+1.7%, +6.8%]

## Tiny-Live 冻结规格

- universe: source-aligned weather cities；只买当前 running-max 所在 bracket 的 YES。
- dedupe: 每个 city / target_date / current_bracket 最多一笔。
- timing: local 13-15 点，且 `decline_c >= 0.5`。
- price/model: `yes_ask >= 0.55`，`p_yes_win >= 0.5`，`p_yes_win - yes_ask >= 0.05`。
- sibling feature guard: live 生成时需要 current YES quote 和 d1 NO sibling quote 都可见；否则跳过。
- execution cap: BUY_YES taker，max $2/order，max $10/target_date，top-of-book available notional < $2 时跳过。
- deployment rule: 只能以 frozen rule 上 tiny-live；不得把 prefix dynamic selector 一起上线。

## 稳健性

- latest half ROI: +16.4%; last 5 active dates ROI: +14.4%.
- max date cost share: 16.7%; top3 date cost share: 40.0%.
- max city cost share: 12.3%; top3 city cost share: 32.9%.
- leave-one-date min ROI: +14.9%; leave-one-city min ROI: +15.2%.
- threshold neighborhood: 51 sample-ok variants, 48 point-pass variants, 6 CI-pass variants.
- tiny liquidity gate: available notional >= $2, rows/dates 31 / 11, YES ROI +17.9%.

## 日期 PnL

| date | rows | cities | YES ROI | cumulative YES ROI |
|---|---:|---:|---:|---:|
| 2026-06-01 | 3 | 3 | -20.9% | -20.9% |
| 2026-06-03 | 2 | 2 | +22.7% | -3.8% |
| 2026-06-05 | 5 | 5 | +34.0% | +14.1% |
| 2026-06-06 | 3 | 3 | +35.7% | +18.8% |
| 2026-06-07 | 1 | 1 | +26.6% | +19.4% |
| 2026-06-08 | 2 | 2 | +34.2% | +21.2% |
| 2026-06-09 | 2 | 2 | +23.5% | +21.4% |
| 2026-06-10 | 3 | 3 | +23.5% | +21.7% |
| 2026-06-11 | 3 | 3 | -17.3% | +16.7% |
| 2026-06-12 | 3 | 3 | +25.4% | +17.7% |
| 2026-06-13 | 5 | 5 | +17.6% | +17.7% |

## 三道门

- significance=PASS：holdout fixed rule ROI CI [+4.6%, +28.1%].
- baseline=PASS：相对 paired d1 NO 增量 CI [+1.7%, +6.8%].
- forward=PASS_FIXED_STRESS：fixed train->holdout, latest window, leave-one stress, and threshold-neighborhood stress passed; prefix dynamic selector remains a non-blocking caution.
- execution=PASS_TINY：top-of-book available_notional_at_ask >= $2 keeps 31 rows / 11 dates with positive ROI and YES-NO CI.
- conclusion=confirmed：live_ready=True。

## 产物

- CSV: `docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/daily_pnl.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/city_contrib.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/date_leave_one.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/threshold_neighborhood.csv`
- CSV: `docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/liquidity_summary.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-yes-current-live-gate-v9.json`
