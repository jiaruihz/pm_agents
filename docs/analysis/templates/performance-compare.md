<!-- M3：同分母 A/B；只允许固定 rows/labels/quotes 后比较一个因素。 -->

# 策略对比：{A} vs {B}

## 结论与动作

{paired delta、CI、forward、动作}

## 固定分母与唯一差异

| 项目 | A | B | 是否同口径 |
|---|---|---|---|
| rows / dates / labels | | | |
| PIT features | | | |
| quote timestamp / executable price | | | |
| source policy | | | |
| model / selector / execution overlay | | | |
| fee / friction | | | |

若 rows、coverage 或 label 不同，先报告 coverage delta；不得直接把结果称为 A/B alpha。

## 双漏斗

| funnel layer | A rows | B rows | delta | 原因 |
|---|---:|---:|---:|---|
| raw universe | | | | |
| mechanism signal | | | | |
| PIT quote | | | | |
| settlement | | | | |
| executable | | | | |
| actual fill | | | | |

## Probability paired comparison

| metric | A | B | paired delta B-A | target-date 95% CI |
|---|---:|---:|---:|---|
| logloss | | | | |
| Brier | | | | |
| calibration | | | | |
| AUC/rank | | | | |

## Fee-adjusted execution comparison

| metric | A | B | delta B-A | target-date 95% CI |
|---|---:|---:|---:|---|
| opportunities / fills | | | | |
| cash cost / fees | | | | |
| PnL / ROI | | | | |
| slippage / fill rate | | | | |
| YES PnL / NO PnL | | | | |

## Forward / multiple testing

- train selection：
- frozen forward：
- variants tried K：
- correction / uncorrected risk：

## 三门

```text
significance={}; baseline={}; forward={}; conclusion={}
```
