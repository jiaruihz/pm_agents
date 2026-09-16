# Tokyo final-settlement market-offset stacked training

## 结论

训练已完成，但 `model_training_ok=false`，不改变 live。最佳 expanding-OOF 候选仍是
不含 next-METAR confirmation 的 `compact / ridge=2000`；把 `q_confirm_30m`、T-13/T-3
phase 叠入同一 final-settlement head 后，Brier 与 logloss 都没有增量。因此
`P(next METAR confirms)` 保留为研究 feature，不作 entry hard gate。

## 固定问题与分母

- target：在每个 Tokyo JMA checkpoint，预测最终 exact Tmax 是否离开当前 bracket，输出
  `P(current NO wins)`。
- baseline：同 checkpoint 的 current-exact NO midpoint probability；先用 prior target dates
  拟合 intercept/slope calibration，再与 weather residual 比较。
- 训练市场数据：2026-04-01..07-15，6,661 rows / 103 target dates。
- selection：前30日只训练，后73日按5段 expanding OOF；每段只看更早 target dates。
- 候选数：2个预注册 feature family × 4个 ridge = 8；不按 PnL 选模型。
- 7/16..7/30 的238 rows / 12日已经用于既有研究，只作 post-audit diagnostic，不冒充
  untouched forward。新 frozen-forward 起点登记为 2026-08-06。

## OOF proper score

| model | Brier | logloss | 相对 calibrated market |
|---|---:|---:|---|
| raw market | 0.049156 | 0.166674 | — |
| calibrated market | **0.048005** | **0.164751** | baseline |
| selected compact r2000 | 0.048078 | 0.165714 | Brier +0.000074；logloss +0.000963 |
| stacked confirmation r2000 | 0.048082 | 0.165724 | Brier +0.000077；logloss +0.000973 |

selected compact 相对 calibrated market 的 Brier delta 95% CI 为
`[+0.000004,+0.000153]`；logloss 为 `[-0.000634,+0.001898]`。两项没有同时小于0，
所以 probability gate 失败。stacked 相对同 ridge compact 的 Brier/logloss delta 分别为
`+0.0000036/+0.0000097`，两项 CI 均跨0：confirmation 的净增量约为零，并非一个已证实
可过滤错误单的信号。

## 市场概率不是“绝对真值”

expanding OOF 的 raw midpoint calibration：

| raw market band | mean p | realized NO rate | dates / rows |
|---|---:|---:|---:|
| 45–55% | 50.41% | 44.14% | 37 / 151 |
| 75–85% | 80.30% | 74.39% | 45 / 160 |

这与全103日事后分箱中“75–85%实现率约90.7%”方向相反，说明局部价格带的 calibration
随日期窗口和分箱成员明显漂移，不能把一次全样本分箱当固定映射。prior-date-only calibration
把整体 Brier 从0.049156降到0.048005、logloss从0.166674降到0.164751，但单个价格带仍不稳定；
正确基线是 fold 内校准 market，而不是假设 `price == settlement frequency`。

post-audit direct book 中，可用149个 checkpoint / 12日。NO taker 相对 midpoint 的半点差均值
1.148c、中位0.400c；加官方 fee 后总摩擦均值1.378c、中位0.450c、P90 3.623c。按
`p_model - ask - fee >= 2c` 没有一笔可交易信号。midpoint 上很小的 proper-score 改善不能覆盖
taker 摩擦，更不能直接换算成收益。

## `.7 cross` coverage blocker

7/16..7/30 连续 feature archive 可构造57个“首次 `JMA-current bracket >=0.7`”proxy，
其中2个 terminal false：7/26 `32`、7/29 `34`。但 market holdout 只覆盖其中12个，两个
false trigger 均缺 exact checkpoint book。它们不能被238行 post-audit score 当作已正确判断。
该 proxy 也不是 runner event identity；正式 cross A/B 继续以 raw lineage 的44个信号/13日为准。

## 产物与动作

- runner：`scripts/analysis/market_structure_edge/research_tokyo_overshoot_market_residual_v2.py`
- artifact：`/Volumes/jrs/pm_agents/research/artifact_store/tokyo_final_settlement_stack_20260806/`
- model SHA-256：`be98f22348a6197c03087d4dcdc86e467cc193f27efa9e99ac6a7eda2e6bfffd`
- 测试：`tests/research_tests/test_tokyo_overshoot_market_residual_v2.py`，4 passed。

当前动作是保留 calibrated-market + compact/stacked 作为 zero-notional telemetry，继续采 exact
trigger 同刻完整 book。只有新 frozen target dates 上同时满足 Brier/logloss date-block CI 全负，且
ask+fee+VWAP 后产生正 edge，才重新讨论 live；本轮不部署、不改 Tokyo 下单规则。
