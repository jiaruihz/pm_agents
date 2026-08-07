# Core Carry LLM Transition Card v1

Status: `prospective zero-notional collection started / no live change`

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

## 首批固定分母

输入为当前 Core raw `pre_live_scores.jsonl`，范围 `2026-08-06..07`，四城
Busan/Lucknow/Munich/Wellington。signal funnel：2,102 raw rows → 73 date rows →
9 city rows → 9 Core-scored → 5 frozen market-domain → 5 bounded exact →
4 research-window checkpoints。按 first city-day 选出 3 张卡，全部由 `gpt-5.4-mini`
成功生成，错误 0。

三档 8 月 6 日已通过 canonical `settlement_outcomes` 结算为 YES：

| city / bracket | market | frozen Core | LLM transition card | outcome |
|---|---:|---:|---|---:|
| Busan 36 | 0.8450 | 0.7805 | plateau→fade；low reheat | 1 |
| Munich 24 | 0.8110 | 0.8169 | fade→upward exit；high reheat | 1 |
| Wellington 10 | 0.9870 | 0.9866 | plateau→fade；low reheat | 1 |

这三行只能检查语义是否有分歧，不能证明 challenger 有 alpha。尤其 Munich 卡判断 upward
exit，但 exact 24 最终仍胜，说明 LLM 不能做 veto。仅一个 target date 的 seed score 为：
market Brier/logloss `0.01997/0.13033`，Core `0.02730/0.15454`；样本远不足以下结论。

## 验收与下一动作

prereg 已冻结：至少积累 10 个新 settled target dates，才按 target-date block bootstrap
比较 city-day equal-weight Brier/logloss。只有 challenger 同时胜过冻结 Core，且两个改善的
95% CI 都排除 0，才讨论替换；否则 transition card 只保留解释/collector 角色。

历史 1,349-checkpoint artifact 已不在当前 artifact store，且历史 TAF/upstream/pressure
first-seen 覆盖不足，所以不补造 retrospective LLM card。当前没有改 Core live 概率、gate、
sizing 或 execution。

## 血缘与产物

- prereg: `2026-08-07-core-carry-llm-transition-card-v1-preregistration.json`
- runner: `scripts/analysis/reheat_risk/research_core_carry_llm_transition_card_v1.py`
- card contract: `src/strategies/weather_edge_v1/tools/intraday_transition_card.py`
- machine artifacts: `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/core_carry_llm_transition_card_v1/`

## 同轮生产数据缺口

`2026-08-06T20:05:10Z..2026-08-07T00:05:34Z` 的 snapshot 发布中断约四小时：
Open-Meteo 429 后，builder 已正确读到小于六小时且有 archive/hash 的缓存 forecast curve，
但 publish guard 错把“本轮 fresh capture 非空”当成唯一证据，连续拒绝 20 次发布。受影响的
Core universe 是 14 城、39 个潜在小时 checkpoint；失败 snapshot 未保存完整 candidate，
因此这 39 个不能写成 missed signal/order，也没有已证实的错误订单、fill 或 realized-PnL 影响。

修复后 publish guard 会逐个核对 fresh 或 exact durable archive evidence，缺证据仍 fail closed。
控制仓 commit `d1cf1ade`，生产 commit `0f1e6d96`；重启后的
`snapshot_20260807_0947.json` 发布 1,012 rows，Core Carry 已消费该文件。
