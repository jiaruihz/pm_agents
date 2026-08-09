# Korea IEM remaining-heat distribution model v1

## 数据快照

- source: IEM ASOS archive RKSI/RKPK；历史 observation-time proxy，
  first-seen unknown，不冒充 AMOS collector-exact PIT。
- window: `2026-05-12..2026-07-28`。
- raw observations: `5,933`。
- hourly states: `1,404` / `78` target dates /
  `156` city-days。
- train: `1,152` states / `64` dates；
  frozen holdout: `252` states /
  `14` dates。
- settlement: canonical `settlement_outcomes`；missing/unsettled rows未进入label。

## Target

每个城市当地 09–17 点、每小时最后一份 routine-airport state，预测最终 winning
market bracket 相对当时 routine running max 的 lattice offset：
`negative / zero / +1 / +2 / +3以上`。

## Frozen holdout proper score

| model | multiclass logloss | multiclass Brier | accuracy |
|---|---:|---:|---:|
| train class prior | 1.234490 | 0.631178 | 54.76% |
| regularized weather model | 0.930365 | 0.480672 | 60.32% |

Model − prior logloss:
`-0.304125`，target-date block 95% CI
`[-0.375167, -0.211007]`。负值才表示改善。

## 模型维度

city、local hour、current/running-max temperature、距高点分钟、1h/3h温变、
RH、露点差、风速风向、云底/sky code、能见度、季节项。没有使用后到的天气或
settlement 字段作特征。

## 边界

该模型负责长期物理分布与机制预训练；只有进入 AMOS + 同刻盘口 overlap 后，
才能检验 `P(outcome)-market` residual 和 fee-adjusted order ROI。它不会把
historical observation timestamp 写成 collector first-seen。

## 结论

`physical_significance=PASS physical_baseline=PASS`
` market_baseline=NA physical_forward=PASS`
（冻结日期仅用于物理 prior，不是 market alpha）；
conclusion=`inconclusive`，动作=作为 Korea AMOS residual 的物理 prior challenger，
继续 zero-notional，不改 live。
