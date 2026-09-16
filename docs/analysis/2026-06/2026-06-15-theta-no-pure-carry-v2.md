# Theta NO Pure Carry v2

Status: snapshot
Generated: 2026-06-15T15:53:51.511840+00:00
Target metric: `pure_theta_carry` = source-aligned d1 NO 中，只看较高 NO ask 的 carry 形态；模型/衰竭信号用于排除再升温风险，不把 0.40-0.55 这种中低价方向单混入 theta。

## 数据快照

- 数据源: v2 calibrated quote replay；DB 只用于强制自检和 live fill gate。
- quote rows after d1/h13-17/source-aligned dedupe: 1082; holdout rows: 456.
- fact_built_at_utc: `2026-06-15T14:43:25.893487+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

你指出的对：0.40-0.55 ask 不是 theta 低保标的。真正的 theta carry 应该是高 NO ask、小收益、低命中风险；如果一个城市真的已经衰竭，市场价格也应该更接近 0.75-0.95，而不是 0.45。

按这个修正口径，方向重新变得有希望：`ask>=0.75 + decline>=0.5°C` 在 holdout 是 ROI +7.0%，38 行/9 天/21 城；`ask>=0.85 + decline>=0.5°C` 是 ROI +3.2%，25 行/8 天/17 城。关键问题不是 holdout 不好，而是 train 同口径为负，forward consistency 还没成立。

所以正确理解应该是：`0.40-0.55` 那批不是策略失败证据，而是 v2/v3 的 EV selector 选错了交易类型；真正要继续研究的是高 ask carry 里，decline/no-reheat 特征能否持续把尾部命中风险压低。

## 关键 holdout 口径

| profile | rows | dates | avg ask | ROI | PnL | win rate | excess vs same ask | CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `d1|h13-17|ask>=0.75|decline>=0.5` | 38 | 9 | 0.89 | +7.0% | +2.34 | 94.7% | +10.3% | [+3.0%, +16.8%] |
| `d1|h13-17|ask>=0.8|decline>=0.5` | 33 | 9 | 0.90 | +4.0% | +1.18 | 93.9% | +7.5% | [-1.4%, +13.9%] |
| `d1|h13-17|ask>=0.85|decline>=0.5` | 25 | 8 | 0.93 | +3.2% | +0.74 | 96.0% | +5.5% | [-4.2%, +12.1%] |
| `d1|h13-17|ask>=0.9|decline>=0.5` | 18 | 7 | 0.95 | -0.7% | -0.13 | 94.4% | +0.8% | [-11.4%, +9.2%] |

## 为什么 v3 跑偏

| profile | rows | avg ask | ROI | PnL | win rate |
|---|---:|---:|---:|---:|---:|
| `d1|h13-17|ask>=0.55|decline>=0` | 350 | 0.79 | -2.5% | -7.04 | 77.4% |
| `d1|h13-17|ask>=0.75|decline>=0` | 230 | 0.87 | -3.3% | -6.60 | 84.3% |

`ask>=0.55/decline>=0` 这类宽口径把大量“还没衰竭、价格也不高”的 NO 混进来，已经不是低保 theta。加上 `decline>=0.5` 后才开始接近我们的本意。

## 探索性 top profiles

| profile | rows | dates | ROI | PnL | win rate | avg ask |
|---|---:|---:|---:|---:|---:|---:|
| `d1|h13-17|ask>=0.55|decline>=0.5` | 50 | 9 | +13.4% | +5.57 | 94.0% | 0.83 |
| `d1|h13-17|ask>=0.55|decline>=1` | 42 | 9 | +12.7% | +4.39 | 92.9% | 0.82 |
| `d1|h13-17|ask>=0.7|decline>=0.5` | 43 | 9 | +10.0% | +3.74 | 95.3% | 0.87 |
| `d1|h13-17|ask>=0.7|decline>=1` | 35 | 9 | +8.4% | +2.56 | 94.3% | 0.87 |
| `d1|h13-17|ask>=0.75|decline>=0.5` | 38 | 9 | +7.0% | +2.34 | 94.7% | 0.89 |
| `d1|h13-17|ask>=0.75|decline>=1` | 31 | 9 | +5.3% | +1.45 | 93.5% | 0.89 |
| `d1|h13-17|ask>=0.8|decline>=0.5` | 33 | 9 | +4.0% | +1.18 | 93.9% | 0.90 |
| `d1|h13-17|ask>=0.85|decline>=0.5` | 25 | 8 | +3.2% | +0.74 | 96.0% | 0.93 |

## 三道门 verdict

在 holdout>=2026-06-01，`d1|h13-17|ask>=0.75|decline>=0.5` ROI 为 +7.0%，相对 same-ask baseline 的超额 ROI 为 +10.3%（95% CI [+3.0%, +16.8%]）；但 train 同口径 ROI 为 -4.9%、excess -1.8%，前后不一致，结论等级 `inconclusive`。

- significance=PASS_ON_HOLDOUT: 关键口径 holdout excess CI 不跨 0。
- baseline=PASS_ON_HOLDOUT: 相对 same-ask baseline 为正且显著。
- forward=FAIL: train 同口径为负，说明这还不是稳定可上线规则。
- conclusion=inconclusive: 不进 shadow/paper/live；下一步做高 ask carry 专用 prefix walk-forward、城市/小时分层和样本扩展。

## 输出文件

- JSON: `docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-v2.json`
- generated CSV dir: `docs/analysis/2026-06/generated/theta_no_pure_carry_v2`
- Script: `scripts/analysis/reheat_risk/research_theta_no_pure_carry_v2.py`
