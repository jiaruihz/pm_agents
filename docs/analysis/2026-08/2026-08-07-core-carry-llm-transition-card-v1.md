# Core Carry sample-level semantic alignment audit

Status: `expanded diagnosis complete / transition-confirmation challenger specified / LLM veto rejected / no live change`

## 结论

这次审计直接回答同一个 checkpoint 上的三个问题：当时的 METAR/forecast 在物理上表达什么，Core v3 的逐特征打分表达什么，两者是否一致。扩展版已完成 120/120 张 weather-only cards，覆盖 40 城、110 个 city-day 和 12 个 target dates，不再只是少量 policy hits。

主要结果：

- 120 条中，64 条方向一致、48 条存在 semantic tension、8 条模糊。31 条真实 policy-selected 中为 26/3/2，说明 Core 大多数实盘候选确实在表达 late plateau/fade carry。
- Core v3 只有 `market_logit + local hour + dewpoint depression + wind speed`。120/120 条当时已经存在的温度路径、距创新高时间、风向/输送、云雨转折、剩余热量和 forecast-observation conflict 都没有进入模型。
- 风速语义仍然最明显：93/120 被 LLM 判为 `mixing_only`，26/120 为 `cooling_transport`，没有一条具备足够证据判成 `warming_transport`；但生产模型让风速沿固定方向抬高 hold odds。35 条出现“只确认混合维持，风速项却抬高 hold”，5 条出现“冷输送存在，但低风速项反而压低 hold”。
- 60 条已有 settlement。LLM 判 `upward_exit` 的 14 条只有 6 条最终 NO，8 条是假警报；因此不能把 LLM card、强风解释或某个 transition 标签直接变成 veto/gate。
- 更重要的共同漏判出现在反方向：Munich、Wuhan、Lucknow、Panama City、Tokyo、Cape Town 六个最终 NO 都被 LLM 判为 hold/fade。它们反复出现同一结构——forecast 说云雨/降温会封顶，但观测端尚未确认 transition，且当前高点很新、路径仍在回升或实际温度已经超过 forecast ceiling。

因此新增的完善方向不是“再加一个天气 gate”，而是 **transition-confirmation residual challenger**：保留 Core/market prior，用连续变量学习“forecast 所说的封顶是否已经被实时观测确认”，并同时按 settlement-native exact-bracket 边界衡量下一档距离。

## 审计分母与信息隔离

- 日期：`2026-07-24..2026-08-07`
- raw rows：2,116
- Core-scored 固定分母：1,178 checkpoints
- cards：120/120，生成错误 0
- 样本组成：31 policy-selected、7 same-city-day near miss、31 never-selected matched controls、51 semantic-diversity controls
- 覆盖：40 cities / 110 city-days / 12 target dates
- 已结算审计样本：60 rows / 9 target dates
- METAR PIT：`first_seen_utc <= decision_snapshot_ts_utc`

样本选择在读取 settlement 前完成：先保留全部 policy hits、near misses 与 matched controls，再以 city/date、market band、Core-market residual、local hour、path、wind、湿度、距创新高时间、forecast relation、intraday state 和 precipitation state 做 deterministic coverage expansion。修改 settlement label 不会改变入选集合。

LLM 在生成 card 时不读 market、Core 分数、selection role 或 settlement；card 冻结后才附上生产模型逐特征 contribution 和最终标签。120 张卡是机制审计 casebook，不是代表性训练集，下面的 Brier 只作描述，不能替代全分母 OOF/forward。

## 生产模型表达的语义

生产 artifact 是 `current_yes_core_carry_model_v3_no_peak_clock`：

| feature | standardized coefficient | 当前表达缺口 |
|---|---:|---|
| `market_logit` | +1.9273 | 合理的强 prior；也说明 Core 本身已含 market |
| `decision_hour_local` | +0.0934 | 线性时钟替代了真实 peak clock、太阳衰减和剩余热量 |
| `dewpoint_depression_f` | -0.1894 | 小露点差一律抬高 hold，无法分辨湿稳/云盖与暖湿输送 |
| `wind_speed_kt` | +0.3168 | 风越大一律抬高 hold，无法区分 mixing、冷/暖输送及城市地形 |

每个样本 reconstructed probability 与线上记录完全一致，最大 reconstruction error 为 0。

## 120 条结构化对照

将 LLM 的 `upward_exit/high reheat` 记为物理上反对 hold，将 `current_high_holds/fade + low reheat` 记为支持 hold，再与 `Core p - market` 的符号比较：

| role | rows | aligned | semantic tension | ambiguous |
|---|---:|---:|---:|---:|
| policy selected | 31 | 26 | 3 | 2 |
| same-city-day near miss | 7 | 4 | 3 | 0 |
| never-selected matched control | 31 | 14 | 17 | 0 |
| semantic-diversity control | 51 | 20 | 25 | 6 |
| total | 120 | 64 | 48 | 8 |

物理标签分布不再只剩 hold：86 hold、26 upward-exit、8 ambiguous；next state 为 51 fade、42 current-high-holds、26 upward-exit、1 unclear。证据质量仍有限：96 partial、24 good，48 条存在 source conflict。

在 60 条 settled cards 上：

| slice | rows / dates | losses | Core − market Brier | 解释 |
|---|---:|---:|---:|---|
| aligned | 34 / 8 | 6 | -0.00880 | Core 点估优于 market |
| semantic tension | 23 / 5 | 6 | +0.01157 | Core 在 15/23 条上比 market 差，但样本/日期仍小 |
| ambiguous | 3 / 3 | 0 | +0.08681 | 不可据此下结论 |
| LLM physical hold | 43 / 8 | 6 | -0.00150 | 37/43 最终 YES |
| LLM physical upward-exit | 14 / 6 | 6 | +0.00226 | 仅 6/14 最终 NO，不能当 veto |

全 120 条的语义缺口计数为：

- observed transition signals absent from Core：120
- weather/source conflict absent from Core：48
- mixing-only but wind speed boosts hold：35
- cooling transport but low wind speed penalizes hold：5
- rising dewpoint/upward-exit but low depression boosts hold：1

## 代表性 case 与共同偏差

### Wellington 2026-07-29 · 12 YES

PIT METAR 尾段为 `12/07, 12/07, 11/07, 12/08, 12/08, 12/09°C`，北到北东北风持续 `18–23kt`。LLM 读法是 `plateau + mixing_maintenance + current_high_holds`：强风阻止快速贴地降温，但没有 upstream thermal/pressure 证据证明暖平流。

Core 从 market `92.15%` 抬到 `96.98%`，其中 21kt 风速贡献 `+0.789 logit`。最终方向虽一致，但模型把“风很大”直接编码成强 hold 支持，不能区分 mixing maintenance 与 warming transport；这就是 Wellington 暴露的语义混写。

### Lucknow 2026-07-31 · 32 YES · settled NO

PIT 路径为 `30→31→31→31→32→32→31→32°C`，最后一次回落后又回到 32°C，仍有约 260 分钟 daylight。Core 从 market `83.00%` 抬到 `87.08%`，但 dip→rebound、剩余 heating window、CB/云层 transition 均未参与打分。

LLM 也因 forecast cloud/rain cap 判成 `fade / low reheat`。它虽在 conflicting facts 中记录 3h 升温和长 daylight，仍让 forecast cap 叙事主导。最终 32 NO，说明这里缺的是“forecast cap 是否已被观测确认”，而不是一个 rain/cloud hard filter。

### 共同的六个 LLM hold/fade 漏判

- Munich：当前 28°C，而 forecast max 仅 25.3°C；新高只有约 11 分钟，forecast-observation level 明显失配。
- Wuhan：forecast 给高云雨概率，但最新 METAR 仍是 SCT、无降水；cap transition 尚未落地。
- Lucknow：`32→31→32°C` rebound，daylight 仍长。
- Panama City：高点很新，forecast max 高于当前档，但派生 `future_peak_relation` 却写 below，存在 forecast field conflict。
- Tokyo：1h/3h 路径仍强升温，forecast max 高于当前档，雷暴封顶尚未被观测确认。
- Cape Town：高点仅约 40 分钟且仍 active warming，forecast 只是略低于当前值。

这些 case 不证明一条新规则已经盈利，却给出比“多放几种天气特征”更具体的可训练假设。

## 新 challenger：transition-confirmation residual

目标不是让 LLM 直接报概率，而是把它反复指出的缺口转换成 deterministic PIT features：

1. **settlement-native boundary**：由 exact bracket 解析 `upward_exit_threshold_native = bracket_high + 0.5 native unit`，构造 forecast/running/current 到下一档的连续 margin；避免把“还会升温”和“足以跨下一 native bracket”混为一谈。
2. **observation path**：1h/3h slope、`rebound_after_dip`、距 last strict new high、equal-high persistence，表达 fresh runway、plateau、pullback、fade。
3. **transition confirmation**：forecast 预计云量/降水/降温封顶的时刻，与最新 METAR 是否已经出现对应 cloud/rain/temperature response；未确认时保留 upward-exit tail。
4. **forecast reliability/innovation**：running max 是否已超过 forecast max、forecast fields 是否内部矛盾、forecast revision/source age，而不是把 forecast curve 当真值。
5. **remaining heat and transport role**：daylight/solar/forecast peak clock，以及 `wind direction × city/terrain/season` 的 warming/cooling/mixing role；不再单独把 wind speed 当物理方向。

建议模型形式：

```text
logit(p_hold_challenger) = logit(p_core) +
  g(boundary_margin, observation_path, transition_confirmation,
    forecast_innovation, remaining_heat, transport_role)
```

首轮只比较 compact ridge/monotonic shallow tree，不新增 eligibility threshold。训练和评估必须回到全部 1,178 个 Core-scored checkpoints（扩历史后用更宽固定分母），按 target-date expanding OOF，并保留最终日期 frozen forward；在完全相同 PIT book rows 上同时比较 Core 与 market 的 Brier/logloss、calibration 和 fee-adjusted expression。只有同分母概率质量通过后才研究 sizing/live。

LLM taxonomy 只用于提出和审查 deterministic interaction，不进入线上模型输入，也不作 gate/veto。当前动作仍是 `no live change`。

## 产物

- expanded prereg: `2026-08-07-core-carry-expanded-semantic-audit-preregistration.json`
- runner: `scripts/analysis/reheat_risk/core_carry_llm_transition_card.py`
- card contract: `src/strategies/weather_edge_v1/tools/intraday_transition_card.py`
- expanded artifact: `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/core_carry_llm_transition_card_v1/expanded_sample_semantic_alignment_gpt54_n120/`
- original 63-card artifact: `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/core_carry_llm_transition_card_v1/sample_semantic_alignment_gpt54/`
