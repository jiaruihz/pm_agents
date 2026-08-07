# Core Carry sample-level semantic alignment audit

Status: `diagnosis complete / feature gaps confirmed / LLM veto rejected / no live change`

## 结论

上一版把问题偏成了 selected 与 rejected 的表现对照，没有直接回答“同一个样本中，天气报文的物理语义与模型打分是否一致”。本版已按样本重做。

Core v3 对 63 个预先固定的 checkpoint 逐一完成了三层审计：

1. `gpt-5.4 / medium` 先只读决策时刻已经 first-seen 的最多 16 份 METAR、forecast curve 和物理状态，不读 market、Core 分数、selection role 或 settlement；
2. LLM card 冻结后，才附上生产 v3 的逐特征 logit contribution；
3. 最后对照 `LLM physical direction` 与 `Core probability residual relative to market`，再读 settlement。

主要发现：

- 63 个样本中，41 个相对方向一致、21 个存在语义张力、1 个模糊。这里的“张力”只表示 LLM 天气判断和 Core 相对 market 的修正方向相反，不等于 Core 预测错。
- 28 个真实 policy-selected 样本中，24 个一致、3 个相反、1 个模糊。Core 大多数时候确实在表达 LLM 也认可的 late plateau/fade carry。
- 但 Core v3 只有 `market_logit + local hour + dewpoint depression + wind speed`。63/63 样本现场已经存在的温度路径、距上次创新高、风向变化、云雨转折、剩余热量、露点趋势和 METAR 反转/持续形态都没有进入 v3。
- 风速的语义尤其过粗：LLM 将 43/63 判为 `mixing_only`、20/63 判为 `cooling_transport`，没有一个能凭现有证据确认为 `warming_transport`；生产模型却让每增加 1kt 都沿固定方向提高 hold odds。26 个样本出现“LLM 只确认维持混合、风速项却正向抬高 hold logit”。这正是 Wellington 所暴露的“阻止降温不等于推动升温，也不自动等于 exact bracket 更安全”。
- 两个已结算 upward-exit 样本——Lucknow 32 和 Wuhan 32——LLM 与 Core 都偏向 hold/fade，说明当前 LLM card 不能直接当 veto。两例共同弱点是 forecast/cloud-rain cap 叙事压过了观测路径里的尾部风险。

所以应做的不是给 Core 再加 hard gate，也不是直接让 LLM 决定下不下单，而是训练一个同分母 challenger，把上述连续的路径与 transition 特征作为 market-offset residual 输入。

## 审计分母与信息隔离

- 日期：`2026-07-24..2026-08-07`
- raw rows：2,103
- Core-scored checkpoints：1,165
- LLM cards：63/63，错误 0
- 组成：28 个全部 policy-selected、7 个同城同日 near miss、28 个 never-selected matched controls
- settlement：37 rows / 8 target dates
- METAR PIT 条件：`first_seen_utc <= decision_snapshot_ts_utc`

63 张卡不是训练集，也不是重新挑出来的盈利切片；它们只用于逐样本机制审计。

## 生产模型到底在打什么分

生产 artifact 为 `current_yes_core_carry_model_v3_no_peak_clock`。四个输入及系数为：

| feature | standardized coefficient | 当前语义问题 |
|---|---:|---|
| `market_logit` | +1.9273 | 合理作为强 prior，但意味着 Core 不是纯天气模型 |
| `decision_hour_local` | +0.0934 | 只用线性时钟代替实际 peak clock、太阳衰减和剩余热量 |
| `dewpoint_depression_f` | -0.1894 | 小露点差一律抬高 hold，无法区分湿稳/云盖与暖湿输送 |
| `wind_speed_kt` | +0.3168 | 风越大一律抬高 hold，无法区分 mixing、冷输送、暖输送及地形/海陆背景 |

每个样本的 reconstructed probability 与线上记录完全一致，最大 reconstruction error 为 0，因此下面不是用近似模型解释线上分数。

## 代表性逐样本对照

### Wellington 2026-07-29 · 12 YES

PIT METAR 尾段为 `12/07, 12/07, 11/07, 12/08, 12/08, 12/09°C`，北到北东北风持续 `18–23kt`，云层由 BKN/OVC 到最后 SCT+BKN。LLM 读法是：`plateau + mixing_maintenance + current_high_holds`；强风维持边界层混合、阻止快速降温，但没有 upstream thermal/pressure 证据证明暖平流。

Core 从 market `92.15%` 抬到 `96.98%`。logit 中 market `+1.289`、hour `+0.104`、低露点差 `+0.211`、21kt 风速 `+0.789`。最终 hold 方向与 LLM 一致，但模型把“风很大”直接编码成强 hold 支持，无法表达 LLM 给出的关键限定：这是 mixing maintenance，不是 warming transport。该样本尚未结算，不能叫预测错误，但已经是明确的特征语义混写。

### Lucknow 2026-07-31 · 32 YES · settled NO

PIT METAR 依次显示 `30→31→31→31→32→32→31→32°C`；最新为 `09013KT 32/27 FEW030CB BKN100`。也就是 08:30 的回落后，09:00 又回到 32°C，仍有约 260 分钟 daylight，露点长期在 27–28°C。

Core 从 market `83.00%` 抬到 `87.08%`：market `+0.469`、hour `-0.044`、9°F 露点差 `+0.154`、13kt 风速 `+0.255`，加 intercept 后精确还原 87.08%。温度 dip→rebound、露点路径、风向、CB/云层变化和剩余 heating window 均未参与打分。

LLM 也判成 `plateau → fade / low reheat`，因为 forecast max 低于已打印高点且未来云雨概率很高；它虽然把“3h 仍升温、daylight 仍长”列为冲突证据，仍未翻转结论。最终 32 NO，说明这是 **Core 和 LLM 共同漏掉的尾部**，不是“LLM 已看懂但模型没看懂”。最值得测试的特征是 `rebound-after-dip × daylight remaining × forecast-observation conflict`，而不是简单加一个 rain/cloud gate。

### Wuhan 2026-07-28 · 32 YES near miss · settled NO

market 为 `95.00%`，Core 为 `95.43%`；LLM 同样判 `plateau → fade / rain_onset / low reheat`，forecast 给出 98% 云雨概率且 future max 低于当前高点。最终升到 33°C，随后 Core 在 33 档触发并结算 YES。

这与 Lucknow 构成同类失败：问题不只是 Core 没有路径特征，还是 forecast cap 叙事在极端湿热/对流环境中可能系统性低估最后一跳。需要显式建 `forecast says fade but observations have not confirmed transition` 的 innovation，而不是把 forecast path 当真值。

### NYC 2026-07-28 · 80–81 YES · settled YES

LLM 看到 `+1.08°F/1h、+3.06°F/3h`、forecast peak 仅 0.22h 后且高于当前 max，判 `active_warming → upward_exit`。Core 却从 market `91.00%` 抬到 `93.39%`，其中 15kt 风速 `+0.388`、9°F 露点差 `+0.154`。

这是清楚的语义分歧，但最终 exact bracket 仍 YES，Core 的修正方向优于 LLM 的风险提示。它说明“发现模型没表达某个风险”不等于该风险足以成为 veto；直接把 LLM upward-exit 卡成拒单会制造 false negative。

### Cape Town 2026-07-31 · 24 YES control · settled YES

LLM 根据新高后 forecast 转降、云量上升和干燥 mixed layer 判 `plateau → fade / low reheat`。market 为 `89.00%`，Core 反而降到 `82.46%`，主要因为 36°F 大露点差贡献 `-0.278 logit`、7kt 低风速贡献 `-0.146`。最终 24 YES。

这是反方向的候选偏差：模型把“干、风小”机械解释为不利于 hold，但在已经临近峰值且未来曲线下降的语境下，低风速和大露点差并不自动意味着还能升穿。Amsterdam 7/28 和 8/01 也出现 LLM 识别 cooling transport、但仅因风速低于训练均值而给 hold 负贡献，两例最终均 YES。

## 63 个样本的结构化对照

将 LLM 的 `upward_exit/high reheat` 记为物理上反对 hold，将 `current_high_holds/fade + low reheat` 记为支持 hold，再与 `Core p − market` 的符号比较：

| role | rows | aligned | semantic tension | ambiguous |
|---|---:|---:|---:|---:|
| policy selected | 28 | 24 | 3 | 1 |
| same-day near miss | 7 | 4 | 3 | 0 |
| matched control | 28 | 13 | 15 | 0 |
| total | 63 | 41 | 21 | 1 |

已结算的 tension 行进一步说明不能把 LLM 当 gate：

- 4 个 `Core raises hold / LLM says upward exit` 全部最终 YES；
- 8 个 `Core lowers hold / LLM says hold` 也全部最终 YES，且 Core 相对 market 的 Brier 都变差；这组更像“模型低估 hold”的 challenger 候选，但来自小规模 matched controls；
- 唯一两个最终 NO 都落在 `Core raises hold / LLM says hold`，现有 LLM 没抓住。

## 应进入 challenger 的特征，而不是 gate

63/63 样本都有以下现场证据，但生产 v3 全部未使用：

- `temperature_path_1h_3h` 与 `rebound_after_dip`；
- `minutes_since_last_strict_new_high`；
- `wind_direction/change × city/terrain/season`，输出 warming/cooling/mixing role；
- cloud ceiling、rain onset/persistence 的 transition，而不是静态 rain=true；
- daylight、solar decay、forecast peak relation 和 remaining heat；
- dewpoint trend 与 dewpoint depression 的交互；
- forecast 与最新 METAR 是否已经确认同一 transition；
- 完整 METAR path 的 persistence/reversal。

下一版 challenger 应保持 market logit 为 offset，在相同 PIT checkpoint 上加入这些连续特征，并以 target-date blocked OOF/frozen forward 比较 Brier/logloss。LLM card只用来生成机制标签、审查 case 和设计 interaction，不直接输出概率或交易 gate。

当前动作仍是 `no live change`：这次确认了特征表达缺口，但没有证明新增语义已经能在 forward 上提升概率或 PnL。

## 产物

- prereg: `2026-08-07-core-carry-sample-semantic-alignment-preregistration.json`
- runner: `scripts/analysis/reheat_risk/core_carry_llm_transition_card.py`
- card contract: `src/strategies/weather_edge_v1/tools/intraday_transition_card.py`
- machine artifact: `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/core_carry_llm_transition_card_v1/sample_semantic_alignment_gpt54/`
