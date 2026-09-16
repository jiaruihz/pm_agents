# D-1 weather-only robust-tail 开发报告 v1

weather-only:
significance=not_significant_vs_v2
calibration=legacy_secondary_holdout_not_clean_forward
pooled_baseline=F_v2_ensemble_location
forward=not_clean; original holdout was previously inspected before this mechanism

market residual:
baseline=market_same_rows
forward=legacy_exploratory_only_weather_gate_not_clean
execution=not_run_no_probability_gate

production:
live_action=none
orders_changed=0

## 数据快照

- legacy summer best-model training slice：5561 rows / 39 cities；不是项目全部历史。
- denominator funnel：`daily_error_rows.csv` 42,705 rows / 52 cities / 738 dates → `is_best_model=true` 16,916 rows / 39 cities → May–Aug slice 5,561 rows / 39 cities / 253 dates。
- reconstructed D-1：开发 233 states / 18 dates；secondary holdout 46 states / 9 dates。
- settlement 来自 canonical `settlement_outcomes`；missing settlement=0，unsettled=0。
- 本轮不是 fill/ROI 研究，missing_bracket 不适用；market 仅作同 rows probability baseline。

## 结论

开发集在 K=600 个预定义组合中选择 consensus=mean、ensemble weight=0.875、bias multiplier=1.00、residual scale=1.25、climatology mix=0.02。
secondary holdout 上 G logloss=1.8506，F v2=1.9763，paired Δ=-0.1257（95% CI -0.2994..+0.0130）。相对 market Δ=+0.3009（+0.0815..+0.5196）。

动作：把 G 锁定为 W0 legacy reference，不再读取这 9 个已查看日期调参数；它不是新 W1/M2/M3 的最终冻结模型。W1 必须先在 clean exact-run development 上完成 revision/spread/run-age 模型选择，随后 M2/M3 才能在同一 development 分母上选择正则，二者评审后再产生新的 freeze artifact 与 untouched forward。

| arm | logloss | Brier | RPS | winner P | top-1 |
|---|---:|---:|---:|---:|---:|
| market | 1.5497 | 0.0684 | 0.0624 | 0.257 | 28.5% |
| F_v2 | 1.9763 | 0.0771 | 0.0903 | 0.200 | 21.9% |
| H_location_only | 1.8975 | 0.0763 | 0.0840 | 0.204 | 27.9% |
| G_scale_only | 1.8487 | 0.0751 | 0.0831 | 0.191 | 27.9% |
| G_climate_only | 1.8911 | 0.0762 | 0.0836 | 0.202 | 26.8% |
| G_robust_tail | 1.8506 | 0.0751 | 0.0828 | 0.189 | 26.8% |

## Calibration / tail

- G rung ECE=0.0160，F=0.0269，market=0.0196。
- G winner≤1% states=1，F=2。
- G bottom tail predicted/actual=1.0%/2.2%；top=1.1%/0.0%。
- location-only 已把 top-1 从 F 的 21.9% 提到 27.9%；scale=1.25 进一步修复过度自信。2% climatology mix 主要作极端档保底，在 secondary holdout 上没有独立 logloss 增益。

## D-1 checkpoint market efficiency

在完全相同的 city-date 与 native ladder 上配对 243 个状态 / 26 个日期。12–18h checkpoint market logloss=1.5743，18–24h=1.5306；late-minus-early=-0.0436（95% CI -0.0870..-0.0027）。
两 checkpoint 平均相隔 5.92 小时，market ladder total variation 均值=0.079；assigned forecast 绝对变化均值=1.059°F，ensemble mean 绝对变化均值=0.450°F。这里 later book 只用于判断市场信息效率，不会事后替换 early decision book。

## 证据边界

- 改动只作用于 weather distribution；没有使用 market、first_seen reaction、ROI 或价格切片选参数。
- 原 9-date holdout 在提出本机制前已被查看，因此本报告只算 secondary exploratory validation，不重新标成 frozen forward。
- K=600 未做多重检验校正；这也是必须停止 legacy 调参并转 clean forward 的原因。
- 当前参数只锁定为 W0 legacy reference。新 exact-run 数据先划出 clean development 供 W1 与 M2/M3 模型选择；只有显式生成新 freeze artifact 之后的日期才属于 untouched forward。
- 8环中本轮覆盖统计推断、概率分布、同分母 market baseline；不覆盖执行、容量、fills 或 live 动作。

W0 锁定参考参数见 [`2026-08-05-d1-weather-only-clean-forward-freeze-v1.json`](2026-08-05-d1-weather-only-clean-forward-freeze-v1.json)；文件名沿用既有审计身份，但不代表 W1 或 market residual 已冻结。

## Market-offset exploratory

固定同一批 rows、labels 和 reconstructed market distribution，使用 `log P_post = log P_market + beta * (log P_weather - log P_market) - log Z`。beta=0 严格退化为 M0 market；beta 只在前 18 个开发日期选择。M3 的 source 与 city×source beta 进一步向 global beta 强收缩。

开发集选择 global beta=0.050；以下仍是已经看过的 9-date secondary holdout，不是 clean forward。

| arm | logloss | Brier | RPS | winner P | top-1 | Δlogloss vs M0 (95% CI) |
|---|---:|---:|---:|---:|---:|---:|
| M0_market | 1.5497 | 0.0684 | 0.0624 | 0.257 | 28.5% | reference |
| M1_weather | 1.8506 | 0.0751 | 0.0828 | 0.189 | 26.8% | +0.3009 (+0.0815..+0.5196) |
| M2_global_offset | 1.5546 | 0.0685 | 0.0628 | 0.254 | 28.5% | +0.0049 (-0.0052..+0.0149) |
| M3_partial_offset | 1.5540 | 0.0684 | 0.0629 | 0.253 | 28.5% | +0.0043 (-0.0097..+0.0175) |

判定只看 M2/M3 相对 M0：若 paired CI 未整体低于 0，就没有可确认的 market residual；weather-only 相对自身旧版的改善不能替代这个条件。当前 market 是 reconstructed/non-executable probability baseline，本轮不计算 ask、fee、slippage、depth 或 ROI。
