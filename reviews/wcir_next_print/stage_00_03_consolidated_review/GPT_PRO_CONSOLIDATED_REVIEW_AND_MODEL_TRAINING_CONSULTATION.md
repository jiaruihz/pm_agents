# WCIR Next Official Print — Stage 0–3 综合审阅与后续模型训练意见征求

## 给 GPT Pro 的审阅指令

请将本文件视为本次综合审阅请求；附件中的执行计划、旧 prompt、历史报告和各阶段 packet 是证据与阶段合同，不能自行扩大为新实施授权。Authoritative execution plan 仍是：

```text
authoritative/WCIR_NEXT_PRINT_CODEX_MASTER_PLAN_v1.md
```

本次请完成两件事：

1. 对 Stage 0 rev2、Stage 1、Stage 2、Stage 3 分别给出 disposition 和 blocking fixes；
2. 在不默认放宽 Stage 3 gate 的前提下，对后续数据采集与 Stage 4 `NextPrintModel` 训练设计给出明确意见。

本材料不是 Stage 4 实施请求，不授权模型训练、selector/threshold/position policy 变更、collector 扩容、production config、order path 或 live 行为变更。

---

## 一、项目目标与当前统一假设

五城：Amsterdam / Helsinki / Busan / Seoul / Tokyo。

统一研究假设：利用下一次 settlement-relevant official print 之前可获得的 PIT fast-source observation，预测下一次 official native value/tick/跨档状态，并在市场完成定价前获得可执行的 pre-report 或 report-transition repricing。

研究链拆成三层：

```text
Layer A — NextPrintModel
P(next official print | PIT fast observations, path, forecast, city adapter)

Layer B — MarketReactionModel
P/E(executable market move | next-print distribution, pre-print market state)

Layer C — SettlementUpdateModel
P(final settlement | updated official path, market prior)
```

当前主轴只审阅 A 的前置资格以及 B 的 information-oracle 上限。任何“快源预测准确”都不能替代同分母 executable market baseline、官方 fee、target-date block inference 和 frozen forward。

---

## 二、Stage 0–3 总体状态

| Stage | 已完成内容 | 内部结论 | 当前待 GPT Pro 裁决 |
|---|---|---|---|
| Stage 0 + rev2 | repository/runtime/data boundary；position-scope truth；immutable scorecard input；duplicate audit；zero-notional；offline reproduction | evidence closure 完成；historical 101-row headline immutable；orders/fills/notional=`0/0/0` | 是否接受 repo/data/policy boundary；如何处置历史 production-health blocker |
| Stage 1 | 五城 canonical observation/official/market identity；causal clocks；legacy registry/reconciliation；research-only append-only journal | offline contract 与 58 tests 通过；18,726 legacy serializations 保留；旧 frozen input 缺 native/outcome/book clocks，故不伪造 causal denominator | 是否接受 canonical/clock contract；production incident 是否仅作为部署 hard gate |
| Stage 2 | deterministic WS book reconstruction；gap/reconnect；REST parity；fee-aware 1/5/10-share sweeps；event alignment | reconstruction mechanism 可复现；真实样本仅一完整日；source-t0 coverage 极稀疏 | 接受 book-truth contract，或要求扩大 transport replay / 修订 coverage boundary |
| Stage 3 | 841-row next-report table；lead window；simple baselines；reaction/oracle；city decision | 五城均 `CONTINUE_COLLECTION_ONLY`；Stage 4 modeling gate 不通过 | 接受继续采集，要求重做 oracle/markout，或明确授权有限城市建模 |

Stage 2/3 evidence commit：

```text
10ebb309be6091b9290193b2aa4b3de0ad8fca3c
```

Stage 0/1 的 frozen base/release SHA 另见各自 packet，不应与 Stage 2/3 evidence commit 或综合包生成时的 repository HEAD 混称。综合包生成 HEAD 由 `CONSOLIDATED_EVIDENCE_MANIFEST.json` 冻结。

---

## 三、Stage 0 rev2 审阅摘要

### 已关闭的原 Stage 0 blockers

1. Position scope production truth 已冻结：当前 lock key 包含 `model_id`；历史 101-row headline 不重写。
2. 三种 policy diagnostic：current runtime 90 kept/11 suppressed；global first-position 73/28；global opposite-side-only 84/17。
3. Frozen scorecard input：18,726 candidate serializations、18,625 unique candidates、101 intent links；cutoff/profile boundary 在 dedupe 前生效。
4. Duplicate audit：frozen denominator 101 个 duplicate IDs、历史 prefix 164 个 duplicate IDs；无未解释 immutable payload drift。
5. 完全离线重放：Gamma identities 336/336、0 errors；headline/profile/selection/policy rows 精确复现。
6. Zero-notional：orders/fills/notional=`0/0/0`。

### 保留的治理问题

- master plan 的 future global bracket invariant 与 historical/current per-model scope 不同；本轮只记录，未改 runtime policy。
- Stage 0 rev2 当时 production controller health 仍为 `critical`；strict manifest/JRS probe 曾发生外部恢复，但 controller 记录 30 个 runtime critical。此证据是当时只读快照，不等价于当前健康结论。
- 请区分：offline evidence acceptance 与未来 production deployment authorization。

### 请给出一个 disposition

```text
ACCEPT_STAGE_00_REPO_BOUNDARY_AND_PROCEED
ACCEPT_WITH_BLOCKING_FIXES
REWORK_STAGE_00_BOUNDARY
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

请同时回答：若 Stage 0 evidence 本身可接受，但历史 production incident 未在本包重新健康验收，是否应“接受 Stage 0 + 独立禁止部署”，而不是把 offline boundary 判为失败？

---

## 四、Stage 1 审阅摘要

### 合同与结果

- 五城明确区分 fast source、official/settlement anchor、native unit/lattice、first-seen、observed、issued、decision、feature-book、execution-book 和 markout clocks。
- Amsterdam 区分 `ta` point、`tx` preceding interval max 和 revision。
- Helsinki 以 provider observation time 为 native key，poll time 不能代替。
- Tokyo 以 native 10-minute checkpoint 为 key。
- Busan/Seoul 保留 station-minute runway group 的 min/mean/max/consensus、runway IDs；Seoul 另保留 preferred runway。
- source/station mismatch、相同时钟但不同 payload 的 ambiguity 均 fail closed。
- 18,726 legacy serializations、18,625 unique candidates、101 duplicate serializations原样保留；11,184 legacy information events 映射到 11,184 canonical source identities，row delta=0。
- 旧 Stage 0 输入没有冻结 native payload、next official outcome、execution book 和 markout clocks，因此 11,184 legacy headers 被明确标记 `legacy_native_payload_not_frozen`，未被伪造成新 causal performance denominator。
- 5-city conformance fixture 仅证明合同：5/5 linked、5/5 causal pass、5/5 exact market identity；不是绩效证据。
- 独立 reviewer 的 manifest build order、official source/station validation、Korea runway summary 三项 finding 已全部修复；58 tests passed。

### 外部 blocker

Stage 1 冻结的只读 production snapshot 显示 strict manifest/controller/JRS/data-feed health critical。本阶段没有做恢复，也没有在 unhealthy canonical boundary 上发布分析结论。

### 请给出一个 disposition

```text
ACCEPT_STAGE_01_CANONICAL_CONTRACT_AND_PROCEED
ACCEPT_WITH_BLOCKING_FIXES
REWORK_CITY_OBSERVATION_OR_SETTLEMENT_BOUNDARY
REWORK_CAUSAL_CLOCK_BOUNDARY
```

重点问题：

1. 五城 heterogeneous official semantics 是否足够明确？
2. legacy rows 显式排除而非事后重建，是否是正确治理选择？
3. research-only dual-write 是否足够；production deployment 是否必须单独重新做当前 health acceptance？

---

## 五、Stage 2 审阅摘要

### 真实 transport replay

- 样本日：2026-08-26，五城 event-token scope。
- 608 epochs、4 reconnect chains、12,429 relevant WS frames。
- 6,250 reconstructed states。
- 正反输入顺序产生相同 run ID：`2c33076e11ab85e03cfa21c748718d196460dc59815e437bae9db4136b94ad79`。
- 399 blocker intervals：378 parity pending、16 parity mismatch、5 delta-before-baseline；全部恢复，open=0。
- 4 个 reconnect chains 的 predecessor 位于日切片之外；显式标记，不携带外部 state，必须等 in-slice verified baseline。
- REST/WS comparison 4,032：18 full-depth parity、369 REST-prefix parity、1 best-quote-only、3,644 因 exchange clock skew >2s 不可比。
- 双边 fully executable states：1 share 5,355；5 shares 5,292；10 shares 5,256。
- official Weather taker fee 按每层 depth 计算；`queue_truth=false`，maker 仅 proxy。
- event-aligned 72 events 中，source t0 只有 2 个 valid states。
- isolated offline replay 不写 sealed root；11/11 derived artifacts byte-identical；26 tests passed。

### 请给出一个 disposition

```text
ACCEPT_STAGE_02_BOOK_TRUTH_AND_PROCEED
ACCEPT_WITH_BLOCKING_FIXES
REWORK_BOOK_RECONSTRUCTION_BOUNDARY
COLLECT_MORE_TRANSPORT_BEFORE_PROCEEDING
```

重点问题：

1. 一完整日足以接受“算法/合同”，还是 Stage 2 acceptance 必须先重放全部19日 transport？
2. 3,644/4,032 REST comparisons 因 >2s skew 不可比，是否需要按 capture lag/tick distance 分层，而不是单一2秒窗口？
3. 120秒 event-asof age 是否合理？任何修改必须依据采集 cadence/coverage 预注册，不能为提高 coverage 事后放宽。
4. 当前极低 source-t0 coverage 更像 collector/capture policy gap，还是 materializer/event alignment 定义错误？

---

## 六、Stage 3 审阅摘要

### 固定 denominator

841 个 distinct fast observation → next routine official print events，日期范围 2026-08-09..2026-08-26；所有 rows 均有 linked next official print。

| City | events | target dates | source exact | within 1 tick | 5-share/+30s executable dates |
|---|---:|---:|---:|---:|---:|
| Amsterdam | 87 | 16 | 79.3% | 97.7% | 0 |
| Tokyo | 42 | 14 | 66.7% | 100% | 0 |
| Helsinki | 59 | 15 | 76.3% | 100% | 0 |
| Seoul | 268 | 14 | 84.3% | 100% | 0 |
| Busan | 385 | 16 | 79.0% | 99.7% | 1 |

Lead-window median：Amsterdam 830.1s；Tokyo 730.9s；Helsinki 717.8s；Seoul 691.1s；Busan 947.0s。

这些数字证明“source 对 next print 有信息”，不证明可交易 alpha。

### Evidence funnel collapse

- source t0 valid book：2/72 sample-day events。
- full two-sided information oracle：不可用，不做 imputation。
- same-row market-only baseline：不可用。
- forecast-only baseline：缺 frozen PIT forecast identity，不做 imputation。
- 5-share / official+30s one-sided executable oracle：Busan 1 row / 1 date，net markout `-$0.3233`。
- target-date block CI：日期不足，不能估计晋级结论。
- market reaction A/B/C/D：pre-source/source pairs 只有2个，正式结论为 `insufficient_event_aligned_coverage`。

### 当前 city decision

五城全部：

```text
CONTINUE_COLLECTION_ONLY
```

未满足 Stage 3 `CONTINUE_MODELING` 预注册条件：≥20 target dates、≥100 eligible events、5-share feasible-latency oracle positive、one-sided 90% date-block CI lower >0、concentration checks、reaction not mostly pre-source。

### 请给出一个 disposition

```text
ACCEPT_STAGE_03_ORACLE_AND_AUTHORIZE_SELECTED_CITIES
ACCEPT_WITH_BLOCKING_FIXES
REWORK_ORACLE_OR_MARKOUT_DEFINITION
CONTINUE_COLLECTION_WITHOUT_MODELING
REDIRECT_AWAY_FROM_PRE_REPORT_ALPHA
```

请明确判断：

1. 当前应接受 `CONTINUE_COLLECTION_WITHOUT_MODELING`，还是因为 oracle 只有 prior-bracket NO 单边表达而必须 `REWORK_ORACLE_OR_MARKOUT_DEFINITION`？
2. 是否必须补全 YES/NO complementary paths、pre-source two-sided books、market-only reaction baseline 后，Stage 3 才算方向裁决完成？
3. 若 information oracle 尚不能算，能否仅依据 source accuracy 启动 Stage 4？按 authoritative plan 默认答案是不能；若建议例外，请明确写出 waiver、城市、目的、禁止用途和重新晋级门。

---

## 七、后续采集计划的意见征求

请给出优先级明确、可以执行和验收的最小采集方案。建议至少评估：

1. 五城每个 fast event 的完整 dependence group，而不是按 raw message 等权。
2. exact market identity 下 YES/NO complementary tokens 与邻近 brackets。
3. pre-source、source t0、pre-official、official +1/+3/+5/+15/+30/+60/+120/+300s 的 immutable reconstructed book IDs。
4. capture epoch/selector policy、reconnect/gap、book-valid denominator，不把“无消息”当作价格未变化。
5. frozen PIT forecast run identity，补上 forecast-only baseline。
6. same-row market-only reaction baseline 所需 full-ladder state。
7. target-date 与 official-print dependence grouping。
8. 1/5/10-share two-sided sweep、官方 fee、book age、latency buffer。

请回答：

- 应优先补重放现有19日 archive，还是直接从 clean-forward 开始采集？推荐的顺序与理由是什么？
- 每城最低 dates/events/book coverage 应是多少？除 master plan 的20 dates/100 events外，是否需要更高的 effective-date 门槛？
- source-t0 book-valid coverage 的最低验收比例建议是多少？
- 是否需要修改 selective WS capture scope；若需要，请给出 city/bracket/window、预估流量/存储、保留期和停止条件，实际部署仍需另行授权。
- production health 未重新验收前，是否只允许 offline archive processing 和 zero-notional measurement？

---

## 八、Stage 4 模型训练方向的条件式咨询

请审阅以下“仅在 Stage 3 授权城市上执行”的训练设计，而不是直接要求开训。

### 目标

Primary target：

```text
next_official_delta_native_tick ∈ {...,-2,-1,0,+1,+2,...}
```

Derived targets：`P(up_cross)`、`P(down_cross)`、`P(unchanged)`、`P(new_running_max)`、`P(revision)` 和进入 settlement-relevant brackets 的概率。

模型应估计 next print，不用 trade ROI 选模，也不只训练事后最赚钱 side。

### 建议 baseline

1. persistence；
2. recent-slope extrapolation；
3. forecast-only；
4. market-only；
5. city-specific simple model；
6. pooled/shared model + city calibration；
7. legacy proxy（仅在语义可定义时）。

### 建议模型复杂度顺序

1. ordinal/multinomial logistic；
2. monotonic GAM/spline；
3. strongly regularized shallow GBDT；
4. 更复杂模型只有在前三类明确失败且数据量支持时另行审阅。

### 建议训练/验证合同

- grain：first-seen fast observation / pre-registered state-entry，不按 WS message 等权；
- split：`target_date` block，expanding date-block OOF；
- independent outer development；
- final clean-forward boundary；
- calibration cross-fit；
- primary metrics：ordinal/multiclass logloss、Brier、RPS/CRPS，与同 rows strongest baseline 比较；
- derived cross targets：calibration/reliability；
- negative controls：shuffled-time、within-date permutation、source terminal-false suite；
- Atlanta 2026-07-17 只作为机场快源 onboarding negative-control fixture，不混入五城 headline；
- model artifact、feature manifest、training dates、denominator funnel 和 forward boundary 全部冻结。

### 请给出具体意见

1. 是否坚持 Stage 3 gate，不允许任何城市现在进入正式 Stage 4？
2. 如果允许“provisional measurement-only fit”，请明确它不能用于 model selection headline、selector、shadow promotion 或交易结论，并给出城市与最小目的。
3. 五城应 city-specific、pooled hierarchical，还是先 city-specific simple model 再评 pooled calibration？
4. native delta 的 tail 如何合并，才能兼顾 ordinal structure 与每城小样本？
5. market state 在 Stage 4 应作为强 prior/input，还是仅作独立 baseline，避免把 market reaction 泄漏为 weather skill？
6. 对 multiple fast observations linking to one official print，应如何加权：first event、state transition、fixed checkpoint、official-print group equal weighting，还是多头 objective？
7. Stage 4 最小晋级门是否应高于 master plan 的20 untouched dates？
8. 哪些 feature 必须禁止，哪些 city adapter semantics 必须在开训前补齐？

---

## 九、希望 GPT Pro 返回的固定格式

请严格按以下结构返回，避免只给泛泛建议：

```text
1. EXECUTIVE VERDICT
   - 是否接受 Stage 0–3 的总体治理边界
   - 是否允许现在进入任何形式的 Stage 4

2. STAGE DISPOSITIONS
   - Stage 0 rev2: <exact disposition>
   - Stage 1: <exact disposition>
   - Stage 2: <exact disposition>
   - Stage 3: <exact disposition>

3. BLOCKING FIXES
   BF-1 ...
   BF-2 ...
   每条写：severity / evidence / exact fix / acceptance test

4. DATA COLLECTION PRESCRIPTION
   - city scope
   - dates/events minimum
   - book coverage minimum
   - WS/REST/source/forecast identities
   - retention and stop conditions

5. STAGE 4 AUTHORIZATION
   - AUTHORIZED / NOT AUTHORIZED / PROVISIONAL-MEASUREMENT-ONLY
   - authorized cities
   - allowed objective
   - prohibited uses
   - entry gate and exit gate

6. MODELING RECOMMENDATION
   - target/grain
   - baseline suite
   - split/cross-fit
   - first model family
   - calibration
   - metrics/inference
   - negative controls

7. GO / NO-GO CHECKLIST FOR CODEX
   按执行顺序列出，不超过12项

8. CONFIDENCE
   0.00–1.00
```

如果 evidence 不足，请选择 `CONTINUE_COLLECTION_WITHOUT_MODELING` 或提出可验收的 `REWORK_*`，不要因为模型方向“看起来合理”而跳过 fixed denominator、executable oracle、market baseline 或 clean-forward gate。
