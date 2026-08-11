# 跨城市细粒度温度模型：统一研究与评测约定

Status: current-source
Updated: 2026-08-12 Tokyo market-weather posterior frozen-forward review
Scope: 城市级日内温度概率模型的方法、评测和知识沉淀；运行边界服从 `WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md`，不规定统一算法或统一特征

## 1. 核心决定

本研究体系统一运行在 **Weather City Intraday Runtime（WCIR）**，框架标识
`weather_city_intraday_runtime_v1`，策略族 `weather.city_intraday_probability`。
Amsterdam、Busan、Helsinki、Seoul、Tokyo 和以后新增城市都必须通过 WCIR profile/adapter 接入。
如果模型尚未冻结，先接 `coverage-only` adapter 留完整分母和 blocker；这代表链路接入，不代表已有概率或 alpha。

不同城市的数据源、观测频率、可用特征、结算单位和盘口结构不同，**不强制共用同一个模型或训练模块**。

统一的只有四件事：

1. 研究问题必须写清目标、decision time、label 和 PIT 边界。
2. 实时采集 profile、事件时钟、PIT checkpoint 和 replay 服从同一 runtime contract。
3. 模型最终导出同一种 prediction table / `SignalCandidate`，需要执行时只输出标准 `TradeIntent`。
4. 使用同一套概率指标；有 PIT 盘口时，再使用同一套 market baseline 和交易指标。

城市代码能自然复用就复用；不能复用时可以独立实现，只要最终导出统一结果。不要为了接口整齐扭曲城市自己的数据和物理机制。
目标模块边界、接口、迁移顺序和新城市接入工作单见
[WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md](WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md)。

### 1.1 当前五城知识账

城市报告不再按 `v7/v8/v9...` 文件名推断当前模型。下表只记录已吸收的耐久结论；
运行中的 adapter/process/订单仍从 production manifest 和 raw runtime 动态读取。

| 城市 | 稳定模型/接入身份 | 已吸收结论 | 当前研究动作 |
|---|---|---|---|
| Amsterdam | WCIR Amsterdam adapter；V9 fixed-lead ECMWF day1 + KNMI/official/path weather head；YES/NO exact-bracket expression | V7 production artifact 145字段中65个forecast/path forcing长期NaN，8/3–11的538 outputs与10个zero-size intents标记污染，0 fill/$0影响。修复后V9训练92,637 checkpoints/665日；2025 expanding OOF Brier/logloss `0.04574/0.15819`，12–16点为`0.10258/0.32913`。真正一次性8月frozen V8在同盘口显著输market；V9 post-freeze audit把ΔBrier收窄至`+0.01002`、CI跨0。`:10/:40`双边5-share taker表达7笔4胜、ROI `+2.40%`，但CI跨0且market favorite同窗`+12.72%` | V9 code/release已pin；city runtime受既有shared proxy/data-feed dependency故障fail-closed，首个clean-forward decision尚未产生。恢复后固定2pp net-edge policy和100字段合同，满30个新settled dates再验同分母proper score、market baseline与fee ROI；不改live · [V9 PIT parity/frozen strategy](analysis/2026-08/2026-08-12-amsterdam-knmi-v9-pit-parity-frozen-strategy-v1.md) · [旧8/11 scorecard](analysis/2026-08/2026-08-04-amsterdam-crossno-v7-shadow-scorecard-v1.md) |
| Busan | `busan_intraday_exact_no` physical head + `busan_intraday_exact_no_online_market_prior_residual` expression candidate | AMOS cross persistence 不能替代 source→routine/WU basis；weather-only固定champion在8/4–11同盘口65 states/8日与market打平。新增online market-prior head每天只用此前settled dates选weather logit weight，OOF权重`0/0/0/0/0/.125/.25/.25`；posterior LL/Brier `0.2682/0.0835`胜market `0.2902/0.0932`，6单/3日fee后ROI `+28.96%`。但创新日仅3天，family又是看过该窗后确定，clean forward仍0天 | runnable zero-notional research candidate；下一完整日期state为`.25`、trained through 8/11；不部署live、不把WS硬塞进模型，盘口链恢复后用同一runner append frozen forward · [online market-prior expression](analysis/2026-08/2026-08-12-busan-online-market-prior-expression-v1.md) · [stable architecture](analysis/2026-08/2026-08-04-busan-stable-model-architecture-v1.md) |
| Helsinki | A8 frozen weather reference + FMI-before-next-METAR entry posterior + METAR held-position correction/exit | A8 8/1–8 exact-PIT为936 events/8 dates，exact/within-one=`70.01%/85.23%`。旧mixed-source entry已supersede；FMI-only 35 entries hold ROI `-2.77%`，解释性1c–99c slice 24笔ROI `-4.47%`。market-offset OOF Brier胜market但logloss CI跨0。共享ladder core已在相同808 OOF rows测试：相对incumbent Brier退化`+0.002325` CI `[+0.000798,+0.003979]`，周期checkpoint版本不采用。2026-08-09 16:15 UTC起exact first-seen WS版已开始forward：首个真实FMI event的pre/t0/+10/+30/+60为5/5 scorable、0 blocker/0 reconstruction error，但只有1个未形成settled评测的event，不能判断增量。METAR delayed-snapshot exit点估改善，但quote lag p50 216s、仅1/20≤30s且exit depth为0，执行证据BLOCKED | 保持 zero-notional；entry只读FMI first-seen，METAR永不开新仓；现有模型不读取WS特征，等至少30个新settled target dates后按同rows frozen A/B比较 incumbent 与 `weather+market+WS dynamics`，期间不调参、不换模型，A8 forward仍0/30 · [end-to-end plan/result](analysis/2026-07/2026-07-30-helsinki-remaining-heat-expression-research-plan-v1.md) · [v7 lineage](analysis/2026-07/2026-07-31-helsinki-v7-forecast-lineage-missing-expert.md) · [reliability](analysis/2026-07/2026-07-31-helsinki-model-reliability-audit-v1.md) |
| Tokyo | 冻结 v5 weather head + 同刻 market 的强收缩 current-exact NO posterior；旧 current model 只作输入/负面对照，不是验收基准 | 数据区间修正后，8/1–11 strict raw-exact 为554 rows/11 settled dates；旧152 rows/3日来自迁移 bundle clock/book-association 的非随机缺日，结论撤回。仍以7/16–28的57 rows/7日 development-only archive选定`.5` weight；完整 forward blend Brier/logloss `0.09063/0.27956`，market `0.07473/0.24058`，delta=`+0.01590/+0.03898`且CI跨0；weather-only显著输market。首次 date×bracket 正净edge为30笔20胜，5-share fee后PnL`+$1.8183`、ROI`+1.85%`，date bootstrap CI `[-20.61%,+18.79%]` | 状态`inconclusive / baseline FAIL`；不再把`.5` blend当shadow升级候选。8/1–11锁为新market-offset+source-innovation模型holdout；不从holdout错例追加hard filter，不改live · [market-weather posterior](analysis/2026-08/2026-08-12-tokyo-market-weather-posterior-v1.md) · [episode fixed A/B](analysis/2026-08/2026-08-11-tokyo-continuous-ladder-episode-state-ab-v1.md) |
| Seoul | Korea source-event adapter，尚无独立 frozen probability artifact | 与 Busan 共用的 CrossNO/dual-head 不能绕过城市 source→settlement basis；外部负面对照使当前表达不能晋级 | coverage-only；先建 Seoul 自己的 PIT probability/basis，再谈 expression · [Korea dual head](analysis/2026-08/2026-08-03-korea-cross-event-dual-head-v5.md) |

跨城共同结论：模型是否“预测天气不错”与是否“打败同刻 market”必须分开。

**Helsinki 2026-08-12 更新**：已把这一原则落成可执行策略 `helsinki_bounded_market_residual_c015_symmetric_v1`。market 是 prior，FMI weather 只允许最多0.15 logit修正；同档 YES/NO 用真实5-share ask+fee竞争且不加额外entry阈值。旧OOF双边ROI `+7.44%`，8/2–11 retrospective PIT 9笔ROI `+21.91%`；同行模型Brier/logloss略优market，但两类date bootstrap CI仍跨0，因此完整策略进入zero-notional freeze-forward，不升live。详见 [strategy report](analysis/2026-08/2026-08-12-helsinki-bounded-market-residual-strategy-v1.md)。
没有冻结 artifact 的城市输出 structured blocker；有 artifact 的城市也只有在同 checkpoint
proper score、market baseline、frozen forward 和 executable expression 四层闭合后才能改变交易状态。

### 1.2 First-seen / repricing 跨城 review（2026-08-09）

结论不是再做一批城市模型，而是把 first-seen 分成两个不能混训的 event family，共用同一个
event-ladder panel、market baseline 和执行评测：

- `forecast_revision`：D-2/D-1 forecast content first-seen，主 markout horizon 固定为
  `5/15/30/60m`；
- `fast_observation`：城市快源 observation first-seen，主 markout horizon 固定为
  `30/120/300s/next_official`，`5/15/30/60m` 只作较慢诊断。

两类事件都使用 pre-event full ladder 作 market prior，但 forecast curve revision 与快源升温打印的
物理语义、cadence、source basis 和可交易半衰期不同，不能靠一个 `event_type` dummy 强行池化。

#### 当前可训练分母审计

下表来自 2026-08-09 当前 Mac/JRS raw。`distinct obs` 只描述各 source 自己的 immutable observation key，
不能跨 source 比大小；`cross panel` 是现有通用 stale-book shadow 的事后 cross 子集，不能代替 all-event 分母。

| 城市 / family | 当前 raw 分母 | 当前 book 证据 | 到可用模型还缺什么 | 研究角色 |
|---|---:|---:|---|---|
| 52 城 forecast revision | collector-exact unique content：D-1 `5,339/13 dates`（material `2,342`）；D-2 `555/12 dates`（material `177`） | 尚无绑定每个 revision 的 event-driven pre/t0/5/15/30/60m full-ladder burst | provider run/issue time 仍不可见；先建固定 all-rung event panel，不能继续用周期 snapshot 猜执行时钟 | 跨城 partial-pooling 主样本；不是 52 个独立模型 |
| Amsterdam / KNMI | captured panel `3,530 events/12 obs dates` | `21,180` event-offset rows，`20,894` complete（`98.65%`）；burst 为 `t0/+15/+30/+60/+120/+300s` | pre-event/next-METAR 已能成表；当地12–15点140个new-content events中，30/60s至少1c整梯变化为`40.0%/54.3%`。下一步要预测方向和可执行markout，不能把quote movement率当alpha | **fast-observation 首个 golden pilot** |
| Helsinki / FMI | `1,586 distinct obs/20 dates` | cross 子集 `46/12 dates`，`44` 有 book | 把 all observations（含 non-cross）接入 KNMI 同级的 pre-event/full-ladder burst；active date-X 作为主 grain | 第二批；保留 frozen weather head 作 challenger feature |
| Tokyo / JMA | `1,621 distinct obs/19 dates` | cross 子集 `37/7 dates`，`36` 有 book | 每个 material event 保存双边 mid/ask/full depth；`.5/.7` 只作固定规则 baseline；terminal-false 与 next-METAR confirmation 连续入模 | 第二批；不再用阈值扩样本 |
| Busan / AMOS | `12,221 raw point/revision rows/21 dates` | locked model states `255/20 dates`；同盘口 `65/8 dates`；online prior OOF仅3个非零创新日/6个表达；WS只有2个独立settled dates可作增量诊断 | 多 runway/5 秒轮询先归一成 immutable point-group event；单独校准 AMOS→routine/WU basis；online posterior虽点估和CI胜market，但family post-selection且clean forward=0 | zero-notional online prior candidate；暂不部署 probability adapter |
| Seoul / AMOS | `36,152 raw point/revision rows/21 dates` | WCIR current coverage blockers `6,828`；没有冻结概率或WS subscription | 与 Busan 共用 parser/先验但不共用城市 basis；先建 Seoul settlement probability head，再评 repricing | coverage-only |
| Singapore / MSS | `3,170 distinct obs/19 dates` | cross 子集 `33/13 dates`，`28` 有 book | all-event ladder、source→settlement basis 和负例；当前更新快不等于结算信息强 | 后续 pooled source-family challenger |
| Atlanta/Miami 等 US MADIS/METAR | 当前 collector Atlanta/Miami 各约 `280/20 dates`；历史 cross 城市更多 | cross-only，Atlanta terminal false 为固定负对照 | all-event denominator、真实 source publication/collector clock、同站 settlement basis | negative control / lower priority |
| Ankara/Istanbul / MGM | 各约 `1,46x distinct obs/20 dates`，obs→first-seen p50 约 `18.4m` | cross 子集 `24/12`、`15/10 dates` | detection lag 已吃掉大部分窗口；先证明仍有增量再训练 | latency control |
| HongKong/Shenzhen、TelAviv 等 | 当前只有稀疏 cross episode | 不足 | 先补 all-event collector 和 authoritative/proxy 身份，不做城市模型 | coverage-only |

production identity 审计中 canonical DB route 为 healthy；全局 health 的 observation-cache critical 来自
Denver 单城 stale record，另有 6 个非 trading 城市缺 live METAR state。这两项不污染上表的 exact first-seen raw，
但说明 production 不能笼统称为全健康。

#### 可用模型的统一形态

1. **训练表**：grain 固定为 `event × full-ladder rung`，保留 selected/unselected、material/non-material、
   missing book 和 no-trade 行；每个 event 等权、每个 `target_date` 等权。重复 poll/checkpoint 不增加样本权重。
2. **markout head**：先预测 coherent ladder 的 `Δlogit(market probability)` / ordinal mass transport；另存
   `entry ask -> future bid`、depth/VWAP 和双边 fee 的 executable markout，不能用 mid 模型冒充 taker PnL。
3. **market-prior correction**：`logit(p_post)=logit(p_market_pre)+g(event innovation, path, source basis, city adapter)`。
   城市只提供 settlement lattice、source-basis calibration 与少量强收缩 random effect；不得按城市历史 ROI 做 eligibility。
4. **同分母四组**：固定 rows、labels、clocks、quotes 和 split 比较 market-only、innovation-only、
   innovation+market、innovation+market+microstructure。WS raw 必须先确定性 materialize 成 checkpoint features。
5. **两个概率头分离**：short-horizon markout/repricing head 决定是否存在未吸收信息；EOD exact-bracket settlement head
   只负责最终分布。settlement score 好不能替代 markout，短期 markout 好也不证明最终温度判断更准。
6. **执行头分离**：taker 认真实 ask、future bid、depth/VWAP 和 Weather fee；maker 只有真实 post/ack/queue/
   partial fill/expire/cancel/fill journal 后才训练 fill/adverse-selection head，future touch 永远不算 fill。

#### Frozen gate 与唯一动作

- development 内只允许一次按 `target_date` blocked inner-CV 选定 event family、horizon、模型和 threshold；
  后续至少 `30` 个新 settled target dates 做 chronological frozen forward，并按 `target_date` block bootstrap。
- 先要求 innovation+market 对同 rows market baseline 的 primary loss delta CI 全负；再要求 taker 或真实 maker
  的 fee-adjusted uplift CI 为正。城市 leave-one-out / source-family holdout 只检验可迁移性，不用于挑赢家城市。
- 当前没有城市通过 market baseline + significance + frozen forward + actual execution 四门，容量与 fee 后 ROI 都是
  `not estimable`；Amsterdam 的 coverage 最好不等于 alpha 最强。

**下一步唯一动作**：Amsterdam/Helsinki/Tokyo 的 zero-notional `source_event_full_ladder_v1` 已于
2026-08-10 UTC 接入共享 exact-bracket probability stack；首批部署后真实 Helsinki 与 Amsterdam event 均输出11档完整 native ladder，
market/weather/source-basis/final 四层概率和均为1，单边盘口按显式概率区间进入 market prior。当前
source-basis/calibration 仍为 `identity_unfitted`，Amsterdam 只有8/10–11两日106 rows，交易链不消费该 sidecar。
继续积累至少30个新settled target dates；Amsterdam 同步用现有 first-seen panel冻结开发
`+30/+60/next-official` ladder markout head。满窗后以固定rows/labels/split做 market、incumbent、
incumbent+innovation/WS dynamics frozen A/B；其间不调参、
不部署真实订单、不改变现有 live 策略、不追加 threshold。

## 2. 城市内部可以不同

以下内容允许每个城市独立：

- 历史训练数据 adapter，以及满足共享 event contract 的城市 source adapter；
- 特征集合和缺失值处理；
- logistic、HGB、survival、hazard 或其他算法；
- 1h、2h、EOD、remaining-heat、exact-bracket 等模型 head；
- source-to-settlement basis 和 native-unit lattice；
- Polymarket condition / bracket 映射。

盘口与模型不要求物理分层；统一边界放在城市插件外：

```text
shared PIT DecisionContext
  -> city-specific feature/model/policy
       (weather-only | market-offset | joint weather+market)
  -> standardized prediction / SignalCandidate
  -> TradeIntent
  -> shared plan / order / fill / settlement
```

城市插件可以把 PIT 盘口作为 prior、offset、联合特征或 microstructure 特征，也可以完全不用盘口；但必须声明
`market_feature_role` 和 `market_feature_clock`，分别记录模型输入的 `feature_book_snapshot_id` 与执行报价的
`execution_book_snapshot_id`。城市代码不得自建 collector、回放时钟、order client、fill/PnL 或 settlement 链。

接入前必须用实际 deployed sample 声明 source payload 是 point observation、measurement interval 还是 revision；
`target_date` 不能用于猜物理 shard。source、official/settlement 和 market expression 的 lattice anchor 分开记录，
`relative_offset` 必须声明 anchor。repo tests 与 running producer/consumer 的 code/config/schema fingerprint 都要通过 parity。

## 3. 最薄的统一接口：prediction table

每个模型至少导出以下字段；CSV、Parquet 或 DataFrame 均可：

| 字段 | 含义 |
|---|---|
| `city` | 城市规范名 |
| `target_date` | 结算城市本地日期 |
| `decision_ts_utc` | 概率真正可计算的时点 |
| `target_id` | 明确的预测目标，如 `eod_cross_d1` |
| `target_kind` | `physical_path` / `settlement_outcome` / `market_expression` |
| `p_model` | 对该目标的预测概率 |
| `label` | 最终 0/1 标签；未结算时为空 |
| `split` | `train` / `validation` / `oof` / `frozen_forward` |
| `model_id` | 城市内可复现的模型版本 |
| `feature_set_id` | 特征版本或稳定 hash |
| `pit_provenance` | `live_capture` / `archive_reconstruction` / `historical_non_pit` |
| `checkpoint_id` | 共享 runtime 生成的 PIT checkpoint identity；历史离线研究可为空但须说明 |
| `scorable_status` | `scorable` 或结构化不可评分原因；不能静默丢行 |

有盘口时可附：

| 字段 | 含义 |
|---|---|
| `market_p` | 同一 row、同一时点、同一 outcome 的市场概率 |
| `market_feature_role` | `none` / `prior_offset` / `joint_feature` / `microstructure_feature` |
| `market_feature_clock` | `pre_event` / `first_post_event` / `decision_current` / `none` |
| `feature_book_snapshot_id` | 模型实际读取的盘口证据 |
| `execution_book_snapshot_id` | expression/成本判断实际使用的盘口证据 |
| `expression_side` | 实际映射的 YES/NO |
| `executable_cost` | 真实 side ask/VWAP 加官方 fee 后成本 |
| `market_snapshot_ts_utc` | 行情证据时间 |

`target_id` 不得混淆 touch、break、stop-exact 和 final-exact。不同 horizon 或不同目标必须使用不同 `target_id`。

## 4. 统一切分和 PIT 规则

- 按 `target_date` 做时间切分，不随机拆同一天的 observation rows。
- 参数和特征选择只能发生在 train/validation 或 expanding OOF 内。
- frozen holdout/forward 只复核，不继续调参。
- 每个特征必须在 `decision_ts_utc` 已真实可得。
- 历史 state 必须由 event store 按 `available_at_utc <= decision_ts_utc` fold；不能只按 observation time 截断 raw journal。
- revision/late-backfill 保留原 first-seen 和父事件；measurement interval 不得冒充 point observation。
- forecast 必须保存 issue/run/first-seen；不能用后发 run 回填。
- forecast/feature artifact 发生刷新时，所有下游 OOF 必须核对同一个
  semantic content hash，并按依赖顺序重放；压缩文件应 hash 解压内容，不能让
  gzip header timestamp 冒充数据版本变化。
- METAR/WU/settlement 后到值只能作 label，不能作事前特征。
- 历史 EDR、archive reconstruction 等非 PIT 数据必须显式标记，不能冒充实时领先性证据。

## 5. 统一评测体系

### 5.1 数据覆盖

每份结果先报告：

- 独立 `target_date` 数；
- prediction rows 数；
- 正例率；
- 各 split 日期范围；
- 关键特征覆盖率和缺测日期；
- PIT / non-PIT rows 数。

### 5.2 天气概率模型

主指标：

- Log loss；
- Brier score；
- calibration table / reliability curve；
- 相对同 rows 简单 baseline 的 delta。

辅助指标：

- AUC 或 rank 指标；
- 固定阈值 accuracy、precision、recall；
- mean predicted probability 与真实 base rate。

Accuracy 不能代替概率指标。阈值必须在验证集冻结，不能在 holdout 上寻找最好正确率。

至少保留一个简单 baseline，例如 train base rate、clock climatology 或城市当前最简单模型。比较算法或特征时固定 rows、label 和 split。

日内高频概率模型还必须把“评测 grain”与“原始更新频率”分开。默认同时报告：

- checkpoint：每个合法 PIT 更新；
- transition：物理/持仓状态发生变化的首行；
- state entry：每日首次进入每个 confirmed state（例如 current X）。

每个 grain 先在 `target_date` 内平均，再跨日期平均；模型比较用 paired
target-date block bootstrap。若训练目标同时覆盖多个 grain，权重必须预先固定，
各 grain 先按日期等权，不能按事后错误、价格或 edge 重加权。

有序结果（例如 `Δmax={0,1,2,3+}`）除 binary Brier/logloss 外，同时报告
multiclass logloss、RPS 和 exact/within-one accuracy。多 horizon 事件应优先使用
coherent survival/hazard 或其他保证概率单调的联合分布；独立 horizon heads
必须检查并报告 coherence。校准默认使用固定 bin、target-date-equal 权重。

forecast 缺失不是 eligibility filter。若全局模型在缺失行结构性退化，可在固定
全分母上预注册 `available -> full model / missing -> physical+official expert`
路由；missing expert 只能使用 decision-time 可得特征，且必须与不路由版本做
同 rows、同 grain bootstrap。前季 OOF 的事后 calibration 不保证跨季稳定，
不能因为 ECE 变差就默认追加一个校准器。

### 5.3 有 PIT 盘口时

在 prediction rows 与盘口完全对齐后，增加：

- model 与 raw/calibrated market 的同 rows Log loss、Brier 和 calibration；
- `p_model - executable_cost`；
- market coverage gap，不能把缺盘口当成策略过滤。

天气模型能预测天气，不自动等于打败市场。

若盘口进入模型，仍须在固定 rows、label 和 split 上至少比较纯天气、纯 market 与 market-aware 模型；
pre-event、first-post-event 和 decision-current 不得混成同一个 `model_id`。依赖 midpoint 的模型遇到
one-sided book 时返回 `not_scorable_missing_midpoint`，但 checkpoint 仍留在 coverage 分母；明确支持纯天气或
ask-only 的模型可以继续计算。

#### 5.3.1 选择性 CLOB WebSocket microstructure

使用 WebSocket 微观特征时，先按 runtime contract 将 baseline `book/snapshot` + `price_change` delta
重建成 token-level PIT book state。模型表保存重建 state ID、selector/capture-policy version、subscription set、
producer build 和 gap/reconnect status。无消息、未订阅、窗外或 paused city 均不能解释成盘口没变。

微观增量的固定同分母 A/B 至少包含：

1. `weather-only`；
2. `market level-only`（同时点 price/spread/depth）；
3. `weather + level`；
4. `weather + level + WS dynamics`。

四组固定 rows、labels、clocks、quotes 与 split；否则不能把 level 信息的改善归因给 WS dynamics。
raw frame/message 不是样本，必须映射到预注册 checkpoint/time-bin/first-event/state-transition，
并先按 `target_date` 等权。quote add/cancel/replace 只是报价活动；无 trade print/order lifecycle 时，
不得声称 executed volume、queue/fill 或 maker alpha。

Helsinki 当前生产实现固定使用 FMI `first_seen_at_utc`，输出 `pre/t0/+10/+30/+60s`、5-share双边深度、
relative markout、mode distance、邻档传播/lead-lag 与 weather-shock interaction。每行保留
`feature_book_snapshot_id`、subscription epoch、selector/build identity、gap blocker 和 `orders_submitted=0`；
同socket selector变更只继承仍在订阅的token state，reconnect和新token仍必须等fresh `book` baseline。
产物位于 `/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_event_ladder_features/`。

共享 raw-to-book authority 已收敛到 `weather_data_feed/ws_incremental_book.py` 的
`weather_ws_reconstructed_book_v2`；城市研究只能引用其 immutable state/blocker，不再实现自己的 baseline/delta fold。
`best_bid_ask` 先于同 exchange update 的 delta 时先产生 `best_quote_parity_pending`，期间不可评分；delta 对齐后恢复，
未对齐则升级为 `best_quote_parity_mismatch` 并等待 fresh baseline。当前 exchange raw 没有 sequence 字段，必须把
`exchange_sequence_unavailable` 保留为 evidence limit，不能用 best-quote parity 冒充 sequence-complete capture。

#### 5.3.2 交易模型默认使用 market prior + source innovation

城市 weather-only probability 是物理预测基线，不默认等同于可交易 fair price。只要同 checkpoint 市场已经存在，
交易层的默认问题应改为“新 source event 给市场已知状态增加了多少信息”，而不是直接计算
`p_weather - ask`：

```text
pre-event full ladder -> market prior
new source first-seen + path/forecast innovation -> likelihood/log-odds correction
market prior + correction -> post-event posterior
posterior vs fresh executable quote -> candidate / skip
```

最薄的 binary 形式为：

```text
logit(p_post) = logit(p_market_pre_event) + g(source_innovation, weather_state,
                                               source_basis, cadence, path_state)
```

其中 `g` 必须用 expanding/OOF 或 train-only fold 拟合并强正则；market offset 固定在模型中，避免 weather model
在稀疏尾部无约束覆盖市场。若只能使用同时点 sampled market reference，可先研究 regularized stacking，但必须标成
`historical_price_reference_non_executable`，不能冒充 pre-event innovation 或订单簿回放。

短期 repricing 与最终 settlement 分成两个 head：

- markout head：预测 `t0 -> +30/+120/+300s/next official` 的价格变化，交易目标是 source event 后市场是否尚未完成重定价；
- settlement head：预测最终 exact-bracket outcome，market 为 prior，weather/source 只输出 posterior correction。

极端分歧（例如 market 接近 0/1、weather 仍给中等概率）首先是可靠性与 domain-shift 诊断，不自动视作最大 edge，
也不靠事后价格带 hard gate 处理。候选必须同时保存 pre-event、feature 和 execution 三个 book clock，并以
posterior uncertainty 下界扣除 ask/VWAP、官方 fee、spread/退出摩擦和 adverse-selection 后再评价。

默认 A/B 固定相同 rows/labels/quotes，至少比较 `market`、`weather-only`、`market-prior posterior`；先要求 posterior
在 frozen forward proper score 上打败 market，再发布 fee-adjusted expression 结果。source checkpoint 的静态重复行
不能重复计作独立交易，主交易 grain 是 first-seen event / state transition / first position entry。

每个城市在研究 market-aware model 前还应固定一个 market zero-EV null：直接用 settlement label 检验 market calibration，
并报告无额外信息的 favorite/randomized-side policy 在理论 market price、官方 fee 和可执行 ask 三层的 ROI。若 market
概率正确，理论 gross EV 应为 0；spread/fee 后应为负。candidate 的 paired proper-score delta 必须以 settlement 为标准，
不是以“更接近 market”为标准；完全复制 market 的 candidate delta=0，不能通过 baseline gate。

### 5.4 策略执行映射

信号 policy 必须事前固定，然后报告：

- 信号数和独立 `target_date` 数；
- BUY 升温 / BUY 不升温或具体 YES/NO expression 数；
- 平均和分位 executable cost；
- 胜率、正确/错误清单；
- 官方 fee 后 PnL 与 ROI；
- 最大单笔损失、日期集中度；
- 缺盘口、不可执行和未结算数量。

没有历史盘口时，这一层标 `not_available`，不能填 0，也不能用天气 accuracy 代替 ROI。

## 6. 知识库怎么维护

不要为每个城市再建一套重型架构。每个 durable 城市研究在报告中固定保留五段：

1. 数据源、结算源、单位和 PIT 边界；
2. 预测目标、特征和模型；
3. 数据覆盖与切分；
4. 概率结果，以及有盘口时的信号/胜率/ROI；
5. 遇到的坑、反例和可复用启示。

单城发现标 `single_city_evidence`。只有在其他城市复现，或机制适用边界已有明确证据时，才写进本文件作为跨城默认经验。

实验数字留在 `docs/analysis/YYYY-MM/` 和可重复生成产物中；本文件只保存稳定方法，不保存不断变化的排行榜。

## 7. 给其他 Codex 对话的短指令

可以直接说：

> 这项城市温度模型研究请遵循
> `docs/WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md` 和
> `docs/WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md`：模型、特征、盘口是否进入模型和 source adapter
> 可以按城市独立；但采集 profile、事件时钟、PIT checkpoint/replay、prediction/SignalCandidate、
> TradeIntent 与执行链必须复用公共框架。按 target_date 做 PIT/OOF/frozen-forward 切分，统一报告
> logloss、Brier、calibration 和同分母 market baseline；有盘口再报告信号、胜率和官方 fee 后 PnL/ROI，
> 没有盘口标 not_available。不要为城市另建 collector、回放时钟或 order/fill/PnL 链。
