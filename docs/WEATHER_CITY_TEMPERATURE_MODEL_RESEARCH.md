# 跨城市细粒度温度模型：统一研究与评测约定

Status: current-source
Updated: 2026-08-13 Seoul decision-ladder residual v3 backtest
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
| Amsterdam | WCIR Amsterdam adapter；V9 weather head + `amsterdam_knmi_market_offset_probability_v3`；pre-first-seen direct current-NO prior，first-seen t0 YES/NO execution | 独立红队发现旧收益回放使用first-seen前约4–5分钟价格，故v2 `+11.85%`不再作为执行证据。v3改为nested expanding：6–7月6,136 rows/57日的posterior Brier/logloss=`0.04246/0.14154`，market=`0.04718/0.15212`，delta点估胜但CI仍跨0。七月`observation+240s` sampled-price压力测试42笔/27日、34胜、fee proxy ROI`+24.04%` CI`[+3.84%,+45.58%]`，同rows market favorite ROI`-6.33%`，但仍无ask/depth。collector clock-parity为443 checkpoints/9日，19次方向分歧中model 14对、market 5对；proper-score delta CI上界仍约`+0.0008`。锁定表达9笔/6日全胜、counterfactual ROI`+27.22%`，却与market方向全部相同，paired uplift=0、fills=0 | `shadow candidate / not live`。已修完整ask ladder透传、date×bracket去重、pre-prior/t0 execution双时钟与boundary fail-closed；v3 artifact冻结于`2026-08-12T08:24:21Z`，config code-ready但未改变live CrossNO。达到30个新settled executable signals、10 active dates且proper-score与fee-uplift双CI过门后再评tiny-live · [V9 + v3 red-team update](analysis/2026-08/2026-08-12-amsterdam-knmi-v9-pit-parity-frozen-strategy-v1.md) |
| Busan | `busan_intraday_exact_no` physical head + `busan_intraday_exact_no_online_market_prior_residual` expression candidate | AMOS cross persistence 不能替代 source→routine/WU basis。exact pending-state 可比历史扩到79 rows/13 dates；用前3日作seed后的10日 expanding evaluation，posterior LL/Brier delta=`-0.01762/-0.00777`，CI上界均为0，仍只有6单/3个非零创新日、ROI`+28.96%`。更宽的periodic favorite-state压力测试覆盖7/08–8/11共936 rows/35 dates；固定7/08–20开发后，7/21–8/11的678 rows/22日holdout显著输market（LL delta`+0.08159`，CI`[+0.00412,+0.16541]`），22笔首单ROI`-12.76%`。说明近期正cluster不能外推成长期alpha；clean forward settled仍0日 | 保留runnable zero-notional candidate采集正式forward；不升live、不按6笔调gate。至少30个新settled clean-forward dates后，要求proper-score delta CI全负且fee uplift CI为正再admit · [online + expanded history](analysis/2026-08/2026-08-12-busan-online-market-prior-expression-v1.md) · [Harness real-case replay](analysis/2026-08/2026-08-13-weather-agent-harness-busan-real-case.html) · [stable architecture](analysis/2026-08/2026-08-04-busan-stable-model-architecture-v1.md) |
| Helsinki | `helsinki_regime_calibrated_bounded_residual_c015_v2`：FMI first-seen开仓、METAR仅校正/退出；5-share YES/NO symmetric taker | v2只用2025 OOF的51,451 rows/365日做四段expanding选择：按local clock、forecast future-peak clock/availability、path state与weather-logit交互统一校准；`L2=256`在三段Brier/logloss均胜raw，平均delta=`-0.000317/-0.002260`。接固定`c=.15`后，7/20–29同362 rows上Brier/logloss=`0.07388/0.24195`，旧版=`0.07430/0.24309`，paired CI全负；8/2–11仅作压力测试，新版=`0.05234/0.17027`，market=`0.05442/0.17542`，9笔6胜、ROI`+30.31%`且与旧版交易身份完全相同 | v2已于08:57:58Z加载zero-notional runtime：干净shared release `17425cdf…`、artifact `fd09be9c…`、0 errors/0 orders、最终定向测试29/29；首轮旧FMI→book超过120秒正确落clock blocker。untouched forward锁08:31:24Z+，先验证taker，maker仅放大已验证edge；不升live、不加坏例gate · [strategy + complete training/deploy](analysis/2026-08/2026-08-12-helsinki-bounded-market-residual-strategy-v1.md) |
| Tokyo | first pre-cross source state + bounded market-anchored posterior；每档只在JMA首次到`current+0.3/+0.4°C`且尚未native cross时判断一次，market NO<0.5不反转，≥0.5时用开发窗冻结的`2×logit`强化 | scope不变：63,384历史feature rows/819日→2,244首次pre-cross candidates/737日；7月development 9 candidates/6日，8/1–11 strict raw-exact 34 candidates/11日。新增full-path相对clock+margin base的logit innovation，开发窗在alpha `0/.25/.5`中明确选择0，故不强塞无增量天气修正。v2执行表达扣`max(1 tick, half spread)`而非高价hard filter；reused audit从v1的29笔28胜、ROI`+2.30%`收敛到12笔12胜，cost`$53.4381`、PnL`+$6.5619`、ROI`+12.28%`，date CI`[+7.23%,+17.69%]`；ask均值/中位`88.61/94.35¢`，≥98¢仅2笔 | 状态`inconclusive / clean-forward accumulating / live gates FAIL`；8/1–11已用于发现执行问题，只算reused audit。8/12 exact 27 expressions→1 candidate→0 signal：28-NO在JMA 28.3时bid/ask `.44/.60`、posterior `.53994`，fee前后均无正edge；不是99¢定死。冻结offline zero-notional，不接intent/order/fill · [pre-cross v2 report](analysis/2026-08/2026-08-12-tokyo-pre-cross-market-sharpening-v1.md) · [superseded broad blend](analysis/2026-08/2026-08-12-tokyo-market-weather-posterior-v1.md) |
| Tokyo V3 | `weather.city_intraday_probability.tokyo_continuous_full_probability`；完整conditional market ladder + `coherent_multigrain_hgb_v3` weather head，连续输出`P(stay/+1/+2/+3+)` | 7/24–29 reused validation曾优于market；后发现研究入口仍指向7月 artifact，且canonical materializer错误丢弃缺单侧互补token的同日ladder。修复后8/1–12有581 strict expressions/12日，8/1–11形成430 causal settled joins/11日；V3/market multiclass Brier=`0.68576/0.62922`，delta=`+0.05654`、CI=`[+0.01170,+0.10701]`，主概率门FAIL。5-share研究表达19笔12胜，fee PnL`+$13.73155`、ROI`+29.68%`，但该窗已看过且概率输market | `inconclusive / August probability FAIL / trade-expression positive / clean-forward required / research-only / no-live-change`；保留7月正结果为历史evidence，冻结参数未用8月refit，8/13起收clean forward；不替换Tokyo V2、不接intent/order/fill · [V2/V3 report](analysis/2026-08/2026-08-12-tokyo-pre-cross-market-sharpening-v1.md) · artifact `tokyo_continuous_full_probability/august_replay_20260813_v3` |
| Seoul | Korea source-event adapter + research-only `seoul_intraday_remaining_heat_distribution`；production仍是coverage-only | v3已把每份完整ladder设为decision clock，严格取此前5分钟内AMOS state，并用下一份独立ladder的direct ask回放。7/22–8/11有1,649 market-grain rows、582 scorable rows/20日；8/7–11 frozen为22 state entries/5日。冻结posterior相对market的Brier/logloss/RPS delta=`+0.00320/+0.01204/+0.00366`，三项均未胜且CI跨0。12笔5-share fee后回放PnL`+$5.2337`、ROI`+26.48%`，date CI`[-54.92%,+103.50%]`，不能覆盖概率门失败 | `inconclusive / market gate FAIL / historical PnL positive but LOW_SAMPLE / coverage-only / no-live-change`。v2的full-post coverage blocker已解除；下一步不调8/7–11，直接从8/12后累计至少30个新settled clean-forward dates，再要求proper-score全胜market且fee-uplift CI为正 · 见下方三版训练账 |

### WCIR canonical 与 Amsterdam 双边 expression 修复（2026-08-23）

- canonical 根因是 legacy adapter 对同一 immutable source event 从不同模型轮次/rollover shard
  生成不同 delivery header，以及同一 state checkpoint 携带 model/profile alias。修复后 bridge 仍严格拒绝
  immutable identity 冲突，只把同 ID 的 delivery 取最早完整 header，并把 legacy checkpoint ref 归一到
  feature-state identity。最终复盘快照对 18,080 bundles 的 canonical apply 得到 17,932 unique candidates，
  `candidate_delta=0`；首次修复新增 15,120 candidates / 11,017 checkpoints / 9,588 events，随后仅增量
  吸收 runtime 新写入。历史 12 个 conflict event IDs、45 个非winning deliveries被显式计入 reconciliation。
- Amsterdam V9 的 expression dedupe 从 `city_date_bracket_side_model` 改为
  `city_date_bracket_model`，并兼容 append-only journal 中旧的 YES/NO side-scoped key；同一
  date×bracket 以后只保留第一次被选中的方向。审计开始时有10组；部署前 8/23 又形成第11组。
  当前42个旧 policy selections重放后保留31、压掉11个后到方向：
  `8/12 28 NO→YES`、`8/13 32 NO→YES`、`8/14 33 NO→YES`、`8/14 34 YES→NO`、
  `8/16 23 NO→YES`、`8/18 21 NO→YES`、`8/19 21 NO→YES`、`8/20 20 NO→YES`、
  `8/20 21 NO→YES`、`8/21 20 YES→NO`、`8/23 20 NO→YES`。旧的 city/date/bracket 近似 settlement
  join 已 superseded-for-decision-use；按 exact `market_id + condition_id` 后，前10个已结算晚到方向为
  4胜6负、unit PnL `-$1.3739`，所以移除它们使已结算切片改善 `+$1.3739`；8/23 一条仍 open。
  全窗口实际 orders/fills/notional 均为0。
- 生产 zero-notional runtime 已加载 release `fb40f669bc2153ec37d0c2cca137cee785cb89e5`，
  loaded core SHA256=`ca71d177…f9b`、runtime config hash=`120fea57…258`；首轮
  `status=ok / errors=0 / new_paper_intents=0 / orders_submitted=0`。
- 这批数据本来就是 admission forward，不能因数据层修复另起一套：各模型的 probability forward
  boundary 与原始 ModelOutput 全部保留。只有 Amsterdam V9 的交易表达 policy 发生了改变，因此
  `2026-08-23T15:25:27Z` 起是 bracket-scoped expression 的 untouched forward；此前窗口继续作为
  probability forward 和固定规则 counterfactual replay，旧双边 expression 版本单独分层，不删除。
  这次修复只恢复可分析血缘和正确 expression denominator，不代表 proper-score、market baseline、
  significance 或30个独立 settled dates admission gate 已通过。

### Admission-forward 首次全 active-profile exact-market 复盘（2026-08-23）

固定各 profile 原 frozen boundary 至 `2026-08-23T15:43:41Z`：8 个 active profiles 共15,596个
expression-checkpoint candidates，3,848个scored，85个真实zero-notional intents；80个exact settled为
44胜36负，unit fee-adjusted ROI `+1.08%`、target-date block CI `[-20.36%,+19.89%]`。
按当前 bracket policy 重放保留74 intents，70个settled的ROI为`+4.83%`、CI
`[-19.45%,+23.50%]`，仍不显著。概率层同rows合计model/market Brier=`.09356/.09048`、
logloss=`.30229/.27655`，market仍好；Helsinki与Amsterdam market-offset只有微弱Brier点估改善且CI跨0。
Tokyo三个head与Amsterdam cross-.7均为0 selections；Busan 12个、Helsinki 16个、Amsterdam V9/offset
为42/15个。当前结论统一为`inconclusive / continue zero-notional / no-live-change`。完整双漏斗、赔率带、
side拆分与逐条 selection/outcome 见
[admission-forward exact-market review](analysis/wcir_admission_forward_market_trigger_review.md)。

Tokyo的0必须分层解释：唯一开启`emit_paper_intents`的pre-cross V2只有3条可评分候选，扣5-share成本、
fee和reserve后的edge均为负；state-entry/overshoot虽分别有27/2条scored `would_enter`，但配置明确禁止
发intent，且全分母proper score仍输market，不能补入实际forward headline。执行层也尚未过live门：85个intent
均无真实order/fill；Amsterdam 57/57条t0盘口可扫5股，但+15s后只有54/57仍满足原edge，Busan/Helsinki
又有22/28条edge不超过2c且缺统一post-decision tape。任何tiny-live评估前都必须在submit前重新抓盘口、
重算5-share sweep+fee，并用不追价的限价/FOK/FAK记录真实fill denominator；Helsinki现行900s book-age
上限不能直接作为live授权。

补充分层判断：Helsinki目前是active profiles中最值得继续验证的潜在residual，但不是confirmed alpha。
其732个settled expression rows/11日的Brier delta为`-.00117`、CI仍跨0；15个settled intents的
ROI`+16.57%`也由NO侧贡献98.3%净PnL，移除两笔最大赢家后ROI只剩`+.24%`，且16/16 edge≤2c。
Amsterdam则不能混称“模型不行”：V9全分母Brier/logloss均输market，适合保留为dormant weather
comparator；market-offset V3全分母Brier点估较好但selected tail过度自信（model/market/realized
`.848/.698/.667`），应研究state-entry/date×bracket等权的bounded residual calibration，而不是加事后gate。

### Amsterdam market-offset 第一性原理 retraining（2026-08-26）

已完成首轮训练与同分母重放。4/3–7/29固定11,800 checkpoints/112日，6/1–7/29 nested OOF为
6,136 rows/57日。保持market logit系数1，并把checkpoint、V5 path-regime transition、state-entry各
按日期等权再各占1/3；24组预定候选中，balanced模型在June、July均选`weather_path_mechanism / L2=.01 /
无cap`，multigrain Brier/logloss=`.06211/.19998`，优于incumbent `.06448/.20901`和market
`.07080/.22386`。去掉V9 disagreement的physical-only为`.06329/.20192`。

已看过的8/12–23只作reused diagnostic：484同rows/11日上，incumbent、balanced、physical-only相对t0
market的ΔBrier分别`+.00556/+.00488/+.00326`，Δlogloss分别`+.02304/+.02036/+.01200`，CI均跨0；
edge02 replay ROI分别`-8.95%/-8.36%/-20.80%`。因此challenger只证明相对incumbent有点改善，没有通过
market、fee或significance门；均为v2 research-only artifact、`live_eligible=false`，不替换当前WCIR。
开发中曾发现并修复“price join后重算grain覆盖canonical membership”的新实现bug，作废错误run后以
pre-evidence reference membership重跑；production未受影响。当前forecast collector stale，修复前不延伸
新forward。完整数字见[admission-forward living review](analysis/wcir_admission_forward_market_trigger_review.md#amsterdam-第一性原理-retraining-首轮2026-08-26)，artifact摘要为
`amsterdam_knmi_market_offset_balanced_bounded_v1/run=20260826_first_principles/comparison_summary.json`。

随后在同一固定分母完成了部署前封闭tournament：8个feature sets × 3个L2 × 2个caps × 5个correction
scales，共240候选/fold，同时包含raw/calibrated market和各weather residual。选模只看三grain date-equal
logloss（Brier tie-break），不看ROI；最终仍选择`weather_path_mechanism / L2=.01 / 无cap / scale=1`。
其6–7月expanding OOF multigrain Brier/logloss=`.06273/.20120`，market=`.07080/.22386`；但已见的
8/12–23 outer audit相对t0 market仍为`+.00488/+.02036`，edge02 12笔7胜、ROI`-8.36%`。所以当前
decision-probability champion是raw pre-event market，balanced residual只是当前weather champion。
最终refit并入8月498个PIT rows后为12,298 rows/123日，artifact SHA
`9e337219…1b20f6`，clean boundary=`2026-08-26T12:00:15.867834Z`；只允许zero-notional完整分母A/B。
v2 adapter兼容代码已补并保留v1，release `da87d3c1…befa0`已部署到既有Mac zero-notional runtime。
首条12:10 UTC KNMI event输出bracket 24双边，coverage 100%、missing=0；YES/NO概率
`2.4076%/97.5924%`，两侧fee后edge均负而skip，`errors=0 / orders=0`。collector当前health已恢复，前述
stale只描述首轮运行时刻。机器身份见`amsterdam_knmi_market_offset_champion_tournament/run=20260826_current_best/champion_manifest.json`。

跨城共同结论：模型是否“预测天气不错”与是否“打败同刻 market”必须分开。

### Amsterdam unified next-print pilot（2026-08-29）

统一三层数据协议后，Amsterdam historical-final archive 为253,290个10-minute checkpoints/1,814日，
全部缺原始first-seen；因此只用于weather-only development，不声称strict PIT。固定最后20个可用target dates
（7/09–29）作historical outer时，M1强正则ordinal model相对最强B2 latest-fast baseline的date-equal
RPS差为`-0.00128`，95% date-block CI `[-0.00174,-0.00084]`。但在既有Stage-3 captured-PIT
的87 events/16日上结果反转：M1/B2 RPS=`.01815/.01012`，差`+.00803`，CI
`[+.00405,+.01273]`，且logloss也明显更差。按预注册裁决为`FAIL`：artifact只保留为negative
control，不启动新的forward epoch、不部署shadow、不复制到Helsinki。Amsterdam recorded-book同分母没有
一条完整可执行entry，market-only ordinal prior也是0，因此economic replay为`INCONCLUSIVE/PnL NULL`，
orders/fills/notional仍为0/0/0。另已显式记录Stage-1把official source写为KNMI 10m、而Stage-3/runtime
reaction lineage使用下一EHAM routine METAR/WU print的provenance差异；跨城canonical promotion前必须收口，
不能静默混用。审阅入口：`reviews/wcir_unified_data_amsterdam_pilot_v1/GPT_PRO_REVIEW_PACKET.md`。

#### v1.1 feature parity、market archive 与 frozen score-only closure

v1.1 保留 v1 immutable evidence，并把 fast source、prediction target、settlement source 拆为三个独立
identity；`ta/tx/tn` 分别固定为 KNMI 10-minute average/max/min，其中 frozen input 的 `tn` 明确不可用。
historical-final、captured-PIT、prospective shadow 现统一调用 `AmsterdamFeatureBuilderV2`。87 个 captured
opportunities 中 75 个具备从 Amsterdam 当地日初到 cutoff 的连续 10-minute source path；5 个缺当地日初、
7 个存在 20-minute gap，共 12 个以 `INCOMPLETE_CAPTURED_SOURCE_PATH` fail closed。v1 稀疏 opportunity
groupby 影响 87/87 行：`path_volatility` 87、`recent_acceleration` 76、`recent_slope` 57、
`reheat_strength` 85，其他逐特征计数见 parity audit。

同分母 primary RPS 排名为 P0/P1 `M1 < M2 < B2`，corrected P2（75 rows/51 print groups/14 dates）
为 `B2 .01038 < M2 .01099 < M1 .01183`。P2 target-date bootstrap 的 M1−B2 CI
`[+.000003,+.003273]`、M2−B2 CI `[+.000017,+.001243]`，故冻结 B2 为 primary reference，M1/M2
只作 challengers；但两组 official-print-group cluster CI 均跨 0，推断不具 cluster robustness。

旧 42/42（现 living evidence 更新为 57/57）是 V9 selected final-temperature expression 分母，不是本轮
87 个 next-print opportunities。v1 的 0/87 根因是只消费 Stage-2 deterministic WS rows，未接独立 REST
full-ladder archive；逐行旧口径原因为 83 no archive、3 checkpoint missing、1 one-sided。接回 exact-identity
Tier-B REST 后 1/5-share entry 均为 36，但 Tier-A 仍为 0；+30/+60/+120s markout 各仅 2 rows/1 date，
Layer B 因而 `NOT_ESTIMABLE`，MR0/MR1 均未拟合、uplift 为 NULL。

epoch `wcir_amsterdam_score_only_v2_c18fa016b0612195` 已初始化 0600、append-only 空 journal，三臂固定为
`B2_REFERENCE / M1_CHALLENGER / M2_CHALLENGER`。没有注册 continuous owner，因此状态诚实保持
`INITIALIZED_NOT_CONTINUOUSLY_SCHEDULED`；不生成 BUY/SELL/TradeIntent，orders/fills/notional 为 0/0/0。
继续 Amsterdam prospective evidence，不复制到 Helsinki、不授权 live。完整证据位于
`reviews/wcir_unified_data_amsterdam_pilot_v1_1/GPT_PRO_REVIEW_PACKET_AMSTERDAM_V1_1.md`。

### Seoul WCIR exact-NO 首版训练（2026-08-13）

结论：现有 WCIR/Korea AMOS raw 已能训练 Seoul 独立 probability artifact，但首版训练门失败，
不能接 runtime。开发窗在预注册的 market/weather 融合权重 `0/.125/.25/.5` 中选择 `0.0`，
即完全退回同刻 market prior。8/7–11 的五个 frozen historical holdout dates 上，
preferred-runway weather-only logloss/Brier 为 `0.89034/0.19491`，显著差于同 rows market 的
`0.17277/0.04448`；logloss delta `+0.71757`，target-date block 95% CI
`[+0.44645,+0.98170]`。动作保持 `coverage-only`，不生成 candidate/intent/order，
不改 live；artifact 的 `runtime_eligible/live_eligible` 均为 false，clean-forward boundary 为
`2026-08-13`。

实验 target 是每个 PIT state 的
`P(final winning exact bracket != current routine running-max rung)`，即当前档 exact-NO，
不是 touch probability。source clock 为 AMOS grouped point first-seen；盘口取该 source event 后第一份
exact-current-rung 双边 book；主模型单独重建 Seoul preferred-runway path，多跑道最大值只作 ablation。
development 为 7/22–8/06，但同刻盘口实际从 7/30 才覆盖；前5个可评分日期 warmup，后3日 expanding
OOF 只选 `alpha`。primary grain 是每个 `target_date × current routine rung` 第一条有 PIT book 的
checkpoint；缺盘口只记 evidence gap。

双漏斗：signal 为
`24,283 raw rows → 19,020 unique checkpoints → 145 raw state entries → 2,019 ten-minute
checkpoints → 0 holdout expressions`；evidence 为
`19,020 PIT rows → 4,104 current-rung market mids → 3,883 settled/scorable rows →
54 scorable state entries / 13 dates → 0 actual fills`。preferred-runway coverage 为100%；
source→book lag p50/p95=`2.98/5.37s`，失败不是 collector latency 造成。大量5秒轮询 row 是高度相关
checkpoint，不等于独立天气日。

开发 OOF 的 `alpha=0/.125/.25/.5` logloss 依次为
`0.18982/0.20226/0.22355/0.28388`。Frozen holdout 24 state entries/5 dates 上，
max-runway weather-only logloss/Brier=`0.83624/0.21149`，也失败；preferred basis 只在部分指标较好，
不能确认。selected posterior 等于 market mid，扣 direct-NO ask 和官方 fee 后为0 signal。

下一实验不调这个短样本 binary head：沿用 Korea remaining-heat 物理层，训练 Seoul 专属、
settlement-native 的 `P(stay/+1/+2/+3+)` 连续分布。长历史只学 weather/source→settlement basis，
AMOS preferred-runway 做 PIT transfer，近期 market 只作 coefficient-one prior；至少积累30个新的
settled clean-forward dates 后，再检验同 rows multiclass Brier/logloss/RPS 与 fee-adjusted expression。

机器产物位于
`/Volumes/jrs-archive/pm_agents/research/artifact_store/weather_city_intraday_probability/korea_city_exact_no/city=seoul/run=20260813_preferred_runway_v1/`：
`summary.json` SHA-256=`71d82cb5…ecd3e`，`model.joblib` SHA-256=`e7b567d1…72a9`，
prediction table SHA-256=`c2202f2d…33ff`，序列化 parity max abs error=`0.0`。三门为
`significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=inconclusive_research_artifact`。

### Seoul WCIR remaining-heat distribution v2（2026-08-13）

第二版已实际训练为 settlement-native 守恒分布，固定五类为
`negative / stay / +1 / +2 / +3+`；`negative` 显式保留 AMOS/routine 与 WU settlement
basis，不把快源跨档机械当成结算事实。长历史 physical 层只用 Seoul IEM routine states：
5/12–7/07 共 `513 rows / 57 dates` 训练，7/08–21 为 `126 rows / 14 dates`
historical holdout。v2 weather head 相对 train climatology 的 Brier/RPS delta 为
`-0.00849/-0.02727`，但95% CI分别为 `[-0.03915,+0.02148]`、
`[-0.05994,+0.00589]`；logloss delta `+0.00561`，因此 physical 独立门未通过。

AMOS 层使用 preferred-runway pullback、距高点时间、1h slope、RH/露点差和当地时钟做
PIT feature transfer，不用近期 settlement label 重训 physical model。7/22–8/06 development
在 coefficient-one market prior 上只选择 likelihood-ratio 权重
`alpha=0/.125/.25/.5/1`，按logloss选中 `.25`。完整 event ladder 的 tail 只能取 source
前最近快照，source后仅五个 active conditions 有 exact book；为避免把旧盘口冒充同刻分母，
market score 只保留五档全部刷新成功的 row。最终 funnel 为
`1,059 market-grain rows → 17 scorable / 10 dates → frozen 6 state entries / 4 dates`；
tail age p50/p95=`340s/1,877s`，所以这仍不是 synchronized full-post ladder。

8/7–11 frozen historical market分母上，raw market multiclass
Brier/logloss/RPS=`0.09898/0.86005/0.07206`，`.25` posterior 为
`0.11340/0.91226/0.08121`；candidate−market delta=`+0.01442/+0.05222/+0.00916`，
CI均跨0。weather-only在同rows三项显著更差。由于没有同步 full-post event ladder 的整条
ask/depth，fee-adjusted expression=`not_estimable`，actual fills=`0`。

机器产物位于
`/Volumes/jrs-archive/pm_agents/research/artifact_store/weather_city_intraday_probability/korea_city_remaining_heat_distribution/city=seoul/run=20260813_remaining_heat_distribution_v2/`；
summary/model/prediction SHA-256分别为`4d51cf88…e45ab`、`f72ab90b…4e320`、
`5ddf9606…f4146`；模型 serialization parity error=`0.0`，代码身份为`7ea49b58…2ef2`，
`runtime/live eligible=false`。三门仍为
`significance=FAIL; baseline=FAIL; forward=FAIL`。动作不是继续调这4个market日期，而是补同步
full-post ladder，并从8/12之后累计至少30个新settled clean-forward dates。

### Seoul WCIR decision-ladder residual v3（2026-08-13）

结论：v3已解决v2的异步tail/full-post盘口缺口，也完成信号数和fee-adjusted PnL回放；但
**冻结概率仍未打败同刻market，因此不进shadow**。稳定target仍是settlement-native
`P(negative/stay/+1/+2/+3+)`，primary grain为每个`target_date × routine rung`首次
state entry。每份完整event ladder是decision clock，只使用其前5分钟内最新AMOS状态；交易层另取
decision后的下一份完整ladder，禁止用特征盘口成交。

训练切分固定为：physical model使用5/12–7/07的`513 rows / 57 dates`训练、7/08–21的
`126 / 14 dates`作physical holdout；market residual只在7/22–8/01的10日从
`beta=.75/1/1.25/1.5 × alpha=0/.125/.25/.5/1`共20组选择，得到
`market_power=1.0, weather_weight=.5`；8/02–06为development，8/07–11为未参与选择的
historical frozen holdout。该窗口不是8/12后的clean forward。

双漏斗：signal为`23,322 raw city rows → 18,059 unique checkpoints → 88 raw state
entries / 1,059 ten-minute checkpoints → 40 selected expressions`；evidence为
`1,649 complete-ladder rows → 582 scorable rows / 20 dates → 64 state entries →
574 next-ladder executable rows / 20 dates → 0 actual fills`。其余blocker为
`937 missing recent AMOS + 81 empty class + 49 missing exact center`。scorable行的
source age p50/p95=`31.2/63.6s`；每条ladder单侧可见outcome中位7档，v3按可见bid/ask归一化
market分布，但执行只认selected side的direct ask与size。

primary frozen结果为22 state entries/5日：posterior的Brier/logloss/RPS=
`0.08373/0.71764/0.05884`，market=`0.08053/0.70560/0.05518`；candidate−market
delta=`+0.00320/+0.01204/+0.00366`，95% CI分别为
`[-0.00714,+0.01862] / [-0.06718,+0.12701] / [-0.00363,+0.01429]`。
十分钟辅助grain虽在Brier/logloss点估略好，但RPS略差且三项CI都跨0，不能替代预注册state-entry门。

交易回放只表达单token的`stay/+1/+2`，每个date×condition取首个净edge超过1pp的最佳YES/NO，
size=`min(5, ask size)`，费用为`shares × 0.05 × price × (1-price)`。residual train为
18 signals/10日、PnL`+$1.4745`、ROI`+3.39%`；development为10/5、`+$4.7939`、
`+27.80%`；frozen为12/5、5胜、11 YES/1 NO、cost`$19.7664`、fee`$0.5664`、
PnL`+$5.2337`、ROI`+26.48%`，target-date block CI=`[-54.92%,+103.50%]`。
同底层rows/quotes/rules的market-favorite head在frozen选择9信号/4日、PnL`-$3.0269`，但两者
选中的交易集合不同，不能把差额当paired uplift。实际fills仍为0。

机器产物位于
`/Volumes/jrs-archive/pm_agents/research/artifact_store/weather_city_intraday_probability/korea_city_remaining_heat_distribution/city=seoul/run=20260813_decision_ladder_residual_v3/`；
summary/model/prediction SHA-256分别为`6287d0a3…e434b`、`7ecced72…1960`、
`04928e63…9b95`，serialization parity error=`0.0`，代码commit=`69502f9d…2065`。
三门为`significance=FAIL; baseline=FAIL; forward=FAIL`，artifact继续
`runtime/live eligible=false`。唯一动作是保留coverage-only，从8/12后累计至少30个新settled
clean-forward dates，再在冻结参数下复核同分母proper score和fee-adjusted uplift；不按这12笔调gate。

**Helsinki 2026-08-12 更新**：`helsinki_regime_calibrated_bounded_residual_c015_v2`已完成结构化重训并成为 zero-notional replacement candidate。模型选择只使用2025 OOF，不读取8月ROI；三段rolling proper score全胜后才冻结。7月market开发分母上相对旧模型的Brier/logloss paired CI全负；8月回放交易仍是原来的9笔6胜、PnL`+$6.98`、ROI`+30.31%`，没有靠改历史信号制造收益。artifact SHA=`fd09be9c…14e9`，runtime parity最大误差`1.11e-16`；v2 forward从`08:31:24Z`开始，不升live。详见 [strategy report](analysis/2026-08/2026-08-12-helsinki-bounded-market-residual-strategy-v1.md)。
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

### 5.5 Next-official-print 跨城迁移的实证边界（2026-08-29）

Amsterdam rev2 的 shared full-path ordinal 方法迁移到 Helsinki 与 Tokyo 后，完整路径根因修复后的历史分母为
Helsinki 59,868 rows、Tokyo 58,047 rows。Tokyo 的 expanding OOF、opportunity-matched 与最后20日 outer 上
M1/M2 RPS 都优于城市原生 B2；Helsinki 在 all-checkpoint/outer 上两者更好，但 opportunity-matched 上只有 M2
更好，M1 略差。真正 captured-PIT 分母上两城 challenger 也只是点估改善：Helsinki 45/67 rows、14 dates的
B2/M1/M2 RPS=`.01183/.01091/.01107`，M1−B2 CI=`[-.00363,+.00108]`；Tokyo 37/43 rows、14 dates为
`.00973/.00931/.00955`，M1−B2 CI=`[-.00127,+.00032]`。两城都保持 `INCONCLUSIVE_NO_PROMOTION`，继续
zero-notional collection，不启动新的 frozen forward 或部署。

该结果固化三条跨城默认经验：第一，fast source、official target、settlement truth、station 和 routine clock
必须逐城冻结，FMI/EFHK 与 JMA/RJTT 不能继承 KNMI/EHAM 语义；第二，captured feature 必须从 decision 前已
available 的完整城市 raw path 计算，不能从 opportunity rows 重算 slope/running max/pullback/reheat；第三，
historical-final improvement 不能替代 captured-PIT promotion。下一官方 print 到 exact settlement token 的 action
mapping 尚未注册时，book 只做 repricing diagnostic，不把 `p_new_running_max` 写成 token fair probability。本轮82个
eligible event 均有 Tier A/B book evidence，但没有任何一行同时具备 archive-native event_id/market_id/condition_id/
token_id/checkpoint 五字段；因此 historical executable replay 明确为不可估，不能把派生 event join 称为 full exact identity。
1200秒合同修复最终恢复 Helsinki 13行、Tokyo 10行；本地05:50完整path起点合同使 Helsinki 22行、Tokyo 6行
fail closed。完整路径修复影响 Helsinki 59,590/59,868、Tokyo 57,211/58,047 个历史 eligible rows，逐行差异已封存。
完整逐行证据见 `reviews/wcir_next_official_print_helsinki_tokyo_rev1/`。

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
