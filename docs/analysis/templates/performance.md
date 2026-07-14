<!-- M1：已有策略绩效；口径见 WEATHER_ANALYSIS_CONTRACT 与 weather-strategy-performance。 -->

# 绩效分析：{topic}

> 窗口：{date_start} — {date_end}
> 策略身份：{instance_id / strategy_id / config_id / execution_policy}
> evidence layer：{canonical DB / current raw / both}

## 结论与动作

{fee-adjusted 结论；research/shadow/live 边界；保持/collector/shadow/不改 live}

```text
significance={PASS/FAIL/NA}; baseline={PASS/FAIL/NA}; forward={PASS/FAIL/NA}; conclusion={level}
```

## 数据快照

| 项目 | 值 |
|---|---|
| raw/canonical 覆盖截止 | |
| DB `fact_built_at_utc` | |
| opportunity / fill rows | |
| 独立 target dates | |
| unsettled / missing settlement | |
| CLOB coverage gate | |
| fee evidence classes | |

## Target metric 与固定分母

- unit/grain：
- PIT decision timestamp：
- universe / denominator：
- label / settlement source：
- price / executable cost / fee：
- 主指标：
- same-denominator baseline：
- train / forward split：

## Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| raw universe | | | | |
| mechanism candidate | | | | |
| first event/city-day signal | | | | |
| selected | | | | |

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| PIT source/features | | | | |
| PIT quote | | | | |
| settlement | | | | |
| executable expression | | | | |
| actual fill | | | | |

## Probability / ranking quality

| candidate | rows | logloss | Brier | calibration | AUC/rank | delta vs market | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|

## Fee-adjusted trade performance

| slice | opportunities | fills | dates | cash cost | fees | PnL | ROI | 95% CI | excess vs baseline |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|

YES 与 NO 必须分行；gross 可附录，不作主结论。未结算另列 `[UNSETTLED]`。

## Execution / selection quality

| 指标 | 值 |
|---|---:|
| eligible -> actual fill | |
| slippage vs decision/paper | |
| missed winners / avoided losers | |
| maker fill / cancel / adverse selection | |
| submitted / posted / filled size anomalies | |

## Forward 与稳健性

| 检查 | 结果 |
|---|---|
| frozen forward same sign | |
| target-date block bootstrap | |
| top-date / top-trade removal | |
| multiple testing K / correction | |
| coverage sensitivity | |

## 三门与残余风险

| 门 | PASS/FAIL/NA | 证据 |
|---|---|---|
| significance | | |
| same-denominator baseline | | |
| forward | | |

列出 PIT、source basis、archive timing、fee、fill、样本量和 runtime 漂移风险。
