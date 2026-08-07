# Core Carry LLM Transition Card v1

Status: `balanced retrospective diagnosis complete / challenger not promoted / no live change`

## 结论

Core Carry 不是“纯天气模型”。冻结 v2 已把同刻 `market_logit` 放进概率模型，且标准化系数
`1.838` 明显大于 forecast peak、wind、dewpoint 等单项；执行时再用完整 ask ladder 和 fee
计算 net EV。当前缺口不是“完全没用市场”，而是天气语义仍只有 local hour、peak clock、
dewpoint depression 和 wind speed，不能直接表达路径转折。

本轮选择的 challenger 是 weather-only LLM transition card：LLM 只把 PIT 天气包压成版本化
类别，不输出概率、EV、gate 或订单。后续概率头在同一行上比较：market、冻结 Core、
market+deterministic transition、market+deterministic+LLM card。盘口继续作为概率 prior；
spread/depth/imbalance/price drift 留在执行与成交质量层，避免 LLM 偷看市场后循环论证。

## 新增语义

- `fresh_runway / active_warming / plateau / pullback / fade` 与未来 0–1h、1–3h 转换；
- warm/cold advection 与 boundary-layer mixing maintenance 分离，不按风向单独判平流；
- clearing/clouding、rain onset/persistence、dewpoint rise/fall；
- second heat lobe、reheat risk、source conflict、缺失证据和 invalidation signals；
- pressure/upstream network 与 TAF 缺失时显式记 `unclear`，不做静默推断。

## 初版撤回与 balanced contrast

初版只在四城两日的 first city-day 上生成 3 张 `gpt-5.4-mini` 卡，遗漏了真实 policy hits
和大量 policy rejects 的对照，不能回答 challenger 问题；该 seed 设计已撤回，不再作为证据。

修订 prereg 固定 `2026-07-24..2026-08-07` 当前 raw universe。2,103 rows 中有 1,165 个
`scored_by_current_yes_core_artifact` checkpoint，覆盖 357 city-days、12 target dates；28 个
policy selected，1,137 个 rejected。所有 1,165 行均进入 ledger，473 行/10 target dates
已有 canonical settlement。

强模型层使用 `gpt-5.4 / medium reasoning`，不读取 market、Core 概率、selection role 或 label。
为避免让 1,137 个重复 checkpoint 淹没 28 个真实 hit，预先固定 63 个对照点：全部 28 个
selected、7 个同城同日较早的 non-positive-EV near miss、28 个来自 never-selected city-day 的
城市/market/时钟匹配控制。63/63 卡生成成功，错误 0；其中 37 行已结算、覆盖 8 个日期。

## 全分母与执行结果

| bucket | rows | settled | hold rate | mean market | mean Core | market Brier/logloss | Core Brier/logloss |
|---|---:|---:|---:|---:|---:|---:|---:|
| selected | 28 | 19 | 94.7% | 92.35% | 94.92% | .04292/.16446 | .04309/.15342 |
| non-positive taker EV | 384 | 156 | 96.2% | 92.52% | 90.75% | .03873/.16319 | .04188/.17459 |
| market domain reject | 464 | 184 | 29.9% | 34.57% | 33.57% | .14054/.42350 | .14434/.43638 |
| market support reject | 208 | 85 | 100% | 99.57% | 99.49% | .00002/.00428 | .00004/.00507 |

checkpoint-weighted 473 行总体 market Brier/logloss 为 `.07690/.24791`，Core 为
`.07934/.25607`。这只是 retrospective 描述，未做 target-date equal-weight bootstrap，但足以
否定“未选中的 YES 都是模型漏单”：non-positive-EV 组虽然经常最终 YES，market 平均已经
92.5%，且在同 rows 上明显比 Core 更准。

63-card matched slice 的 fixed-10、effective-cost 反事实也说明“方向正确”和“值得买”不同：

| role | settled / dates | wins | fixed-10 PnL | ROI |
|---|---:|---:|---:|---:|
| selected | 19 / 8 | 18 | +$1.85 | +1.04% |
| same-city-day near miss | 6 / 5 | 5 | -$5.89 | -10.55% |
| never-selected matched control | 12 / 5 | 12 | +$4.64 | +4.02% |

最后一组只有 5 个日期且是 retrospective matched slice，不能据此放宽 entry；它只是下一轮
forward collector 需要重点覆盖的 candidate。near-miss 组则直接显示：5/6 方向猜对仍会因买得
太贵而亏钱，net-EV gate 不是多余过滤。

## 强模型卡是否补到了天气语义

没有形成可用区分。selected 中 28/28 被判 `current_high_holds/fade`，27/28 为 low reheat；
matched controls 中也有 26/28 被判 `current_high_holds/fade`，24/28 为 low reheat。60/63 卡的
evidence quality 只有 `partial`，主要缺 pressure/upstream network、TAF transition 与更密集的
dewpoint/source path。

更关键的是，两个已结算失败 case 都没被强模型识别：Lucknow selected loss 和 Wuhan near-miss
loss 均被判为 `fade + low reheat`。也就是说，换掉 mini 后，当前 PIT packet 仍不能把真正的
upward-exit 尾部风险与普通 carry 区分开；问题不只是 LLM 大小，还包括输入证据缺失和该类风险
本身的低频性。

## 验收与下一动作

当前动作是 **不把 LLM card 加入 Core、不改 gate、不改 live**。保留 full-ledger collector，
但下一轮优先补 upstream/pressure/TAF/dewpoint transition 的 first-seen coverage，然后把这些
字段变成 deterministic features；只有它们能在 OOF/frozen forward 上相对 market 改善
Brier/logloss，才值得重新测试 LLM residual。现有 63-card retrospective slice 只用于 failure
taxonomy，不用于训练或 live veto。

`significance=FAIL baseline=FAIL forward=FAIL conclusion=inconclusive`。

## 血缘与产物

- original seed prereg: `2026-08-07-core-carry-llm-transition-card-v1-preregistration.json`
- balanced prereg: `2026-08-07-core-carry-llm-transition-card-balanced-contrast-preregistration.json`
- runner: `scripts/analysis/reheat_risk/core_carry_llm_transition_card.py`
- card contract: `src/strategies/weather_edge_v1/tools/intraday_transition_card.py`
- machine artifacts: `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/core_carry_llm_transition_card_v1/balanced_policy_contrast_gpt54/`

## 同轮生产数据缺口

`2026-08-06T20:05:10Z..2026-08-07T00:05:34Z` 的 snapshot 发布中断约四小时：
Open-Meteo 429 后，builder 已正确读到小于六小时且有 archive/hash 的缓存 forecast curve，
但 publish guard 错把“本轮 fresh capture 非空”当成唯一证据，连续拒绝 20 次发布。受影响的
Core universe 是 14 城、39 个潜在小时 checkpoint；失败 snapshot 未保存完整 candidate，
因此这 39 个不能写成 missed signal/order，也没有已证实的错误订单、fill 或 realized-PnL 影响。

修复后 publish guard 会逐个核对 fresh 或 exact durable archive evidence，缺证据仍 fail closed。
控制仓 commit `d1cf1ade`，生产 commit `0f1e6d96`；重启后的
`snapshot_20260807_0947.json` 发布 1,012 rows，Core Carry 已消费该文件。
