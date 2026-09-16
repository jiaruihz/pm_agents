# Ladder Mass Transport：补齐至 8/8 的冻结 Forward 复核

**终态：`BRANCH_EXHAUSTED`。未创建或启动 zero-notional shadow；orders=0，fills=0。**

## 1. 这次纠正了什么

上一版训练并不是从 7 月才开始：direct-book 历史从 **2026-05-19** 开始。固定时间切分保持不变：

- historical training：2026-05-19..07-10，874,281 fixed rungs / 53 dates；
- development/model selection：2026-07-11..07-21，15,729 rungs / 11 dates；
- frozen historical validation：2026-07-22..07-28，23,887 rungs / 3,174 snapshots / 7 dates / 47 cities；
- post-freeze forward：2026-07-29..08-08，本报告首次一次性评分，不参与 feature、alpha、selector 或表达选择；
- true untouched forward：2026-08-11..08-17，尚未开始。

第一次延长到 8/8 时误用了 `strategy_snapshots` 消费视图，每个 ladder snapshot 实际只剩一条可评分 rung，因此该产物已改名为 `ladder_mass_transport_forward_20260808.partial_strategy_view_invalid`，只保留审计，不能引用为研究结果。

本次从 append-only `market_books/batches` 恢复同一 capture batch 的 YES/NO direct books，以整组最后一次 response/fetch 作为保守可见时钟，并追加进入 canonical `tmax_v2_ladder_snapshots/rung_quotes`。补入 47,806 ladder snapshots / 525,565 rung quotes，覆盖 7/29..8/8、11 dates、47 cities；其中新增入库 44,315 / 487,164。冻结 forward 的 30-minute sampled fixed denominator 为 148,961 rungs / 19,375 snapshots / 11 dates / 47 cities。

Settlement head 与 60m markout head 分开。7/29、7/30 settlement 仅各覆盖 2/47 cities；7/31..8/8 为 47/47。该缺口不影响 60m markout label，但限制早两日 settlement proper-score 的解释。

## 2. 固定模型

development 只打开一次并冻结：`M2 ladder transition`，Ridge alpha=100，primary horizon=60m。M2 相对 M1 只增加 mode/entropy/skew/tail/multimodality、相邻比与曲率、跨 snapshot mass transport、neighbor lead-lag 和 rung move minus ladder common move。`static kink` 只作 control；M3 weather-event response lag 没有被选中。

旧 7/22..28 frozen historical validation 上，M2 60m MSE `0.00115401`，相对 M0/M1/static 的 delta 为 `-0.00003554/-0.00000772/-0.00003470`，target-date CI 均小于 0。这个结果保持冻结，但不能替代 7/29..8/8 的 forward。

## 3. 7/29..8/8 forward 结果

| 60m 指标 | M0 | M1 | M2 candidate | static kink |
|---|---:|---:|---:|---:|
| relative-markout MSE | 0.001535 | 0.001545 | **0.001540** | 0.001534 |
| direction Brier | 0.236244 | 0.220461 | **0.217202** | 0.235025 |
| direction logloss | 0.665545 | 0.637910 | **0.628659** | 0.663094 |
| direction AUC | 0.4643 | 0.4888 | **0.5042** | 0.4926 |
| settlement Brier | 0.792867 | 0.775900 | **0.771466** | 0.789859 |
| settlement logloss | 1.846859 | 1.792408 | **1.779054** | 1.835940 |

M2 对 M1 的 60m markout MSE delta 为 `-0.00000596`，CI `[-0.00000742,-0.00000337]`；但相对 M0 为 `+0.00000413`，CI `[-0.00000103,+0.00000831]`，相对 static kink 为 `+0.00000554`，CI `[-0.00000003,+0.00000986]`。因此没有同时打败固定 controls。

绝对 sanity 更弱：M2 date-equal MSE `0.00153961`，zero predictor 为 `0.00152827`。direction accuracy `71.598%`，而永远预测 non-positive 是 `71.606%`；AUC 仅 `0.5042`。direction Brier/logloss 的改善主要是 base-rate calibration，不能当成可排序的独立方向 alpha。

稳定性也不支持建 allowlist：逐城 M2 仅 10/47 优于 M0，leave-one-city-out 后 0/47 个 pooled comparison 优于 M0；9 个 spread×depth regimes 只有 `wide_thin` 同号，反而更像薄盘口/陈旧报价风险。禁止据此事后挑城市或 liquidity regime。

## 4. 它是不是反向指标

**不是。** 固定 max-minus-min pair selector 的排序方向是正确的：9,861 个一股全腿可执行报价 rows 上，实际 pair-relative markout 合计 `+$110.8195`，平均 `+1.1238c/pair`，10/11 target dates 为正，target-date bootstrap CI `[$73.8365,$145.1532]`。把 selector 符号翻转后为 `-$111.5485`，仅 1/11 dates 为正。

这说明模型有弱的横截面 extreme-rung 排序能力，但回归幅度与全 rows proper loss 不合格；“taker 全城亏”不能解释成训练出反指。

## 5. 为什么交易仍然全亏

固定表达：每个 city-date-event 首个不重叠 positive signal，买 1 share predicted-max rung YES + 1 share predicted-min rung NO，持有 60m。

| 表达 | PnL | ROI | target-date CI | 日期 / 城市 |
|---|---:|---:|---:|---:|
| taker entry → taker exit | **-$273.0897** | **-2.7087%** | `[-$341.009,-$216.073]` | 11/11、47/47 亏 |
| maker entry → taker exit | +$76.7781 | +0.7889% | `[$50.262,$102.376]` | conditional price only；无 fill 证据 |
| taker entry → maker exit | +$40.7242 | +0.4039% | `[$8.301,$70.904]` | conditional price only；无 fill 证据 |
| maker entry → maker exit | +$390.5920 | +4.0134% | `[$305.449,$467.330]` | 双边 100% maker 幻想，不可验收 |

纯 relative move `+1.1238c/pair` 小于平均 crossing loss `2.8402c/pair` 加 fee `1.0529c/pair`。taker 路径需要额外改善 `2.7694c/pair`（entry-only 或 exit-only 每腿 1.3847c；四腿共同改善每腿 0.6923c）才到 break-even。

maker 数字不是 fill replay：future touch 不算 fill，历史 WS sequence/trade prints/queue position 尚未满足 readiness。maker-entry+taker-exit 在每个 maker leg 仅 0.5c adverse selection 时已从 +$76.78 变为 -$21.83；double-maker 在每腿 1c 时变为 -$3.85。generic ladder-maker 的同价 maker-entry+taker-exit为 -$30.70。故不能部署。

收益/亏损不由少数样本驱动：taker 最大 absolute date share 16.18%，最大 city share 4.23%。失败是普遍 execution friction，不是单日或单城赢家噪声。

## 6. 结论与唯一下一步

该分支没有满足“60m proper score 同时胜 M0/M1/static + 可执行 PnL CI>0”的联合条件，最终定为 `BRANCH_EXHAUSTED`。不创建 selector runtime、`SignalCandidate`、`TradeIntent` 或 zero-notional shadow；也不修改任何 live 配置。

**唯一下一步：转向独立的 weather-event-conditioned order-flow/queue response 机制。** 先完成 WS book sequence、REST parity、真实 trade prints 与 order lifecycle materializer，再检验 signed flow、queue depletion/replenishment 是否预测可成交的 60m move，并把 candidate passive fills 与 generic ladder maker 做同 fill denominator 对比。不得继续给 M2 增加 city、price、spread、depth gate，也不得用 future touch 代替 fill。

## 7. 可复现入口

- runner：`weather_model_evaluation/lmvm_repricing_challenger.py --mass-transport-forward-parent ...`
- evaluator：`weather_model_evaluation/ladder_mass_transport.py`
- raw→canonical adapter：`weather_data_feed/market_book_ladder_history.py`
- bounded materializer：`scripts/etl/materialize_market_book_ladders_canonical.py`
- parent freeze artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/market_structure_edge/ladder_mass_transport_time_aligned_20260809`
- forward artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/market_structure_edge/ladder_mass_transport_forward_20260808`
