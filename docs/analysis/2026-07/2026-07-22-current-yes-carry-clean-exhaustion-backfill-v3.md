# Current-YES Carry clean heating-exhaustion 历史冻结回填 v3

Status: `historical_report_time_PIT_proxy / frozen_feature_ablation / no-live-change`
Generated: `2026-07-22T09:19:28+00:00`

## 直接回答

可以回测，而且已经回填完成。之前的“只有 7月22日”仅指新版 shadow ledger 的 live first-seen telemetry；不是历史 observation/forecast 不存在。36 城的逐报 IEM/METAR 与固定 CITY_MODEL Single Runs 小时曲线足以重建 strict-new-high、太阳和 remaining runway。TAF transition 因没有历史 first-seen 留档，本轮排除。

## 历史覆盖

- Universe：4061 states，46 target dates，2026-05-19..2026-07-08。
- strict-new-high：4061/4061 (100.0%)。
- solar：4061/4061 (100.0%)。
- forecast remaining runway：4061/4061 (100.0%)。
- exhaustion index：4061/4061 (100.0%)。

## 同分母 expanding OOF 结果

所有模型仍以 market 为基准，测试日只用严格更早日期训练；carry domain 固定为 market midpoint≥0.80，交易为每 city-day 首个 fee 后正 taker EV。

| model | first EV n | win | avg ask | taker ROI [95%CI] | Brier Δ vs core [95%CI] | logloss Δ vs core [95%CI] | front/back ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_cal | 0 | NA | NA | NA [NA,NA] | — | — | NA/NA |
| core | 148 | +95.27% | 0.908 | +4.47% [+0.65%,+7.91%] | — | — | +3.75%/+5.52% |
| core_plus_strict_high | 175 | +94.86% | 0.916 | +3.12% [+0.22%,+6.04%] | -0.00023 [-0.00093,0.00045] | -0.00035 [-0.00327,0.00260] | +1.94%/+4.71% |
| core_plus_solar | 165 | +95.15% | 0.911 | +4.02% [-0.02%,+7.47%] | 0.00027 [-0.00040,0.00088] | 0.00140 [-0.00128,0.00408] | +3.01%/+5.48% |
| core_plus_runway | 158 | +96.20% | 0.909 | +5.36% [+1.36%,+8.64%] | 0.00045 [-0.00050,0.00131] | 0.00137 [-0.00177,0.00426] | +4.05%/+7.12% |
| market_plus_clean_exhaustion | 86 | +88.37% | 0.905 | -2.79% [-13.65%,+5.11%] | 0.00173 [-0.00080,0.00448] | 0.00831 [-0.00133,0.01832] | -2.92%/-2.59% |
| core_plus_clean_exhaustion | 165 | +95.76% | 0.916 | +4.17% [+0.30%,+7.53%] | 0.00070 [-0.00036,0.00193] | 0.00210 [-0.00205,0.00670] | +2.60%/+6.84% |
| core_plus_exhaustion_index | 166 | +95.78% | 0.914 | +4.36% [+0.51%,+7.77%] | 0.00080 [-0.00005,0.00170] | 0.00305 [-0.00016,0.00640] | +3.74%/+5.40% |

## 结论

旧 core：148 city-days，ROI +4.47%。加入完整 clean exhaustion components 后：165 city-days，ROI +4.17%。
相对 core 的 proper-score delta：Brier 0.00070 CI[-0.00036,0.00193]；logloss 0.00210 CI[-0.00205,0.00670]。负值才表示 clean exhaustion 有增量。

Clean exhaustion 在本次同分母历史检验没有改善 core 的两项 proper score；正确物理语义已回填，但不因语义合理就强行入模。继续 forward 记录用于验证，不替换 core。

这次历史 observation 的可见性只能按 report timestamp 近似，不能恢复真正 ingest first-seen latency；所以它比事后天气切片严格，但仍需用 7月22日起的 forward ledger 做最终 production-parity 验证。

## 产物

- Script: `scripts/analysis/reheat_risk/research_current_yes_carry_clean_exhaustion_backfill_v3.py`
- JSON: `docs/analysis/2026-07/2026-07-22-current-yes-carry-clean-exhaustion-backfill-v3.json`
- Historical clean states: `docs/analysis/2026-07/generated/current_yes_carry_clean_exhaustion_backfill_v3/historical_clean_exhaustion_states.csv`
- OOF predictions: `docs/analysis/2026-07/generated/current_yes_carry_clean_exhaustion_backfill_v3/oof_predictions.csv`
