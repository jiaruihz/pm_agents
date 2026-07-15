# Current YES Heat-Death Physical Backtest v1

Status: current-reference
Date: 2026-07-14
Verdict: `historical_proxy_positive_but_no_incremental_alpha`

## 结论

历史 proxy 不是 NA：strong cohort holdout 有 7 行 / 6 天，current YES fee ROI +5.8%，d1 NO fee ROI +6.0%。
但 current YES 相对同价 base-fade baseline 的超额只有 +0.4%，95% CI [-4.3%, +6.5%]；物理支持没有提供可确认的增量 alpha。

## Funnel

- 99,819 bracket rows -> 9,800 city-date-hour states -> 244 base candidates -> 23 strong proxy candidates。
- train: < 2026-06-01；holdout: >= 2026-06-01。规则是在读取结果前按 forward runner 口径冻结。

## Peak clock provenance（2026-07-15 复核）

- backfill CSV `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv`：single-runs 占比 100.0%，run policy = `{"d_minus_1_12z": 1500, "d_minus_2_12z": 36}`；factory 行内嵌值与该 CSV 的 GFS peak hour 一致率 = 100.0%（1040 city-dates）。
- **provenance = verified_clean**：本次输入 factory 行的 peak clock 与 single-runs D-1 12z 重建一致，无未来信息泄漏；剩余 source 风险是与生产 runner 最新 run 的 parity，不是 leakage。

## Holdout

| Expression | Rows | Dates | Win | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| current YES | 7 | 6 | +100.0% | +5.8% | [+1.2%, +18.1%] |
| d1 NO | 6 | 6 | +100.0% | +6.0% | [+1.1%, +16.5%] |

同一 6 行 / 6 天 paired denominator 上，current YES - d1 NO ROI = +0.7%，95% CI [+0.1%, +1.6%]。点估和 bootstrap 偏向 current YES，但只有 6 个日期，低于策略确认门槛，不能升格为稳定表达优势。

## Data Integrity Self-Check

- date coverage: 2026-05-19..2026-06-17；dedup key duplicates=0。
- settlement status: `{"settled": 9800}`。
- current YES ask coverage=82.6%；d1 NO ask coverage=80.3%。

## Feature Coverage Boundary

历史层能重建温度路径、minutes-since-max、云、湿度、风速、双模型 peak clock/gap；不能 PIT 重建本次新增的降雨、风向、remaining-3h forecast weather 和 solar geometry。因此这里是新策略的 historical proxy，不是假装完整的新特征回测。

## Three Gates

```text
significance=FAIL_LOW_SAMPLE for absolute current YES proxy ROI (rows=7, dates=6; preregistered floor: 30 rows / 12 dates)
baseline=FAIL for same-price physical-overlay excess
forward=FAIL_THIN for complete new weather_state_v2 features
conclusion=inconclusive; zero-notional forward only
```
