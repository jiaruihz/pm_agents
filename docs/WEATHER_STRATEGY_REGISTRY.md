# Weather 策略总账（我们到底试过哪些 · 灵感/规则 · 是否可行 · 血缘归属）

Status: `current-reference`
Updated: 2026-07-03 tail strategy family map
Source of truth: 状态/结论以各 living doc 为准，本表只做汇总入口

这份是"我们一共研究过哪些策略"的单页总账。每条策略：**灵感/盈利规则 → 当前状态 → 是否可行 →
属于量化血缘哪一层**。状态/结论的权威来源是评估层 living docs（`docs/analysis/*.md`），本表汇总它们，
有冲突以 living doc 为准。

> 关联主轴：[0]–[6] 分层定义见 [WEATHER_ARCHITECTURE_SPINE.md](WEATHER_ARCHITECTURE_SPINE.md)；
> 一条策略跑完怎么复盘见 [WEATHER_STRATEGY_REVIEW_PIPELINE.md](WEATHER_STRATEGY_REVIEW_PIPELINE.md)。

## 状态图例

| 状态 | 含义 |
|---|---|
| `live` | 当前真实下单（实盘城市池/方向见 `WEATHER_CITY_POOL_DECISIONS.md` / `WEATHER_STRATEGY_ENTRYPOINT.md`） |
| `shadow` | 跑零 notional 影子遥测，不下单 |
| `paper` | 纸面/回放记账 |
| `research` | 仅离线研究，未达 shadow 标准 |
| `dormant` | 因方向切换暂时不用，**但未被永久证伪，保留备用**（如某分支日后跑通可复用），不归档不删 |
| `shelved` | 停用/被新实现取代，仅留历史；本项目默认仍保留文件，不主动删除 |

> 重要：本项目研究结论大多是 `inconclusive`（暂未确认），不是 `disproven`（已否定）。
> 一条策略"当前不在主线"≠"可以清掉"。Range RV / pre_predict 等方向可能回归，相关底表/脚本一律**保留备用**。

## 注意：策略会换代，执行/评估血缘不换

区分两层，别混：

- **策略 / 特征层 [0]–[1]（会流动）**：observed_max → reheat factory、哪些信号 live/shadow/dormant。
  本总账跟踪的就是这层；它换方向是正常的。
- **执行 / 评估血缘 [3]–[6]（永久基础设施，不随策略换代而重做）**：
  `order → fill → live/shadow 对比 → PnL → 关联 strategy_config 参数 → 看板`。
  这条链与具体策略无关，由 canonical 表（`orders` / `fills` / `fact_trades.strategy_instance` /
  `settlements` / `strategy_config`）和看板（`run_stack.sh` 的 `/weather/live`、`/weather/runs`）支撑，
  living docs 是 [live_performance.md](analysis/live_performance.md)([5][6]) 与
  [account_reconcile.md](analysis/account_reconcile.md)([5])。
  **换策略方向不动这条链；清理/重构也不碰它。**

## 血缘分支（白皮书口径）

```text
pre_predict   赛前/早盘：没看到日内路径时，预测最终最高温分布（给 prior）
reheat_risk   日内路径：已看到 running max 后，判断会不会再升温（给 conditional update）
两支共享一个事实层 reheat_feature_factory_v1
```

---

## 分支一：pre_predict（[1] 概率 / [2] 结构 / [3] 选择）

| 策略 / 家族 | 灵感 / 盈利规则 | 状态 | 是否可行（当前结论） | 血缘层 · 入口 doc |
|---|---|---|---|---|
| 普通单腿 YES/NO | forecast max + 历史误差 + 市场隐含，挑 mispriced bracket | `research` | baseline；裸 `model_p_yes - price` 不是确认 alpha | [1] model_vs_market |
| model×market 融合 overlay | `0.3*model + 0.7*market`，承认市场吃掉大部分公开天气信息 | `research` | 提升太小，未确认 alpha；global model alpha 为负 | [1] model_vs_market |
| forecast quality / reliability base | entropy/adjacent mass/city-model history 转可复用可靠性标签 | `shadow` | **只作共享可靠性层 / soft 标签**，非独立 live 策略 | [1] model_vs_market |
| forecast-bounded Range RV | forecast 锁定档位区间内做相对价值 | `shadow` | 三统计门过、但 live-standard/forward 不过；零 notional shadow | [2] market_structure_edge |
| adjacent / range basket | 相邻档/区间篮子的相对定价 | `research` | inconclusive，holdout/top5 不稳 | [2] market_structure_edge |
| all-YES underround（no-arb 篮子） | 互斥档 YES ask 之和 <1 的无套利结构 | `research` | **离线确认（+3.16% settled unit ROI）但散户 live 被否**（per-leg buffer~0.3¢、全腿成交/部分成交风险） | [2] market_structure_edge |
| side-band / BUY_NO side alpha | BUY_NO 历史胜率高、特定价带方向偏好 | `research` | **胜率 ≠ alpha**；clean 测试三门不过，仅作特征/标签 | [2] side_alpha |
| low_price_yes_lottery_tiny_live_v1 | refined low-price BUY_YES longshot：`edge>=0.20`、ask `0.05..0.20`、每 city-date 一笔，fresh-book maker-first，固定 `$0.8/order`，并记录 station-basis / p_cal / bracket-distance shadow telemetry | **`live`（tiny-live forward probe）** | 用户 2026-07-02 批准微仓前向取证；研究结论仍是 `shadow_candidate_keep_collecting`，核心风险是少数尾部命中驱动与成交质量，所以只按独立 strategy family 记录，不接 regime-routed NO runner，不视为 confirmed alpha；telemetry 只作 fresh-forward 验证，不参与 live selector | [1]-[4] pre_predict / forecast_quality · [selector refinement](analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md) |
| low_price_yes_take_profit_exit_v1 | V1 low-price YES 已有 position 的 TP20 退出 overlay：position 出现后预挂 `SELL YES @0.20` maker；若首次发现时 bid 已 `>=0.20`，先尝试 maker improve，maker cancel 后才 taker fallback | **`disabled`（2026-07-03 15:21Z 停用）** | 2026-07-03 早批准跑 20c 止盈版本；同日 sizing-stop v1 live-like replay 发现预挂固定 20c 与 max-bid 回放是不同执行产品（fixed-20c 全窗 -9.1% vs hold +25.9%），当日 `disable_tp20_exit_overlay` 撤掉全部 resting SELL、LaunchAgent 移除；当前 live 姿态回到 hold-to-settlement，TP/stop 仅 shadow would-trigger telemetry | [1]-[4] forecast_quality · [take-profit replay](analysis/2026-07/2026-07-03-low-price-yes-take-profit-v1.md) |
| low_price_yes_integrated_tail_shadow_v2 | 在 V1 low-price YES 分母旁边记录 source-aware v3、station-basis/p_cal、实时 METAR/observed path、简化 regime score 和 expression-selector context，用 fresh forward 判断到底是 forecast-tail、station-basis 还是市场低估尾部 | **`shadow`（zero-notional）** | `shadow_candidate` 但不升 live：source-aware 历史最好，no-city integrated 分数 holdout 不稳，city-diagnostic p_cal 有 city/source memory 风险，METAR same-denominator 覆盖太低且不能改善。已在 Mac LaunchAgent `com.pm-agents.low-price-yes-integrated-tail-shadow` 跑独立 shadow journal；不改 V1 `$1` selector、不 size-up。**2026-07-03 加 `pcal_v2_*` tag**：修正 raw edge 非单调问题，train 校准 decile 单调、selected-CI>0，但 excess-vs-v1 CI 跨 0，acceptance 未全过，仍 diagnostic-only。**2026-07-04 加 `hot_tail_boundary_v1`/`bracket_dist_br_v1`/`book_state_v1` tag**（数据审计后预注册）：train 上 `dist≤0` 的"预报向下 bust"票 -10.6% vs hot 子集 +38.3% CI>0；hot 边际集中在 book missing/宽 spread 行而 feasible 行 ROI≈0，fresh forward（7/04 起）裁决 stale-quote 假边际 vs 注意力真错价 | [1]-[4] forecast_quality · [integrated tail v2](analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.md) · [pcal v2](analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.md) · [数据审计+边界 v1](analysis/2026-07/2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md) |
| station-basis（结算源 basis） | 官方结算站点 vs 市场所用站点的温差 basis | `shadow` | 当前主操作 shadow 线，`NOT_READY_ACCUMULATE_SHADOW`，有前向阻塞 | [0]-[2] 见 ENTRYPOINT |
| metar_reversal（intraday expression matrix / rich-current collapse） | 独立于 D-1 forecast-tail 的日内策略族：同一 city-date-hour snapshot 下比较 current_high_yes / current_bracket_no / d1-d2 yes-no / high_tail_yes 表达。当前主候选是 `rich_current_collapse_d1_yes`：current YES 仍高价、obs 还在升温、forecast bracket-aware 落点至少高一格、peak ahead，买 d1 YES；`false_fade_reheat_conflict` 是同一底层形态的旧阈值视角，`heat_death` 只是 telemetry | **`research`（zero-notional shadow candidate，未 live）** | `rich_current_collapse_d1_yes`/B4：59 rows/28 dates，d1 YES hold ROI +103.2% CI [+38.3%,+163.8%]，top5-removed +50.8%；与旧 anchored/false-fade 触发并集 69 rows ROI +87.4% CI>0。执行结论与低价彩票相反：**taker entry + hold**，TP20/TP30 和 maker-first 均否决。全部合格行 pre-6/21，核心风险是 state starvation，所以只能单独开 Head B shadow，不并入低价彩票仓或 regime-routed NO runner | [1]-[4] reheat_risk · [family map](analysis/2026-07/2026-07-03-tail-strategy-family-map-v1.md) · [reversal shapes](analysis/2026-07/2026-07-03-hotter-tail-reversal-shapes-v1.md) · [strategy lineage](analysis/2026-07/2026-07-03-metar-reversal-strategy-lineage-v1.md) |

## 分支二：reheat_risk（[0] 事实 / [1]-[2] 模型与表达）

| 策略 / 家族 | 灵感 / 盈利规则 | 状态 | 是否可行（当前结论） | 血缘层 · 入口 doc |
|---|---|---|---|---|
| current_yes_fade_confirmed | 日内已回落后更稳健地买 current YES | **`live`（tiny-live $5/单·$5/城日）** | **当前 live 之一**（N100 `weather_theta_current_yes_tiny_live.py --entry-profile-mode fade_confirmed --live`，2026-06-19 起）；默认 timing head，但整支仍卡 execution freshness / fresh-ask 滑点 → 当作前向取证探针，按执行质量评估不按 PnL | [1]-[2] reheat_risk |
| current_yes_peak_forming_micro | 当前仍在高位时买 current YES（微仓） | **`live`（tiny-live $5）** | **当前 live 之一**（同脚本 `--entry-profile-mode peak_forming_micro --enable-peak-forming-live`）；注意：已从白皮书旧口径"shadow only"**升级为 micro live**（用户 2026-06-19 确认有意为之） | [1]-[2] reheat_risk |
| metar_cross_prev_no | 用实时 METAR 交叉前日 NO（latency/source basis） | **`live`（$10/单·$50/天）** | **当前 tiny-live 前向取证**（N100 `weather_metar_cross_prev_no_shadow.py --live`）；Busan/BuenosAires 已成交小额汤底，但核心瓶颈是 public METAR/tgftp 上游延迟，盘口最快样本在 report 附近 0-60s 或更早 pre-cross 撤/清；入口见 latency microstructure doc | [0]-[2] reheat_risk · [latency microstructure](WEATHER_LATENCY_ARB_OBSERVATION_MICROSTRUCTURE.md) |
| post_cross_repricing | 刚穿温度后观察 current/new-high 和 T+1/T+2 bracket 如何重新分配概率；不抢已死 T-1 NO，而研究市场是否过冲/慢半拍 | `research` | 新研究任务；初步 N100 book 样本显示 crossed bracket 最快归零，但 current `T` YES 与 tail repricing 很不均匀，可能比纯 latency 抢单更适合散户；必须按真实 book、same-price baseline、forward date gate 评估，不 live | [2]-[4] market_structure_edge · [post_cross_repricing](analysis/post_cross_repricing.md) |
| higher_no_carry | 买更高温档 NO（ladder carry） | `shadow`（telemetry only） | 没证明能稳定打赢同窗 current YES，仅 shadow 表达遥测 | [2] reheat_risk |
| tmax_distribution_edge_shadow_v1 | 估计 `P(current/d1/d2/tail)`，再按 `P(win)-ask` 在 `current YES/current NO/d1 NO/d2 NO` 中选最高 edge 表达；selected 和 blocked 都写 zero-notional journal | **`shadow`（zero-notional）** | 新主线：把 regime 从买卖规则降级为概率模型特征。数据已同步并重建到 settlement_outcomes max `2026-06-30`、atlas/P4-P6 max `2026-07-01`。Verified settlement-backed forward：clean edge02 459 rows ROI +13.5% CI [+2.7%,+23.2%]，city-source edge02 577 rows ROI +11.1%；extension/observed rows 仍正但 CI 跨 0。runtime shadow runner 已写入 562 journal rows；结论仍 `inconclusive_positive_signal`，不改 live、不 size-up | [1]-[4] reheat_risk · [strategy](WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md) |
| current_bracket_no_pass_through（含 climbing_no_peak_runway） | 买当前 running-max 档 **NO**，赌午后继续创新高把它打穿；分类器挑「会午后创新高」的**便宜 NO(ask 0.01–0.35)** | `shadow_candidate`（PIT 线，zero-notional，未结算，不 live） | **canonical 线 = pass-through → afternoon-peak classifier → prevday PIT shadow**：classifier 三门 PASS（+37.6%，CI[+11%,+66%]，相对同价 baseline excess +46%，holdout AUC 0.828），PIT 前一日 GFS forecast 版 +29.7%（CI[+3.8%,+54.6%]，excess +38.1%）；同价未筛 baseline 是 -8.4%/-8.5%。forward 仅 zero-notional candidates、forecast 口径尚非生产级 PIT，**不 live**。我方 `climbing_no_peak_runway`（runway re-gate + near-noon direct）是**同笔交易、更早更糙、非 PIT** 的版本，贡献=证「整片/贵 NO(≈0.88) 已被定价、edge 只在便宜 NO+午后创新高子集」，与 baseline 一致；其 forecast 特征有**前视风险**，数字以 PIT 线为准。**待办：两个 feature factory 收敛成一个** | [1]-[2] reheat_risk · [PIT shadow v1](analysis/2026-06/2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md) · [classifier v1](analysis/2026-06/2026-06-23-current-bracket-no-afternoon-peak-classifier-v1.md) · [climbing-no（我方/非PIT）](analysis/2026-06/2026-06-22-current-yes-climbing-no-peak-runway-regate-v1.md) |
| regime_routed_no_route_price_disciplined_tiny_live_v1 | `route_price_disciplined + no_pullback + row_risk_soft`：live-executable legs 只保留 fresh runway current-NO 与 capped d2-NO；false-fade reheat / cheap stale-tail current-NO 已改为 shadow-only diagnostics，不并入 pullback YES | **`live`（tiny-live forward probe）** | 替换旧 `regime_routed_no_soft_balanced_tiny_live_v1`。2026-06-29 起按 route-specific ask cap、row-risk soft sizing、market event date lineage、current-local-day filter、feature parity、duplicate city/date/token veto 执行；2026-07-04 P1/P2 guard fix 对 `false_fade_reheat_current_no`、`cheap_stale_tail_current_no` 加 `shadow_only_route_leg` 执行硬 veto。研究结论仍是 `inconclusive_shadow_only`，所以该 live 只能作为用户批准的微仓前向取证，不是 confirmed edge；离线 replay 主口径为 `first_eligible`，`best_ask` 只作乐观上界诊断。 | [1]-[4] reheat_risk |
| low_price_yes_reheat_reversal | 需二次升温才命中的低价 YES，升级成 `forecast prior × reheat condition` | `research` / runner ready locally | 这是 reheat-risk 共享底座的反买头，不是独立彩票线；v1 holdout 有凸性但 CI/日期稳健性不过，zero-notional runner 已落地但 fresh observed/reheat feature 生产未接到当前日期，forward shadow blocked；下一步 trend-signal v2 验证 | [1]-[2] reheat_risk · [expression map](analysis/2026-06/2026-06-21-reheat-risk-yes-no-expression-map.md) |
| reheat 尾部机制特征补全 | 机制覆盖审计→尾部特征补全：高 ask EV 在尾部，补暖平流(风向)/云导数等机制，而非调阈值 | `research`（方向） | **不是“气象没用”：METAR core 对 survive 有真实判别力。A/B 后续(2026-06-20)：`market+METAR core(+alti)` 正则 logistic 点估最好(logloss 0.2978，dLL vs market -0.00646)，但日期 CI 跨 0，promotion FAIL；HGB/ML 在当前 27 日期窗口退化；`d_alti_3h` 有小的条件信号，但候选模型仍不过 raw market/base gate。** 当前动作是不加模型、不改 live；继续需扩样或只把 ML 当严格 challenger。 | [1] reheat_risk · [feature-gap v1](analysis/2026-06/2026-06-19-reheat-tail-mechanism-feature-gap-v1.md) · [proper-form tail features v1](analysis/2026-06/2026-06-20-current-yes-proper-form-tail-features-v1.md) · [residual+alti v1](analysis/2026-06/2026-06-20-current-yes-residual-calibrator-alti-v1.md) |

## 共享 / 基础设施层（[0] 事实层）

| 组件 | 作用 | 状态 | 备注 |
|---|---|---|---|
| `reheat_feature_factory_v1` | 两分支共享事实物化：observed path + current YES/d1-d2 NO/target YES quotes + source-grain settlement | 在用 | **取代旧 observed_max 各自 materialize**；仍缺 forecast peak context（`forecast_peak_hour_local` 等 0% 覆盖，待 backfill） |
| observed_max 旧底表 | 早期日内最高温底表 | `dormant` | 当前主线改走 reheat factory 故暂不用，**但未证伪，保留备用**——若 Range RV / pre_predict 跑通可能复用；不归档不删 |
| forecast quality base | 共享可靠性标签层 | `shadow` | 见上 pre_predict 行 |

## 执行 / 组合 / 城市层（[3] 选择 / [4] 执行）

| 家族 | 灵感 / 规则 | 状态 | 是否可行 | 血缘层 |
|---|---|---|---|---|
| entry_timing | target-date lead time / forecast checkpoint / decision window 限制 | `shadow` | 部分 timing 限制 shadow，未确认广义 live 自动化 | [3] entry_timing |
| sizing / entry band | 替代统一 0.25–0.75 的入场区间与仓位 | `design-draft` | 当前 live sizing/band 仍由 entrypoint/config 定义 | [3] sizing_entry_band |
| execution_quality | maker 扣 spread/queue/逆选后是否仍有可成交 edge | `research` | inconclusive | [4] execution_quality |
| current-YES maker-then-taker | 现 current-YES 改 maker 挂单优先、挂不上且穿价仍划算才转 taker、ask 跑掉 toxic 单跳过；复用执行器现有 maker 基建(maker_queue_v2/mid_price_core_v2 报价引擎)，非复活旧策略 | `design-draft` | 盈利杠杆在执行端(模型已到顶)；shadow-first 四阶段，P1 hard gate=maker 成交集不 toxic | [4] · [maker-then-taker plan v0](analysis/2026-06/2026-06-20-current-yes-maker-then-taker-execution-plan-v0.md) |
| city_selection / city-day basket | city×side×instance 选择、篮子组合 | `shadow` | 篮子仅 shadow，live 城市池由 CITY_POOL_DECISIONS 治理 | [3] city_selection |
| blender / edge-engine | blender 字段作 shadow/paper/size signal | `shadow` | 不作 live hard gate | [1] blender_shadow |
| mid_price_core v1 / v2 / maker_queue | 早期中价核心策略 | `shelved` | **2026-06 因实盘亏损被用户停掉**（v2 6-06，其余 live_real 成交停在 6-11）；是停用决策，非证伪 | 历史 |

---

## 当前优先级（白皮书口径，2026-06-16）

1. 共享 reheat feature factory（A）已 v1：策略头默认消费它，不再各自 materialize。
2. current YES timing（B）已 v1。**更新（2026-06-19）：fade_confirmed 与 peak_forming_micro 现已双双 tiny-live（$5）**，
   白皮书旧口径"peak-forming 仅 shadow / 不改 live"已被取代。
3. **下一步 E**：current YES 已进 tiny-live，但核心未解的仍是 execution freshness / fresh-ask 滑点 → 复盘看执行存活，不看早期 PnL。
4. higher NO carry（C）已 v1：未稳定打赢 current YES，仅 shadow telemetry。
5. low-price YES reheat reversal（D）单独做凸性研究，不与 no-reheat 策略混 PnL。

> 早期"已知盈利模式"（5 月 BUY_NO/Warsaw/ECMWF/LA）是 near-binary 修复前口径，**已作废**，
> 见 `WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md §1.1`。**当前没有任何"已确认稳定盈利"的 live alpha**：
> 在跑的 current YES（fade_confirmed + peak_forming_micro）与 metar-cross 是 **tiny-live 前向取证**（$5–$10 微仓），
> 不是已证实策略；评估按执行质量/滑点，别按早期 PnL。旧 mid_price_core 已因亏损停用。
