# d1 overshoot hazard v1：三态统一模型与 regime 过滤检验

日期：2026-07-23  
状态：`completed / negative model result / keep research, no live change`

## 结论

`d1_overshoot_hazard_v1` 没有打败完整 market ladder，不能替代现有模型，也不能作为
`d1 YES mid>=0.80` 的 live overshoot filter。

- 在完整 ladder、同分母、date-expanding OOF 的 3,938 个 state rows / 18 dates 上，
  raw market 的 date-equal multiclass logloss / Brier 为 `0.4671 / 0.2781`。
- `market + path` 的 logloss 比 market 差 `+0.0079`
  （date-block 95% CI `[-0.0036,+0.0200]`）；再加 regime 后差 `+0.0133`
  （`[+0.0020,+0.0251]`），即 regime 版本在 logloss 上显著更差。
- physics-only 即使在更宽的机制分母训练，完整 ladder 同分母 logloss 仍为 `0.6179`，
  明显差于 market `0.4671`。
- 冻结 historical forward 的 43 个可做 physics OOF first signals 中，p75 risk filter
  没抓到唯一 overshoot，反而删掉 9 个赢家；ROI 从 `+7.03%` 降到
  path `+5.89%` / path+regime `+6.09%`。
- 9 个 clean live rows 上，path+regime filter 删掉 2 个 overshoot、1 个 stall，
  仍漏掉 1 个 overshoot；保留 6 行 ROI `-7.69%`
  （95% CI `[-55.11%,+13.71%]`）。这是小样本负结果，不是可上线证据。

因此动作是：不加 hard regime gate、不改 live、不用本模型 size；保留三态 hazard
作为正确的统一研究框架，先补完整 ladder 与 live feature parity，再收 fresh forward。

## 研究问题与概率空间

固定锚点为决策时已经出现的 current bracket，模型分两档 hazard：

```text
h0 = P(final reaches d1)
h1 = P(final reaches d2+ | final reaches d1)

p_stall     = 1 - h0
p_exact_d1  = h0 * (1 - h1)
p_overshoot = h0 * h1
```

三态和为 1。策略判断用 `p_exact_d1 - executable d1 YES cost`，而不是只看
`p_overshoot`；否则会遗漏 stall 风险。

比较模型固定为：

1. `market_raw`：完整 current/d1/d2+/tail YES ladder 归一化；
2. `market_calibrated`：只重标定 market 两档 hazard；
3. `physics_path`：连续 path/forecast/weather/native-lattice 特征；
4. `physics_only`：physics path 加 categorical regime；
5. `market_plus_path`；
6. `market_plus_path_regime`。

所有 logistic heads 固定 `C=0.1`，先积累 12 个 train dates，再逐日 expanding OOF；
没有用 live rows 训练或调阈值。2026-07-02..05 的已知 forecast fallback 污染日期不进模型。

## 数据与分母

### Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| 当前 atlas | state | 14,369 | 50 | 当前文件比复用审计时多 1 state |
| bounded 三态 label | state | 13,545 | — | current / d1 / d2+ |
| d1 bid+ask+settlement、截至 7/07 | state | 11,554 | 49 | 当前刷新后比旧审计的 11,549 多 5 |
| 剔除 7/02..05 污染 | state | 11,132 | 45 | physics 训练分母 |
| complete ladder 同分母 | state | 6,333 | — | market 模型与 paired score 的 coverage |
| expanding OOF paired score | state | 3,938 | 18 | 12-date warm-up 后 |
| 冻结 first signal | city-day | 218 | 48 | 206 exact、11 overshoot、1 stall |
| physics first-signal OOF | city-day | 139 | 32 | warm-up/污染后；不是策略过滤 |
| complete-ladder first-signal OOF | city-day | 53 | 17 | paired proper-score 分母 |

218 行冻结 cohort 直接读取前次产物并校验 SHA256，没有用刷新后的 atlas 重新选样。
当前 atlas 若重选会额外带入 2026-07-08 Amsterdam / Beijing 两行；它们在冻结结束日
2026-07-07 之后，明确排除。上游刷新对本次策略分母的影响为 0 行。

### Evidence funnel

| 层 | rows | coverage gap |
|---|---:|---|
| frozen PIT feature + d1 quote + settlement | 218 | 无 |
| frozen first signal 可重建 complete ladder | 88 | 130 行缺完整 tail |
| 6/21+ historical forward complete ladder | 0 | archive timing/coverage，不是策略筛除 |
| clean live complete ladder + settlement | 9 | 无 |
| clean live canonical as-of forecast | 7 | 2 行缺失；可回连者 age 13.13–22.61h |
| clean live categorical regime parity | 0 | journal 未保存历史同口径 regime |

历史完整 ladder 的 88 行全部在 2026-06-17 以前，所以不能从该分母调一个 cutoff，
再声称它已在 218 行或 6/21+ historical forward 验证。

## 同分母 proper score

| model | rows / dates | logloss | Brier | logloss Δ vs market (95% CI) |
|---|---:|---:|---:|---:|
| market raw | 3,938 / 18 | **0.4671** | **0.2781** | baseline |
| market calibrated | 3,938 / 18 | 0.4696 | 0.2787 | `+0.0025 [-0.0012,+0.0063]` |
| market + path | 3,938 / 18 | 0.4751 | 0.2815 | `+0.0079 [-0.0036,+0.0200]` |
| market + path + regime | 3,938 / 18 | 0.4804 | 0.2837 | `+0.0133 [+0.0020,+0.0251]` |
| physics path + regime | 3,938 / 18 | 0.6179 | 0.3612 | `+0.1508 [+0.1277,+0.1755]` |
| physics path | 3,938 / 18 | 0.6275 | 0.3654 | `+0.1604 [+0.1378,+0.1855]` |

regime 对 pure physics 比不带 regime 略有帮助，但仍远逊于 market；把 regime 加到
market residual 后反而恶化。这说明 regime 能解释部分天气状态，却没有证明能解释
market 尚未计价、可交易的 residual。

在 first-signal 的 53 行完整-ladder OOF 公共分母上，market logloss `0.2616`；
`market+path` 为 `0.2986`，`market+path+regime` 为 `0.2986`，仍未改善。

## 过滤与 fee-adjusted ROI

阈值只在 2026-06-21 前的 OOF first signals 上冻结为各模型 `p_overshoot` p75，
再看 6/21+ historical forward。

| filter | forward rows | removed | failure caught | winners removed | all ROI | retained ROI | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| physics path | 43 | 9 | 0/1 overshoot | 9 | +7.03% | +5.89% | -1.13pp |
| physics path + regime | 43 | 9 | 0/1 overshoot | 9 | +7.03% | +6.09% | -0.94pp |

过滤后的正 ROI 来自原始 cohort 本身为正，不能归功于 filter；filter 的增量为负。
完整-ladder market filters 在 historical forward 为 0 行，只能记 coverage gap。

9 个 clean live rows 的未过滤 signal replay ROI 为 `-36.62%`：

| live filter | removed composition | retained | retained ROI | 95% CI |
|---|---|---:|---:|---:|
| market raw p75 | 1 winner | 8 | -43.28% | `[-85.30%,-4.14%]` |
| market calibrated p75 | 1 overshoot | 8 | -28.51% | `[-66.41%,-0.53%]` |
| physics path p75 | 2 overshoot + 1 stall + 1 winner | 5 | -12.66% | `[-72.06%,+10.10%]` |
| physics path + regime p75 | 2 overshoot + 1 stall | 6 | -7.69% | `[-55.11%,+13.71%]` |

最后一行只是 9 行上的小样本改善，而且 historical forward 同一规则方向相反；
regime 字段在 live 仍为 unknown，不能把它晋升为 filter。

按完整三态 EV，clean live 上所有 calibrated/physics rows 都是
`p_exact_d1 - fee-adjusted cost <= 0`；raw market 只有 2 行略大于 0，其中 1 赢 1
overshoot，ROI `-43.2%`。这支持“先估 exact probability 再算 EV”，不支持继续用
`mid>=0.80` 作为充分买入理由。

## 与 faded / heating exhaustion 的关系

两者共享同一个 latent state，但不是同一个交易：

| expression | 赢的状态 | 主要风险 | 应用概率 |
|---|---|---|---|
| faded / heating exhaustion 买 current YES | 最终停在 current | 任何 reheat 到 d1+ | `p_stall` |
| d1 high-mid 买 d1 YES | 最终恰好停在 d1 | stall current **和** overshoot d2+ | `p_exact_d1` |

所以正确架构应是一个三态/ladder probability layer，同时给 current YES 与 d1 YES
定价；不是维护两套互相矛盾的“已消退”和“还会升一档”hard rules。

但本 run 只证明统一概率空间是对的，没有证明当前 feature/model 有 alpha：

- 不取代 `current_yes_fade_confirmed` / heat-death / no-reheat 模型；
- 不取代 absolute Tmax distribution prior；
- 不取代 source-basis、快源或 execution 模型；
- 未来只有在 `market + physics residual` 连续 OOF proper score 和 fresh forward
  都优于 market 后，才有资格把 current-break、no-reheat、d1-overshoot 等 binary
  selectors 收口为一个共享 probability layer。

## 与旧模型的对标

本结果与旧研究的负面结论一致，而不是一个独立的新阳性：

- current-YES no-reheat hazard 的 holdout AUC 曾低于 market；
- heat-death overshoot edge 扩窗后 logloss 也劣于 market，早期 edge 在后段吐回；
- Tmax market-path fusion 没有稳定打败 market；
- 本 run 再次看到 raw market 是最强 baseline，regime 没有稳定 residual。

因此现在不应“用新模型取代旧模型”，而应把这次三态框架保留为统一 challenger，
把旧 binary models 保留为 dormant/reference baselines；没有删除或标废弃任何旧产物。

## 下一步与血缘

1. 现有 d1 collector 继续 zero-notional 保存完整 ladder；先拿到覆盖 6/21 后同口径的
   fresh forward，不补 hard gate。
2. journal 增补 canonical forecast issue/run/hash/age、strict-high clock、solar window、
   regime/source basis，使 live 与历史 feature parity 可审计。
3. 下一版只检验 `market + compact physics residual`，不再扩 categorical regime 笛卡尔积；
   预注册 proper-score improvement 后才看 ROI。
4. 若后续胜出，产出的 `p_stall/p_exact_d1/p_overshoot` 挂到
   `fact_signal_candidates`；本 run 未改生产 runner、参数或真实下单。

## 复现

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_d1_overshoot_hazard_v1.py
```

代码：
`scripts/analysis/market_structure_edge/research_d1_overshoot_hazard_v1.py`

主要产物：

- `generated/d1_overshoot_hazard_v1/summary.json`
- `generated/d1_overshoot_hazard_v1/wide_model_summary.csv`
- `generated/d1_overshoot_hazard_v1/wide_model_deltas_vs_market.csv`
- `generated/d1_overshoot_hazard_v1/first_signal_oof_predictions.csv`
- `generated/d1_overshoot_hazard_v1/first_signal_policy_summary.csv`
- `generated/d1_overshoot_hazard_v1/clean_live_predictions.csv`
- `generated/d1_overshoot_hazard_v1/clean_live_policy_summary.csv`

输入 hash、DB build time、funnel counts 与模型 contract 都写入 `summary.json`。
