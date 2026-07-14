# Current YES Heat-Death Physical Backtest v1

Status: current-reference
Date: 2026-07-14
Verdict: `historical_proxy_positive_but_no_incremental_alpha`

## 结论

历史 proxy 不是 NA：strong cohort holdout 有 15 行 / 7 天，current YES fee ROI +2.1%，d1 NO fee ROI +1.9%。
但 current YES 相对同价 base-fade baseline 的超额只有 +0.1%，95% CI [-0.1%, +0.6%]；物理支持没有提供可确认的增量 alpha。

## Funnel

- 99,819 bracket rows -> 9,800 city-date-hour states -> 268 base candidates -> 36 strong proxy candidates。
- train: < 2026-06-01；holdout: >= 2026-06-01。规则是在读取结果前按 forward runner 口径冻结，但 peak clock 来自 historical forecast API backfill，并非当时生产 snapshot 原生 PIT 字段，因此整个历史仍属于 source-sensitive retrospective replay。

## Holdout

| Expression | Rows | Dates | Win | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| current YES | 15 | 7 | +100.0% | +2.1% | [+1.5%, +2.9%] |
| d1 NO | 13 | 6 | +100.0% | +1.9% | [+1.4%, +2.6%] |

同一 13 行 / 6 天 paired denominator 上，current YES - d1 NO ROI = +0.4%，95% CI [+0.1%, +0.9%]。点估和 bootstrap 偏向 current YES，但只有 6 个日期，低于策略确认门槛，不能升格为稳定表达优势。

## Data Integrity Self-Check

- date coverage: 2026-05-19..2026-06-17；dedup key duplicates=0。
- settlement status: `{"settled": 9800}`。
- current YES ask coverage=82.6%；d1 NO ask coverage=80.3%。

## Feature Coverage Boundary

历史层能重建温度路径、minutes-since-max、云、湿度、风速、双模型 peak clock/gap；不能 PIT 重建本次新增的降雨、风向、remaining-3h forecast weather 和 solar geometry。因此这里是新策略的 historical proxy，不是假装完整的新特征回测。

## Three Gates

```text
significance=PASS for absolute current YES proxy ROI
baseline=FAIL for same-price physical-overlay excess
forward=FAIL_THIN for complete new weather_state_v2 features
conclusion=inconclusive; zero-notional forward only
```
