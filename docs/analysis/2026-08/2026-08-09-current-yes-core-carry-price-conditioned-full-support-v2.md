# Current-YES Core Carry：全赔率支持连续校准 challenger v2

Status: `historical_holdout_failed`

## 结论与动作

全赔率连续 challenger 没有在 temporal holdout 同时改善 Brier 与 logloss；本版不进入 shadow，当前 Core 继续运行。

- 当前 live Core 继续运行，本研究没有改 production。
- 本次修正了上一轮分母：上一轮 1,349-row ledger 已经是 0.80+ 切片；本轮回到原始 4,061-row / 全赔率训练 support。
- 最后 7 个 target dates 不参与拟合或选型；由于其结果此前已在其他研究出现，只是 temporal holdout，不冒充 clean forward。

## 固定口径

- universe：4,061 checkpoints / 1305 city-days / 46 dates / 36 cities。
- market mid support：`0.0110`–`0.9895`。
- expanding development OOF：25 dates / 2495 rows；holdout：7 dates / 263 rows（2026-06-28–2026-07-08）。
- 候选 K=3；开发窗选中 `core_reweighted_linear`。

## Holdout 概率结果

| 模型 | Brier | Logloss |
|---|---:|---:|
| challenger | 0.182963 | 0.539388 |
| deployed-style Core | 0.173113 | 0.506866 |
| same-row market | 0.140487 | 0.426002 |

- challenger − Core：Brier `+0.009850 CI [-0.000755,+0.027970]`；logloss `+0.032522 CI [-0.005609,+0.100068]`。
- challenger − market：Brier `+0.042476 CI [+0.001322,+0.116271]`；logloss `+0.113386 CI [-0.004865,+0.332224]`。

## 按赔率带诊断

| mid band | rows | challenger Brier | Core Brier | market Brier |
|---|---:|---:|---:|---:|
| below_0.20 | 47 | 0.043307 | 0.042824 | 0.044699 |
| 0.20_to_0.50 | 36 | 0.241070 | 0.236274 | 0.199886 |
| 0.50_to_0.80 | 48 | 0.276525 | 0.268921 | 0.226015 |
| 0.80_to_0.90 | 28 | 0.082899 | 0.086596 | 0.087701 |
| 0.90_to_0.9895 | 104 | 0.043764 | 0.043942 | 0.044668 |

## Readiness、双漏斗与三道门

- PIT/features/labels：READY，来自原始 Core frozen training universe。
- market baseline：READY，同 rows 的 PIT midpoint。
- current canonical：本轮不读取；当前 production critical 不污染冻结历史分母。
- clean frozen forward：BLOCKED，需从本次冻结后的新 target dates 开始。
- signal funnel：4,061 full-support checkpoints → expanding development OOF → one selected candidate → 7-date holdout。
- evidence funnel：PIT feature、market、label同分母完整；本轮是概率研究，不宣称 execution/fill/PnL。
- significance=FAIL；baseline=FAIL；forward=NA_clean_forward_not_started；conclusion=inconclusive。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --price-conditioned-challenger
```
