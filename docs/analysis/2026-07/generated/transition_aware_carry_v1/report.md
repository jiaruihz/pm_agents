# Transition-aware carry v1 PIT replay

> 描述性 shadow；transition exposure 尚未校准成 outcome probability，不授权 live gate。

- Signal funnel: `{'raw_decision_rows': 16176, 'strong_h1_rows': 67, 'first_city_day_signals': 27}`
- Evidence funnel: `{'taf_capture_available': 27, 'taf_change_window_available': 25, 'settled': 27}`
- Baseline H1: `{'signals': 27, 'settled': 27, 'wins': 26, 'win_rate': 0.9629629629629629, 'pnl_per_share_sum': -0.3452499999999997, 'cost_sum': 26.34525, 'roi': -0.013104829143773535}`

| fragility bucket | signals | settled | wins | win rate | ROI |
|---|---:|---:|---:|---:|---:|
| coverage_gap | 2 | 2 | 2 | 1.0 | 0.027870714421540058 |
| exposure_1_to_3x_budget | 7 | 7 | 7 | 1.0 | 0.025372100211080183 |
| exposure_gt_3x_budget | 15 | 15 | 15 | 1.0 | 0.019991146476848597 |
| exposure_le_tail_budget | 3 | 3 | 2 | 0.6666666666666666 | -0.3023288263775517 |
