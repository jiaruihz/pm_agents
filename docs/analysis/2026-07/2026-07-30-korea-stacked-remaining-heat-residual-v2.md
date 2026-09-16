# Korea stacked remaining-heat residual v2

## 数据快照

- physical source：`/Volumes/jrs/pm_agents/research/korea_iem_remaining_heat/v1/hourly_states.csv.gz`；IEM observation-time proxy，
  first-seen unknown。
- residual source：`/Volumes/jrs/pm_agents/research/korea_intraday_residual/v4/full_train_holdout_opportunity_replay.csv`；AMOS first-seen state + archived
  executable taker book + canonical settlement。
- generated：`2026-07-30T16:29:27.086898+00:00`。
- trade_class：`research_replay`，actual fills=`0`。
- residual holdout：`174` states /
  `8` target dates /
  `15` city-days；settled ratio `100%`，
  unsettled `0`，missing_bracket `0`。

## 串联与时间隔离

1. portable remaining-heat prior 只用
   `2026-05-12..2026-07-07`
   IEM labels 训练；
2. 在 `2026-07-08..2026-07-28`
   做 physical historical audit；
3. prior 对 AMOS 每个 PIT state 输出五档概率，再映射 favorite 所在 offset bucket；
4. residual 用 `2026-07-08..2026-07-20`
   训练，在 `2026-07-21..2026-07-28`
   做 historical holdout audit。

因此 residual train/holdout 都没有进入 physical prior 的训练标签。

## Physical prior audit

| model | logloss | multiclass Brier | accuracy |
|---|---:|---:|---:|
| train class prior | 1.174562 | 0.598467 | 59.26% |
| portable remaining-heat | 0.881829 | 0.450056 | 63.49% |
| routine clock/running-max prior | 0.889806 | 0.452427 | 62.43% |

Model − prior logloss：
`-0.292733`，
date-block 95% CI `[-0.367881, -0.200350]`。

Portable prior features：
`local_hour, temp_c, running_max_c, minutes_since_running_max, relative_humidity_pct, dewpoint_depression_c, wind_speed_kt, day_of_year_sin, day_of_year_cos, city`。

Routine-transfer prior features：
`local_hour, running_max_c, day_of_year_sin, day_of_year_cos, city`；它保持 label 与
settlement-facing routine running max 的同一 lattice，AMOS 只进入下一层
residual，不再拿 source max 重新定义 target bucket。

## Residual historical holdout

这个窗口在结构映射调试中被重复查看，因此只记 historical audit，
`forward=NA`；它可以否定当前模型，但不能用来晋升策略。

| model | Brier | logloss | Brier delta vs raw market (95% CI) |
|---|---:|---:|---:|
| raw_market | 0.158985 | 0.448042 | baseline |
| physical_bucket | 0.238254 | 0.722344 | +0.079269 [+0.033102, +0.122527] |
| routine_bucket | 0.267930 | 0.753311 | +0.108945 [+0.050775, +0.157724] |
| market_calibrator | 0.176659 | 0.538427 | +0.017675 [-0.010323, +0.059831] |
| market_plus_physical | 0.187953 | 0.554078 | +0.028968 [+0.011066, +0.047509] |
| market_plus_routine_prior | 0.196936 | 0.569311 | +0.037951 [+0.015475, +0.066684] |
| market_plus_short | 0.203199 | 0.584421 | +0.044214 [+0.023864, +0.075064] |
| market_plus_physical_short | 0.215153 | 0.615780 | +0.056168 [+0.029558, +0.085735] |
| market_plus_routine_short | 0.220359 | 0.620280 | +0.061374 [+0.033049, +0.097293] |
| market_offset_physical | 0.208082 | 0.596707 | +0.049097 [+0.007799, +0.092213] |
| market_offset_routine_prior | 0.209847 | 0.570346 | +0.050862 [+0.011527, +0.084714] |
| market_offset_short | 0.234276 | 0.666629 | +0.075291 [+0.028589, +0.121137] |
| market_offset_physical_short | 0.246765 | 0.728387 | +0.087780 [+0.033142, +0.134911] |
| market_offset_routine_short | 0.246845 | 0.709659 | +0.087861 [+0.032741, +0.139633] |

负 delta 才是优于 raw market。

`market_offset_*` 把 raw market logit 固定为 offset，只学习天气 residual；
收缩强度仅在 residual train 内按 expanding-forward target-date logloss 选择，候选
包含 `null`（完全退回 raw market）。选择结果：
`market_offset_physical=0.1, market_offset_routine_prior=0.1, market_offset_short=0.1, market_offset_physical_short=0.1, market_offset_routine_short=0.1`。

## Fee-adjusted expression replay

固定规则：每个模型在每个 state 比较 favorite YES 与 NO 的
`p_win - ask - official Weather fee`；最大 edge `>0` 才产生 signal，
每 city-day 只取首个，最多 5 shares。不是实际成交。

| model | orders | dates | wins | fee PnL | ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| raw_market | 0 | 0 | 0 | $+0.0000 | NA | NA |
| physical_bucket | 12 | 8 | 8 | $+11.0968 | +42.50% | [-2.23%, +72.48%] |
| routine_bucket | 10 | 7 | 5 | $+3.8850 | +18.40% | [-29.69%, +76.29%] |
| market_calibrator | 14 | 8 | 7 | $+6.7333 | +23.82% | [-49.29%, +96.42%] |
| market_plus_physical | 12 | 8 | 7 | $+10.0851 | +45.58% | [-37.26%, +117.37%] |
| market_plus_routine_prior | 11 | 8 | 6 | $+9.0301 | +55.81% | [-28.59%, +137.53%] |
| market_plus_short | 11 | 7 | 6 | $+8.3535 | +38.59% | [-51.90%, +111.37%] |
| market_plus_physical_short | 11 | 7 | 7 | $+12.8963 | +61.58% | [-14.50%, +111.48%] |
| market_plus_routine_short | 10 | 7 | 5 | $+5.9906 | +37.80% | [-46.38%, +110.13%] |
| market_offset_physical | 14 | 8 | 12 | $+7.2144 | +13.67% | [-12.78%, +44.78%] |
| market_offset_routine_prior | 15 | 8 | 14 | $+2.5117 | +3.81% | [-10.30%, +15.97%] |
| market_offset_short | 10 | 8 | 7 | $+0.4970 | +1.44% | [-42.49%, +50.35%] |
| market_offset_physical_short | 11 | 8 | 7 | $+0.2270 | +0.65% | [-50.80%, +43.76%] |
| market_offset_routine_short | 10 | 8 | 7 | $+0.4970 | +1.44% | [-42.26%, +50.71%] |

## 双漏斗

Signal funnel：

`483 joined states → model continuous probabilities → positive fee-adjusted
expression → first city-day signal`。

Evidence funnel：

`35,469 AMOS states → 885 book states → 483 joined executable states
→ 174 historical holdout expressions → 0 actual fills`。

Seoul `07-08..14` 与 `07-28` 仍是历史盘口 coverage gap，不是策略筛除。

## 结论

primary stacked candidate `market_offset_routine_short` 相对 raw market 的 Brier delta
`+0.087861`
（95% CI `[+0.032741, +0.139633]`），logloss delta
`+0.261617`
（95% CI `[+0.094451, +0.406495]`）。

`significance=FAIL
baseline=FAIL forward=NA
conclusion=stacked_residual_does_not_pass_market_baseline`。

动作：长历史 prior 已经真正接入 residual 并完成重训；当前 stacked residual
明确输给 raw market，不进入 zero-notional/live，不据 selected ROI 绕过
probability baseline。

## Semantic follow-up（2026-07-31）

后续逐时点审计确认：五档 remaining-heat prior 本身和为1，但最终
`market_offset_*` head 是随 market favorite 变化的 binary residual，不是固定
exact-bracket full-ladder distribution。它不能保证同一时点各档概率和为1，也不能
在 favorite 换档后继续重估原持仓。详见
`docs/analysis/2026-07/2026-07-31-korea-stacked-residual-semantic-audit-v1.md`。

因此新增 `model_semantics=FAIL`：保留已声明窗口的 IEM physical prior，当前 residual head
停止用于 entry/position 优化，下一版改为 fixed-ladder joint distribution。
