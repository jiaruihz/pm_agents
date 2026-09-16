# reheat-risk Jump Model v3 Bad-Case Attribution

Status: snapshot
Generated: 2026-06-15T15:52:39.540716+00:00
Target metric: `theta_no_bad_case_attribution` = source-aligned pure theta NO 中，d1/13-17h/ask<=0.75/EV>=0.08 候选在 holdout 亏损来自哪些日期、城市、价格段和天气路径。

## 数据快照

- 数据源: v2 calibrated quote replay + v1 METAR path feature cache；DB 只用于强制自检和 live fill gate。
- quote rows: 10275; feature rows: 330754; joined feature coverage: 100.0%。
- fact_built_at_utc: `2026-06-15T14:43:25.893487+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

这轮没有证明“现在可以开 theta NO”，但把问题说清楚了一层：天气模型确实能更好地判断会不会再升温；真正坏掉的是“把这个概率变成可交易报价”的选择层，尤其是校准后选出来的单不赚钱。

- raw_v1 这个未校准版本在 holdout 反而比 isotonic 版本好：raw_v1 ROI +2.1%，iso_dist ROI -5.1%。这说明 v2 的校准改善了概率评分，但没有改善交易选择。
- raw_v1 的 holdout 盈亏不是每天稳定小赚，而是被单日冲击主导：最差日 2026-06-09 PnL -1.76，当天 ROI -22.7%。
- 城市上也有集中度：raw_v1 最大亏损城市是 Madrid，holdout PnL -1.69，ROI -62.8%。
- 价格段里最拖累 raw_v1 的是 ask 0.40-0.55，PnL -2.43，ROI -9.6%；这不是纯 theta carry 的目标价位，而是 EV selector 把中低价方向单混进来了。
- 时间上，raw_v1 最差的是 local 16 点，PnL -3.44，ROI -53.4%；13-14 点反而是正的。
- 最关键的事前信号仍是“真的衰竭了吗”：decline<0.5°C 的 raw_v1 holdout ROI -1.1%，decline 1-2°C 的 ROI +40.8%，但后者只有 11 行。
- 事后机制很清楚：d1 NO 最怕刚好再升 0.5-1°C，raw_v1 在这桶 PnL -30.16；再升 1-2°C 反而 PnL +15.89，因为很多时候会跳过所买的下一档。

我的当前判断：继续研究是值得的，但 v3 暴露的是口径污染：0.40-0.55 这类中低价 NO 本来就不是“低保 theta”，真正 theta carry 应该回到高 NO ask + 明确衰竭 + no-reheat 风险排除，并用日期 walk-forward 单独验证。

## 候选表现

| variant | holdout rows | active dates | ROI | PnL | win rate | excess vs all d1 ask<=0.75 | CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `raw_v1_ev08_d1_h13_17_ask075` | 161 | 9 | +2.1% | +1.55 | 46.6% | +4.8% | [-4.9%, +12.1%] |
| `iso_dist_ev08_d1_h13_17_ask075` | 151 | 9 | -5.1% | -3.31 | 40.4% | -2.4% | [-15.2%, +8.2%] |

Row grain: 一行是一个去重后的 `city,target_date,bracket` NO 买入机会，不是一笔真实成交，也不是 city-day basket。

## 亏损来源

### 最差日期

| variant | target_date | rows | ROI | PnL | cities |
|---|---:|---:|---:|---:|---:|
| `iso_dist_ev08_d1_h13_17_ask075` | 2026-06-01 | 16 | -31.2% | -2.26 | 13 |
| `iso_dist_ev08_d1_h13_17_ask075` | 2026-06-05 | 13 | -33.1% | -1.98 | 13 |
| `iso_dist_ev08_d1_h13_17_ask075` | 2026-06-03 | 13 | -27.5% | -1.52 | 12 |
| `iso_dist_ev08_d1_h13_17_ask075` | 2026-06-09 | 15 | -22.2% | -1.43 | 13 |
| `iso_dist_ev08_d1_h13_17_ask075` | 2026-06-08 | 19 | -18.2% | -1.34 | 16 |
| `raw_v1_ev08_d1_h13_17_ask075` | 2026-06-09 | 17 | -22.7% | -1.76 | 15 |
| `raw_v1_ev08_d1_h13_17_ask075` | 2026-06-07 | 14 | -18.2% | -1.11 | 14 |
| `raw_v1_ev08_d1_h13_17_ask075` | 2026-06-05 | 15 | -9.7% | -0.75 | 15 |
| `raw_v1_ev08_d1_h13_17_ask075` | 2026-06-01 | 13 | -3.0% | -0.19 | 11 |
| `raw_v1_ev08_d1_h13_17_ask075` | 2026-06-03 | 13 | -0.1% | -0.01 | 11 |

### 最差城市

| variant | city | rows | active dates | ROI | PnL |
|---|---|---:|---:|---:|---:|
| `iso_dist_ev08_d1_h13_17_ask075` | Atlanta | 7 | 7 | -69.8% | -2.31 |
| `iso_dist_ev08_d1_h13_17_ask075` | Chengdu | 4 | 4 | -100.0% | -2.20 |
| `iso_dist_ev08_d1_h13_17_ask075` | Madrid | 6 | 5 | -61.2% | -1.58 |
| `iso_dist_ev08_d1_h13_17_ask075` | SaoPaulo | 6 | 6 | -60.6% | -1.54 |
| `iso_dist_ev08_d1_h13_17_ask075` | Busan | 3 | 3 | -100.0% | -1.52 |
| `iso_dist_ev08_d1_h13_17_ask075` | Lucknow | 4 | 4 | -100.0% | -1.47 |
| `iso_dist_ev08_d1_h13_17_ask075` | Wuhan | 4 | 4 | -48.2% | -0.93 |
| `iso_dist_ev08_d1_h13_17_ask075` | Tokyo | 4 | 4 | -46.5% | -0.87 |
| `raw_v1_ev08_d1_h13_17_ask075` | Madrid | 6 | 6 | -62.8% | -1.69 |
| `raw_v1_ev08_d1_h13_17_ask075` | NYC | 7 | 7 | -41.0% | -1.39 |
| `raw_v1_ev08_d1_h13_17_ask075` | Atlanta | 5 | 5 | -58.1% | -1.39 |
| `raw_v1_ev08_d1_h13_17_ask075` | Busan | 4 | 3 | -55.9% | -1.27 |
| `raw_v1_ev08_d1_h13_17_ask075` | Chengdu | 7 | 5 | -27.3% | -1.13 |
| `raw_v1_ev08_d1_h13_17_ask075` | Wuhan | 4 | 4 | -48.2% | -0.93 |
| `raw_v1_ev08_d1_h13_17_ask075` | Tokyo | 4 | 4 | -46.5% | -0.87 |
| `raw_v1_ev08_d1_h13_17_ask075` | CapeTown | 1 | 1 | -100.0% | -0.73 |

### 价格段

| variant | ask bucket | rows | ROI | PnL | win rate |
|---|---:|---:|---:|---:|---:|
| `iso_dist_ev08_d1_h13_17_ask075` | 0.40-0.55 | 63 | -19.8% | -5.91 | 38.1% |
| `iso_dist_ev08_d1_h13_17_ask075` | 0.55-0.70 | 29 | -5.0% | -0.90 | 58.6% |
| `iso_dist_ev08_d1_h13_17_ask075` | 0.70-0.75 | 2 | +37.0% | +0.54 | 100.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | 0.00-0.40 | 57 | +19.7% | +2.96 | 31.6% |
| `raw_v1_ev08_d1_h13_17_ask075` | 0.40-0.55 | 53 | -9.6% | -2.43 | 43.4% |
| `raw_v1_ev08_d1_h13_17_ask075` | 0.70-0.75 | 9 | +6.1% | +0.40 | 77.8% |
| `raw_v1_ev08_d1_h13_17_ask075` | 0.55-0.70 | 44 | +3.2% | +0.86 | 63.6% |
| `raw_v1_ev08_d1_h13_17_ask075` | 0.00-0.40 | 55 | +19.1% | +2.73 | 30.9% |

### 路径分桶

`jump_bin_ex_post` 是事后归因，不能当事前过滤器；它用来解释 d1 NO 为什么会输。

| variant | bucket type | bucket | rows | ROI | PnL | win rate |
|---|---|---:|---:|---:|---:|---:|
| `iso_dist_ev08_d1_h13_17_ask075` | `decision_hour_local` | 16 | 12 | -80.4% | -4.09 | 8.3% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decision_hour_local` | 15 | 25 | -20.2% | -2.02 | 32.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decision_hour_local` | 17 | 2 | +56.2% | +0.36 | 50.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decision_hour_local` | 14 | 40 | +4.1% | +0.63 | 40.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decision_hour_local` | 13 | 72 | +5.5% | +1.81 | 48.6% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decline_bucket` | <0.5 | 141 | -8.0% | -4.69 | 38.3% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decline_bucket` | >=2.0 | 1 | -100.0% | -0.60 | 0.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | `decline_bucket` | 1.0-2.0 | 9 | +39.4% | +1.98 | 77.8% |
| `iso_dist_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 0.5-1 | 83 | -93.8% | -30.14 | 2.4% |
| `iso_dist_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 2+ | 4 | +98.0% | +1.98 | 100.0% |
| `iso_dist_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 0 | 30 | +67.0% | +10.03 | 83.3% |
| `iso_dist_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 1-2 | 34 | +97.6% | +14.82 | 88.2% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decision_hour_local` | 16 | 14 | -53.4% | -3.44 | 21.4% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decision_hour_local` | 15 | 26 | -13.2% | -1.53 | 38.5% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decision_hour_local` | 17 | 3 | -15.3% | -0.18 | 33.3% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decision_hour_local` | 14 | 42 | +17.5% | +2.98 | 47.6% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decision_hour_local` | 13 | 76 | +10.0% | +3.72 | 53.9% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decline_bucket` | <0.5 | 148 | -1.1% | -0.74 | 43.9% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decline_bucket` | >=2.0 | 1 | -100.0% | -0.60 | 0.0% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decline_bucket` | 0.5-1.0 | 1 | +38.9% | +0.28 | 100.0% |
| `raw_v1_ev08_d1_h13_17_ask075` | `decline_bucket` | 1.0-2.0 | 11 | +40.8% | +2.61 | 81.8% |
| `raw_v1_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 0.5-1 | 81 | -93.8% | -30.16 | 2.5% |
| `raw_v1_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 2+ | 6 | +79.6% | +2.66 | 100.0% |
| `raw_v1_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 0 | 38 | +63.2% | +13.16 | 89.5% |
| `raw_v1_ev08_d1_h13_17_ask075` | `jump_bin_ex_post` | 1-2 | 36 | +92.9% | +15.89 | 91.7% |

## 天气路径差异

下表的 `loser-winner` 是 holdout 亏损单均值减盈利单均值；正数表示亏损单更高。

| variant | feature | winner mean | loser mean | loser-winner |
|---|---|---:|---:|---:|
| `iso_dist_ev08_d1_h13_17_ask075` | `best_ask` | 0.48 | 0.39 | -0.09 |
| `iso_dist_ev08_d1_h13_17_ask075` | `d1h_b` | 0.67 | 0.70 | +0.03 |
| `iso_dist_ev08_d1_h13_17_ask075` | `d2h_b` | 1.58 | 1.39 | -0.20 |
| `iso_dist_ev08_d1_h13_17_ask075` | `decline` | 0.13 | 0.08 | -0.05 |
| `iso_dist_ev08_d1_h13_17_ask075` | `dep_f` | 17.46 | 22.16 | +4.70 |
| `iso_dist_ev08_d1_h13_17_ask075` | `gap_cur_to_thresh_b` | 0.58 | 0.60 | +0.01 |
| `iso_dist_ev08_d1_h13_17_ask075` | `hours_since_max` | 0.13 | 0.10 | -0.04 |
| `iso_dist_ev08_d1_h13_17_ask075` | `jump_c` | 1.21 | 0.99 | -0.22 |
| `iso_dist_ev08_d1_h13_17_ask075` | `relh_now` | 58.23 | 50.52 | -7.71 |
| `iso_dist_ev08_d1_h13_17_ask075` | `sknt_now` | 8.24 | 7.78 | -0.47 |
| `iso_dist_ev08_d1_h13_17_ask075` | `sky_now` | 1.83 | 1.44 | -0.39 |
| `raw_v1_ev08_d1_h13_17_ask075` | `best_ask` | 0.52 | 0.40 | -0.12 |
| `raw_v1_ev08_d1_h13_17_ask075` | `d1h_b` | 0.72 | 0.70 | -0.02 |
| `raw_v1_ev08_d1_h13_17_ask075` | `d2h_b` | 1.66 | 1.31 | -0.35 |
| `raw_v1_ev08_d1_h13_17_ask075` | `decline` | 0.14 | 0.08 | -0.06 |
| `raw_v1_ev08_d1_h13_17_ask075` | `dep_f` | 17.55 | 21.28 | +3.73 |
| `raw_v1_ev08_d1_h13_17_ask075` | `gap_cur_to_thresh_b` | 0.61 | 0.59 | -0.01 |
| `raw_v1_ev08_d1_h13_17_ask075` | `hours_since_max` | 0.30 | 0.10 | -0.20 |
| `raw_v1_ev08_d1_h13_17_ask075` | `jump_c` | 1.16 | 0.99 | -0.17 |
| `raw_v1_ev08_d1_h13_17_ask075` | `relh_now` | 57.95 | 51.67 | -6.28 |
| `raw_v1_ev08_d1_h13_17_ask075` | `sknt_now` | 7.91 | 7.84 | -0.07 |
| `raw_v1_ev08_d1_h13_17_ask075` | `sky_now` | 1.80 | 1.42 | -0.38 |

## 三道门 verdict

在 holdout>=2026-06-01，raw_v1 d1/h13-17/ask<=0.75/EV>=0.08 相对 all-d1 ask<=0.75 baseline 的超额 ROI 为 +4.8%（95% CI [-4.9%, +12.1%]），前瞻 FAIL，结论等级 `inconclusive`。

- significance=FAIL: holdout excess CI 跨 0。
- baseline=FAIL: raw_v1 虽有正点估计，但相对同价/同距离 baseline 不稳；iso_dist 直接为负。
- forward=FAIL: v2 prefix walk-forward 为负，本 v3 没有反转这个结论。
- conclusion=inconclusive: 不建议 shadow/paper/live；下一步只做研究脚本层的分层验证。

## 输出文件

- JSON: `docs/analysis/2026-06/2026-06-15-m3-jump-model-v3-bad-case-attribution.json`
- generated CSV dir: `docs/analysis/2026-06/generated/m3_jump_model_v3_bad_case_attribution`
- Script: `scripts/analysis/reheat_risk/research_m3_jump_model_v3_bad_case_attribution.py`
