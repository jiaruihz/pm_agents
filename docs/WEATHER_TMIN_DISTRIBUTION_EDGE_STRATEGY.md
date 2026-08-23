# Weather Daily Minimum Temperature Strategy

Status: current-reference
Updated: 2026-08-13 frozen cross-prev-NO policy deployed to zero-notional shadow
Source of truth: yes for this strategy family
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_STRATEGY_REGISTRY.md;
WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md

## 当前结论

这条研究家族的稳定 identity 是：

```text
framework_id = weather_city_intraday_runtime_v1
strategy_family = weather.city_intraday_probability
mechanism_id = daily_low_temperature_exact_bracket_v1
extreme_kind = min
```

当前状态：

```text
design = frozen_v1
collection_runtime = active central market_books Tmin full ladder for HongKong/Seoul/Tokyo
model_implementation = D-1 18:00 PIT panel + W0 weather residual development baseline
probability_artifact = none
forward = running for tmin_cross_prev_no_cap90_first_cityday_v1; probability model forward not_started
runtime = central full-ladder collection + cross-prev-NO zero-notional shadow; zero orders
promotion = no live
```

每日最低温值得单独研究，但不能把 Tmax 模型的符号反过来复用。对当地日历日
`00:00–23:59` 来说，最低温通常有两个仍可改写结算结果的冷却窗口：凌晨至日出后的
清晨窗口，以及日落后至午夜的晚间窗口。上午已经打印出的 running minimum 只是暂时下界；
当天晚间仍可能落入更低 exact bracket。

实现复用中央 `weather_market_books` 与共享 `weather_model_evaluation` CLI；不创建 Tmin 私有
collector，不创建订单，也不修改任何 live strategy 实例。

## 唯一可证伪假设

在同一 `city × target_date × checkpoint` 的完整 exact ladder 上，以 market full-ladder
概率为 prior，仅用 PIT 可见的 forecast revision、双冷却窗口状态、running-min 路径和
source→settlement basis 做强收缩 residual，能够在新的 target dates 上同时降低
date-equal multiclass logloss 与 RPS；如果不能稳定胜过同 rows market，这个家族就不进入
资金表达研究。

研究不是检验“最低温方向看起来有道理”，而是检验：

```text
P(final daily minimum exact bracket | PIT weather + market state)
  - P_market(same exact bracket at the same checkpoint)
```

是否包含可泛化增量。

## 产品语义与 target

最低温 market 是 exact-bracket market，不是 touch market：

- `X YES` 只有最终 daily minimum 正好落在 `X` 对应档位才赢。
- 早晨已经触到 `X` 并不锁定 `X YES`；如果晚间再降到 `X-1`，`X YES` 仍输。
- `target_date` 始终按 market rule 的城市本地日历归属，不按 UTC 日期或采集日期替代。
- raw source value/unit 先保留，再由 city settlement adapter 映射到 native lattice；不同城市的
  °C/°F、整数/小数、round/floor 和 tails 不得共用一条隐式规则。

模型需要三个彼此分开的 head：

| Head | Target | 用途 |
|---|---|---|
| settlement head | 最终 exact-bracket 完整分布，含上下 open tail | probability 主门与最终表达 |
| transition head | `30m/120m/to-dawn/EOD` 内再向下跨至少一档的累计概率 | 解释 cooling path，不直接充当结算 label |
| repricing head | first-seen 后 `30/120/300s` 的 rung-relative markout | 检验市场反应速度，不与 settlement alpha 混算 |

所有 bracket 概率必须在同一 ladder 上非负且和为 1；transition 的 horizon 概率必须 coherent。

## 双冷却窗口状态

状态表示使用连续特征，下面的名字只作诊断标签，不是 eligibility hard gate：

```text
pre_dawn_cooling
post_sunrise_provisional_low
daytime_warming
evening_reopening
late_evening_finalizing
```

每个 checkpoint 必须同时回答：

1. 当前 running minimum 是多少，多久前打印，最近 10/30/60 分钟下降速率如何；
2. 清晨窗口还剩多少，还是已经结束；
3. 晚间窗口是否尚未开始，forecast evening floor 是否可能打穿 morning minimum；
4. 当前 source 与 settlement source 的 basis、cadence 和 freshness 是否足够可信。

核心连续特征按机制分组：

- temperature path：current temperature、running minimum、time since strict new low、
  10/30/60m slope、morning-low 到当前温度的 rebound；
- cooling floor：dew point、dew-point depression、RH、wet-bulb/fog proxy；
- radiation/mixing：sunrise/sunset clock、cloud amount/change、precipitation、wind speed/direction、
  calm/mixing transition；
- forecast distribution：各小时 forecast low、最低点时钟、morning/evening 两个 local minima、
  source dispersion、first-seen forecast innovation；
- lineage/quality：source age、cadence、station identity、source→settlement native-tick basis、
  settlement profile 与 expression lattice identity。

缺失值显式记录。云、雨、风、价格带或某个 state 名称都不因少数坏案例被追加成 hard filter。

## 固定 PIT checkpoint 与分母

### Scheduled settlement panel

每个 city-day 固定记录下列本地/solar checkpoint；同一分钟冲突时以稳定 checkpoint priority
去重，不重复加权：

```text
D-1 18:00 local
D0 00:15 local
sunrise - 120m
sunrise
sunrise + 120m
sunset
D0 21:00 local
D0 23:00 local
```

这是 probability 主分母。评分单位是 `city × target_date × checkpoint`，但训练权重和 bootstrap
以 `target_date` 为 block，防止长轮询城市支配结果。

### Event panel

forecast revision 与 fast observation 是两个不同 information-event family：

- `forecast_revision_first_seen`：比较 revision 前后的 settlement distribution；
- `running_min_source_first_seen`：记录 pre、t0、+30、+120、+300s full ladder，并区分
  source cross、settlement confirmation 与 terminal false cross。

event rows 不混进 scheduled panel 冒充更多独立日期。它们先服务 transition/repricing 和
source-basis calibration；只有 event identity、四时钟和同刻 full ladder 完整时，才可进入
settlement residual 的 event-conditioned A/B。

## 模型与固定 A/B

主模型使用 market-offset residual，固定比较四个 arms：

```text
M0 = normalized same-checkpoint market full-ladder prior
W0 = weather-only hierarchical ordinal/hazard distribution
M1 = M0 + dual-window weather/path residual
M2 = M1 + source-to-settlement basis residual
```

实现约束：

- `M0` 从同刻完整 ladder 构造；单边 book 只给概率边界，不用 midpoint 或 `1-bid` 伪造可成交 ask。
- `W0` 用 city/source partial pooling 和 monotone cumulative thresholds，不能为每城复制 runner。
- `M1/M2` 在 market log-probability 上做强收缩 correction，再投影回 simplex；选择模型只看
  proper score，不看 selected-trade ROI。
- station/source basis 只使用 prior-date 训练；Seoul AMOS、Tokyo AMeDAS 等 alternate sensor 在
  basis 未校准前保持 `coverage-only`，不能生成交易 candidate。
- model feature book 与 execution book 分离；模型可以读 market prior，但成交成本必须重新用
  decision-time fresh depth 计算。

## Source、settlement 与 expression anchors

三个 anchor 必须同时留在 `DecisionContext`：

| Anchor | 含义 | 当前用途 |
|---|---|---|
| source anchor | HKO/JMA/AMOS 等实际 first-seen raw observation | 构造 path 与 source innovation |
| settlement anchor | market rule 指定的官方 daily minimum/station/date | 生成最终 label |
| expression anchor | Polymarket 真实 bracket title、token 与 tails | 构造 ladder、盘口与订单表达 |

首轮 contract census 的三个角色：

- Hong Kong：HKO `Absolute Daily Min` 可作为 official-source authority control；现有数据只是
  legacy development evidence，正式 forward 前仍需 WCIR adapter 与当前 full-ladder contract。
- Seoul：现有 AMOS runway 高频源是 alternate-sensor proxy，先校准到 settlement source。
- Tokyo：现有 JMA AMeDAS 高频源同样先做 source/settlement profile census 和 basis calibration。

其他 active lowest-temperature cities 不因为 market 名称相似就直接 pooled；逐城通过 rule、station、
calendar、unit、rounding、tails 和 book coverage census 后，再注册为 WCIR city adapter。

## Signal 与 evidence 双漏斗

### Signal funnel

```text
all registered city-days
→ fixed scheduled checkpoints / first-seen events
→ valid city calendar + settlement profile + complete expression ladder
→ ModelOutput on every valid row
→ net-EV expression candidates（只在 probability gate 之后启用）
→ first eligible city-day TradeIntent（未来阶段）
```

### Evidence funnel

```text
raw source / forecast deliveries
→ four-clock PIT reconstructable
→ same-checkpoint full-ladder book
→ settlement label aligned to native lattice
→ probability same-denominator rows
→ fresh executable depth
→ actual/realistic fill evidence
```

盘口、settlement、source-basis 或 depth 缺失只能记为 coverage gap，不能作为策略筛选结果。

## 2026-08-11 readiness card

本次只读 audit 以 production manifest、当前 Mac/JRS raw runtime、现有 min observer journal 和
market rules 为依据：

| 项目 | 状态 | 证据与 blocker |
|---|---|---|
| canonical/build identity | READY | manifest 的 `db_route.status=healthy`，canonical physical DB 与 repo 入口同 device/inode，findings 为空 |
| source event 四时钟 | PARTIAL | 当前 Seoul/Tokyo lowest observer 有 source observed/detected 与 quote clocks，但尚未物化为统一 WCIR panel |
| fresh full-ladder book | BLOCKED | 当前中心 `weather_market_books` 仍以 highest-temperature inventory 为主；min observer 的局部 quotes 不能替代单一 raw owner 的完整 event ladder |
| settlement profile/label | PARTIAL | HKO rule 可明确对齐；Seoul/Tokyo proxy basis 标为 `alignment_and_repricing_pending`，跨城市 rules 尚未完成 census |
| 独立日期 | BLOCKED | 当前 lowest journal 只有 16 个非连续 target dates；另有 5 个 legacy development dates，但引用退役消费路径，不能凑成 formal forward |
| clean frozen forward | BLOCKED | 没有冻结 probability artifact，也没有 30 个新 settled target dates |
| execution/WS evidence | BLOCKED | 当前是 `telemetry_only_no_orders`；没有 min-specific canonical WS reconstruction、真实 fills 或 queue evidence |

当前 raw snapshot（只说明 coverage，不代表 alpha）：截至 2026-08-11 09:51Z，Seoul/Tokyo
current lowest observer 累计 48 个 source events、3,371 个 quote snapshots，`orders_submitted=0`；
source calibration 明确为 pending。7/29–8/9 的日期断层按 coverage gap 保留，不能从剩余日期反推策略质量。

readiness verdict：`BLOCKED_FOR_FIT / KEEP_ZERO_NOTIONAL_COLLECTION`。

### Cross previous-NO forward shadow

`weather_tmin_cross_prev_no_shadow_v1` 把 Seoul AMOS 与 Tokyo JMA AMeDAS 的第一次向下跨档
映射为“观察刚离开的上一档（更暖 exact bracket）的 NO”。它只消费现有
`source_event_ladder_repricing_shadow/lowest_10m` event 与 fresh quote journal，不请求盘口、不读取
私有 collector，也不创建订单。

2026-08-11 启动时固定 raw denominator 为 49 个 first-cross events / 16 target dates；49 个都回连到
首个 quote checkpoint，29 个有 raw executable NO ask。全部写成 WCIR `ModelOutput +
SignalCandidate`。机制 candidate 状态为 `observed`，不把尚未冻结的概率冒充 score；每条保留
`cold_cross_margin_native`，但所有首次跨档均进入 signal funnel，不设 `.3/.4` hard gate。有 raw ask
的行标为 zero-notional `shadow_would_enter_at_raw_ask`，仍不创建正式 TradeIntent/order/fill。

2026-08-11 初版曾把这 49 条 previous-warmer-NO candidate 错绑到 `next_colder_no` model identity。
表达 bracket/side 与 quote 均正确，但 model/blocker family 错误；影响半径为 49 candidates、0 selected、
0 intent/order/fill。原 journal 保留为 superseded evidence，修正版改用
`tmin_prev_warmer_no_given_cross_pending_v0`。next-colder exact-NO 是另一条表达，不参与本 runtime 评分。

实例 identity 为 `weather_tmin_cross_prev_no_shadow_v1`。2026-08-13 起使用独立 production release
`tmin_cross_prev_no_shadow`，代码 SHA `d3a439356f8346dded3af051df3216b44af9406e`；共享
`fast_observation` release 保持原 pin，避免影响其他 collector/shadow。

2026-08-12 事件合同审计推翻了“51 条都是严格 first-cross”的分母解释。可用高频 raw 重放的
10 个 target dates 中，21 条 legacy candidate 只有 6 条能证明为 strict cross，15 条是
initial-state/非 strict，另有 5 个 raw strict cross 被旧链路漏掉；更早 7 个日期的 30 条缺对应
高频 raw，不能重分类。旧 51 条整体保留为 `legacy mixed semantics`，其下述 settlement/ROI/cap90
结果只作历史探索，不再支持 strict-cross alpha 或 forward promotion。主 journal 已在
`2026-08-12T02:46Z` 清零，并只接收显式 `source_transition_kind=strict_cross` 的新事件；盘口统一读取
中央 `market_books/latest.json`，事件落盘不再等待盘口。影响为 0 selected/intent/order/fill。
完整清单见 [event contract correction](analysis/2026-08/2026-08-12-tmin-cross-event-contract-correction-v1.md)。

以下为合同修复前的 legacy mixed-semantics snapshot：2026-08-12 settlement/quote 回连曾把 raw
snapshot 扩到 51 candidates / 17 target dates；8/12
两条 open event 保留为 unsettled，禁止用 near-binary `outcomePrices` 冒充结算。49 条 closed candidates
中 29 条有 direct NO ask，27 条同时有至少 5 shares top depth。全 27 条按 5 shares 和官方 fee 重放为
25 胜 2 负、PnL `+$0.1169`、ROI `+0.09%`，target-date bootstrap CI
`[-15.74%,+11.32%]`，没有可用 edge。开发窗口冻结的 challenger
`tmin_cross_prev_no_cap90_first_cityday_v1`（NO ask<=0.90、每 city-day 第一条 eligible、5 shares）为
5 条/5 dates、4 胜 1 负、ROI `+13.62%`，CI `[-49.24%,+88.32%]`。2026-08-13 已部署到现有
production runner 做 zero-notional frozen forward：所有 strict-cross candidates 继续进入全分母
`signal_candidates.jsonl`，合格行另写 append-only `shadow_decisions.jsonl`；每笔记录 5-share raw ask、
top ask depth、官方 fee 与 producer SHA，不创建 TradeIntent/order/fill，也不改变 live。

部署验收时 clean journal 为 1 个 pre-policy candidate、0 个合格 frozen-forward decision，目标进程
加载上述 SHA，summary 为 `policy_status=frozen_forward_shadow`、`orders_enabled=false`、0 intent/order/fill、
0 venue write；从部署后的新 strict-cross 开始累计 untouched forward。

2026-08-13 首夜审计发现共享高频 observation 主路由使用当地 `06:00–22:00` 窗口，导致 Tmin
shadow 的 `00:00–06:00` 核心降温段没有 same-source first-seen 证据；该 target date 因此标记为
coverage gap，不计入 frozen forward。修复没有扩大共享主路由或现有 Tmax live 的触发时段，而是在同一
`weather_live_cross_observations` producer 内增加隔离的全天 `tmin_seoul` / `tmin_tokyo` fast lanes，
lowest observer 只额外读取这两个 append-only shard。collector release 为
`f32d813da9f4874c4e3084afd225e918e7a46f59`，lowest consumer release 为
`331f309ebeea14fd6b61727584a8cf19b40dc6a0`。routine METAR 反事实显示缺口内 Seoul 有
`25→24`、`24→23`，Tokyo 在窗口边界有 `25→24`；它们不是 alternate-source 的可回填 forward
事件。对应 Tmin previous-NO 近邻盘口分别为无 ask、`0.99`、`0.982/0.993`，均不满足冻结的
`ask<=0.90`，所以影响半径为 3 个 proxy cross、0 个可确认合格 shadow decision、0 order/fill。

同一 denominator 的替代表达也已排查：三腿 `previous NO + current YES + next NO` 在 26 条完整
5-share coverage rows 上没有 fee-adjusted cost<1；两腿 `current YES + next NO` 的 15 条 under-1
settlement ROI 为 `+3.06%`、CI `[-37.80%,+64.78%]`。previous-NO 在 10m 后按真实 5-share bid taker
退出为 ROI `-6.55%`、CI `[-12.35%,-1.49%]`；30/60/120/240m 均无正证据。因此不把 basket 或
短时 repricing 另起 live/shadow；当前唯一新增动作是上述 cap90 zero-notional frozen-forward journal。

remaining-cooling `0/1/2/3+` hurdle head 在 98 OOF rows / 15 dates 的 proxy labels 上显著胜 clock，
且 structured exact-NO 胜 direct binary head；但加入同 rows market 的 expanding residual 在 68 rows /
10 dates 上仍输 market：logloss delta `+0.0267`、Brier delta `+0.0137`，CI 均跨 0。因此 depth 是保留的
physical feature，不是可交易 probability artifact。完整口径见
[2026-08-12 follow-up](analysis/2026-08/2026-08-12-tmin-depth-and-cross-prev-no-followup-v1.md)。

### 首轮实现与真实 denominator

中央 collector 已实现 `extreme_kind=max|min` 三元 event contract identity，避免同城同日 Tmax/Tmin
cache 冲突；Tmax selective WS 显式忽略 Tmin REST rows。首批 Tmin inventory 只登记
HongKong/Seoul/Tokyo，真实 Gamma discovery 在 2026-08-11 找到当天与次日各 1 个 event：共 6 个
Tmin events、每 event 11 rungs、132 YES/NO tokens，0 operational failures。运行时新增
`running_min_c / running_min_obs_utc / minutes_since_running_min / rebound_c`，并在截断 source
fallback 时保持 station-day running minimum 单调。

共享命令 `python -m weather_model_evaluation.cli daily-minimum` 已产出 D-1 18:00 local 的首个
model-ready panel，artifact 位于 production contract 登记的 research artifact store：

```text
daily_minimum_exact_bracket_v1/run_20260811_initial/
```

固定分母结果为 96 rows（3 城各 32 target dates）；proxy label coverage 为 Seoul 3 dates、Tokyo
24 dates、HongKong 0 dates。W0 empirical weather-residual expanding walk-forward 有 20 scored rows / 17
dates，proxy-label MAE `0.960°C`、multiclass actual-bin logloss `1.795`。这些数只验证 PIT panel 与
walk-forward 代码可运行：label 是 observation-cache intraday-min proxy，不是 settlement truth，且
当时中央 Tmin full ladder 为 0 dates，所以不能与 M0 同 rows 比较、不能冻结 artifact、不能据此
产生 candidate/intent。

生产 rollout 后首个 canonical batch（2026-08-11 11:21:16Z）已写入 6 个 complete Tmin events、
132/132 `weather_market_book` rows，全部 `status=ok`，cycle 11 秒；三城当天/次日各一套 11-rung
ladder。data-feed 首个新 observation batch 为 41/41 rows 写入 running-min path；Seoul 样例为
`running_min_c=26`、`rebound_c=2`。release identity 为 market-books
`35c9d65bf16f285dec45fa08ba05b2202045b4c6`、data-feed
`e3fa90be4bd372080be2b130dc5a99e3c02bd73c`。pre/post manifest sessions 无缺失、findings 为空，
DB/storage identity healthy；Tmin raw 只有 book rows、没有 order 字段，现有 source telemetry mode
仍为 `telemetry_only_no_orders`。

post-deploy readiness artifact 位于 `daily_minimum_exact_bracket_v1/run_20260811_post_deploy`；中央
full-ladder coverage 已从 0 更新为三城各 2 个 target dates（合计 6），仍远低于 30-date gate，且
settlement truth 仍为 0。

### Next-colder NO development baseline

`daily_low_temperature_next_colder_no_v1` 已作为同一家族的 research-only transition/expression head
接入共享 `weather_model_evaluation` CLI。它明确分开两个不能互换的 target：

- `no_next_colder_touch_to_eod`：从 checkpoint 起不再打印更低 native tick；
- `next_colder_exact_no`：最终 Tmin 不等于紧邻的下一更冷 exact rung。若最终一次跨两档，前者为 false，
  后者反而为 true，不能用 no-touch probability 直接给 exact NO 定价。

首轮固定 local 06/09/12/18/21/23 checkpoints，保留 morning/daytime/evening window，不按价格或结果筛行。
真实 development 分母为 594 fixed rows、152 PIT running-min rows、128 completed proxy-label rows；
expanding OOF 为 86 rows / 14 target dates（Tokyo 79 rows/14 dates、Seoul 7 rows/2 dates，HongKong 无
observation proxy）。physical no-touch head 相对 date-equal clock baseline 的 logloss
`0.2471 vs 0.5508`，paired delta `-0.3037`、95% CI `[-0.4835,-0.1465]`；但实际要交易的 exact-NO
head 仅为 `0.5664 vs 0.5821`，delta `-0.0157`、CI `[-0.0654,+0.0434]`，Brier 还退化
`+0.0030`、CI `[-0.0068,+0.0168]`。morning exact-NO 点估略差，evening 仅点估略好，均不构成 gate。

这些 label 是 observation-cache EOD minimum proxy，不是 settlement truth。中央 PIT Tmin books 只有
Seoul/Tokyo 各 1 date、4 checkpoint rows 带 fresh direct NO ask，且与 completed proxy label 的 same-row
交集为 0；因此 market baseline、fee、ROI 和 frozen artifact 均不可估。结论是保留 research、继续中央
collector，不创建 candidate/intent，不部署这个模型。可复跑证据见
`daily_minimum_next_colder_no/run_20260811_development_v2` 与
[development report](analysis/2026-08/2026-08-11-tmin-next-colder-no-development-v1.md)。

## Probability 与资金晋级门

### Gate A：历史/开发概率门

- chronological expanding OOF，只用 prior dates 训练；
- 同 rows 比较 M0/W0/M1/M2；
- primary：date-equal multiclass logloss 与 RPS；Brier、calibration slope/intercept、tail
  calibration 作 secondary；
- 候选相对 M0 的 logloss 和 RPS date-block bootstrap 95% CI 必须都 `< 0`；
- 报告 morning/evening window、city、source-basis、season 和 tail 切片，但切片不决定主结论。

### Gate B：frozen forward 概率门

模型、feature contract、checkpoint、city adapters 和 missing policy 全部 freeze 后，累计至少
30 个新的 settled `target_date`；期间不读 settlement label 调参。新窗口继续要求同 rows
logloss/RPS CI 均胜 market，且没有由单城、单季或单个 cooling window 独占改善。

### Gate C：资金表达门

只有 Gate A/B 都通过后才启用 zero-notional expression replay：

- 完整 ladder 的每个 YES/NO 都计算 `P(win) - fresh executable cost - official fee`；
- entry 使用 direct ask/VWAP 与目标 shares depth，不把 indicative midpoint 当成交；
- selector、edge threshold、每 city-day 一次和 hold-to-settlement policy 在 forward 前冻结；
- 至少 30 个新 target dates、足够 executable tickets，fee-adjusted excess-vs-market/基准 CI
  全正后，才讨论 tiny-live；maker、markout exit 和多腿篮子各自需要独立 execution evidence。

当前 `SignalCandidate`、`TradeIntent`、plan、order、fill 均为 `none`。

## 2026-08-24 no-further-cooling frozen-forward 复核

本节 supersedes 上述 2026-08-17 同期快照用于当前决策。只取当前 artifact `969bbbf…`，固定分母为
495 checkpoints / 12 target dates，91 rows 有 model、market 与 exact condition settlement，15 个
first-positive-edge city-date selected / 9 active dates，当前 artifact 内无重复。5-share
direct-ask+官方fee假设为 15胜0负、cost `$70.2541`、PnL `+$4.7459`、ROI `+6.76%`；但 Tokyo
8/21 `YES@0.49` 单笔贡献 52.4% PnL，任一 win 改为 loss 后组合 ROI 为 `-0.36%`。

更关键的是同 rows model-minus-market logloss/Brier delta 为 `+0.02779/+0.01555`，CI 均跨0，
模型点估输 market；forward 仅12/30 dates，且0 recorded depth、0 intent/order/fill。结论
`INCONCLUSIVE / keep zero-notional shadow / not live-ready`。

数据快照：Mac raw journal SHA `f852fa8c…`（501 total rows，2026-08-24 01:26 北京），canonical DB
`/Volumes/jrs/pm_agents/runtime/weather.db` device/inode `16777244/54444`，settlement 覆盖至 8/23。
本轮定向补抓 Shanghai 8/12、Seoul 8/17 Tmin event，append-only ingest 新增12个settlements；其中2个
condition补齐本评测5个checkpoint，scored settlement coverage从86/91到91/91，selected/PnL不变，
model-minus-market logloss delta从`+0.02488`变为`+0.02779`，结论不变。

signal/evidence 双漏斗：

| funnel | rows | dates | 说明 |
|---|---:|---:|---|
| artifact checkpoints | 495 | 12 | 8城；Miami/NYC coverage-only |
| model+market scored | 91 | 12 | 404为coverage/model/quote blockers |
| first positive edge selected | 15 | 9 active | 当前artifact内0重复city-date |
| PIT observation+forecast refs | 433 | 12 | 62 missing PIT observation |
| exact settlement / direct ask / recorded depth / fill | 91 / 15 / 0 / 0 | 12 / 9 / 0 / 0 | 只是假设direct-ask taker成本 |

同分母 proper score：market/model logloss=`0.30409/0.31310`，Brier=`0.10013/0.10869`；
date-equal model-minus-market logloss delta `+0.02779` CI `[-0.05414,+0.13478]`，Brier delta
`+0.01555` CI `[-0.01373,+0.05336]`。baseline gate FAIL。

入场归因（切片仅描述、未做多重检验、不得选 gate）：

| slice | entries | wins | fee-adjusted ROI |
|---|---:|---:|---:|
| local 06 / 09 / 12 / 18 / 21 | 4 / 5 / 1 / 4 / 1 | 全胜 | +6.61% / +1.53% / +7.15% / +1.57% / +99.01% |
| Tokyo / Seoul / London / Paris | 7 / 6 / 1 / 1 | 全胜 | +12.29% / +2.99% / +0.77% / +0.10% |
| ask `<=.60` / `(.60,.95]` / `>.98` | 1 / 7 / 7 | 全胜 | +99.01% / +6.22% / +0.60% |

北京时间 entries 为05点4、08点3、11点1、15点1、16点1、17点4、20点1。Tokyo 8/21 当地21点
`YES@0.49` 单笔贡献总PnL 52.4%，所以不能解释为21点或Tokyo已形成稳定edge。15/15胜率的Wilson
95%下界仅79.61%；去最大赢家ROI仍`+3.33%`，但任一binary win改成loss会令5-share组合从
`+$4.7459`变`-$0.2541`。当前entry是连续`p_model-ask-fee>0`，不是`.7/.6`阈值；不从本slice新增
价格gate。

三门：significance 仅为all-win退化样本下的provisional pass；same-denominator baseline FAIL；
forward FAIL（12/30 dates，且无depth/fill）。8环中描述性、date-block统计、概率、日期相关性与market
基准已覆盖；signal discrimination仅间接覆盖，执行微结构、capacity、actual fill缺失。机器结果唯一格式：
`/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_no_further_cooling_shadow_performance_v1/2026-08-24/summary.json`；
复跑器 `scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py`。

## 血缘与落地边界

正式实现必须复用 WCIR：

```text
EventEnvelope
→ DecisionContext(extreme_kind=min, dual-window state, three anchors)
→ ModelOutput(full exact-ladder probabilities + blockers)
→ SignalCandidate
→ TradeIntent
→ shared plan/order/fill/settlement
```

设计不允许新增 city-private collector、回放时钟或 PnL 旁路。最低温 market discovery 与 book
采集必须进入唯一 `weather_market_books` raw owner；消费层只读并 join。coverage-only adapter 在
缺 frozen model、settlement profile、basis 或完整 ladder 时输出结构化 blocker，不伪造 probability、
candidate 或 intent。

需要进入 `fact_signal_candidates` 的最小字段包括：

```text
framework_id / strategy_family / mechanism_id / run_id
city / target_date / checkpoint_id / event_family / four clocks
extreme_kind=min / source_profile_id / settlement_profile_id / bracket_lattice_id
running_min_native / morning_min_native / forecast_evening_floor_native
cooling_window_state / source_basis_class / model_probabilities / market_probabilities
eligibility_status / blocker_reason / feature_ref / model_ref / book_ref
```

## 唯一下一动作

让中央 `weather_market_books` 连续采集三城 Tmin full ladder，并把新增 Tmin rows 与现有 forecast、
official/proxy observation、settlement profile join 成固定 checkpoint WCIR coverage rows。累计至少
30 个新的 settled target dates 且 settlement truth 完整后，才在同 rows 比较 M0/W0；完成前不拟合
M1/M2、不跑 ROI、不创建 strategy instance 或 candidate/intent。
