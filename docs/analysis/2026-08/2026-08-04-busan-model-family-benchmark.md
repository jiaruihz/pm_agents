# Busan model-family benchmark

## 结论

不同模型族已经在完全相同的PIT rows、labels、date splits和quotes上比较。development最优是 `p_factorized_random_forest_full_weather`，但在同盘口18 states/5天上logloss `0.6419`，仍差于market `0.3665`；selected−market delta `+0.2755`，95% CI `[-0.1010, +0.7441]`。因此问题不只是“没换高级算法”：当前最大的限制是独立日期与conditional-reheat证据不足。

### 2026-08-08 锁模 forward 更新

8/3 后不再重选模型或更新参数。8/4–8/7 已补齐 canonical settlement 后，同盘口 26 states/4天的固定冠军 logloss `0.6955`，market `0.3106`；model−market `+0.3849`，95% CI `[-0.0893,+1.0335]`。13 个正 edge date-rung replay PnL `-$3.4298`、ROI `-6.42%`，95% CI `[-23.02%,+12.85%]`。四天仅 8/7 胜 market，因此 `forward=FAIL`，继续 collector/research，不部署 Busan probability adapter。完整快照见 [8/8 forward performance](2026-08-08-korea-model-forward-performance-v1.md)。

### 2026-08-12 追加日期更新

固定同一 8/3 champion 重放到 8/11：同盘口 `65 states/8天`，model logloss `0.2913`、market
`0.2902`，delta `+0.0011`，95% CI `[-0.2181,+0.2618]`；24 单 replay ROI `+10.26%`，
CI `[-8.32%,+30.81%]`。新增 8/8–8/11 四日单独显著为正，但与前四日方向相反；因此新增数据有价值，
模型从“明显落后 market”变成“全窗基本打平”，仍未达到全 forward significance。完整分母和 WS 边界见
[8/12 追加结果](2026-08-08-korea-model-forward-performance-v1.md#2026-08-12-追加新增日期确实提供了正证据但尚未确认-alpha)。

## 固定研究设计

- target：`P(final exact current routine rung NO | PIT source/path/weather state)`。
- grain：15-minute pending source-cross state；exact bracket，不是touch。
- development：86 states/6 dates；8/4只作untouched next-routine forward，不参与选择。
- market aligned：18 states/5 dates。
- K=18个candidate；未做multiple-testing校正，因此最优模型仍只算探索性。

模型族包括ridge、elastic net、二次spline、regularized histogram gradient boosting、random forest、extra trees；同时比较factorized two-stage与direct exact-NO。特征按basis → basis+path → full weather预先分组，没有按单个case补feature或threshold。

full-weather已覆盖全部 `159/159 states、13天`；其中PIT archive回填 `79 states/7天`，checkpoint原生携带 `80 states/6天`。 回填严格使用 `forecast_available_at_utc <= decision_ts_utc`，没有使用未来revision。即使weather coverage完整，独立日期仍少，因此full-weather模型的领先仍未得到可靠验证。

按日期看最优candidate为 `[{"target_date": "2026-07-29", "winner": "p_factorized_hist_gradient_full_weather", "winner_logloss": 0.02211914762341282, "selected_logloss": 0.035925405672933064}, {"target_date": "2026-07-30", "winner": "p_factorized_random_forest_full_weather", "winner_logloss": 0.1851535045326209, "selected_logloss": 0.1851535045326209}, {"target_date": "2026-07-31", "winner": "p_factorized_geometry_beta", "winner_logloss": 0.255514925618905, "selected_logloss": 0.32375364593911937}, {"target_date": "2026-08-01", "winner": "p_direct_random_forest_full_weather", "winner_logloss": 0.37486627705239395, "selected_logloss": 0.47452877041144614}, {"target_date": "2026-08-02", "winner": "p_direct_random_forest_full_weather", "winner_logloss": 0.011563837767943806, "selected_logloss": 0.0189131283320982}, {"target_date": "2026-08-03", "winner": "p_factorized_random_forest_full_weather", "winner_logloss": 0.009151370332792303, "selected_logloss": 0.009151370332792303}]`；development冠军只赢了 `2/6` 个日期，说明模型排名尚不稳定。selected在同盘口AUC虽高，但logloss差，主要问题是概率校准/过度自信，不是缺少更复杂的分类器。

## Development排名

| candidate | expression | logloss | Brier | AUC |
|---|---|---:|---:|---:|
| p_factorized_random_forest_full_weather | factorized | 0.1746 | 0.0622 | 0.9944 |
| p_factorized_extra_trees_full_weather | factorized | 0.1789 | 0.0599 | 0.9844 |
| p_direct_random_forest_full_weather | direct | 0.1884 | 0.0639 | 0.9952 |
| p_direct_extra_trees_full_weather | direct | 0.1935 | 0.0667 | 0.9922 |
| p_factorized_ridge_full_weather | factorized | 0.2082 | 0.0695 | 0.9993 |
| p_factorized_geometry_beta | factorized | 0.2082 | 0.0644 | 0.9575 |
| p_factorized_ridge_basis_path | factorized | 0.2275 | 0.0747 | 0.9967 |
| p_factorized_ridge_basis | factorized | 0.2374 | 0.0785 | 0.9993 |

## 同盘口与交易表达

- selected同盘口logloss `0.6419`，market `0.3665`。
- positive-edge research replay：6单/4天，PnL `$-1.9007`，ROI `-8.68%`，CI `[-1.0, 0.11599531281968614]`；不是actual fills。
- signal funnel：159 pending states/13天 → 86 development OOF/6天 → 6 selected expressions/4天。
- evidence funnel：86 settlement states/6天 → 18 PIT quote states/5天 → 6 executable replay/4天 → 0 actual fills。

## 科研判断

1. 非线性模型是否能解决：本轮已直接检验；如果树/样条只在development排名靠前、却不能在market-aligned/frozen复现，就不能称为更合适。
2. feature selection是否能解决：basis、path、full weather的ridge ablation已在同分母比较；只报告OOF，不看训练拟合。
3. 下一步不继续无序换模型。保持这个固定tournament，新增日期只append并重跑；conditional nonconfirmation达到预注册日期数后，才允许训练独立reheat head。

验收（2026-08-08 更新）：`significance=FAIL`，`baseline=FAIL`，`forward=FAIL`，`conclusion=inconclusive`；不改live，不启动概率shadow。
