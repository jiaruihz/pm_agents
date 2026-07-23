# d1 overshoot 漏判机制审计 v1

日期：2026-07-23  
状态：`completed / mechanism identified / not a live gate`

## 结论

修正 forecast lineage、peak-clock 与 Celsius settlement lattice 后，当前 p75 hazard
仍漏掉的 overshoot 是：

- historical forward：Shanghai 2026-06-30；
- clean live：Kuala Lumpur 2026-07-21。

两条不是互不相关的特殊天气，而是同一机制：

```text
fixed CITY_MODEL forecast 已低于当前 running max
+ 当前仍在 active warming（3h trend > 0、没有回落）
= forecast-busted active warming
```

模型仍把负的 `forecast_gap/ceiling` 当作“不会继续升温”的证据；但 forecast 已被事实
打破后，这些字段应降权，剩余风险主要由 path continuation、market residual 和
source/cadence 决定。

这个机制不能直接变成 hard filter：冻结 218 行中它选出 88 行，只包含 6 个
overshoot、同时包含 82 个 exact-d1 winners；43 行 historical OOF forward 中它抓到
唯一 overshoot，但也会删掉 20 个赢家。

## 为什么前一版会把问题看错

漏判审计发现三个 feature-lineage 问题，并已在研究脚本中修正：

1. **expression forecast misalignment**：atlas 的混合 forecast 字段可能来自同一
   state 的另一条 YES/NO expression。按项目固定 `CITY_MODEL` 回连各 source factory
   的 GFS/ECMWF Single Runs 列后，11,554 个机制 rows 中有 3,684 行原 peak-clock
   与固定模型的 `decision_hour - peak_hour` 相差超过 1 小时。9 个 source files 的
   19,176 个 state groups 内部 dual-model 字段冲突为 0。
2. **live peak-clock 符号**：正确定义是 `decision - peak`，正数表示 peak 已过。
   首版 live enrichment 写成了 `peak - decision`。
3. **Celsius settlement lattice**：`27.78°C` 会结算在 `28` 档，不能把进入 28 档
   的连续阈值写成 `28.0°C`。修正为 half-up lattice 后，7,676 个有 forecast 的
   Celsius 机制 rows 中有 356 行的 forecast d2-ceiling 状态从负翻到非负。

这些修正没有把模型变成 alpha；同分母 market 仍显著优于 physics/path/regime。
它们的作用是确保下面的“机制还是特例”判断建立在正确 PIT 特征上。

## 两条真正漏判

| case | fixed forecast gap | peak clock | 3h trend | high clock vs obs age | path p(over) / cutoff | 判定 |
|---|---:|---:|---:|---:|---:|---|
| Shanghai 6/30 | `-0.67°C` | peak ahead `1h` | `+3.6°F` | `49.45m / 49.45m` | `0.190 / 0.328` | forecast-busted active warming |
| Kuala Lumpur 7/21 | `-2.60°C` | peak passed `0.57h` | `+9.0°F` | `31.87m / 31.87m` | `0.274 / 0.328` | forecast-busted active warming |

两行的 `minutes_since_running_max` 都等于 observation age，说明期间没有新的 post-high
observation。这个 clock 是右删失的“等下一报”，不是已经连续 30–50 分钟没有创新高。
因此它不能作为 stall/plateau evidence。

这是需要补的机制特征：

```text
post_high_observation_count
high_clock_censored_by_obs_age
forecast_busted_active_warming
forecast residual after bust
```

修复方式应是对 clock 做 censoring、对已被打破的 forecast 降权，并学习连续 residual；
不是追加 `Shanghai/Kuala Lumpur` 城市黑名单。

## Taipei 是不是特殊情况

Taipei 确实有独立的重复风险：

- frozen historical：4 overshoots / 9 signals；
- 其他城市：7 / 209；
- 单侧 Fisher exact odds ratio `23.09`，未校正 p=`0.00040`；
- 加上 clean live 后，Taipei 为 5 overshoots / 10 signals。

但这仍不足以把城市名做成新 hard gate：样本只有 10，且这是看过城市切片后的统计。
现有执行本来就把 Taipei 保持 shadow，这个动作不需改变。

在修正后的模型中，Taipei 7/19：

- fixed GFS forecast 对 d2 settlement threshold 尚有 `+0.8°C` ceiling；
- path-only `p_overshoot=0.458`，高于 frozen p75 cutoff `0.328`；
- 已被风险模型抓到。

因此 Taipei 这次不是“模型还缺一种新天气机制”，而是前版 mixed forecast、live
peak-clock 符号、missing trend/regime parity 共同造成的实现/数据问题。城市的长期
forecast/source bias 应作为 hierarchical calibration 特征继续收集。

## 全部 failure 的结构

冻结 historical 有 11 个 overshoot + 1 个 stall，clean live 有 3 个 overshoot +
1 个 stall，共 16 个 failure cases：

- `forecast-busted active warming`：7；
- Taipei：5；
- 两者并集：10；
- 剩余 overshoots 主要是 forecast runway + active warming；Wuhan 等已被连续 hazard
  排在高风险，不构成另一个清晰漏判机制。

所以结论不是“都是特殊情况”，而是：

1. 主要漏判来自一个共享的 forecast-bust/path-continuation 机制；
2. Taipei 另有重复的城市/source calibration 风险；
3. p75 hard cutoff 本身不稳，距离 cutoff 很近的 Kuala Lumpur 说明阈值化会制造
   人为漏判；最终仍应比较 `p_exact - executable cost`。

## 动作

- 不新增 city/regime hard gate，不改 live。
- 在共享 feature layer 增加 post-high observation/censoring 与 forecast-bust residual；
  `minutes_since_running_max` 在没有 post-high observation 时不得解释为 stall duration。
- d1 journal 保存 trend、fixed CITY_MODEL issue/run/hash/age、regime 与 observation
  sequence；当前 9 个 live rows 的 high clock 全部等于 obs age，不能验证 stall。
- 下一版只做 `market + compact residual` OOF；先赢同分母 market proper score，再看
  fee ROI。

## 复现

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_d1_overshoot_hazard_v1.py
```

产物：

- `generated/d1_overshoot_hazard_v1/failure_mechanism_cases.csv`
- `generated/d1_overshoot_hazard_v1/summary.json` 的 `miss_mechanism_audit`
- `generated/d1_overshoot_hazard_v1/historical_state_rows.csv`
- `generated/d1_overshoot_hazard_v1/clean_live_input_rows.csv`
