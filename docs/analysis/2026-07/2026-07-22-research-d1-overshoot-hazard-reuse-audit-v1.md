# Weather 研究：d1 overshoot hazard 既有资产复用审计 v1

## 结论与动作

`d1 overshoot_hazard` 不需要从零重建数据层，但必须重新跑一个以 d1 为 anchor 的 target-specific probability head。

- 可直接复用：temperature/reheat feature factory、intraday regime atlas、canonical physical features、完整 ladder/hazard-chain 的建模骨架、expanding OOF/proper-score/bootstrap 框架、d1 first-signal 固定分母。
- 不可直接复用：旧 current-YES overshoot 模型的 label、系数、概率、edge threshold 和选中 cohort；旧 Tmax v3 的历史概率也不能直接拼到 d1 rows。
- 历史 v1 不需要新采集：现有 atlas 已覆盖主路径特征。需要在现有数据上重新 materialize d1-anchor rows、重训和重放。
- fresh forward 需要继续 zero-notional shadow：记录 d1 信号时完整 sibling ladder 和三状态概率；不改 live。

```text
significance=NA_DESIGN_AUDIT; baseline=NA_PENDING_TARGET_SPECIFIC_RUN;
forward=NA_PENDING_SHADOW; conclusion=research
```

## Target

```text
估计 P(stall current / exact d1 / overshoot d2+ | d1 signal-time PIT state)，
并检验相对同一时点完整 market ladder 的 residual。
```

- physical target / exact-bracket semantics：bounded current→d1 ladder；最终 current=`stall`，最终 d1=`exact`，最终 d2+=`overshoot`。
- grain / universe：训练为 PIT city-date-state；主评估为每 `(city,target_date)` 首个 `d1 YES mid>=0.80` 信号。
- decision timestamp：历史 `decision_snapshot_ts_utc`；只用该时点及以前信息。
- label / settlement source：canonical final winning bracket；历史 first-signal 218 行全部有 exact settlement。
- executable expression / fee：买 d1 YES；主成本为 PIT ask + Weather taker fee。
- primary metric / market baseline：三分类 logloss/Brier；baseline 是同 row current/d1/d2+/tail sibling ladder，不是单独 `d1 mid`。

## 与既有研究的共通与边界

| 既有资产 | 共通部分 | 不能直接沿用的部分 | 处理 |
|---|---|---|---|
| `current_yes_future_break_hazard_v3/v31` | path/forecast 特征、market-anchored residual、expanding OOF | 预测 current 是否守住；不是已到 d1 条件下是否继续到 d2+ | 复用训练/评估框架，重写 anchor/label |
| `current_yes_no_reheat_hazard_score_v1` | remaining heat、forecast gap、warming、fresh-high、plateau maturity | holdout AUC 0.616，弱于 market 0.747；旧 score 不可当 d1 filter | 只复用 feature hypotheses |
| `current_bracket_no_remaining_heat_model_v1` | `remaining_heat > required_gap` 的 payoff-aligned 机制 | 交易表达相反且 6/21..23 forward 失败 | 复用 required-gap 思路，不复用概率/规则 |
| `intraday_weather_regime_atlas_v1` | d1/d2 sibling quotes、path/regime、final bracket、`skip_over_d1` 已齐 | regime 是描述标签，不是概率或 gate | 作为主历史 feature base |
| `current_yes_overshoot / heat-death edge v2` | market logit + physics、OOF、proper score、ablation | label=`current_bracket_held`；全窗模型劣于 market，旧 edge 跨期消失 | 复用方法并保留其 negative-control 教训 |
| `regime_routed_carry_v1` | ceiling debias + path faded 的机制交集 | 目标是高价 current-YES carry，物理方向偏“防突破”；不是 d1 overshoot head | ceiling/path 特征进入连续模型，不复制 route |
| `tmax full-ladder / survival / distribution v3` | sequential hazard、完整 ladder 概率空间、market prior、path/source features | 旧版本未过 proper-score/forward；历史 artifact 与 d1 signal-time 对齐稀薄 | 复用 hazard-chain结构，重新 fit d1 head |
| `d1 YES high-mid regime audit` | 218 first signals、三态 label、首次触发、fee/forward/事故隔离 | 目前只有切片，没有 OOF probability model | 直接作为固定评估分母 |

## Data integrity / PIT

| 项目 | 值 |
|---|---|
| raw source and coverage | regime atlas 14,368 state rows / 50 dates / 36 cities；d1 first signals 218 / 48 dates / 33 cities |
| label availability | 218/218 settled；206 exact d1、11 overshoot d2+、1 stall current |
| path feature coverage | 1h/3h trend 217/218；minutes since max 215/218；regime labels 218/218 |
| forecast issue/run/hash/age | forecast gap / peak delta 191/218（87.6%）；缺失保持 missing，不 fallback |
| meteo context | RH / wind 217/218；moisture/path/day regimes 218/218 |
| sibling book | d1 mid/ask 218/218；d2 NO bid 177/218、ask 176/218；缺口只能记 coverage gap |
| old current-YES OOF overlap | 同 city/date/hour 61/218（28.0%） |
| old Tmax v3 artifact overlap | signal 前任意 as-of 34/218；60 分钟内 14/218（6.4%） |
| source-to-settlement basis | native-unit settlement lattice 必须沿用 canonical bracket mapping；快源只作 feature |

上述覆盖证明历史特征层足够启动新 run，也证明旧预测值不能通过 join 直接复用。

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| raw atlas universe | city-date-hour | 14,368 | 50 |
| usable d1 mechanism rows | state + quote + settlement | 11,549 | 49 |
| `mid>=0.80` trigger rows | state | 296 | 48 |
| fixed first d1 signal | city-day | 218 | 48 |

训练不能只用 218 行，因为只有 11 个 overshoot positive。正确做法是在宽机制分母训练逐档 hazard，再只在冻结的 218 个 first-signal rows 上评估策略目标；bootstrap 按 target date block。

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | first city-day signal | 218 | 48 | forecast fields缺27行 |
| PIT d1 quote | first city-day signal | 218 | 48 | 无 |
| d2 sibling quote | first city-day signal | 176–177 | 47+ | 其余是 market-ladder coverage gap |
| settlement | first city-day signal | 218 | 48 | 无 |
| clean live forward | first city-day signal | 9 | 6 | 样本不足；7/19 continuity incident 已剔除 |
| actual clean fill | fill | 12 | 6 | 8 city-day，不能作为模型训练分母 |

## 建议的新 run：`d1_overshoot_hazard_v1`

### 1. 概率空间

```text
p_stall     = P(final=current)
p_exact_d1  = P(final=d1)
p_overshoot = P(final>=d2)
sum = 1
```

主决策不是单独最小化 `p_overshoot`，而是：

```text
edge_d1_yes = p_exact_d1 - executable_d1_cost
```

否则只过滤 overshoot 会把 stall 风险遗漏。

### 2. 模型层次

1. `market_ladder_raw`：current/d1/d2+/tail sibling mid 归一化。
2. `market_calibrated`：只重标定 market，作为强 baseline。
3. `physics_only`：path/forecast/source/solar/weather，不含价格。
4. `market_plus_physics`：只允许物理特征解释 market residual。

候选 PIT 特征直接复用：forecast ceiling/peak delta、remaining-heating window、temperature trend、minutes since strict high、plateau/pullback/fade、RH/dewpoint、cloud/rain/wind、source age/basis、native-lattice distance、完整 ladder geometry。

### 3. 必须重新跑的部分

- 按 d1 bracket 重新生成 `required_gap_to_d2`，不能沿用 current→next 的 gap。
- 重新定义三态 label，排除 `X+` 非 bounded expression。
- 重新按 d1 signal-time 连接 sibling ladder；不能用旧 latest artifact 回填。
- expanding/OOF 逐日训练，比较 multiclass logloss/Brier 与 market；再评 first-signal fee EV。
- 单独保留 train、historical forward、7/16+ clean live forward，禁止窗口混训后声称 forward。

## 旧研究给出的负面先验

- `heat-death overshoot-edge v2` 扩到42天后，模型 logloss 0.3532 劣于 market 0.3486，前段 edge 被后段 -4.0% 吐回；说明抗单城/单日检验不能替代跨期。
- `tmax distribution v3` 的 primary market-path 在 paired proper score 上仍略差 market，且 fresh forward 为0；不能把旧 full-ladder artifact 当已验证概率。
- d1 regime audit 中最强单变量是 market mid（failure separation 0.722），天气 regime 没有稳定 residual；新 head 必须从 market prior 出发。

## Frozen forward

- train choices frozen：先在历史宽分母确定模型结构；阈值不在 live forward 调。
- forward dates/results：现有 clean live 仅9 rows / 6 dates，不足以晋升。
- multiple testing：特征 ablation 与模型层次预注册；不把 regime 笛卡尔格子变成 selector。
- unresolved blockers：完整 sibling ladder历史覆盖约81%；strict-high clock、solar/rain/forecast-curve的历史 PIT coverage需在run中逐项审计。

## Bloodline placement

- shared data logic：继续用 `weather_data_feed/physical_features.py`；不新建策略私有天气事实表。
- feature layer：复用 reheat factory / regime atlas；若补 strict-high/solar 字段，进入 canonical feature layer。
- `fact_signal_candidates` fields：后续物化 `p_stall`、`p_exact_d1`、`p_overshoot`、market counterparts、model version和PIT lineage。
- shadow/collector runtime：先扩现有 d1 zero-notional journal，记录完整 ladder + score；不改 live execution。
- docs/index/registry update：本文件是复用/重跑设计审计；模型实际跑出后再登记新 strategy/model version。
