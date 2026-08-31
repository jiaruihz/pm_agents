---
status: current-stage-knowledge
source_version: 2026-08-29
scope: tmin-model-layer
live_authorization: false
supersedes: null
version_boundary: "Any later forward result or model revision must create a new version."
review_sources:
  - ../../../reviews/tmin_model_layer_v2_v3_research_v1/GPT_PRO_REVIEW_PACKET.md
  - missing_from_supplied_package:TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md
  - missing_from_supplied_package:TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json
source_archive_entry: TMIN_V2_1_V3_CODEX_EXECUTION_AND_KB_PLAN_20260829.md
---

# Codex 执行主计划：Tmin V1 诊断关闭、V2.1 Transfer Model、V3 Hazard 与项目知识库持久化

**版本日期：2026-08-29**  
**执行对象：** Codex 研究工程代理  
**最终消费者：** GPT Pro external principal reviewer  
**本轮边界：** 只研究 Tmin probability/model layer；保持 zero-notional。

---

# 0. 你的角色与最终目标

你现在是本项目的：

```text
Principal Quant Research Engineer
Weather Model Scientist
PIT Data Architect
Evidence Auditor
Project Knowledge Maintainer
```

本轮必须在同一个闭环中完成四件事：

```text
WP-0  将两份项目知识文档持久化进仓库知识库
WP-A  关闭 V1 evaluator / routing / alpha 诊断缺口
WP-B  建设 V2.1 routed transfer model
WP-C  建设 V3 settlement-source event-time truth 与 hazard challenger
```

最终生成一个新的、可独立审阅的 GPT Pro review package。

本轮不得：

```text
发送 live order
Maker/Taker 优化
fill 策略
撤单策略
position sizing
exit policy
price-cap mining
trade-threshold mining
PnL-driven model selection
把任何结果描述为 tiny-live 授权
```

---

# 1. Authoritative inputs

本任务包应与以下文件一起交给 Codex：

```text
TMIN_MODEL_LAYER_ELI5_GLOSSARY_20260829.md
TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN_20260829.md
TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md
TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json
```

前两份是必须写入项目知识库的知识文档；后两份是本轮研究裁决和独立复算依据。

必须接受以下阶段事实，不得静默覆盖：

```text
P0 = 168 rows / 15 target dates
P1 = 52 rows / 14 target dates
negative P0 rows = 12
negative P1 rows = 10

V1 alpha=.10 routed:
  date-equal ΔLogLoss = -0.003546
  date-equal ΔBrier   = -0.000497

current V2:
  date-equal ΔLogLoss = +0.000475
  date-equal ΔBrier   = +0.000129

current V2 status:
  UNDERPOWERED_NEAR_IDENTITY_NULL
```

不得把当前 V2 写成 `NO_INCREMENTAL_WEATHER_ALPHA_FOUND`。

---

# WP-0 — 项目知识库持久化（首要强制项）

## 0.1 发现 canonical knowledge-base 位置

先检查仓库根目录及项目约定，依次阅读：

```text
AGENTS.md
CLAUDE.md
README.md
CONTRIBUTING.md
docs/
knowledge/
project_knowledge/
任何已有 docs index / research index
```

优先遵循项目已有知识库路径、命名、front matter 和索引惯例。

如果仓库没有明确的知识库，则创建：

```text
docs/knowledge/tmin/
```

禁止把文件只留在临时目录、review packet、`/tmp`、个人绝对路径或聊天附件中。

---

## 0.2 持久化两份知识文档

必须将以下两份源文档持久化：

```text
TMIN_MODEL_LAYER_ELI5_GLOSSARY_20260829.md
TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN_20260829.md
```

建议 canonical 路径；若项目已有惯例则按惯例调整：

```text
docs/knowledge/tmin/TMIN_MODEL_LAYER_ELI5_GLOSSARY.md
docs/knowledge/tmin/TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN.md
```

规则：

1. 保留原文的实质内容，不得压缩成摘要；
2. 可以增加项目标准 front matter，但不得修改结论；
3. front matter 至少包含：

```text
status: current-stage-knowledge
source_version: 2026-08-29
scope: tmin-model-layer
live_authorization: false
supersedes: null 或旧版本路径
review_sources: 对应审阅文件
```

4. 若已有同名旧知识文档，不得静默覆盖：
   - 保存旧版本或通过 git history 明确可追溯；
   - 在新文档写明 supersedes；
   - 生成语义差异说明；
5. 不得把开发期数字写成永恒事实；必须保留“版本边界”和“新 forward 后另建版本”的说明。

---

## 0.3 建立知识索引和交叉链接

创建或更新：

```text
docs/knowledge/tmin/README.md
```

至少索引：

- ELI5 glossary：新成员与非量化读者先读；
- V1/V2 plain-language teardown：理解当前研究判断；
- 当前 Codex execution plan：理解下一阶段；
- authoritative external review 与 audit artifact 的仓库位置。

如果仓库存在更高层的 docs/knowledge index，也必须加入 Tmin 入口。

在模型 spec、review packet 或项目 README 中涉及以下术语时，应优先链接到 glossary，而不是重复生成互相漂移的新定义：

```text
P0/P1/E0/T0
market offset
routing
alpha
OOF
frozen forward
score gradient
survival/hazard
```

---

## 0.4 知识持久化验收证据

最终 review package 必须包含：

```text
KNOWLEDGE_PERSISTENCE_REPORT.md
KNOWLEDGE_PERSISTENCE_MANIFEST.json
```

报告至少列出：

```text
repository root
canonical knowledge-base path
source file → persisted path 映射
SHA-256
Git status / commit SHA
新增或更新的索引路径
是否存在旧版及 supersedes 关系
内容完整性检查
所有绝对临时路径扫描结果
```

至少实现自动测试：

1. 两份 canonical 文档存在；
2. 关键 heading 存在；
3. source 与 persisted 文档实质内容一致；
4. index 中存在有效相对链接；
5. 文件中不存在 `/Users/...`、`/private/tmp/...`、临时 review 路径；
6. `live_authorization: false` 存在；
7. 后续 review packet 可以通过仓库相对路径定位。

若 WP-0 未通过，本轮不得给出 `RESEARCH_PACKAGE_COMPLETE`。

---

# WP-A — V1 Diagnostic Closure

## A1. 修复 routed score-gradient

正确预注册定义：

```text
physical_innovation
= [logit(p_v1_incumbent) - logit(p_market)] / 0.50

z_routed
= physical_innovation
  × I(window in {morning_cooling, post_sunrise_provisional_low})

score_gradient_alpha0
= z_routed × (y - p_market)
```

当前旧 audit 忘记乘 routing indicator。必须：

- 保存 old/unrouted 与 corrected/routed 两列；
- 加 row-level equality/negative tests；
- 报 date-equal mean；
- target-date block bootstrap 至少 20,000 次；
- leave-one-date-out；
- leave-one-city-out；
- P1 外 corrected gradient 必须严格为 0；
- 不用修复结果追认旧历史 promotion。

---

## A2. 正交分解 routing 与 alpha

在相同修复后的 P0/P1 分母上，公开：

```text
M0 raw market
M1 alpha=.50 all windows
M2 alpha=.10 routed
M3 alpha=.25 routed
M4 alpha=.50 routed
```

每个 arm 报告：

- row/date/city-date equal LogLoss 与 Brier；
- P1 内、P1 外；
- each window；
- each city；
- fixed early/late temporal split；
- target-date bootstrap；
- leave-one-date/city influence；
- probability move distribution；
- high-probability calibration bands。

必须明确区分：

```text
routing effect
alpha-strength effect
```

不能再用“`.50 all-window` vs `.10 routed`”混合改变两件事后归因。

---

## A3. Forward contract

保持现有 arm 完全不变：

```text
V1_ALPHA010_ROUTED
forward start = 2026-08-28
zero notional
```

新增：

```text
V1_ALPHA025_ROUTED_DIAGNOSTIC
V1_ALPHA050_ROUTED_DIAGNOSTIC
```

新 arms 必须：

- 从本轮 evidence seal 后第一个 outcome-unseen target date 开始；
- 不回填成 prospective；
- 与 `.10` 使用共同 forward denominator；
- 全部只输出概率；
- 不产生 selector/order；
- 使用预注册 joint max-T 处理三 arm 比较；
- forward 过程中不得删除表现差的 arm。

---

## A4. 可复现性关闭

提交：

```text
clean commit SHA，或 base SHA + exact patch
model/evaluator source snapshot
all config
requirements/environment lock
standalone reproduction command
negative PIT tests
manifest hash verification
```

reviewer 不得依赖个人机器绝对路径。

---

# WP-B — V2.1 Routed Transfer Model

## B0. 核心科学结构

V2.1 不再用 168 个 settlement rows 重新学习完整天气模型。

固定结构：

```text
大量 weather-only history
→ physical foundation / forecast-error foundation
→ OOF physical probabilities
→ 少量 settlement rows 学低维 market residual adaptor
```

目标是：

> 在市场概率已经给定后，天气状态和预报不确定性是否能稳定预测 settlement residual？

---

## B1. Canonical settlement-source path truth

建立：

```text
TMIN_SETTLEMENT_SOURCE_PATH_TRUTH_V1
```

对每个 city × target_date 保存 canonical official observation path：

- observation event time；
- published/available time；
- raw unit；
- normalized native-lattice value；
- source/version；
- local target day；
- reconstructed final minimum rung；
- exchange resolved rung；
- reconciliation status。

必须处理：

```text
timezone
local midnight
C/F conversion
rounding/native lattice
COR/update
revision
missing observation
duplicate
source identity
exchange discrepancy
```

Provisional truth gate：

```text
final-rung reconstruction coverage >= 98%
resolved-rung exact match >= 99%
all mismatches row-level audited
no silent exclusion
```

若失败：

```text
STOP_V2_1_AND_V3_SETTLEMENT_SOURCE_TRUTH_BLOCKED
```

门槛是项目 gate，不得称行业标准。

---

## B2. 大规模 weather-only training panel

不要只使用 Polymarket P0。

从所有可用 city-day official paths、PIT forecast curves 与 observation history 构造：

```text
(city, target_date, checkpoint_time, current_native_rung)
```

labels：

```text
Y_eod_no_next_colder
T_first_next_colder
right_censored_at_local_day_end
```

不变量：

```text
feature available_at <= checkpoint_time
future path 只生成 label
same target_date 不跨 train/test
future append 不改变旧 row
```

报告独立 city-days、event city-days 和城市权重，不得用 checkpoint row count 冒充独立样本。

初始 provisional data gate：

```text
>= 120 independent city-days
>= 40 next-colder event city-days
Seoul and Tokyo each >= 25 city-days
no single city > 55% effective weight
```

不足时先扩数据，不上复杂模型。

---

## B3. PIT forecast-error archive

建立：

```text
TMIN_PIT_FORECAST_ERROR_ARCHIVE_V1
```

每条记录至少包含：

```text
forecast issue/vintage
available_at
lead time
forecast remaining minimum
realized official remaining minimum
forecast error
whether next colder native rung was crossed
city/source/weather regime
lineage
```

重要：

- 不要求当天有 Polymarket 市场；
- 不要求 settlement label 为 negative；
- issue 之后的 truth 只用于 label；
- OOF uncertainty 只使用 earlier target dates。

实现 global → city → city×lead 的 hierarchical shrinkage，输出：

```text
P_forecast_no_next_colder
P_cross_next_rung
forecast_error_q10/q50/q90
uncertainty_width_native_steps
```

如果 archive 不足，准确列出缺失日期、城市、vintage 和 source，不得只用“P0 只有12个负例”作为 blocker。

---

## B4. Physical foundation arms

实现两个低复杂度基础臂。

### B4.1 Binary EOD physical arm

```text
P(Y_eod_no_next_colder = 1 | PIT physical state)
```

允许：

- strongly regularized logistic；
- low-df monotonic GAM；
- hierarchical city intercept。

禁止：

- unrestricted high-order interactions；
- tree/boosting/deep model；
- target-date ID；
- future information。

### B4.2 Forecast threshold arm

直接通过 OOF forecast-error distribution 计算：

```text
P_forecast_no_next_colder
```

不是再训练一个无法解释的黑箱 classifier。

机制 feature 限制为：

```text
native-rung safety margin
rebound since running minimum
time since running minimum
effective cooling time remaining
recent temperature path
forecast remaining-min margin
forecast crossing probability
radiative/mixing state
```

先做 algebraic deduplication。

---

## B5. Settlement-level market residual adaptor

Primary：

```text
z_phys
= logit(p_physical_eod) - logit(p_clock_window_base)

z_forecast
= logit(p_forecast_no_next_colder) - logit(p_clock_window_base)

logit(p_v2_1)
= logit(p_market)
+ I(active_window) × (alpha * z_phys + gamma * z_forecast)
```

约束：

```text
alpha >= 0
gamma >= 0
settlement-level free parameters <= 3
```

允许一个 secondary soft-routing arm，但 soft gate 必须只由 weather-only history 学习，不能按 Polymarket historical score/PnL 挖窗口。

估计优先：

- penalized likelihood with documented prior scale；
- Firth 或 Bayesian rare-event logistic；
- nested prior-date OOF。

每折保存：

```text
training dates/rows/event counts
coefficients
prior/regularization
coefficient norm
convergence
fallback reason
mean/max probability move
```

---

## B6. Baselines 与消融

必须比较：

```text
B0 raw market
B1 shrink-to-identity calibrated market
B2 V1 alpha=.10 routed
B3 V1 alpha=.25 routed
B4 V1 alpha=.50 routed
B5 current direct-settlement V2
B6 physical-only foundation
B7 forecast-only routed residual
B8 physical-only routed residual
B9 full V2.1 routed transfer
```

通过 controlled ablation 分别回答：

```text
改善来自 routing？
改善来自 physical foundation？
改善来自 forecast uncertainty？
改善只是 generic market calibration？
```

若简单臂没有 residual skill，不得增加复杂度救结果。

---

# WP-C — V3 Event-Time Truth 与 Hazard Model

## C1. 构造 first-next-colder event-time truth

基于 B1 official path，对每个 checkpoint 生成：

```text
next_colder_rung
T_first_next_colder_observation
T_available_first_next_colder
right_censor_time
crossed_before_day_end
```

Primary event time 使用 official observation event time；available time 单独保留供未来 execution 研究使用。

验证：

- event 发生时，最终 reconstructed rung 必须不高于 next-colder rung；
- censored 时，最终 rung 必须保持 current rung；
- append invariance；
- no future feature leakage；
- revision lineage；
- tie semantics；
- row-level mismatch audit。

---

## C2. Discrete-time hazard

Primary interval 固定为：

```text
1 hour
```

模型：

```text
h_k
= P(first next-colder event in hour k
    | survived to hour k, PIT state)

p_survival_eod
= product_k (1 - h_k)
```

训练只使用 weather-history panel，不用 market price。

保持低维 regularized，报告：

- event/censor coverage；
- hazard calibration；
- survival curve calibration；
- time-dependent Brier；
- per-city/per-lead stability；
- failure modes。

若 V3 不能胜过简单 binary physical foundation，不得增加 hazard complexity。

---

## C3. V3 market residual adaptor

```text
z_survival
= logit(p_survival_eod) - logit(p_clock_window_base)

logit(p_v3)
= logit(p_market)
+ I(active_window) × lambda * z_survival
```

约束：

```text
lambda >= 0
```

lambda 必须用 prior-date OOF 与强 shrinkage估计，不得在 full development 上手选。

---

# WP-D — Validation、Forward 与停止条件

## D1. Historical status

当前 2026-08-12 至 2026-08-26 的结果全部是 development-only。

任何新 V2.1/V3 必须：

```text
new model ID
new artifact hash
new manifest
new evidence seal
new outcome-unseen forward start
```

不得追认旧 rows 为 prospective。

---

## D2. Primary probability evidence

所有模型必须在共同 denominator 上报告：

```text
row-weighted
date-equal
city-date-equal
P0
P1
P1 outside
fixed probability bands
leave-one-date-out
leave-one-city-out
```

主要比较：

```text
ΔLogLoss vs raw market
ΔBrier vs raw market
ΔLogLoss vs shrink calibration
ΔBrier vs shrink calibration
corrected routed score-gradient
```

---

## D3. Forward sample gate

新模型的 provisional gate：

```text
>= 30 new settled target dates
>= 180 new P0 rows
>= 80 new P1 rows
>= 15 negative P1 city-date/condition episodes
Seoul and Tokyo each >= 20 P1 rows
```

事件 gate 不满足时继续收集，不能只因为日期数到了就 readout。

---

## D4. Promotion gate

晋级新的 probability challenger，至少同时满足：

1. 相对 raw market 的 date-equal ΔLogLoss 与 ΔBrier，joint one-sided max-T upper bound < 0；
2. 相对 shrink-to-identity calibration 的主 LogLoss upper bound < 0；
3. corrected routed score-gradient lower bound > 0；
4. leave-one-city-out 不发生结构性方向反转；
5. 单一日期贡献不超过总 improvement 的 35%；
6. `[.80,.90) / [.90,.95) / [.95,.98) / [.98,1]` 高概率区间没有系统性恶化；
7. probability coverage 完整，不因 execution availability 删除；
8. no forward-driven model selection；
9. PIT、truth、hash、knowledge persistence 全部通过。

通过后也只可：

```text
FREEZE_PROBABILITY_CHALLENGER
```

不得直接进入 tiny live。

---

## D5. Stop / falsification conditions

出现以下任一情况，应停止对应路线而不是继续堆复杂模型：

```text
settlement-source path 无法可靠重建
forecast archive 无法形成 PIT vintage truth
physical foundation 不优于 clock baseline
V2.1 不优于 raw/calibrated market
V3 不优于 binary foundation
结果只由单一日期或城市贡献
high-probability tail calibration 恶化
coefficient 在 rolling OOF 中频繁换符号
正结果对 source alignment / rounding 规则高度敏感
```

若 V2.1 与 V3 均失败，明确：

```text
NO_INCREMENTAL_WEATHER_ALPHA_FOUND_UNDER_CURRENT_CONTRACT
```

此时停止模型复杂化，保留数据基础设施和 V1 forward ledger。

---

# WP-E — Required Deliverables

最终统一 review package 至少包含：

```text
00_REVIEW_CONTRACT.md
01_EXECUTIVE_DECISION_BRIEF.md
02_KNOWLEDGE_PERSISTENCE_REPORT.md
03_V1_DIAGNOSTIC_CLOSURE.md
04_SETTLEMENT_SOURCE_PATH_TRUTH.md
05_WEATHER_HISTORY_PANEL_REPORT.md
06_FORECAST_ERROR_ARCHIVE_REPORT.md
07_V2_1_MODEL_SPEC_AND_RESULTS.md
08_V3_EVENT_TIME_AND_HAZARD_RESULTS.md
09_MODEL_LEADERBOARD.csv
10_ROUTING_ALPHA_ORTHOGONAL_DECOMPOSITION.csv
11_HIGH_PROBABILITY_CALIBRATION.md
12_ABLATION_REPORT.md
13_CITY_DATE_STABILITY.md
14_PIT_LEAKAGE_AND_APPEND_INVARIANCE_AUDIT.md
15_FORWARD_ARM_MANIFEST.md
16_FINDINGS_AND_OPEN_QUESTIONS.md

ROW_LEVEL_PROBABILITY_AUDIT.parquet
EVENT_TIME_TRUTH.parquet
FORECAST_ERROR_ARCHIVE_SAMPLE.parquet
MODEL_PREDICTIONS.parquet
KNOWLEDGE_PERSISTENCE_MANIFEST.json
EVIDENCE_MANIFEST.json
REVIEW_EVIDENCE_SEAL.json
GPT_PRO_REVIEW_PACKET.md
```

若完整大数据文件过大，可提交可复算 sample + immutable artifact reference + hash，但 reviewer 必须能够定位 canonical local artifact，不能只给个人临时路径。

---

# 最终必须请求 GPT Pro 裁决的问题

`GPT_PRO_REVIEW_PACKET.md` 必须逐项请求：

```text
A. 两份知识文档是否已正确持久化并建立索引？
B. V1 routed score-gradient bug 是否关闭？
C. routing 与 alpha 的作用是否已经正交识别？
D. V1 alpha=.10 原 forward 是否保持未污染？
E. alpha=.25/.50 新 diagnostic forward 是否边界合法？
F. settlement-source path truth 是否足以支持 V2.1/V3？
G. forecast uncertainty archive 是否为真正 PIT OOF？
H. V2.1 是否提供 incremental weather residual evidence？
I. V3 是否提供 incremental survival evidence？
J. 下一阶段 frozen challenger 应选择 V1、V2.1、V3、并行，还是 NONE？
```

Disposition 只能从以下选择：

```text
BLOCKED_KNOWLEDGE_OR_EVIDENCE_LINEAGE
STOP_SETTLEMENT_SOURCE_TRUTH_BLOCKED
NO_INCREMENTAL_WEATHER_ALPHA_FOUND
KEEP_V1_FORWARD_ONLY
FREEZE_V2_1_PROBABILITY_CHALLENGER
FREEZE_V3_PROBABILITY_CHALLENGER
FREEZE_V2_1_AND_V3_IN_PARALLEL
```

不得自行输出 live、tiny-live、Maker 或执行授权。

---

# 成功标准

本轮成功不是训练出更复杂的模型，也不是历史 PnL 更高。

成功标准是：

> **把当前研究知识写进可持续维护的项目知识库；在可靠 settlement-source truth、大规模天气历史、PIT forecast-error archive、低维 market adaptor 和独立 forward contract 下，判断天气信息是否真的能稳定纠正 Polymarket 的 Tmin 概率误差。**
