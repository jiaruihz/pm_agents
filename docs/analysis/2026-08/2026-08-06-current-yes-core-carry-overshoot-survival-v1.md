# Core Carry overshoot survival challenger v1

**动作：不改 live。结论 `rejected`。** 这是同一 Core Carry expression 的概率头 A/B。

## Readiness 与固定口径

- 冻结 parent：1,349 checkpoints / 36 cities / 31 target dates / 95 upward overshoots。
- Development expanding OOF：15 dates；frozen forward：2026-06-26–2026-07-08（8 dates）。
- PIT state、market midpoint、10-share cost、settlement READY；当前 canonical/JRS 与 hourly cache 读取 BLOCKED。
- 因此 v1 是 scalar-exposure current-status survival，不冒充完整逐小时 time-to-event hazard。

## Primary：frozen-forward state entry

| model | rows | Brier | logloss | mean p |
|---|---:|---:|---:|---:|
| survival | 138 | 0.078556 | 0.290365 | 0.9214 |
| core v2 | 138 | 0.077255 | 0.278171 | 0.9060 |
| market | 138 | 0.078782 | 0.291308 | 0.9153 |

- Survival − core Brier：+0.001301 CI [-0.006946,+0.009474]
- Survival − core logloss：+0.012194 CI [-0.017136,+0.040972]
- Survival − market Brier：-0.000226 CI [-0.001377,+0.000875]
- Survival − market logloss：-0.000943 CI [-0.005843,+0.003790]
- Development state-entry vs core Brier/logloss：+0.002049/+0.006648。
- 冻结拟合实际保留的非零物理项：`forecast_cross_pressure, heat_exposure_0_2h, warming_runway_per_tick`；2–4h、4h+、solar 与 plateau 项被正则/单调约束压到 0。

## Secondary grain 与执行描述

- Path transition vs core Brier/logloss：+0.001673/+0.015178。
- Checkpoint vs core Brier/logloss：+0.001965/+0.015401。
- Frozen-forward fixed-10 descriptive：survival 5 entries / PnL $-3.11；core 22 entries / PnL $+9.53。概率 gate 未通过时该项不构成策略结论。

## 三道门与 8 环

- significance=FAIL；baseline=PASS；forward=FAIL；conclusion=inconclusive。
- 覆盖 probability、统计推断、market baseline、frozen forward 与历史 10-share 描述；不覆盖 maker/fill、最新 canonical、容量扩张或 live 动作。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --overshoot-survival
```
