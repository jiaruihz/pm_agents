# Core Carry semantic challenger v3

**结论：`rejected`；未修改 live。** 开发窗选中 `core_recalibration`。

## 固定口径

- 分母：1,349 个 PIT checkpoint、772 个 city-day、31 个 target dates（2026-06-02–2026-07-08）。
- 开发 expanding OOF：15 dates；frozen forward：8 dates（2026-06-26–2026-07-08）。
- 候选 K=4；forward 没参与选型；同 rows 比 frozen core 与 market。

## 结果

| 模型 | Forward Brier | Forward logloss |
|---|---:|---:|
| challenger `core_recalibration` | 0.089091 | 0.301849 |
| frozen core | 0.087488 | 0.294822 |
| same-row market | 0.089860 | 0.309174 |

- Challenger − core Brier：+0.000548（95% CI [-0.002322, +0.003540]）
- Challenger − core logloss：+0.002410（95% CI [-0.007959, +0.012910]）
- Challenger − market Brier：-0.001255（95% CI [-0.005501, +0.002625]）
- Challenger − market logloss：-0.009934（95% CI [-0.026581, +0.006427]）

## 三道门与动作

- significance=FAIL；baseline=PASS；forward=FAIL；conclusion=inconclusive。
- 本研究只回答概率 challenger；不改 eligibility、fixed 10 sizing、maker policy 或 live 配置。

## 数据完整性与 8 环

- 输入是已归档冻结 PIT ledger，未因 challenger 丢行；source first-seen 不可用，因此没有把 source proxy 纳入模型。
- 覆盖：统计推断、概率评估、同分母 market baseline、frozen forward。未覆盖：真实 fill 微结构、容量、组合资金曲线与新 live 日期 canonical 合并。
- 当前 production manifest 的 canonical refresh LaunchAgent 有 critical non-zero last exit，所以没有把 7/08 后 live case 拼入旧分母，也没有做任何生产动作。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --semantic-challenger
```
