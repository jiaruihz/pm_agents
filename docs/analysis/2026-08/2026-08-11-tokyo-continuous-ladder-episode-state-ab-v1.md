# Tokyo continuous-ladder episode-state fixed A/B

## 数据快照

| 字段 | 值 |
|---|---|
| observed_at_utc | `2026-08-11T10:07:54Z` |
| canonical identity | production manifest `healthy`；`runtime/weather.db` → `/Volumes/jrs/pm_agents/runtime/weather.db`，device `16777247` / inode `54444`；本实验不从 DB 构造特征或 PnL |
| weather input | `tokyo_jma_multivariate_path_v1/feature_rows.csv.gz`，semantic SHA `a8bab726…44385` |
| market input | immutable five-city full-ladder snapshot `20260730T120000Z` + archived raw depth |
| settlement evidence | hot `pm_history` 已不保留 7 月；用归档旧 prediction table 的唯一 `winning_bracket` 补 evaluation label，label 不进入特征 |
| artifact | `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tokyo_continuous_ladder/episode_state_ab_20260811_final_v2` |
| execution | research counterfactual，5 shares、taker ask、Weather fee；orders/fills=`0/0`，live behavior 未改变 |

## 单轮 brief

- hypothesis：在既有 Tokyo coherent/relative full-ladder 内加入 prefix-only episode morphology，能在相同 frozen checkpoints 上降低 Brier，尤其改善 pullback/reheat/recross，而不需要新建 conditional-reheat 模型。
- data scope：Tokyo JMA/RJTT；训练 `target_date<=2026-07-15`，概率 forward 固定 `2026-07-16..30`。
- acceptance：candidate 相对各自同架构 baseline 的 Brier delta CI 全负，并在同分母不劣于 market；交易层只作后验诊断。
- 唯一动作：同 runner、同 rows/labels/clocks/hyperparameters/temperature 的 feature-set A/B。
- 不在范围：不调 threshold，不换模型家族，不用 8/11 案例选型，不改 shadow/live。

## Readiness

| 项目 | 状态 | 证据 / 缺口 |
|---|---|---|
| PIT state + clocks | READY（部分 exact） | 历史 observation-clock；137 个 market rows 为 collector exact，其余 archive `+15m` 明示 |
| canonical/build identity | READY | manifest/DB route healthy；输入 artifact/hash 冻结 |
| market quote/depth | READY / partial | 248 states/12 dates；7/17、7/18、7/30 无 market join |
| settlement label | READY / partial | market 分母12日均唯一 winner；15日 weather-only分母标签完整 |
| independent dates | READY | train 804 dates；forward 15 dates |
| clean frozen-forward | READY（probability） | forward labels 未进入 fit；交易 selector 是 post-forward diagnostic，不是 untouched policy |
| strict PIT forecast peak/ceiling | BLOCKED | 旧历史没有 run/issue/first-seen 完整 lineage，未用 local-hour proxy 冒充 |
| WS | N/A | 本实验不读 WS |

## 改动

没有新建模型版本。稳定 runner `research_tokyo_continuous_ladder_forward_v3.py` 增加11个可选特征：episode peak/trough/giveback、trough recovery/fraction/age、30m-vs-60m reacceleration、10m-vs-60m slope reversal、current-boundary cross count、pullback-recovery 与 active-reheat 标志。

所有值只使用同一 target-date 到当前 JMA checkpoint 的前缀；测试明确验证追加未来观测不会改变历史 checkpoint。派生语义版本为 `tokyo_episode_state_prefix_v1`、hash `9ff2dff4…4756d`。三组 A/B 分别为 direct、coherent checkpoint、coherent multigrain 的 baseline 与 `__episode_state`，本轮候选数 `K=3`；另列6个机制解释切片，未做多重检验校正，因此切片只作诊断。

## Signal / evidence funnel

| funnel | stage | grain | rows | dates |
|---|---|---|---:|---:|
| signal | training slice | checkpoint | 62,214 | 804 |
| signal | untouched probability forward | checkpoint | 1,170 | 15 |
| signal | first positive edge per date-bracket（direct episode） | research signal | 62 | 12 |
| evidence | PIT full-ladder + settlement | state | 248 | 12 |
| evidence | collector-exact subset | state | 137 | 8 |
| evidence | 5-share executable（direct episode） | counterfactual trade | 62 | 12 |
| evidence | actual fill | fill | 0 | 0 |

## Probability A/B

负 delta 才表示新增特征更好。

| architecture | forward Brier delta vs paired baseline | 95% date-block CI | 判断 |
|---|---:|---:|---|
| direct checkpoint | `+0.000494` | `[-0.004116,+0.004878]` | 无增量 |
| coherent checkpoint | `+0.001988` | `[-0.001591,+0.005627]` | 无增量 |
| coherent multigrain | `-0.000701` | `[-0.002983,+0.001766]` | 点估改善、CI跨0 |

direct 的 recross slice 为134 states/12 dates，Brier delta `-0.017438`、CI `[-0.043902,+0.006646]`；这是唯一较明显的方向，但仍跨0。`reheat_active` 109 states/15 dates反而为 `+0.007735`、CI `[-0.001002,+0.017884]`。说明现有 running max、pullback、30/60m slope、warming run 已吸收大部分 episode 信息；把同义状态再喂给树模型没有形成稳定增量。

## 同分母 market baseline

| slice | model | states/dates | Brier | delta vs market | 95% CI |
|---|---|---:|---:|---:|---:|
| market available | conditional market | 248/12 | `0.125418` | — | — |
| market available | direct + episode | 248/12 | `0.165809` | `+0.040390` | `[-0.013556,+0.089144]` |
| collector exact | conditional market | 137/8 | `0.128120` | — | — |
| collector exact | direct + episode | 137/8 | `0.141754` | `+0.013634` | `[-0.037464,+0.065087]` |

`reheat_active` 的19 market rows/11 dates里，原 direct baseline Brier `0.113188`，episode candidate `0.122023`，market `0.180026`。两版天气模型都在这个已知机制切片点估胜 market，但新增 episode 特征比旧模型更差；不能把“天气模型在切片里较好”误写成“新特征有效”。

## 交易表达诊断

first-per-date-bracket 是用户此前指定的研究表达，不允许补仓；它会形成多档组合，不把62腿当62个独立日期。

| model | signals | wins | fee PnL | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| direct baseline | 62 | 6 | `-$10.07` | `-25.13%` | `[-61.97%,+27.82%]` |
| direct + episode | 62 | 7 | `-$7.93` | `-18.47%` | `[-66.67%,+46.45%]` |
| coherent multigrain + episode | 61 | 9 | `+$3.60` | `+8.71%` | `[-35.76%,+66.29%]` |

direct candidate 相对 baseline 只有56个同动作、4个改动作、2个新增、2个移除，PnL差 `+$2.14`；最大正差来自7/26把33 YES翻成NO，最大负差来自7/29把35 NO翻成YES。点估改善由少量翻向主导，proper score没有同步改善，且 selector 已看过 forward，不能作为策略升级证据。

## 结构性结论与动作

1. **不替换当前 Tokyo 模型。** episode/reheat 只是 feature component，这次没有变成新模型家族；三个同架构 A/B 都未通过 paired probability gate。
2. **保留代码与 telemetry，不做 hard gate。** recross 的点估值得在现有 `source_event_full_ladder_v1` clean forward 中继续记录，但不得从134个历史 states或19个 market-reheat rows造 eligibility。
3. **下一信息缺口不是更多同义 path 特征，而是 strict PIT forecast peak/ceiling 与 source→official timing。** 历史缺 run/issue/first-seen 时钟，禁止用事后 forecast 或 local hour 冒充。当前 collector 已按既定合同积累，满30个新settled dates后在同一模型身份下复核 incumbent vs incumbent+episode/forecast；期间不调参。
4. `significance=FAIL baseline=FAIL forward=PASS(probability only) conclusion=inconclusive`；live/shadow运行与订单均不变。

## 血缘与验证

- feature layer：runner 内 prefix-only materialization；未新增并行事实表。
- ModelOutput：同一 `tokyo_continuous_ladder_forward_v3` run identity，以 artifact/run目录区分 feature set。
- SignalCandidate/TradeIntent/plan/order/fill：仅 counterfactual；实际均为0。
- tests：Tokyo continuous ladder v1/v2/v3 定向套件 `19 passed`。
- artifacts：`summary.json`、`model_scores.csv`、`episode_slice_scores.csv`、`market_episode_slice_scores.csv`、`paired_trade_changes.csv`、prediction/market/trade明细与6个model artifacts。
