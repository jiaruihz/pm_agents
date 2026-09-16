# Tokyo JMA Source-Specific Path Plan v1

Status: `research_only`; no plan/order/fill/exit change

## 结论与动作

保持 research，不改 live。Tokyo 的 JMA 原始 payload 确实包含此前被 collector 丢掉的风速、风向、阵风和降水；本次已补进共享 parser 与完整历史路径。旧 exact 事件只在当前 JMA archive 内容与原始 `raw_payload_hash` 完全一致时才补特征，不一致行保持缺失，禁止用晚到值伪造 PIT。

source-specific 模型仍只预测 JMA next-lattice cross，不代表 final exact bracket；只有后续与同 checkpoint market probability 比较，才可能形成策略 residual。

本轮消融没有发现新增 JMA 风/阵风/降水维度带来稳定增益：30m 基本持平，60m 明确变差，120m 的微小改善 CI 跨 0。因此补字段是正确的数据建设，但当前不能据此制造 Tokyo eligibility gate。

## Timestamp / feature audit

- raw Tokyo JMA rows: `693`
- distinct observation revisions: `681`
- hash-verified exact enrichments: `623`
- archive missing/hash mismatch excluded: `58`
- timestamp violations: `0`
- first-seen age min/median/max: `5.73/7.09/14.17` minutes
- exact-state joins: `{'missing_or_ambiguous': 74, 'hash_verified_joined': 621}`

`decision_ts = source_first_seen_at_utc`。`observation_time_utc` 只描述气象观测时刻；`source_published_at_utc` 是 collector poll publication，不是 JMA issue time，不得当作更早的信息时钟。`maxTemp/maxTempTime/minTemp/minTempTime/gustTime` 因跨日语义不安全，保持 raw-only。

## Current Tokyo capture dimensions

| source | cadence | current production raw | implemented locally | PIT clock |
|---|---:|---|---|---|
| JMA Haneda AMeDAS | 10m | journal 仅 temp + hashes/timestamps | parser 补 wind speed/direction、gust、10m/1h/3h/24h precip、QC；未部署 | collector exact first-seen |
| RJTT routine METAR | ~30m | temp、dewpoint/RH、wind、cloud/ceiling、weather/precip；QNH 仅在 raw METAR | QNH 规范化为 pressure_hpa；未部署 | 必须用 report first-seen 做 as-of join |
| forecast curve | hourly | temp、cloud、precip probability、wind speed/direction | 不改字段，只要求保存/选择 issue/run/first-seen 版本 | forecast first-seen |

## Frozen-forward feature ablation

| horizon | exact dates/events | snapshot Brier | generic path | Tokyo JMA path | delta vs generic (95% date CI) |
|---:|---:|---:|---:|---:|---:|
| 30m | 9 / 657 | 0.1413 | 0.1310 | 0.1312 | +0.0002 ([-0.0008, +0.0014]) |
| 60m | 8 / 626 | 0.1397 | 0.1208 | 0.1262 | +0.0054 ([+0.0020, +0.0102]) |
| 120m | 8 / 566 | 0.0950 | 0.0721 | 0.0713 | -0.0008 ([-0.0034, +0.0013]) |

三组模型固定相同 exact rows/labels；历史训练截止 exact collector 首日前。负 delta 表示新增 JMA 风/阵风/降水路径改善。日期数仍少，只用于决定是否继续采集，不用于 live gate。

## Strategy research plan

### P0 — Tokyo source probability head（已具备）

- grain: 每个 hash-identified JMA first-seen observation event。
- target: `P(JMA next-lattice cross within 30/60/120m | PIT state)`。
- baseline: snapshot、generic path、Tokyo JMA path，同 rows 做 Brier/logloss/calibration。
- 输出所有事件概率，不按阈值只保存赢家。

### P1 — PIT METAR/forecast fusion

- 对每个 JMA decision checkpoint，只允许连接 `METAR first_seen <= decision_ts` 的最近 RJTT 报文；补湿度、露点、云层、降水、QNH 与风向。
- forecast 必须按 `issue/run/first_seen <= decision_ts` 选版本，加入 peak clock、remaining heating window、cloud/rain/wind forecast。
- 后到 METAR/WU/JMA revision 只作 label/basis audit，绝不回填为特征。

### P2 — Final-exact distribution bridge

- 把 source-cross probability 转成全 ladder terminal distribution，显式建模 source→settlement basis 与 overshoot；不能直接把 touch/cross 买成 exact YES。
- 同 checkpoint 保存 normalized market ladder，主信号是 `P(final bracket)-market_probability`。

### P3 — Zero-notional forward telemetry

- 写 `fact_signal_candidates v2` opportunity：全 bracket×side、p_before/p_after、market before/after、fresh executable cost；notional 固定 0。
- signal funnel 与 evidence funnel 分开；按 target_date block bootstrap。
- 至少新增 20 个 Tokyo exact 日期后冻结复核；未打败同分母 market 前不进入 plan/order/fill/exit。

## Gate

`market_baseline=NOT_YET_TESTED forward=COLLECTING execution=ZERO_NOTIONAL_ONLY conclusion=inconclusive`
