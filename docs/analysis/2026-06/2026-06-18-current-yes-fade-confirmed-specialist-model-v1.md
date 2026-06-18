# Current YES Fade-Confirmed Specialist Model v1

Status: shadow-artifact
Generated: 2026-06-18T15:53:05+00:00

Target metric: `current_yes_fade_confirmed_specialist_v1` = 已经从 running max 回落后，当前最高温 bracket 最终是否守住。

## Human Summary

这不是新的信号接收层；它是 fade-confirmed 分支的候选概率层。METAR、盘口、fresh-book guard、city-day cap 继续共用现有 current-YES runner。

线上区别：`fade_confirmed` 会同时记录 base p 和本 artifact 的 shadow p；默认真钱决策仍用通用 v8/v9 artifact。只有显式设置 `FADE_CONFIRMED_MODEL_MODE=specialist` 才会用本 artifact 替换 `p_yes_win`。

## Training Slice

- rows: 396 train / 461 holdout
- active dates: 13 train / 13 holdout
- filter: `decline_c >= 0.5 AND has_d1_no`；训练只用 `period=train`。

## Live-Like Holdout Comparison

| model | orders | dates | YES ROI | win rate | avg ask |
|---|---:|---:|---:|---:|---:|
| base_current_yes_model | 41 | 11 | 18.3% | 95.1% | 0.804 |
| fade_confirmed_specialist | 44 | 12 | 13.7% | 93.2% | 0.819 |

## Outputs

- artifact: `docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/fade_confirmed_model.json`
- metrics: `docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/model_metrics.csv`
- selected holdout rows: `docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/live_like_selected_holdout.csv`
