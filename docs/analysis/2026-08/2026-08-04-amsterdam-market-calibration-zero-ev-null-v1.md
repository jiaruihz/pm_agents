# Amsterdam market calibration and zero-EV null v1

> Executable follow-up：生产 full-book ask/depth 的 5/10-share taker 重算见
> [Amsterdam executable market null v2](2026-08-04-amsterdam-executable-market-null-v2.md)。v1 sampled-price
> proxy 仅保留 probability/null 参考，不再作为真实 taker 成本。

Status: `market baseline supported / historical price reference / non-executable`

## 结论

在不使用任何天气模型的 112 个 Amsterdam 历史日期上，market-following 的 gross return 与 0 一致；扣 Weather fee 后点估转负，所有可选边策略的 target-date bootstrap CI 均跨 0。该结果支持“市场价格是强概率基准、无新增信息时 gross EV≈0”的零假设。

它不证明每个报价都正确，也不证明不存在 KNMI event residual；它说明任何新模型必须在 settlement label 上、同 rows、扣成本后打败 market，而不是仅仅比 market 更复杂。

本研究是 Amsterdam 专项复核；项目已有更宽的跨城基准：49 个结算日 × 36 城 × 5 expressions、53,587 rows 上，market mid 在几乎所有价格带的 calibration bias 小于 1.5c，60 个 taker expression×price cells 中没有一个 fee-adjusted CI 显著为正。两组证据方向一致。

## 理论零假设

若 NO token 的价格为 `q`，真实胜率为 `p`，买一 share 的 gross expected PnL 为：

```text
E[PnL] = p - q
```

若市场校准，即 `p=q`，gross EV 为 0。若实际支付 ask `a>=q` 和 fee `f`：

```text
E[PnL_net] = p - a - f <= -f
```

完整 ladder 同理：如果每档真实概率等于归一化价格，任何不包含额外信息的固定组合 gross EV 都为 0；同时买齐全部互斥档位的 gross cost 与 payout 都约为 1，spread/fee 后必亏。

## 数据与 grain

- window：2026-04-03..2026-07-29。
- coverage：11,800 checkpoints / 112 target dates。
- probability：`1 - coherent normalized full-ladder q0`，即最终离开 current bracket 的 market probability。
- label：历史 EHAM daily-max proxy；不是 canonical settlement fact。
- price evidence：Polymarket timestamped sampled price reference；没有 bid/ask、spread、depth 或 fill，因此不是 executable ROI。
- grains：全 checkpoint、transition、每日首次 state entry；所有 CI 按 target_date block bootstrap 4,000 draws。

## 市场概率是否校准

| Grain | Rows / dates | Market mean p | Realized rate | Brier | Logloss | 0.5方向准确率 |
|---|---:|---:|---:|---:|---:|---:|
| checkpoint | 11,800 / 112 | 49.39% | 49.10% | 0.04362 | 0.14144 | 93.45% |
| transition | 4,621 / 112 | 59.96% | 58.86% | 0.06819 | 0.21793 | 90.02% |
| state entry | 581 / 112 | 80.98% | 79.42% | 0.06986 | 0.22589 | 90.40% |

aggregate calibration 误差约 0.3–1.6 percentage points，说明 market 是很强的基准。高方向准确率受大量日内接近确定状态影响，不能解释成可交易收益。

## 无信息交易零假设

两种非天气 policy：

- `favorite`：每行买 market probability 大于 50% 的一侧；
- `randomized`：以 market probability 选择 NO，否则选择 YES；结果对随机选择做解析期望，不进行 Monte Carlo 抽边。

价格使用理论 market probability；`net` 额外扣 `0.05*p*(1-p)` Weather taker fee，但仍没有真实 spread。

| Grain / policy | Gross ROI | 95% CI | Fee-adjusted proxy ROI | 95% CI |
|---|---:|---:|---:|---:|
| checkpoint favorite | -0.583% | [-2.006%, +0.727%] | -0.807% | [-2.236%, +0.511%] |
| checkpoint randomized | -0.015% | [-0.948%, +0.819%] | -0.247% | [-1.187%, +0.585%] |
| transition favorite | -1.338% | [-3.615%, +0.821%] | -1.672% | [-3.948%, +0.481%] |
| transition randomized | -0.467% | [-2.059%, +0.950%] | -0.818% | [-2.408%, +0.602%] |
| state-entry favorite | +1.182% | [-0.908%, +3.289%] | +0.848% | [-1.235%, +2.933%] |
| state-entry randomized | +0.526% | [-0.677%, +1.658%] | +0.180% | [-1.015%, +1.297%] |

所有可选边 policy 的 CI 都包含 0；checkpoint randomized gross ROI 为 `-0.015%`，最接近理论零值。state-entry 点估为正但 CI 跨 0，不能解释为 market-follow alpha。

同时买 binary 两侧的 gross PnL 按构造恒为 0；仅扣 fee 后 checkpoint proxy ROI `-0.423%`，95% CI `[-0.461%,-0.387%]`，验证成本会把无信息组合推到负期望。

## 这如何避免“加入 market 后当然更像 market”的循环论证

market-aware 模型的评分目标不是 market，而是最终 outcome：

```text
market prediction -> settlement loss
candidate prediction -> settlement loss
paired delta = candidate loss - market loss
```

只有 candidate 在 untouched holdout/frozen forward 的 paired Brier/logloss 明确低于 market，才说明它不只是复制 market。若 candidate 完全收缩为 market，paired delta 就是 0，不会通过 baseline gate；即使概率准确，也没有可交易 residual，支付 fee 后为负。

Amsterdam market-prior prototype 在 July historical holdout 上的 binary logloss delta 为 `-0.00332`，95% CI `[-0.00644,-0.00082]`，这是相对真实 label 的小幅改善，不是“离 market 更近”的几何指标。但它仍是 post-hoc sampled-price research，没有 executable book 和 clean forward，因此只算方向性证据。

## 动作

- market calibration / zero-EV null 成为 Amsterdam 后续模型的固定 baseline。
- V7 weather-only、market-only、market-prior posterior 必须在完全相同的 first-seen rows 与 settlement label 上比较。
- 下一步重点不是继续无条件增加 market 权重，而是估计 KNMI first-seen 前后 market 尚未吸收的 `event innovation`，并用 fresh ask、fee、markout/settlement 分开验证。
- 不改 live；继续 zero-notional collector。

## 可复跑产物

- `scripts/analysis/forecast_quality/research_amsterdam_market_null_v1.py`
- `docs/analysis/2026-08/generated/amsterdam_market_null_v1/summary.json`
- `docs/analysis/2026-08/generated/amsterdam_market_null_v1/calibration_by_grain.csv`
- 跨城基准：`docs/analysis/2026-07/2026-07-15-market-calibration-curve-v1.md`
