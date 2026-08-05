# Weather 策略总账（我们到底试过哪些 · 灵感/规则 · 是否可行 · 血缘归属）

Status: `current-reference`
Updated: 2026-07-15 strategy-search reset and Mac runtime reconciliation
Source of truth: 状态/结论以各 living doc 为准，本表只做汇总入口

这份是"我们一共研究过哪些策略"的单页总账。每条策略：**灵感/盈利规则 → 当前状态 → 是否可行 →
属于量化血缘哪一层**。状态/结论的权威来源是评估层 living docs（`docs/analysis/*.md`），本表汇总它们，
有冲突以 living doc 为准。

## 2026-07-15 当前总判断

- 没有一条策略达到 confirmed、可扩 live；运行中的真实下单实例也只能按 forward probe 评估。
- 主研究改为全量、连续 `P(outcome)-market` residual：先在同分母 PIT proper score 上打败 market，再谈 fee-adjusted trade selection。
- fast-source family 保留 collector/shadow；source/city 资格不等于 live，source print 需校准 settlement basis。
- HeadA/low-price YES 已切回 zero-notional shadow，旧 probability/score sizing 不恢复。
- 本表的状态是研究路由；当前进程、pause 和订单状态必须按 `WEATHER_STRATEGY_ENTRYPOINT.md` 动态盘点。

权威 reset：`docs/analysis/2026-07/2026-07-14-strategy-search-reset-v1.md`。

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
| weather_city_intraday_runtime_v1 / city_probability_runtime_v3 | **Weather City Intraday Runtime（WCIR）**；城市 adapter 负责 source clock、settlement lattice、特征和 artifact，公共 runtime 负责 typed capture、checkpoint/replay、candidate/intent 与后续公共执行血缘 | **`five-city framework / Helsinki+Tokyo model adapters / Amsterdam+Busan+Seoul coverage-only / zero orders`** | 策略族固定为 `weather.city_intraday_probability`。没有冻结 artifact 与 expression mapping 的城市只写 blocker，不伪造概率/candidate/intent；以后所有新城市必须登记 WCIR CaptureProfile+adapter，不得另建 collector、回放、order/fill/PnL 链 | [runtime design](WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md) · [five-city audit](analysis/2026-08/2026-08-02-wcir-five-city-onboarding-v1.md) |
| 普通单腿 YES/NO | forecast max + 历史误差 + 市场隐含，挑 mispriced bracket | `research` | baseline；裸 `model_p_yes - price` 不是确认 alpha | [1] model_vs_market |
| model×market 融合 overlay | `0.3*model + 0.7*market`，承认市场吃掉大部分公开天气信息 | `research` | 提升太小，未确认 alpha；global model alpha 为负 | [1] model_vs_market |
| forecast quality / reliability base | entropy/adjacent mass/city-model history 转可复用可靠性标签 | `shadow` | **只作共享可靠性层 / soft 标签**，非独立 live 策略 | [1] model_vs_market |
| d1_weather_only_probability_challenger | D-1 固定 checkpoint 上，以多模型 consensus 定位最终 Tmax，并用部分分层 city/source bias、pooled empirical residual 与稳健尾部输出 settlement-native exact-bracket 全分布；first_seen 只作 PIT 时钟 | **`legacy challenger frozen / clean exact-run forward pending / no market residual / no-live-change`** | 5561 条 long-history + 18-date reconstructed development 选择 `87.5% ensemble mean + 12.5% assigned + full shrunk bias + 1.25× residual scale + 2% climatology tail`；已查看的 9-date secondary holdout 上 logloss `1.9763→1.8506`、RPS `0.0903→0.0828`、top-1 `21.9%→26.8%`，但 logloss delta CI `[-0.2994,+0.0130]` 仍跨0且显著输 market `1.5497`。K=600 未校正，停止 legacy 调参并冻结到新 exact-run settled forward；不运行 market residual、不改 live | [1] forecast quality · [robust-tail report](analysis/2026-08/2026-08-05-d1-weather-only-robust-tail-v1.md) · [freeze spec](analysis/2026-08/2026-08-05-d1-weather-only-clean-forward-freeze-v1.json) |
| forecast-bounded Range RV | forecast 锁定档位区间内做相对价值 | `shadow` | 三统计门过、但 live-standard/forward 不过；零 notional shadow | [2] market_structure_edge |
| adjacent / range basket | 相邻档/区间篮子的相对定价 | `research` | inconclusive，holdout/top5 不稳 | [2] market_structure_edge |
| all-YES underround（no-arb 篮子） | 互斥档 YES ask 之和 <1 的结构篮子 | `research` | **2026-07-14 fee 勘误后 taker 表达显著为负**：固定旧 270 settled baskets，gross +3.16% 变 fee-adjusted -0.42%，CI[-0.68%,-0.19%]；maker/no-fee 仍有非原子全腿成交风险，只保留 research | [2] market_structure_edge · [fee correction](analysis/2026-07/2026-07-14-all-yes-underround-fee-correction-v1.md) |
| side-band / BUY_NO side alpha | BUY_NO 历史胜率高、特定价带方向偏好 | `research` | **胜率 ≠ alpha**；clean 测试三门不过，仅作特征/标签 | [2] side_alpha |
| low_price_yes_lottery_tiny_live_v1 | refined low-price BUY_YES longshot：`edge>=0.20`、ask `0.05..0.20`、`dist>0`、`hts_22_24`、每 city-date 一笔，fresh-book maker-first；固定 `5 shares` 只作 would-live sizing，旧 fixed-cash / price-tier / score-tier 继续 shadow telemetry；station-basis / p_cal / bracket-distance / book-state 同步记录 | **`shadow`（2026-07-15 起 zero-notional）** | live forward 命中率与 probability calibration 不支持继续真实 probe。相同 selector/execution evaluation 保留，首个 would-live signal 按 `signal_id` 去重写 `would_live_entries.jsonl`，持久化 fresh best ask/size、maker limit、shares、notional；不下单。切换时停止 live LaunchAgent，并撤销 2 张未成交 HeadA maker 单。历史 live 证据保留但不作为扩仓依据；market-anchored residual / overshoot-aware 继续独立 shadow | [1]-[4] pre_predict / forecast_quality · [selector refinement](analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md) · [HeadA refinement](analysis/2026-07/2026-07-04-low-price-yes-heada-refinement-v1.md) · [live audit](analysis/2026-07/2026-07-10-low-price-yes-heada-live-audit-v1.md) · [fresh thesis audit](analysis/2026-07/2026-07-14-heada-fresh-thesis-entry-audit-v1.md) |
| low_price_yes_take_profit_exit_v1 | V1 low-price YES 已有 position 的 TP20 退出 overlay：position 出现后预挂 `SELL YES @0.20` maker；若首次发现时 bid 已 `>=0.20`，先尝试 maker improve，maker cancel 后才 taker fallback | **`disabled`（2026-07-03 15:21Z 停用）** | 2026-07-03 早批准跑 20c 止盈版本；同日 sizing-stop v1 live-like replay 发现预挂固定 20c 与 max-bid 回放是不同执行产品（fixed-20c 全窗 -9.1% vs hold +25.9%），当日 `disable_tp20_exit_overlay` 撤掉全部 resting SELL、LaunchAgent 移除。2026-07-05 hot-only live 姿态重放进一步否决 recover-stake@30c：full delta vs hold -21.1%，CI [-31.6%,-11.4%]。当前 live 姿态回到 hold-to-settlement，TP/stop 仅 shadow would-trigger telemetry | [1]-[4] forecast_quality · [take-profit replay](analysis/2026-07/2026-07-03-low-price-yes-take-profit-v1.md) · [hot subset TP replay](analysis/2026-07/2026-07-05-low-price-yes-hot-subset-tp-replay-v1.md) |
| low_price_yes_integrated_tail_shadow_v2 | 在 V1 low-price YES 分母旁边记录 source-aware v3、station-basis/p_cal、实时 METAR/observed path、简化 regime score 和 expression-selector context，用 fresh forward 判断到底是 forecast-tail、station-basis 还是市场低估尾部 | **`shadow`（zero-notional）** | `shadow_candidate` 但不升 live：source-aware 历史最好，no-city integrated 分数 holdout 不稳，city-diagnostic p_cal 有 city/source memory 风险，METAR same-denominator 覆盖太低且不能改善。已在 Mac LaunchAgent `com.pm-agents.low-price-yes-integrated-tail-shadow` 跑独立 shadow journal；不改 V1 `$1` selector、不 size-up。**2026-07-03 加 `pcal_v2_*` tag**：修正 raw edge 非单调问题，train 校准 decile 单调、selected-CI>0，但 excess-vs-v1 CI 跨 0，acceptance 未全过，仍 diagnostic-only。**2026-07-04 加 `hot_tail_boundary_v1`/`bracket_dist_br_v1`/`book_state_v1` tag**（数据审计后预注册）：train 上 `dist≤0` 的"预报向下 bust"票 -10.6% vs hot 子集 +38.3% CI>0；hot 边际集中在 book missing/宽 spread 行而 feasible 行 ROI≈0，fresh forward（7/04 起）裁决 stale-quote 假边际 vs 注意力真错价 | [1]-[4] forecast_quality · [integrated tail v2](analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.md) · [pcal v2](analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.md) · [数据审计+边界 v1](analysis/2026-07/2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md) |
| heada_rain_convective_shadow_v1 | 原 HeadA `dist>0` exact-bracket YES 分母内，预注册 D-1 peak-window POP≥50% 的 rain/convective cohort；连续记录 multi-source、source/city bias、fresh book 与 target-day warming innovation，检验“市场把对流日分布定得过窄” | **`shadow_candidate`（zero-notional，2026-07-28 注册）** | 历史前/后半（天气 vintage 非 PIT）ROI +116.6%/+136.7%；最近 frozen PIT 19 tickets / 7 dates、4 wins、taker ROI +57.5%，ECMWF/GFS 都为正，但 PIT target-date CI 跨 0。原 integrated-tail runner 已具备所需 telemetry，selector 不改原 HeadA eligibility、不下单。冻结验收：新增≥30 PIT dates 且≥80 tickets，Brier/logloss 相对 fresh market 改善，fee ROI 与 uplift block-CI>0，fresh-book coverage≥90% | [1]-[4] forecast_quality · [rain/convective forward shadow](analysis/2026-07/2026-07-28-heada-rain-convective-forward-shadow-v1.md) · [market-anchored lifecycle](analysis/2026-07/2026-07-28-heada-market-anchored-lifecycle-v1.md) |
| station-basis（结算源 basis） | 官方结算站点 vs 市场所用站点的温差 basis | `shadow` | 当前主操作 shadow 线，`NOT_READY_ACCUMULATE_SHADOW`，有前向阻塞 | [0]-[2] 见 ENTRYPOINT |
| metar_reversal（intraday expression matrix / rich-current collapse） | 独立于 D-1 forecast-tail 的日内策略族：同一 city-date-hour snapshot 下比较 current_high_yes / current_bracket_no / d1-d2 yes-no / high_tail_yes 表达。当前主候选仍是 `rich_current_collapse_d1_yes` 形态，但只作为 zero-notional shadow：current YES 仍高价、obs 还在升温、forecast bracket-aware 落点至少高一格、peak ahead，观察 d1 YES；`false_fade_reheat_conflict` 是 sibling trigger，`heat_death` 只是 telemetry | **`research`（zero-notional shadow，未 live）** | 2026-07-04 Single Runs PIT backfill rejoin 后，B4 d1 YES 变为 70 rows/29 dates，hold ROI +30.8% 但 CI [-33.0%,+105.7%] 跨 0、top5-removed -17.2%、recent rows=0；`false_fade_reheat_conflict -> d1_yes` 47 rows ROI +61.0% 但 CI [-31.6%,+172.7%]。结论降为 `inconclusive_positive_signal_keep_shadow`：不并入低价彩票仓或 regime-routed NO runner，不 live | [1]-[4] reheat_risk · [family map](analysis/2026-07/2026-07-03-tail-strategy-family-map-v1.md) · [reversal shapes](analysis/2026-07/2026-07-03-hotter-tail-reversal-shapes-v1.md) · [strategy lineage](analysis/2026-07/2026-07-03-metar-reversal-strategy-lineage-v1.md) |

## 分支二：reheat_risk（[0] 事实 / [1]-[2] 模型与表达）

| 策略 / 家族 | 灵感 / 盈利规则 | 状态 | 是否可行（当前结论） | 血缘层 · 入口 doc |
|---|---|---|---|---|
| current_yes_fade_confirmed | 日内已回落后更稳健地买 current YES | **`historical tiny-live / current runtime verify`** | 2026-06 曾在 N100 tiny-live；2026-07-15 Mac 盘点未观察到对应 theta live process。保留策略与遥测，不从旧状态推断当前 live | [1]-[2] reheat_risk |
| current_yes_heat_death_physical_v1（H1 late-carry / H2 early dislocation 双头） | 13-17 本地时段 heat-death 确认（decline>=0.5、高点成熟 60m、path 不升温、forecast peak 已过）后买 current YES；d1 NO 只作 secondary diagnostic。按入场价拆两头：ask>=0.95 = late-carry premium，ask<=0.93 = Busan 型 early dislocation | **`shadow + live`（H1/H2 均为每次 5 taker + 5 maker-first：30s fresh-book chase，不限重挂次数，3m 后仅在 ask<=首次 ask 且 depth>=5 时 taker fallback；H1 [0.95,0.99]，H2 [0.50,0.93]；family city-day 去重。两头仍未确认；2026-07-18 H2 扩 maker 是用户授权 execution probe，不视为统计晋升。原单实例广州 `30 YES @0.89 x10` fill 保留原血缘，分析归 H2）** | 干净 peak-clock 重跑：H1 holdout strong 7 行/6 天 +5.8% CI[+1.2,+18.1]，严格 H1 paired 仅 5 行/5 天，两边全赢，current YES +1.70%、d1 NO +1.37%，仍 FAIL_LOW_SAMPLE；相对同价 base-fade 超额 CI 跨 0。H2 输入 7/07-15、ROI 取 7/07-14 八个结算日并剔除 Busan anchor：proxy paired 150 行，current YES +0.31%、d1 NO -1.31%；d1 多赢 2.67pp 但贵 4.24c，YES-d1 +1.63pp CI[+0.98,+2.37]，故 current YES 保持 primary。广州 7/15 在 14:17 PIT strong signal 显示 30 indicative 0.84，14:24 legacy probe @0.89 成交，用户确认获胜；Munich 7/15 H1/H2 重复造成额外 H2 `10 @0.93`，现已改为 family city-day 去重。H1/H2 maker lifecycle config 已注册 | [1]-[4] reheat_risk · [backtest](analysis/2026-07/2026-07-14-current-yes-heat-death-physical-backtest-v1.md) · [early replay](analysis/2026-07/2026-07-14-heat-death-early-event-replay-v1.md) · [shadow](analysis/2026-07/2026-07-14-current-yes-heat-death-physical-shadow-v1.md) · [晋升判据预注册](analysis/2026-07/2026-07-15-heat-death-live-promotion-preregistration-v1.md) |
| current_yes_peak_forming_micro | 当前仍在高位时买 current YES（微仓） | **`historical tiny-live / current runtime verify`** | 2026-06 曾作 micro live；当前是否运行只认进程参数与 raw order，不认旧白皮书标签 | [1]-[2] reheat_risk |
| metar_cross_prev_no / fast_source_prev_no_trial_v1 | 快源先看到跨档后买 previous exact bracket NO；alpha 假设是 source→settlement-facing observation 的时间差 | **`running probe / rejected-as-main-strategy`** | 2026-07-17 Atlanta terminal false cross 已成为该 family 的 canonical failure case：MADISHF/OMO 连续 `91.4F`，WU 最终 `89F`，旧 `88-89` bracket 未离开；direct MADIS 同观测 `temperatureQCR=0`，且 22 个正确 US runner candidates 无一在 10m 内可执行，唯一错误事件反而成交 `15 shares`（`10 @0.87` taker + `5 @0.86` maker），principal `$13.00`、verified fee `$0.05655`、realized loss `$13.05655`。以后所有 airport-fast/source-event/previous-NO 研究必须同时报 terminal false、source→WU basis 和 correct-vs-false executable/fill 分母；fill 必须回连 canonical fills，不能只读 submission-time raw。已有非美国源同口径重跑也发现 AMOS/MSS terminal overshoot，Helsinki/FMI 仅升为 P1 research feature-shadow，无新 live。**Busan 2026-07-20 再次证实同一 failure mode：AMOS `31.9/31.9/32.0C` 后买入 `15 shares 31 NO`，但下一份 METAR 和当时 WU max 仍为 31C；7/19 `feature_only_not_cross_trigger` 结论未同步到生产 live override/start 入口。**遗漏城市补审后，HKO/TelAviv/Ankara 只保留 shadow，Istanbul/Co-WIN/Shenzhen 不进入 exact-bracket live。历史用户授权的 `10 taker + 5 maker`、cap 15、`max_no_ask=0.97` 仅属 probe execution，不改变 shadow/rejected verdict | [0]-[4] latency_arb · [Busan 7/20](analysis/2026-07/2026-07-20-busan-31-no-live-lineage-v1.md) · [US basis](analysis/2026-07/2026-07-18-us-madishf-metar-wu-alignment-v1.md) · [US execution](analysis/2026-07/2026-07-18-us-madishf-execution-competition-v1.md) · [direct MADIS](analysis/2026-07/2026-07-19-us-direct-madis-wu-alignment-v1.md) · [active non-US](analysis/2026-07/2026-07-19-active-realtime-source-alignment-v1.md) · [remaining cities](analysis/2026-07/2026-07-19-remaining-realtime-source-city-audit-v1.md) · [denominator audit](analysis/2026-07/2026-07-14-source-event-denominator-audit-v2.md) · [share-cap incident](analysis/2026-07/2026-07-14-fast-source-share-cap-incident-v1.md) |
| hko_official_tminus1_no_live_v1 | 香港天文台 HKO 官方站已出现 floor(T) 后，只买最高温 `T-1 NO`；不消费 VHHH/METAR，不买 current YES | **`rejected_for_expression / current dedicated process not observed`** | HKO `latest 1-minute mean` 的 1m 是 averaging window，官方发布频率实际为 10m；本地 first-seen 约 8.1m。7/11-16 的 24 个 official locks 中，22 个拿到 fresh book，16 个 ask side 已空、6 个 ask 全为 0.999，0 order/0 fill，因此 official-lock 没有 entry edge。HKO 保留 settlement truth；Co-WIN 只作 feature-shadow，7/17 虽有 0.67→0.93/2m 窗口，但 HKO confirmation 仅 14/21 | [0]-[2] latency_arb · [HK speed](analysis/2026-07/2026-07-19-hk-source-market-speed-v1.md) · [HK readiness](analysis/2026-07/2026-07-11-tokyo-hk-fast-source-live-readiness-v1.md) · [remaining cities](analysis/2026-07/2026-07-19-remaining-realtime-source-city-audit-v1.md) · [share-cap incident](analysis/2026-07/2026-07-14-fast-source-share-cap-incident-v1.md) |
| post_cross_repricing / source_event_hazard_router_v1 | 快源/官方跨档后统一评估 `(T-1) NO` 锁定、`T NO` continuation、`T YES` exhaustion、`T+1 YES` exact-next 和 source-basis reversal；物理只预测相对盘口 residual | **`shadow`（zero-notional collector）** | 2026-07-13 统一回放中只有 cross current-NO continuation 保留正点估：edge2c 42 rows/14 dates ROI +11.6% CI[-11.4,+38.1]、recent +3.5%，仍未过 significance/baseline；cross current-YES -8.0%、T+1 YES -44.7%、60m no-break current YES recent -40.7%、full router -5.5%。独立 Mac collector 已按 auto target-date、fresh_scope=all、30s cadence、90m episode 启动；无真实订单，不改 live | [2]-[4] market_structure_edge · [living task](analysis/post_cross_repricing.md) · [unified replay](analysis/2026-07/2026-07-13-source-event-hazard-router-v1.md) |
| higher_no_carry | 买更高温档 NO（ladder carry） | `shadow`（telemetry only） | 没证明能稳定打赢同窗 current YES，仅 shadow 表达遥测 | [2] reheat_risk |
| tokyo_current_break_market_anchor_v6_v7 | Tokyo 每个 JMA 10-minute checkpoint 估计最终 exact maximum 是否停在 current bracket；v5 weather head 冻结于 `7/15` 前，v6 用 current-exact market logit 作固定 offset，只学习 weather/path correction；v7 审计 PIT 历史覆盖与训练 target-date 数量 | **`weather head frozen / market-offset development only / historical PIT insufficient / 8/1+ zero-notional forward`** | 原15日只对v5 weather head保持untouched；v6用其中前5个market dates训练、后7日OOF并选结构，不能称full-stack 15-day forward。`7/16`前只有`7/15`一日、4条archive+15m join、0 collector-exact，历史不足以重训后恢复该holdout。固定`7/27..29`同分母上，累计3/5/7/9训练日checkpoint Brier=`0.02046/0.01388/0.01245/0.01152`，market=`0.01946`；训练量点估有帮助但3日CI不足，state-entry仍输market。继续collector；达到20 exact train +10 untouched holdout后首轮升级评审，实际order/fill=0，不改live | [v6 market anchor](analysis/2026-07/2026-07-31-tokyo-market-anchor-binary-v6.md) · [v7 coverage + learning curve](analysis/2026-07/2026-07-31-tokyo-market-anchor-training-coverage-v7.md) |
| residual_high_price_no_shadow_v1 | late-window exact bracket 高价 BUY_NO 残值回收：15-18 点按每轮 `paper_snapshot` 枚举 d1/d2/d3 NO，成交价 95-99c、深度≥5，只看薄残值正确率 / tail miss / 每日小收益稳定性 | **`shadow`（zero-notional）** | 与 value 策略拆开：这是“高正确率、薄 residual”头，不用 `p_leg_win-price` 作为主入场 gate。历史高价 NO 约 98% hit 但 ROI 只有薄正点估，一次 tail miss 可吞多天收益；当前只由 `late_window_residual_split_runner_v1.py` 写 shadow journal，不 live | [1]-[4] reheat_risk · [heating done](analysis/2026-07/2026-07-07-late-window-residual-heating-done-v1.md) · [p_leg_win policy](analysis/2026-07/2026-07-07-late-window-residual-p-leg-win-policy-v1.md) |
| value_d1_no_tiny_probe_v1 | late-window d1 BUY_NO value head：用 `p_leg_win_physical_v1 - executable_cost` 找中价 d1 NO 错价，默认 5 shares plan；同时记录 residual exit-recovery shadow（当已有 d1 NO 后，按同 token NO bid 与最新 p_hold 比较是否可回收成本） | **`shadow loop / tiny-live probe candidate`** | 当前本机 tmux 只跑 `live_enabled=false` 的 plan/shadow，不真实下单。历史 clean forward d1 NO 为唯一较干净切片但仍样本少；若升 tiny-live，必须走执行确认与 CLOB/live gate。成都 2026-07-06 复盘：15:28 38 NO 属 value entry，39 NO 属 residual shadow；17:10 38 NO 属 residual/exit-recovery，不是新 value entry | [1]-[4] reheat_risk · [p_leg_win policy](analysis/2026-07/2026-07-07-late-window-residual-p-leg-win-policy-v1.md) |
| tmax_distribution_edge_shadow_v1 | 估计 `P(current/d1/d2/tail)`，再按 `P(win)-ask` 在 `current YES/current NO/d1 NO/d2 NO` 中选最高 edge 表达；selected 和 blocked 都写 zero-notional journal | **`shadow`（zero-notional）** | 新主线：把 regime 从买卖规则降级为概率模型特征。2026-07-04 PIT backfill rejoin + atlas/P5/P6 刷新后，verified clean edge02 为 163 selected rows ROI +9.9% CI [-3.0%,+22.8%]，city-source edge02 为 172 rows ROI +11.1% CI [-5.6%,+28.5%]；extension clean edge02 63 rows ROI +11.6% CI [-4.6%,+23.6%]。结论仍 `inconclusive_positive_signal`，只 shadow，不改 live、不 size-up | [1]-[4] reheat_risk · [strategy](WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md) |
| current_bracket_no_pass_through（含 climbing_no_peak_runway） | 买当前 running-max 档 **NO**，赌午后继续创新高把它打穿；分类器挑便宜 NO(ask 0.01–0.35) | `research`（zero-notional，不 live） | **2026-07-14 重测后降级**：旧 PIT 191 trades 加官方 fee 后 ROI +25.79%，但 CI[-0.07%,+50.75%]，绝对 significance FAIL；修正后的 24-date expression-specific replay 更显示物理 runway 反而是坏信号，post-hypothesis peak-runway BUY_NO 16 rows/9 dates ROI -68.6%。反向 BUY current-YES 点估 +10.0%、相对 baseline excess CI 为正，但绝对 CI 跨 0、样本太薄，只能作为新 shadow hypothesis | [1]-[2] reheat_risk · [fee correction](analysis/2026-07/2026-07-14-current-bracket-no-prevday-pit-fee-correction-v1.md) · [reversal replay](analysis/2026-07/2026-07-14-peak-runway-current-no-v1.md) |
| d1_yes_high_mid_v1（active `d1_yes_high_mid_live_v1`；historical `d1_yes_high_mid_shadow_v1`） | 全分母校准曲线发现的唯一可能穿 taker 摩擦格子：d1（rounded-running current 的紧邻上一档 bounded bracket）yes-mid≥0.80 时买 d1 YES；Taipei 保持 shadow | **`user-authorized tiny-live`（其他城市固定 5 shares；Taipei / `X+` / 语义异常 shadow）** | 研究 verdict 仍 inconclusive：all-rows 296 rows/48 dates fee ROI +2.18%，诚实分母 CI 跨 0；forward 15 dates +6.62% 但 train +0.73%。2026-07-16 用户显式授权拆分 live，不视作统计 promotion pass。runner 在下单前重读 YES token fresh CLOB，要求 mid≥0.80、top ask depth≥5；每天最多 10 笔/$50。已修复 93.92°F 被错误映射为同一 `94-95` current=d1 的 raw-distance bug | [1]-[2] market_structure_edge · [strategy doc](analysis/2026-07/2026-07-15-d1-yes-high-mid-strategy-v1.md) · [calibration curve](analysis/2026-07/2026-07-15-market-calibration-curve-v1.md) |
| broad_price_direction_v1 | 同一 exact bracket 连续小时 YES midpoint 移动至少 2c 后，分别验证 momentum 与 reversal；不加 city/source/weather/ask filter | `research` / **inconclusive_no_directional_alpha** | 宽分母：上涨买 YES 3,133 rows / 49 dates，fee ROI -2.9% CI[-5.0%,-1.0%]；下跌买 NO 632 rows / 48 dates，ROI -8.5% CI[-14.0%,-3.5%]；两条 reversal 同样为负。裸 price direction 不可用，天气 interaction 另见 feature review | [1]-[2] market_structure_edge · [broad replay](analysis/2026-07/2026-07-14-broad-price-reversal-v1.md) · [feature review](analysis/2026-07/2026-07-14-weather-climate-feature-review-v1.md) |
| regime_routed_no_route_price_disciplined_tiny_live_v1 | `route_price_disciplined + no_pullback + row_risk_soft`：历史 live-executable legs 为 fresh runway current-NO 与 capped d2-NO | **`shadow observed / historical tiny-live`** | 2026-07-15 Mac 仅观察到 regime-routed shadow LaunchAgent，未观察到对应 live process；历史 replay ROI -6.3% CI 跨 0，继续 shadow，不从旧配置恢复 live | [1]-[4] reheat_risk |
| low_price_yes_reheat_reversal | 需二次升温才命中的低价 YES，升级成 `forecast prior × reheat condition` | `research` / runner ready locally | 这是 reheat-risk 共享底座的反买头，不是独立彩票线；v1 holdout 有凸性但 CI/日期稳健性不过，zero-notional runner 已落地但 fresh observed/reheat feature 生产未接到当前日期，forward shadow blocked；下一步 trend-signal v2 验证 | [1]-[2] reheat_risk · [expression map](analysis/2026-06/2026-06-21-reheat-risk-yes-no-expression-map.md) |
| reheat 尾部机制特征补全 | 机制覆盖审计→尾部特征补全：高 ask EV 在尾部，补暖平流(风向)/云导数等机制，而非调阈值 | `research`（方向） | **不是“气象没用”：METAR core 对 survive 有真实判别力。A/B 后续(2026-06-20)：`market+METAR core(+alti)` 正则 logistic 点估最好(logloss 0.2978，dLL vs market -0.00646)，但日期 CI 跨 0，promotion FAIL；HGB/ML 在当前 27 日期窗口退化；`d_alti_3h` 有小的条件信号，但候选模型仍不过 raw market/base gate。** 当前动作是不加模型、不改 live；继续需扩样或只把 ML 当严格 challenger。 | [1] reheat_risk · [feature-gap v1](analysis/2026-06/2026-06-19-reheat-tail-mechanism-feature-gap-v1.md) · [proper-form tail features v1](analysis/2026-06/2026-06-20-current-yes-proper-form-tail-features-v1.md) · [residual+alti v1](analysis/2026-06/2026-06-20-current-yes-residual-calibrator-alti-v1.md) |

## 共享 / 基础设施层（L0-L2）

| 组件 | 层 | 作用 | 状态 | 备注 |
|---|---:|---|---|---|
| `weather_data_feed/` observation/source policy/calendar/bracket modules | L0 | 官方观测、source profile、城市日历、market bracket、snapshot 协议 | `current-source` | 新共享数据逻辑默认进这里；生产边界见 DATA_FEED_MODULE / REPO_BOUNDARY |
| `fact_signal_candidates` / `fact_trades` / orderbook snapshots | 主血缘 | 机会、成交、PnL、执行质量的 canonical fact 层 | `current-source` | 不随策略换代重做；live_real 发布前仍走 CLOB coverage gate |
| `reheat_feature_factory_v1`（实际身份 `temperature_state_feature_factory`） | L1 | 两分支共享事实物化：observed path + current YES/d1-d2 NO/target YES quotes + source-grain settlement + forecast peak context | 在用 | 路径仍在 `scripts/analysis/reheat_risk` 和 `docs/analysis/2026-06/generated`；迁 `runtime/weather_feature_store` 是未落地 Phase D |
| pass-through feature factory | L1 | current-bracket NO pass-through 专用平行 factory | `shadow_candidate` / 待收敛 | 不能直接删除；需与共享 factory 做同输入 parity 后再收敛 |
| observed_max 旧底表 | L1 | 早期日内最高温底表 | `dormant` | 当前主线暂不用，**未证伪，保留备用**；不归档不删 |
| `weather_data_feed.weather_context` | L2 | sky/moisture/warming/wind/peak-clock 机制标签 | `current-reference` | 研究和部分 live runner 已同源消费 |
| intraday regime atlas | L2 | 日内天气模式/动态机制图谱 | `current-reference` | 共享机制层；代码仍在 `scripts/analysis/reheat_risk`，不是策略私有语义 |
| `weather_data_feed.city_family` | L2 | 城市气候族群参考标签 | `current-reference` | 2026-07-05 收口为两套命名 taxonomy：`CURRENT_BRACKET_NO_V1` 与 `ATLAS_V1`，仅 `Beijing` 分叉 |
| `weather_data_feed.sky_cover` | L0-L2 | METAR/IEM sky-cover 字符串到数值特征映射 | `current-reference` | 2026-07-05 收口 5 个相同 `SKY_CODE` 定义；parser/fetch 逻辑尚未统一 |
| forecast quality base | L2 | forecast 可靠性 / source-aware soft 标签 | `shadow` | 只作共享可靠性层，非独立 live 策略 |
| station-basis labels | L0-L2 | 结算源 / 市场源 basis | `shadow` | 多策略共享 source/basis 特征；station-basis executor live placement 仍是未实现硬 gate |

## 执行 / 组合 / 城市层（[3] 选择 / [4] 执行）

| 家族 | 灵感 / 规则 | 状态 | 是否可行 | 血缘层 |
|---|---|---|---|---|
| entry_timing | target-date lead time / forecast checkpoint / decision window 限制 | `shadow` | 部分 timing 限制 shadow，未确认广义 live 自动化 | [3] entry_timing |
| sizing / entry band | 替代统一 0.25–0.75 的入场区间与仓位 | `design-draft` | 当前 live sizing/band 仍由 entrypoint/config 定义 | [3] sizing_entry_band |
| execution_quality | maker 扣 spread/queue/逆选后是否仍有可成交 edge | `research` | inconclusive | [4] execution_quality |
| weather_book_microstructure_atlas_v1 | 将所有 archived complete ladders 标准化为 `event × snapshot`，按城市、当地时间、as-of weather path 描述 spread/depth/favorite repricing，并为同 signal 的 maker/taker/skip A/B 提供执行状态层 | `research / descriptive atlas / no-live-change` | 2026-07-15..29 已有 47,508 states/17 target dates；中午 warming/plateau 确认更宽、更薄、repricing更快，但缺 market-wide trade prints、queue 与未成交分母，尚不能形成 execution policy | [4] · [atlas v1](analysis/2026-07/2026-07-30-weather-book-microstructure-atlas-v1.md) |
| five_city_weather_microstructure_v1 | Tokyo/Busan/Seoul/Amsterdam/Helsinki 按完整 ladder、当地时段和 first-seen 快源拆分 maker room、全 ladder repricing、quote-cross toxicity 与静态 underround | `research / descriptive execution routing / no-live-change` | 1,024 个 target-day 06–18 complete-ladder states；亚洲 10–14、欧洲 12–16 为主要重定价窗。maker 可见改善不等于正 EV；quote-cross 条件 markout 明显为负且无真实 fill 分母。Busan/Seoul AMOS half-degree 暴露 settlement-basis 风险，source-implied feasible strip 不能当套利。下一步同 signal 采真实 prints/order lifecycle | [4] · [five-city v1](analysis/2026-07/2026-07-30-five-city-weather-microstructure-v1.md) |
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
