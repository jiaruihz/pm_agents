# Weather Daily Minimum Temperature Strategy

Status: current-reference
Updated: 2026-08-27 next-alpha challenger freeze; neither strategy is live-ready
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
forward = incumbent shadows running; two prospective challengers freeze from target_date 2026-08-28
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

### 2026-08-17 首轮 frozen-forward 官方结算与口径修正

cap90 frozen forward 首轮官方结算（canonical `settlements`，Tmin 走 source_system=`polymarket_api`）：

| decision | entry | settle | 结果 | PnL(5sh,含官方fee公式) |
|---|---|---|---|---|
| Tokyo 8/14 NO@24 | ask 0.47 | 23 | win | `+$2.5877` |
| Seoul 8/16 NO@24 | ask 0.85 | 24 | loss | `-$4.2819` |
| Seoul 8/17 NO@25 | ask 0.86 | 24 | win | `+$0.6699` |

合计 `-$1.0243` / 投入 `$11.0242` = `-9.29%`，2胜1负；n=3 维持 `INCONCLUSIVE`，不改 30-settled-date gate。

**Seoul source→settlement basis 反例（blocker 级证据）**：8/16、8/17 两夜 RKSI
`amos_runway` 10-min `temp_c` 最低均为 `23.4`，但 "Seoul (Incheon)" 官方结算均为 `24`；
同窗 `metar_temp_c` 恰为 `24.0`。即 Seoul 结算基准对齐 METAR/climate 口径，10-min lane 的
strict cross 可能是 settlement-false：8/16 跨入 23 触发 NO@24，结算留在 24 直接亏损。
Tokyo 三夜 lane 与结算一致（`23.1→23`、`23.6→24`、`23.3→23`）。观测 lane 永远只作 proxy；
shadow 结算 join 必须走 canonical `settlements` 的 condition_id/market_id，不得用
(city, target_date, bracket)——Tmax/Tmin 同档同域会串。

**cap 0.90→0.95 敏感性**：14 个候选的 NO ask 分布两极（`<=0.86` 或 `>=0.96`），无任何一笔落在
`(0.90,0.95]`，选择集与结果完全不变。当前真正约束是 5/14 候选无 raw executable ask、至少一笔
（Seoul 8/15 NO@25 @0.82）倒在 depth gate；放宽上限不增加样本，`>=0.96` 的 favorite fee 后
payout 每股 `<=0.038`，无意义。

**Tmin settlement 入库路径（新增）**：`scripts/ops/backfill_weather_pm_history.py --extreme min`
拉 Gamma lowest-temperature 事件到 `cache/pm_history_lowest/`（bracket 内嵌 condition/market id），
`scripts/ops/ingest_weather_lowest_settlements.py` 写入 `settlement_outcomes`
（source_system=`polymarket_api`、id 带 `|lowest` 后缀避免与 Tmax 撞 id）与 `settlements`。
未翻 `closed` 的市场 fail-closed 跳过（如 Seoul 8/17 b24 `0.9995` 未关闭，待重跑）。
Mac 直连 gamma-api 为 DNS 污染，需 `--proxy http://127.0.0.1:7890`。

**no-further-cooling 同期官方结算**：11 行/10 city-date 全胜，费前 `+~$1.97 / $53.58 ≈ +3.7%`，
全部 0.92–0.998 favorite；单笔 miss 需约 20 胜回本，维持 coverage/research 定位。另发现 journal
口径问题：Seoul 8/12 同 city-day 出现两条 `shadow_would_enter=true`（once-per-city-date 未在
journal 层生效），按行累计会重复计数，待修。

**shadow 盘点 recipe**（本轮流程绕路的教训）：实例 journal 路径从
`src/strategies/runtime/production.yaml` 各 instance 的 `health_path` 同目录取
（`shadow_decisions.jsonl`/`signal_candidates.jsonl`/`decision_bundles.jsonl` 与
`latest_summary.json` 的 funnel 字段）；结算 join 用 condition_id/market_id 查 canonical
`settlements`；settlement 链当前无 Mac 侧 owner——`pm_history` 缓存 8/12 起停更（原 owner 为
N100 daily_pipeline），2026-08-17 已手动补 8/12–8/17，长期需把 backfill+ingest 并入 canonical
refresh one-shot（待审批，不另建 scheduler）。

**2026-08-18..20 更新（含触发条件反事实的样本外修正）**：新增 8 个 strict cross 与第 4 笔
decision（Seoul 8/19 NO@26@0.53 → settle 25 → win `+$2.2877`）；累计 4 decisions 3胜1负、
PnL `+$1.2634`/`$13.6865`=`+9.23%`。Seoul lane 假突破扩为连续三晚（8/16–8/18 lane 均只探到
`23.4`、metar 全夜 `24.0`、结算均为 24）；metar 夜最低连续五晚与结算完全一致
（24/24/24/25/25），证实 Seoul 结算基准=METAR/climate 口径、10-min lane 夜间系统性偏冷
~0.6°C。触发合同反事实：margin/连续确认/触发时刻 metar 确认均不能区分真假——8/16–8/18 的
假突破与 8/19 的真突破同为"贴边 0.1、仅 2 条 print"的浅穿透（8/19 cross print 恰为 26.4），
任何深度/持续性规则在挡掉三晚假突破的同时也会挡掉 8/19 的最大赢单；"lane 继续下探与否"
本质是前瞻概率问题（即 BLOCKED_FOR_FIT 的 next-colder/pre-cross head），不是过滤器缺陷。
结论维持：不改 frozen policy；研究方向为 basis 校准的概率模型，标签与回放一律用
metar/settlement 口径。

**2026-08-20 METAR-basis 历史重放（机制定标，signal-level）**：两市市场结算源均为 WU Daily
Observations（RKSI/RJTT）；**IEM METAR 档案 18/18 夜复现结算标签**，标签可历史回补（canonical
`settlements` 已入 4/15–8/20 共 2,885 条 Tmin 行）。lane 内 `metar_temp_c` 字段与官方 METAR 档案
存在不一致（8/15 反例），不得再当标签源。重放核心数字（393 cross / 352 标注）：**NO-loss 基础比率
Seoul 19.4% CI[14.7,24.3]、Tokyo 21.1% CI[16.2,26.3]**；按时段强结构：00–03 点 12%/6% vs
03–06 点 27%/34% vs 傍晚 28%/31% → flat cap90 在凌晨段按基础比率为负 EV（保本 ask≈0.66–0.73），
深夜段保本 ask≈0.88–0.94；Seoul 8/16 的 lane 假突破输单在 METAR 基准下不存在该信号。完整口径与
限制见 [METAR-basis replay](analysis/2026-08/2026-08-20-tmin-cross-metar-basis-replay-v1.md) ·
artifact `runtime/research/tmin_cross_metar_basis_replay_v1/`。

**2026-08-24 frozen-forward 与 canonical 更新**：补抓并增量入库 Seoul/Tokyo 8/19–24 Tmin
settlement 后，第 5 笔 decision 为 Seoul 8/22 `NO@25 @0.74`，结算24，费后 `+$1.2519`。
累计 5 decisions / 5 target dates、4胜1负、cost `$17.4846`、PnL `+$2.5154`、ROI `+14.39%`，
按 target_date bootstrap 95% CI `[-49.72%, +78.61%]`。Tokyo 仅1笔贡献 `+$2.5877`；Seoul
4笔合计 `-$0.0724`、ROI `-0.48%`，不能把总正收益解释为跨城市稳定 alpha。全 signal funnel 为
33 strict-cross candidates / 11 dates（Seoul 19、Tokyo 14），22有 direct entry price，30已结算；
frozen policy 只产生5条 zero-notional decision，仍为0 intent/order/fill。此前 canonical 只物化1条，
本轮用共享 WCIR 增量入口补32条，raw/canonical=`33/33`、delta=0，二次 apply inserted=0。
三门均未过：significance FAIL、无 frozen probability-vs-market baseline、forward 仅5/30 dates；
结论维持 `INCONCLUSIVE / zero-notional shadow / not live-ready`。机器结果见
`tmin_cross_prev_no_forward/run=20260824_0037_bj/summary.json`。


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
模型点估输 market；forward 仅12/30 dates，且0 intent/order/fill。15个 selected 的 raw PIT full book
均可由 candidate input ref + condition_id 精确还原，15/15 在快照时可用best ask无滑点扫5股；结论仍为
`INCONCLUSIVE / keep zero-notional shadow / not live-ready`。

数据快照：Mac raw journal SHA `f852fa8c…`（501 total rows，2026-08-24 01:26 北京），canonical DB
`/Volumes/jrs/pm_agents/runtime/weather.db` device/inode `16777244/54444`，settlement 覆盖至 8/23。
本轮定向补抓 Shanghai 8/12、Seoul 8/17 Tmin event，append-only ingest 新增12个settlements；其中2个
condition补齐本评测5个checkpoint，scored settlement coverage从86/91到91/91，selected/PnL不变，
model-minus-market logloss delta从`+0.02488`变为`+0.02779`，结论不变。

执行证据勘误：初版 evaluator 只检查 candidate 是否内嵌 depth，误报 `0 recorded depth`。中央
`market_books` 实际保存了完整 bids/asks、`book_capture_id`、best-ask size 与5c/10c depth；按原始
SHA/byte boundary 重放后，15/15 selected 都有可重建 full book，best-ask size 最小5、median 50，
book age 最大4.73分钟，5-share VWAP均等于 candidate ask。历史数据没有丢失；但这是静态PIT快照，
zero-notional仍没有post-decision execution book或actual fill，不能声称真实成交。另一个仍需修的
lineage gap 是 candidate 当前把 batch-level hash 写入 `execution_book_snapshot_id`，而不是原始
`book_capture_id`；虽可由 input ref + condition_id 确定性还原，但在升 live 前应直接持久化精确book identity。

signal/evidence 双漏斗：

| funnel | rows | dates | 说明 |
|---|---:|---:|---|
| artifact checkpoints | 495 | 12 | 8城；Miami/NYC coverage-only |
| model+market scored | 91 | 12 | 404为coverage/model/quote blockers |
| first positive edge selected | 15 | 9 active | 当前artifact内0重复city-date |
| PIT observation+forecast refs | 433 | 12 | 62 missing PIT observation |
| exact settlement / direct ask / reconstructed depth / fill | 91 / 15 / 15 / 0 | 12 / 9 / 9 / 0 | 15/15静态快照可扫5股；无actual fill |

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
forward FAIL（12/30 dates）。静态PIT 5-share depth为15/15 PASS，但actual fill/post-decision execution
仍缺失。8环中描述性、date-block统计、概率、日期相关性与market基准已覆盖；signal discrimination仅
间接覆盖，执行微结构/capacity有静态快照证据，actual fill缺失。机器结果唯一格式：
`/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_no_further_cooling_shadow_performance_v1/2026-08-24/summary.json`；
复跑器 `scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py`。

### 2026-08-25 frozen-forward 更新

定向补齐并入库 Seoul/Tokyo 8/24 Tmin settlement 后，当前 raw artifact 为587 checkpoints / 14
observed target dates，其中13日已有scored settlement；104 scored、98 same-row settled scores，18个
policy selected 中17个已结算、分布于10个active dates，8/25 Tokyo `YES@0.94` 仍open。已结算17笔
全部win；按5-share full-book VWAP+官方fee，cost `$80.1923`、PnL `+$4.8077`、ROI `+6.00%`，
target-date bootstrap CI `[+2.03%,+16.11%]`。17/17都有静态PIT full-book depth并可在best ask扫5股。

这支持“当前expression很有希望”，但还不能升级为confirmed alpha：Tokyo 8/21 `YES@0.49` 仍贡献
51.7%总PnL；任一额外binary loss会把组合变为 `-$0.1923/-0.24%`。selected-only诊断上 model/market
logloss=`0.0572/0.0770`，model选出的tail目前确实更准；但完整98-row同分母上model仍点估输market，
logloss/Brier delta=`+0.02628/+0.01505`，两项CI跨0。forward只有13/30 settled dates，且无actual
fill/post-decision execution。结论维持 `INCONCLUSIVE / promising zero-notional shadow / not live-ready`。

运行态另有独立数据连续性问题：`weather_forecast_curve_collector_v1` tmux缺失且health陈旧约10.3小时，
使本runner被controller标为dependency critical；已产出的历史结果不受影响，但恢复前新增checkpoint可能缺
forecast evidence。机器结果更新为
`/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_no_further_cooling_shadow_performance_v1/2026-08-25/summary.json`。

## 2026-08-27 双策略 tiny-live readiness 复核

### 结论与数据边界

本轮只检验两个可运行的 zero-notional policy：
`weather.tmin.no_further_cooling` 与 `weather.tmin.cross_prev_no`。结论是 **两者均不支持 tiny live**；
no-further-cooling 是更值得优先继续积累的 challenger，但仍未过同分母 market baseline、30 个新
settled target dates 和真实执行证据三门。cross-prev-NO 继续作为机制/collector shadow，不应把
flat `ask<=0.90` 的小样本结果解释为可交易 probability edge。

分析冻结到 2026-08-27 当前 raw byte boundary：no-further candidate input SHA
`c1e7619189b1f6c7657cc50f8d8ac7556a9d31758a3cab8d3826fa85f92469d6`，cross candidate SHA
`cd99d5b15196bd10911c2d315b1122afbb99b9b8d4c93d30446c56773ffa08e2`，cross repricing quote SHA
`13f4e52e81dcfe9d67aca25a61edc49ad72d1080a578ae67e77eb5f58f0a05d9`。canonical physical DB
identity 为 `/Volumes/jrs/pm_agents/runtime/weather.db`、device/inode `16777244/54444`；为避免生产
write，本轮把 8 城 8/25–26 共16个 closed Gamma events（176 rungs）只增量写入临时 evaluation DB，
没有改 canonical。严格 production/controller health 快照仍为 critical，包含 JRS canonical tmux
write probe/session health 问题；因此本轮不恢复进程、不部署、不改变 live behavior。

### 固定分母与三门

| policy | signal / evidence funnel | fee-adjusted 5-share replay | probability / baseline | forward / execution | tiny live |
|---|---|---|---|---|---|
| no-further-cooling | 655 checkpoints / 16 observed dates；115 scored，111 same-row settled / 15 dates；20 selected 中19 settled / 12 active dates | 19胜0负；cost `$89.8589`，PnL `+$5.1411`，ROI `+5.72%`，date CI `[+2.14%,+14.06%]` | model/market logloss `0.26960/0.26367`、Brier `0.09188/0.08450`；model-minus-market delta `+0.02046/+0.01280`，CI均跨0，baseline FAIL | 15/30 settled dates；19/19静态快照可扫5股，但0 fill、无post-decision book，精确`book_capture_id`未直接持久化 | **NO** |
| cross-prev-NO | 40 strict candidates / 14 dates；36 closed labels，26 direct asks；严格校验 underlying fetch age 后23条有fresh ask+>=5股depth、21条settled+executable / 10 dates | 全可执行分母18胜3负；cost `$94.8245`，PnL `-$4.8245`，ROI `-5.09%`，CI `[-15.97%,+7.34%]`。冻结cap90首笔policy修正为4决策、3胜1负、ROI `+1.54%`，CI `[-69.87%,+72.91%]` | 无 frozen settlement probability model，无法做same-row model-vs-market baseline | 仅4个有效decision dates，0 fill；另有独立60m repricing challenger从8/28开始 | **NO** |

no-further 的正交易回放不是 admission 充分条件：Tokyo 8/21 `YES@0.49` 单笔贡献48.4%总PnL；
去掉最大赢家仍为 `+3.04%`，但额外1个binary loss后只剩 `+0.16%`，2个loss即为 `-5.41%`。
更关键的是完整同分母 proper score 点估仍输 market，而不是只看 selected rows 的19/19。cross 的
全可执行表达已经点负；10分钟 all-entry repricing ROI `-13.89%`、CI上界仅`+3.75%`。first-city-day
120/240分钟 point-positive 约`+5.35%`虽有正CI下界，但属于开发结果，不能当作已验证alpha；本轮后续
只允许从新的冻结边界验证一个固定60m exit，不再从旧样本继续选horizon。多腿篮子同样样本太小或无
三腿under-1，不进入 shadow promotion。

### 改进方向与实践顺序

1. **no-further 先修“概率选择”而不是加价格 gate。** 在全部相同 checkpoint/label/quote rows 上冻结
   `market selector` 与当前 model selector 的 paired A/B，primary 仍为 logloss/Brier（完整分布可再报
   RPS），trade层报官方fee后的paired excess；继续收集到至少30个**冻结后新增**settled target dates。
   保留 top-winner-removed、1-loss、2-loss stress，不能按当前全胜切片调阈值。
2. **cross 把 flat cap90 替换成可校准的 physical probability challenger。** 预注册连续特征：
   city、local-time、cross rank、source→settlement basis、remaining-cooling depth、forecast revision；
   先在全 strict-cross denominator 上胜同刻 market，再冻结 expression。历史 METAR-basis 的时段差异
   只可作 prior，不能事后加 hard time filter；cap grid、basket 和120/240m exit 均保留 exploratory。
3. **补齐 execution lineage。** 两条策略都直接持久化 raw `book_capture_id`，并在决策后固定采集
   `t0/+15/+30/+60s` 5-share sweep/depth/markout；先做 zero-notional submit-time revalidation，建立
   would-submit→executable→fill denominator。静态t0 depth不能替代 latency、queue 或actual fill。
4. **评估工具先补合同再做下一次 admission review。** no-further evaluator缺专门回归测试；需覆盖
   condition-id settlement join、byte-boundary freeze、city-date dedupe、loss stress和book identity。
   cross evaluator当前把运行中的冻结policy标成`forward_not_started`且仍依赖raw Gamma slug join；应改为
   condition-id canonical join并准确区分historical frozen decisions与freeze后新增forward decisions。

只有 probability-vs-market、30-date frozen forward、fee/execution 三门同时通过后，才单独申请生产
变更；届时初始实践应固定5 shares、每city-date最多一笔、submit前重抓book并重算fee后edge，不同时引入
maker、basket或动态sizing。当前动作仍是 `keep zero-notional shadow / no live change`。

Output routing：本轮结论合并进本 living doc 与 registry/index，不新增重复 dated snapshot；机器结果
保持单一 JSON evaluator 输出，临时 settlement DB 不作为新的 canonical truth。

## 2026-08-27 下一波 alpha challenger freeze

### 裁决

下一波不是继续给两个 incumbent 调价格阈值，而是冻结两个目标更清楚的 challenger：

| family | frozen challenger | 开发证据 | frozen 资格 | forward |
|---|---|---|---|---|
| no-further-cooling | `tmin_no_further_cooling_window_routed_alpha010_v1` | 111 same-row settled scores / 15 dates；相对market的date-equal logloss/Brier delta `-0.004136/-0.000506`，前后两个固定时间段均点改善 | **eligible for prospective zero-notional freeze**；CI仍跨0，不是confirmed alpha | `target_date>=2026-08-28`，当前0行 / not started |
| cross-prev-NO | `tmin_cross_prev_no_first_cityday_exit60m_v1` | 15 first-city-date entries / 11 dates；双边官方fee后PnL `+$3.8005`、ROI `+5.35%`、date CI `[+1.27%,+13.20%]`；15/15 fresh exit coverage | **eligible for prospective zero-notional freeze**；旧数据用于选型，不能回算成forward | `target_date>=2026-08-28`，当前0行 / not started |

两者的 `eligible` 只表示“规则、分母、边界和主指标已经足够清楚，可以开始一次不可回调的零资金
forward”。它不表示通过 tiny-live admission；生产实例、订单开关和live行为均未改变。

### no-further：把物理残差只留在仍由冷却主导的窗口

当前 incumbent 用 `alpha=0.50` 在所有窗口向 market logit 叠加 physical innovation，完整同分母的
proper score 反而输 market。新 challenger 固定为：

```text
p_challenger = logistic(
  logit(p_market)
  + 0.10 * physical_innovation_logit
    * I(cooling_window_state in {morning_cooling, post_sunrise_provisional_low})
)
```

`daytime_warming / evening_reopening / late_evening_finalizing` 三段严格回到同刻 market probability；
不是把它们删出分母。开发时只比较了 `alpha={0.10,0.25,0.50}` 三个新 routed variants，并披露
incumbent 训练时原有5档 alpha grid；不把未校正搜索结果称为显著。

在固定111 rows / 15 dates上，challenger/market 的 row-level logloss 为 `0.260064/0.263675`，Brier
为 `0.084069/0.084502`；date-equal delta 的95% CI分别为 `[-0.010126,+0.001412]` 与
`[-0.002484,+0.001475]`。相对 incumbent 的date-equal logloss/Brier delta为
`-0.024601/-0.013301`，但CI同样跨0。固定前段8/12–20与后段8/21–26均同时点胜market，说明方向
比全窗alpha=.50更合理；强度仍不足以升live。按5-share full-book重放只有2笔/2日全胜、ROI `+6.61%`，
样本太小，仅作执行可还原性检查，不作为freeze主因。

forward 主指标锁为全部same-row scored checkpoints上的 challenger-minus-market date-equal logloss与
Brier；交易层只作secondary。至少30个冻结后新增settled target dates、两项CI上界均小于0、且fee/execution
门同时过，才允许重新讨论资金表达。

### cross：放弃 settlement-hold 拟合，改测可证伪的60分钟市场反应

先做的 settlement probability challenger 没有过门：用历史METAR prior与当前alternate-source basis
更新后，在当前36个closed labels上 logloss/Brier=`0.38239/0.11846`，同rows market为
`0.27349/0.09247`；正edge只选3笔/3日、2胜1负。它不能冻结，也说明 AMOS/JMA 的cross触发不应
直接借用METAR的hold-to-settlement胜率。

可冻结的是另一个 target：source event 后的短时 repricing。规则固定为同一 city-target_date 第一条
strict cross，在exact-checkpoint fresh NO ask有至少5股时以taker买入；在`+60..+72m`第一条
underlying fetch age不超过300秒、top bid至少5股的quote上taker退出，entry与exit都计官方Weather fee，
无price cap。开发检查了5个horizon × all/first-entry两种cohort，共10个比较；30m虽有正CI下界但缺
1/15 exit，按固定分母合同不合格。60/120/240均完整，选择最早的60m以减少basis暴露，未来不再改。

60m开发分母15笔/11 dates，12笔正PnL、3笔负PnL，cost `$71.0946`、PnL `+$3.8005`、ROI `+5.35%`，
target-date bootstrap CI `[+1.27%,+13.20%]`。Seoul为10笔、ROI `+1.85%`；Tokyo为5笔、ROI
`+13.11%`，两市点正但都很小。entry/exit underlying quote最大age分别293.96/294.32秒，candidate ask
与exact quote、token identity均15/15一致。forward主指标锁为同样完整signal denominator上的双边fee
60m round-trip ROI；需至少30个新target dates、CI下界大于0、fresh exit coverage完整且城市集中度可接受。

### stale quote 根因修复与影响半径

cross evaluator此前只校验quote wrapper timestamp与candidate exact join，没有校验wrapper内
`fresh_fetched_at_utc`。Seoul 2026-08-19 candidate
`39dcc60e50a20cbd061315cb90004dc947e91f44a05e0bba3b3972eaba0b64fe` 的wrapper虽在decision时刻，
内部NO book实际已陈旧13,559.01秒（约3小时46分）；旧回放错误计入 `NO@0.53` 的 `+$2.2877` winner。

修复后所有entry/exit均要求 `fresh_status=ok`、`0<=wrapper-fetched_at<=300s`、token一致、candidate ask
与exact quote一致并有5-share top depth。影响窗口和逐条反事实只有上述1笔、0真实order/fill：

- cap90 incumbent 从5笔4胜1负、PnL `+$2.5154`、ROI `+14.39%`，修正为4笔3胜1负、PnL
  `+$0.22765`、ROI `+1.54%`；
- 全settled+executable分母从22笔、PnL `-$2.5367`、ROI `-2.60%`，修正为21笔、PnL
  `-$4.82445`、ROI `-5.09%`；
- 该污染行不在60m first-city-date challenger分母内，因此新challenger的15笔结果不变。

机器结果：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_no_further_cooling_shadow_performance_v1/2026-08-27-challenger-freeze/summary.json`
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_cross_prev_no_performance/2026-08-27-challenger-freeze/summary.json`

artifact SHA分别为 `d60c7d885e15…` 与 `8e8cc8e889ae…`；cross唯一machine frame
`candidate_evaluation.csv.gz` SHA为 `a7a1c23c22ff…`。冻结development前缀为：no-further candidate
`1,339,926 bytes / c1e7619189b1…`；cross candidate `126,204 bytes / cd99d5b15196…`、quote
`60,975,035 bytes / 735048ec1422…`。未来复跑必须继续传这些development byte boundaries；full journal
可以继续追加forward rows，迟到的旧target-date行不能改写development分母，重复candidate/exact quote key会
显式失败。

复跑器分别为 `scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py` 与
`scripts/analysis/tmin/evaluate_tmin_cross_prev_no_shadow_v1.py`。现有raw journals足以从冻结边界继续计算，
无需为了这次research freeze改production runner；production/controller health仍critical，因此本轮也不做部署。

## 2026-08-28 no-further evaluator amendment 与 V2/V3 裁决

旧111-row headline错误地把`candidate_status=scored`（实际包含direct ask/freshness可用性）带入了
probability denominator。修复后固定四层：P0只要求`p_market+p_model+settled label+PIT/identity`，共
168 rows/15 dates；P1为两个active windows，共52/14；E0为execution-clean，共111/15；T0仍是冻结
selector的19笔/12日。旧111分母保留为historical reproduction，不能再代表全概率分母。

在P0上，raw market LogLoss/Brier=`0.191774/0.059820`；incumbent alpha=.50为
`0.193811/0.064940`，date-equal delta=`+0.005268/+0.007017`；冻结alpha=.10 routed为
`0.189310/0.059531`，date-equal delta=`-0.003546/-0.000497`，但date-block CI仍跨0。8/28 amendment
seal前已有12条当日prediction、0 selected，canonical settlement max date为8/27，因此没有成熟8/28 outcome
被用于开发；`CONFIRMATORY_FORWARD_START`保持`2026-08-28`，原alpha/window/feature/selector不变。

V2只用8个事前mechanism features、strong L2=10、strict prior-date OOF和market offset；相对market的
date-equal LogLoss/Brier delta=`+0.000475/+0.000129`，不通过。shrink-to-identity calibration也未提供
稳定改善。负类只有12个checkpoint、7个city-date，forecast uncertainty判为
`FORECAST_UNCERTAINTY_DATA_INSUFFICIENT`，不制造crossing probability。V3所需的next-colder首次穿越时间
没有settlement-native truth，故停止训练，不用observation-cache proxy冒充hazard label。最终
`KEEP_V1_FORWARD_ONLY`：只继续现有零资金V1 forward，不冻结V2/V3，不改production/live。轻量结果包为
`reviews/tmin_model_layer_v2_v3_research_v1/`。

## 2026-08-29 V2.1/V3 truth-gated closure

本轮先关闭 V1 诊断合同，再按预注册 gate 判断是否允许拟合 V2.1/V3。P0 仍为168 rows/15 dates，
P1为52/14。修正后的alpha=0 score-gradient使用
`physical_innovation × I(active_window) × (y-p_market)`；P1外116行全部严格为0，date-equal mean
`0.03802073`，20,000次target-date bootstrap 95% CI `[-0.00823715,+0.09133851]`。同一P0上
alpha=.10/.25/.50 routed相对market的date-equal LogLoss delta分别为
`-0.003546/-0.008011/-0.013631`，Brier delta为
`-0.000497/-0.001024/-0.001527`，三者CI均跨0；只完成routing与alpha强度的正交诊断，不追认
历史promotion。

`TMIN_SETTLEMENT_SOURCE_PATH_TRUTH_V1`以IANA Asia/Seoul/Tokyo本地日重建4/15–8/20路径。
230个exchange-resolved city-days全部有路径，但exact final-rung只有226/230（98.26%），低于项目
预注册99%门槛。四个Seoul争议日均另抓direct WU hourly history；WU与IEM路径一致，仍与exchange
resolved rung不一致，因此不能用“换成官方API”消除差异。另有3个Gamma cache日期没有唯一winner，
单独列为exchange-unresolved，不混入exact denominator。

审计同时发现旧`tmin_cross_metar_basis_replay_v1`用`UTC-15h`分配Asia target_date；正确映射为
`UTC+09h`。旧报告的18/18复现与历史cross统计在单独重建前不得继续作为有效证据，本轮不覆盖旧
artifact。

最终 disposition 为`STOP_SETTLEMENT_SOURCE_TRUTH_BLOCKED`。V2.1 physical/forecast transfer
与V3 1-hour hazard均未拟合；这不是`NO_INCREMENTAL_WEATHER_ALPHA_FOUND`。现有alpha=.10
zero-notional forward保持2026-08-28边界；.25/.50只建立从2026-08-29开始、无回填、无selector/order
的probability diagnostic preregistration。轻量可复现包：
`reviews/tmin_model_layer_v2_1_v3_research_v1/`；知识入口：
[Tmin model-layer knowledge](knowledge/tmin/README.md)。

## 2026-08-29 V2.1/V3 diagnostic completion and strategy readout

truth exact gate 仍为226/230（98.26%），因此新模型没有 promotion 资格；但为完成策略层闭环，已在
research-only sensitivity contract 下同时保留全部四个争议日、以及全部排除四日，完成实际拟合、
strict prior-date OOF 与冻结 selector replay。两种 truth 处理的结论一致。

weather-only panel 为1,536 checkpoints、256 independent Seoul/Tokyo city-days、51 next-colder
event city-days。V2.1 使用strongly regularized binary EOD physical foundation；canonical forecast
archive虽有9,703 curves/96 city-days，但06:00/09:00 active windows没有一致prior-vintage覆盖，因此
forecast uncertainty arm固定`gamma=0` fail closed。V3使用预注册1h interval discrete hazard，并将
逐小时survival product作为单一nonnegative market-residual innovation。两个adaptor都只读此前
target_date settlement labels，P1外概率严格等于market。

在完整P0 168 rows/15 dates上，market date-equal LogLoss/Brier为`0.241535/0.077412`；V2.1为
`0.241552/0.077416`，delta=`+0.000016/+0.000005`；V3为`0.241626/0.077446`，delta=
`+0.000091/+0.000034`。V2.1在fixed validation/test均未稳定胜clock；V3在validation退化、test仅
小幅点改善，不能称稳定foundation alpha。争议日全排除版本同样没有方向改变。

同一168 settled probability rows、111 execution-clean rows、direct YES ask、官方
`0.05*p*(1-p)` fee、两个active windows和每city-date首个正edge下，V2.1与V3都是0 positive-edge
checkpoints、0 trades。对照V1 incumbent为30 positive-edge checkpoints→19 trades/12 target dates，
19胜0负，5-share cost`$89.85885325`、PnL`+$5.14114675`、ROI`5.72%`；冻结alpha=.10 routed为
2 trades/2 dates、2胜、PnL`+$0.619625`、ROI`6.61%`。V1的19笔按window分别为morning 6、
post-sunrise 5、daytime 3、evening 4、late-evening 1，全部获胜；其中唯一`ask<=.60`交易贡献
`+$2.487525`，高价`ask>.98`十笔合计只贡献`+$0.318397`，说明全胜不等于各价格带收益相同。

旧cross replay的`UTC-15h`也已按`UTC+09h`重跑：Seoul 186个labeled strict crosses中NO side
184胜，first-of-night 101/101；Tokyo 165/165，first-of-night 93/93。该结果只量化日期bug影响，
不与no-further YES selector的19-trade PnL混算。

当前 disposition 为`KEEP_V1_FORWARD_ONLY`：保留现有V1 zero-notional forward；不冻结V2.1/V3、
不调selector/threshold、不部署。轻量机器证据和最短复跑命令位于
`reviews/tmin_v2_1_v3_strategy_readout_v1/`。

## 2026-08-29 V2.2 forecast-threshold residual evidence gate

外部复核后的权威解释覆盖上一节中对V2.1/V3范围的过度表述：V2.1只完成了physical-only
diagnostic，该diagnostic失败；full forecast V2.1没有被测试，因此不能写成`V2.1_FULL_FAILED`，也
不能建立`NO_WEATHER_ALPHA_EXISTS`。当前V3固定为`STOP_CURRENT_V3`，不继续调static checkpoint
hazard。操作结论保持`KEEP_V1_FORWARD_ONLY`。

本轮从冻结observation messages逐row重建P0 168/168 checkpoints的exact raw running minimum、
observation event/available clocks、current native rung、next-colder lattice boundary与distance；全部
raw minimum与persisted rung一致，之后的V2.2训练和scoring均不再使用固定`0.5` safety-margin proxy。

V2.2模型合同冻结为06:00/09:00 prior-date forecast-error empirical CDF，经city×checkpoint向checkpoint
global以30强度收缩，再减去20强度clock logit，形成`z_weather`；market adaptor只估
`alpha>=0, HalfNormal(.25)`，posterior objective为各target_date内Bernoulli log likelihood均值之和加
prior。route仍只含morning/post-sunrise；不学习continuous gate。实现包含native-vintage latest-PIT
选择、target-date-block fold、posterior interval、score-gradient 20,000 date bootstrap及unsupported/
outside-route market fallback。

当前输入不允许拟合：forecast inventory虽有9,703 curves/96 city-days，但可验证native model run、
issue time、available_at和full remaining path的eligible city-days为0/120；06:00/09:00 usable coverage
为0%，native available-at coverage为0%。settlement truth仍为226/230=`98.26%`，低于99%。两份命名的
prior external evidence原件也未随知识包提供，现有gap record不冒充原件。因此V2.2在data gate前
停止，P0 168行全部byte-equal回到raw market；zero delta只证明fail-closed，不是model result。

当前研究disposition为`BLOCKED_EVIDENCE_OR_DATA`，操作上仍为`KEEP_V1_FORWARD_ONLY`与
`NO_MORE_MODEL_COMPLEXITY`。V2.2 prospective start预注册为evidence seal之后的首个target date
`2026-08-30`且不得回填；由于data gate未过，该arm状态为`BLOCKED_NOT_STARTED_NO_BACKFILL`。
完整证据以append-only修复版
`reviews/tmin_v2_2_forecast_threshold_residual_v1_r2/`为准；它封装最小frozen inputs并验证
base-SHA+binary-patch→source snapshot及空目录63文件hash replay。旧`..._v1/`与`..._r1/`
保留作审计历史，但不再用于reproduction裁决。

## 2026-08-30 V2.2 可拟合修复与 probability-only freeze

本节 supersede 上一节的“V2.2 未拟合/全部回 market”作为当前研究裁决，但保留上一节作为旧
strict-gate 审计历史。模型公式、06:00/09:00 route、HalfNormal(.25) adaptor、V1 selector、
threshold、price cap、size 与 execution 均未改变；修复只把数据可用 gate 与模型 freeze/promotion
diagnostic 分开。Primary forecast-error history 只使用 exact reconciled truth：256 个有 native forecast
的 city-days 中纳入226，排除23个无 reconciliation row、3个 exchange-rung unresolved 与4个已逐条
审计的 Seoul source mismatch。排除后仍有45个 next-colder event city-days且 native available-at
coverage 100%，因此 fit data gate 通过；全源 truth 仍诚实保留226/230=`98.26%`，不冒充99%通过。

固定 P0 仍为8/12–26的168 rows/15 dates。V2.2 supported active route为50 rows/13 dates；由于
prior-date gate要求至少10 dates、5个negative city-date episodes，只有8/23–26的16 rows/4 dates
真正 expanding OOF fit。该切片V2.2-minus-market date-equal ΔLogLoss=`-0.00296845`、95% CI
`[-0.01290120,+0.00719918]`，ΔBrier=`-0.00005911`、CI
`[-0.00248702,+0.00255904]`：两项点估改善但区间均跨0。最终开发快照 posterior alpha mean/
median=`0.197536/0.167290`；这不是 live admission。

在原168-row direct-ask/fee/selector replay上，V2.2为1 signal→1 trade/1 date、1/1，5-share
cost`$4.7141`、PnL`+$0.2859`、ROI`6.06%`；V1 incumbent仍为30 signals→19 trades/12 dates、
19/19、PnL`+$5.14114675`，alpha=.10 routed为2/2、PnL`+$0.619625`。PnL未用于选模。
V1的19笔只有9笔处于V2.2的Seoul/Tokyo active route，另10笔在scope外，因此V2.2不是V1
全窗口替代。

研究动作是`FREEZE_V2_2_PROBABILITY_CHALLENGER`：规格和参数artifact已冻结，`2026-08-31`是
no-backfill最早允许target date；但当前runtime为`PREREGISTERED_NOT_STARTED`，尚无consumer或
append-only journal，不能写成已经开始采集。若后续显式授权部署，只与`RAW_MARKET`、既有
`V1_ALPHA010_ROUTED`共同记录同分母 probability，不生成selector/order。操作状态仍为
`KEEP_V1_FORWARD_ONLY`，`tiny_live_eligible=false`；满30个新settled target dates、180 P0 rows、
80 P1 rows及预注册negative/city coverage后再裁决。当前轻量权威包为
`reviews/tmin_v2_2_frozen_candidate_readout_v1_r5/`，日期摘要见
[2026-08-30 V2.2 fitted freeze](analysis/2026-08/2026-08-30-tmin-v2-2-fitted-frozen-candidate-v1.md)。

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
