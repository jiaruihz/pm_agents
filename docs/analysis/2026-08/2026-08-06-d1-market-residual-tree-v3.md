# D-1 market-residual tree tournament v3

production:
live_action=none
orders_changed=0

## 结论

同分母 nested expanding OOF：168 states / 17 target dates。

| arm | logloss | Δ vs market | 95% CI | median max shift | p90 shift | α=0 folds |
|---|---:|---:|---:|---:|---:|---:|
| M0_market | 1.560207 | reference | - | - | - | - |
| V09_shallow_gradient_boosting | 1.560207 | +0.000000 | [+0.000000, +0.000000] | 0.00% | 0.00% | 17/17 |
| V10_interaction_gradient_boosting | 1.560207 | +0.000000 | [+0.000000, +0.000000] | 0.00% | 0.00% | 17/17 |

V09/V10 使用真正的 gradient-boosted nonlinear interactions；inner OOF 可选 α=0，因此失败时精确回到 market。重建 market 是 normalized mid，不是 executable ask，任何正结果仍须经过 clean first-seen forward 与 ask/fee/depth。
