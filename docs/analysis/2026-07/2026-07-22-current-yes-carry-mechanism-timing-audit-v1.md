# Current-YES Carry 机制 / 入场时机 / 执行一致性审计 v1

Status: `research_audit / no_new_selector / no_live_change`
Generated: `2026-07-22T07:24:05+00:00`

## 数据快照

- 历史状态/行情总表：`intraday regime atlas shards via explicit current-YES + per-city assigned-model loader`，2026-05-19..2026-07-08，4061 state rows / 46 target dates / 36 cities。
- Per-city 固定 `CITY_MODEL` 的双模型 Single Runs PIT 列覆盖 4061/4061 行、一直到历史末日 2026-07-08；本次主结果没有再把 forecast coverage 混入 signal funnel。
- 行情：原始 orderbook snapshot 同时点 direct bid/ask；当前分析表 quote 非空率 100.0%，但这是 loader 先选 evidence-complete 行的结果，不是上游原始 signal 的 100% 覆盖率。天气 observation age 中位数约 31.9 分钟；历史每城每小时只保留一帧（通常约 :30），不能声称 5–15 分钟 timing。
- 结算：4061/4061 settled；unsettled=0；missing_bracket=0。
- 最近 H1 shadow/execution：`2026-07-22T06:20:36+00:00`；CLOB coverage gate=`True` / 1113 live_real fill rows。数据动作：fixed-model Single Runs backfill refreshed through 2026-07-08; affected feature shard and atlas rebuilt; canonical DB was not full-rebuilt。
- 费用主口径：fresh/direct ask taker，`fee=0.05*p*(1-p)`；maker/rebate 不计入任何 alpha 结论。

## 结论

1. 按固定城市模型重算后，`bias_known ∧ ceiling_busted ∧ path_faded` 只有 171 个 first city-day，taker ROI -0.11%，95% CI [-5.18%, +4.75%]；机制尚未验证。
2. 简单把 CC 与旧 H1 条件相交没有稳定增量；proper-score OOF 也没有同时、稳定改善 Brier 和 logloss，不能因为单个 ROI/Brier 数字好看就合成新模型。
3. 去掉 H1 的 0.95 价格门后是 322 个 city-day、ROI +0.67%；保留旧 0.95 门是 268 个、ROI -0.19%。价格只应进入 EV/成本，当前证据不支持把 0.95 当机制门。
4. 入场时机目前只能确认一个方向：等待会提高市场确认度，但会买贵并丢失/更换部分 bracket；小时级 archive 不能决定分钟级最佳等待。
5. 新算法暂不建立。固定模型历史已补齐；剩余工作是把 date-order bias 升级成 availability-time PIT，并将所有 observed/blocked state、fresh quote/depth 和最终 label 接到同一零仓位 forward collector，之后才冻结 taker-only residual 实验。

## 先修正的数据语义

旧 loader 没显式选择 current-YES expression：current bracket 的 YES/NO forecast max 在 2480/12270 个可配对 city-hour 不同，peak clock 在 2845/12270 个不同。
同时，旧 bias 按历史 state row 加权且缺失时回填 0；修正为 explicit YES + 每个历史 city-day 等权 + bias 缺失记 coverage gap 后，首次 route 从 196 变为 183。
更关键的是 forecast lineage：该中间口径的 183 个 route 中，只有 102 个实际 source family 与 `CITY_MODEL` 对齐，另 81 个不对齐；后者 ROI +6.40%，显著抬高了原 headline。改用表内双模型的指定列后 route 为 171 个。这些变化属于数据语义/覆盖影响半径，不是策略漏斗。

| forecast lineage slice | city-day | win | avg ask | taker ROI | 95% CI |
|---|---:|---:|---:|---:|---:|
| mixed_forecast_all | 183 | +94.54% | 0.917 | +2.77% | [-1.64%, +6.48%] |
| mixed_forecast_assigned_family_aligned | 102 | +91.18% | 0.910 | -0.17% | [-7.73%, +6.43%] |
| mixed_forecast_assigned_family_mismatched | 81 | +98.77% | 0.925 | +6.40% | [+3.10%, +9.90%] |
| fixed_city_model_reconstruction | 171 | +91.81% | 0.916 | -0.11% | [-5.18%, +4.75%] |

## Signal funnel 与机制 selector

| selector | city-day | win | avg ask | taker ROI | 95% CI | front / back ROI | ask size≥5 / ≥10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| cc_route_legacy_loader_audit_only | 196 | +94.39% | 0.913 | +3.07% | [-0.58%, +6.26%] | +3.00% / +3.24% | +96.94% / +82.14% |
| cc_route_expression_bias_clean_mixed_forecast_audit_only | 183 | +94.54% | 0.917 | +2.77% | [-1.64%, +6.48%] | +3.59% / +0.49% | +96.72% / +82.51% |
| ceiling_busted_only | 401 | +73.07% | 0.753 | -3.74% | [-8.54%, +0.94%] | -3.66% / -3.93% | +97.01% / +73.32% |
| path_faded_only | 614 | +79.97% | 0.825 | -3.55% | [-6.42%, -0.81%] | -3.04% / -5.21% | +95.28% / +76.55% |
| cc_route_fixed_city_model | 171 | +91.81% | 0.916 | -0.11% | [-5.18%, +4.75%] | +1.37% / -4.33% | +97.08% / +79.53% |
| legacy_incumbent_gate_no_price | 322 | +94.10% | 0.932 | +0.67% | [-1.83%, +3.18%] | +1.07% / -1.03% | +96.89% / +81.99% |
| legacy_incumbent_gate_ask_ge_0p95 | 268 | +97.76% | 0.978 | -0.19% | [-2.05%, +1.41%] | +0.66% / -3.71% | +97.76% / +86.19% |
| cc_and_legacy_incumbent | 97 | +94.85% | 0.940 | +0.67% | [-5.71%, +6.00%] | +2.76% / -8.87% | +96.91% / +81.44% |
| cc_and_mature_fade | 149 | +91.95% | 0.918 | -0.12% | [-5.80%, +5.49%] | +1.06% / -4.11% | +97.99% / +78.52% |
| cc_and_not_warming | 156 | +92.31% | 0.919 | +0.09% | [-5.11%, +4.96%] | +1.74% / -5.10% | +97.44% / +78.85% |

前两个 CC 行仅用于影响对照；primary 是 `cc_route_fixed_city_model`。`bias_known` 与 fixed-model availability 是证据完整性，不是新增 alpha 阈值。

`ceiling_busted` 或 `path_faded` 单独均未形成可靠 taker alpha；固定模型下两者交集点估也未为正，CI 跨 0。本轮还比较过多个 regime 组合且未做多重检验校正，所以最多保留为 frozen-forward 候选机制。

## 概率增量：同 rows expanding OOF

本节只用 fixed-model evidence-complete 的 4061 行，截止 2026-07-08。每个测试日只用严格更早日期训练；训练和评分都让每个 city-day 总权重相等，避免重复状态或覆盖差异过度加权。所有模型都先看到 market midpoint，再检验天气机制能否提供 residual。固定 6 个模型，未调超参数。

| model | OOF rows | Brier | Logloss | Brier Δ vs raw / market-cal | Logloss Δ vs raw / market-cal |
|---|---:|---:|---:|---:|---:|
| market_raw | 2758 | 0.1129 | 0.3614 | — / — | — / — |
| market_cal | 2758 | 0.1137 | 0.3648 | 0.0008 / — | 0.0034 / — |
| market_plus_ceiling | 2758 | 0.1134 | 0.3645 | 0.0005 / -0.0003 | 0.0031 / -0.0003 |
| market_plus_path | 2758 | 0.1137 | 0.3651 | 0.0008 / 0.0000 | 0.0038 / 0.0003 |
| market_plus_cc | 2758 | 0.1135 | 0.3660 | 0.0006 / -0.0002 | 0.0046 / 0.0012 |
| market_plus_legacy_physics | 2758 | 0.1135 | 0.3607 | 0.0006 / -0.0002 | -0.0007 / -0.0041 |
| market_plus_combined | 2758 | 0.1132 | 0.3608 | 0.0003 / -0.0005 | -0.0005 / -0.0040 |

负的 loss delta 才代表改善。这里的判定不能只看一个点估：Brier 与 logloss 方向不一致、或日期 bootstrap CI 跨 0，都视为该机制尚未证明。完整 CI 在 JSON。

天气增量应先与相同训练过程的 `market_cal` 比，raw market 同时保留为最终外部基线。

**Combined 相对 market-cal 的时间/目标切片稳定性**

| slice | rows/dates | Brier Δ [95%CI] | Logloss Δ [95%CI] |
|---|---:|---:|---:|
| all_oof_states | 2758/32 | -0.0005 [-0.0033, 0.0025] | -0.0040 [-0.0109, 0.0036] |
| oof_early_dates | 1688/16 | -0.0015 [-0.0053, 0.0024] | -0.0064 [-0.0149, 0.0019] |
| oof_late_dates | 1070/16 | 0.0011 [-0.0028, 0.0050] | -0.0004 [-0.0123, 0.0113] |
| carry_market_mid_ge_0p80 | 1350/31 | 0.0001 [-0.0018, 0.0018] | -0.0023 [-0.0100, 0.0052] |
| cc_route_states | 184/30 | 0.0029 [-0.0053, 0.0122] | 0.0098 [-0.0264, 0.0552] |

旧 V3.1 residual 不能直接复用：它的 weather residual 相对 raw market 虽把 Brier 从 0.0912 改到 0.0895，但 logloss 从 0.2914 恶化到 0.3038；主交易规则 ROI -1.59% 且 CI 跨 0。它还使用重复 state rows、旧的无官方 fee 交易口径。

## 入场时机（小时级可执行回放）

| policy | selected/base | win | avg ask | taker ROI | CI | paired ask Δ | paired ROI Δ | bracket changed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| route_immediate | 171/171 | +91.81% | 0.916 | -0.11% | [-5.18%, +4.75%] | 0.0000 | +0.00% | +0.00% |
| fixed_wait_next_hour | 74/171 | +91.89% | 0.927 | -1.19% | [-8.61%, +4.86%] | 0.0780 | -2.45% | +5.41% |
| route_reaffirmed_next_hour | 40/171 | +92.50% | 0.957 | -3.54% | [-14.54%, +4.87%] | 0.0754 | -7.97% | +0.00% |
| route_first_at_15_or_later | 108/171 | +94.44% | 0.946 | -0.42% | [-5.74%, +4.35%] | 0.0312 | -1.25% | +1.85% |
| route_first_at_ask_ge_0p95 | 137/171 | +96.35% | 0.976 | -1.44% | [-4.95%, +1.42%] | 0.0341 | -2.68% | +0.73% |
| route_plus_mature_fade | 149/171 | +91.95% | 0.918 | -0.12% | [-5.80%, +5.49%] | 0.0017 | -0.18% | +0.00% |

固定等一小时与 next-hour route 再确认是不同策略：前者无论机制是否仍成立都进，后者在有下一帧但 route 消失时拒绝。17 点首次触发后无法在 13–17 研究窗内再等一小时的行单列为 replay-window boundary；17 点前没有下一帧的才是 archive coverage gap，均不伪装成策略过滤。价格到 0.95 才进也是可执行 timing policy，但它不能反过来证明 0.95 是天气机制。

最近 30 秒 shadow 的补充证据：15 点前 H1 为 11 settled、ROI -6.52%；15–17 点为 16 settled、ROI +2.25%。两者只有 4–5 个日期，而且唯一 SF loss 在早段；这支持继续研究 later timing，不足以设 15 点 hard gate。

## Historical ↔ Live parity 阻塞项

| 项目 | 历史研究 | 当前 live/shadow | 结论 |
|---|---|---|---|
| 决策 cadence | 每城每小时一帧 | runner 30 秒，天气源按各城 cadence | 只能研究小时级 policy；分钟级必须 forward |
| forecast | D-1 12Z Single Runs 的 GFS/ECMWF 双列已补到 2026-07-08，再按 CITY_MODEL 取列 | live curve 按 CITY_MODEL | 历史 coverage 已修；仍需 golden-row 核对 curve 构造，并统一保存 assigned/actual/run/hash |
| forecast bias | 仅按 target_date 严格早日计算，未验证 label available_at | shared bias 模块存在，但 H1 决策未消费 | 目前只是 date-PIT proxy，必须升级 availability-time PIT |
| path state | canonical intraday/running-max labels | live 已有同名 labels | 需要 golden-row parity，而非相信字段同名 |
| weather扩展特征 | 历史缺 solar/雨/风向/TAF transition 完整 PIT | forward 已开始采 | 只能作为 forward ablation，不能事后补历史 |
| 盘口 | 同时点 direct bid/ask/size，小时粒度 | 下单前 fresh CLOB | replay 必须用 fresh ask；无 top-size 时记 execution coverage gap |
| 表达/费用 | exact current bracket YES；官方 taker fee | 相同 | parity 可实现 |
| 去重 | 首次 city-day | family city-day submitted 去重 | selector shadow 也必须记录所有 observed/blocked |

## 晋级检查表

| 环节 | 状态 | 当前证据 |
|---|---|---|
| 机制本身 | FAIL | fixed-model route 171 个，ROI -0.11%；后段 45 个为 -4.33% |
| 相对 market 的概率增量 | FAIL | combined vs market-cal：Brier Δ -0.0005，logloss Δ -0.0040；两者日期 bootstrap CI 均跨 0 |
| 小时级入场时机 | FAIL / 未冻结 | 等到 ask≥0.95 的 paired ROI Δ -2.68%；next-hour reaffirm Δ -7.97% |
| 历史 forecast PIT | PASS-COVERAGE / PARTIAL-PARITY | fixed-model evidence 到 2026-07-08；bias 仍只有 date-order proxy |
| Taker 可执行性 | PARTIAL | archived 5/10-share 全档 VWAP 已回放；尚无 compute→submission→exchange latency 回放 |
| 生产同分母 forward | FAIL | 新 selector 尚无全量 observed/blocked candidate ledger，也无 golden-row parity |
| Maker | DEFERRED | 仅 6 个 paired opportunity，必须用 planned denominator/ITT，不能用 filled-only 盈利 |
| 组合风险 | FAIL | 历史信号均值 3.72 个/覆盖日，p90/max=6.9/10；5-share 最差日约 $-8.99，尚未冻结相关敞口和 size policy |

## Taker 第一版：固定数量逐档 VWAP

| shares | raw book match | full executable | avg VWAP | avg effective cost | slippage vs best ask | VWAP ROI / best-ask ROI |
|---:|---:|---:|---:|---:|---:|---:|
| 5 | +100.00% | +100.00% | 0.9161 | 0.9192 | 0.004c | -0.11% / -0.11% |
| 10 | +100.00% | +100.00% | 0.9168 | 0.9199 | 0.074c | -0.19% / -0.11% |

这一步直接吃 archived full ask ladder，不再假设 10 shares 全部能在 best ask 成交。仍未模拟 signal 计算到订单到达交易所之间的延迟；该 gap 只能由 forward 的 decision/book/submission/exchange 时间戳测量。
历史 candidate volume 是 3.72 个/覆盖日；有信号日期的中位数/p90/max 为 4.0/6.9/10。按每单 5 shares，最差 target date `2026-06-12` 的组合 PnL 约 $-8.99；这是风险测量，不据此新增日上限。

## Maker 留口，但不进入 v1

CLOB gate 已通过。现有同 signal 配对只有 6 个 opportunity：passive fill rate +50.00%，filled-only 节省 0.571c/share；但 planned denominator maker-taker ROI delta 为 -0.49%。所以第一版必须按 taker 评估。

后续 maker 只保留这些字段：同 signal 的 `taker_now` counterfactual、queue-ahead、每次 reprice 的bid/ask/size、partial fill、cancel reason、真实 exchange fill time、1/5/15m 与 next-weather-epoch markout。没有这些字段前，maker upper bound 不进入收益。

## 冻结前研究结论

```text
significance=FAIL / exploratory mechanisms and timing are not multiplicity-corrected forward evidence
baseline=PARTIAL / same-row market proper-score baseline is now explicit; no combined model has passed both scores
forward=FAIL / fixed-model history coverage repaired, but CC has no production-parity full-denominator forward sample
conclusion=inconclusive; do not create or deploy a new carry selector yet
```

下一步不是再找一个阈值，而是建立统一 `carry_candidate_frame_v1`：每个 active city-day 的每个 checkpoint同时保存 market baseline、CC 连续量、旧物理连续量、transition/source uncertainty、fresh taker book 和最终 label。冻结后只做四组同 rows A/B：market、market+CC、market+旧物理、market+combined；只有 combined 在 proper score、taker residual、前瞻和 execution parity 同时过关，才命名为新算法。

## 8 环覆盖

- 已覆盖：描述性绩效、日期 bootstrap、机制判别、proper score、taker 执行价格、top-ask 容量覆盖、同日相关性、market baseline。
- 未覆盖到可晋升：availability-time bias、独立 frozen forward、分钟级 timing、完整 transition PIT 历史、maker queue/fill hazard、taker submission latency 与更大数量的容量。

## 产物

- Script: `scripts/analysis/reheat_risk/research_current_yes_carry_mechanism_timing_audit_v1.py`
- JSON: `docs/analysis/2026-07/2026-07-22-current-yes-carry-mechanism-timing-audit-v1.json`
- OOF rows: `docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1/probability_oof_rows.csv`
- Timing rows: `docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1/route_immediate_rows.csv`
- Expression/bias semantics impact: `docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1/expression_bias_semantics_impact.csv`
- Forecast-model semantics impact: `docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1/forecast_model_semantics_impact.csv`
- Taker VWAP rows: `docs/analysis/2026-07/generated/current_yes_carry_mechanism_timing_audit_v1/taker_vwap_rows.csv`
