# Current-YES Core Carry：连续赔率条件 challenger v1

Status: `superseded-for-decision-use / high-mid-only denominator`

> 本轮输入的 1,349-row opportunity ledger 已经限定为 `market_mid>=0.80`，只能审计高价 carry 内部校准，不能回答跨赔率模型有效性。跨赔率结论以 [full-support v2](2026-08-09-current-yes-core-carry-price-conditioned-full-support-v2.md) 为准；本文件保留作分母纠偏记录。

## 结论与动作

选中的 challenger 没有在 7-date temporal holdout 上同时改善 Brier 与 logloss；本版不进入 shadow，现有 Core 继续运行。

- 当前 live Core Carry 不变；本研究没有修改 market-mid floor、sizing 或 execution。
- 最后 7 个历史 target dates 未参与拟合或候选选择，但此前研究已经看过这些结果，因此这里只称 temporal holdout，不称 clean frozen forward。
- 真正 clean forward 只能从本 artifact 冻结后的新 target dates 开始。

## 固定分母与选型

- 输入：1,349 PIT checkpoints / 772 city-days / 31 target dates（2026-06-02–2026-07-08）。
- expanding development OOF：16 dates / 727 rows；temporal holdout：7 dates / 182 rows（2026-06-27–2026-07-08）。
- 候选 K=3；开发窗选中 `core_recalibration`。
- 所有 challenger 都以 frozen Core logit 为 offset；不引入赔率 hard bucket，price 只以连续 spline/interaction 进入。

## Temporal holdout 概率结果

| 模型 | Brier | Logloss |
|---|---:|---:|
| challenger | 0.066946 | 0.242615 |
| frozen Core | 0.066881 | 0.241679 |
| same-row market | 0.069775 | 0.261860 |

- challenger − Core Brier Δ `+0.000065` CI `[-0.002557,+0.002401]`；logloss Δ `+0.000936` CI `[-0.008534,+0.010258]`。
- challenger − market Brier Δ `-0.002830` CI `[-0.005543,-0.000005]`；logloss Δ `-0.019245` CI `[-0.029528,-0.008193]`。

## 交易层次要诊断

下表都是同一 temporal holdout 的 10-share full-ladder 反事实，不是 actual fills；概率 proper score 仍是主门。

| selector | entries | W-L | PnL | ROI |
|---|---:|---:|---:|---:|
| challenger_no_floor | 26 | 26-0 | $+21.59 | +9.06% |
| challenger_floor_0.80 | 26 | 26-0 | $+21.59 | +9.06% |
| core_no_floor | 17 | 17-0 | $+16.08 | +10.44% |
| core_floor_0.80 | 17 | 17-0 | $+16.08 | +10.44% |

## Readiness、双漏斗与证据边界

- PIT state + clocks：READY；输入为原 Core frozen PIT opportunity ledger。
- canonical/build identity：READY for frozen input；本轮未读取当前 critical canonical build 形成新同分母结论。
- market quote + depth：READY for archived checkpoint；10-share replay只用冻结 ladder cost。
- settlement/label：READY；输入行均有 binary exact-bracket label。
- independent target dates：低；holdout 只有 7 dates。
- clean frozen-forward：BLOCKED；temporal holdout 已被既往研究间接查看。
- signal funnel：frozen PIT checkpoints → expanding development OOF → one selected candidate → 7-date holdout。
- evidence funnel：same-row market + label 全覆盖；执行仅 archived 10-share counterfactual；无新 actual fill claim。
- 8环覆盖：概率评估、统计推断、同分母 market baseline、历史 ladder execution；缺 clean forward、真实 fill、容量与组合相关性。

## 三道门

- significance=FAIL；baseline=PASS；forward=NA_clean_forward_not_started；conclusion=inconclusive。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --price-conditioned-challenger
```
