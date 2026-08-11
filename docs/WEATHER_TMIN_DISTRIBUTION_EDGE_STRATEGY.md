# Weather Daily Minimum Temperature Strategy

Status: current-reference
Updated: 2026-08-11 initial design and readiness audit
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
probability_artifact = none
forward = not_started
runtime = partial zero-notional telemetry
promotion = no live
```

每日最低温值得单独研究，但不能把 Tmax 模型的符号反过来复用。对当地日历日
`00:00–23:59` 来说，最低温通常有两个仍可改写结算结果的冷却窗口：凌晨至日出后的
清晨窗口，以及日落后至午夜的晚间窗口。上午已经打印出的 running minimum 只是暂时下界；
当天晚间仍可能落入更低 exact bracket。

本设计只冻结研究问题、PIT 分母、模型对照和晋级标准；不部署新 collector、不创建订单、
不修改任何 live 实例。

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

先完成 `Hong Kong + Seoul + Tokyo` 的 min-specific contract census，并把当前 lowest observer 的
四时钟、source basis 与完整 event ladder 映射为 WCIR coverage-only rows；同时由现有
`weather_market_books` owner 补 min event inventory。完成前不拟合 M1/M2、不跑 ROI、不部署新
strategy instance。完成后的第一次正式交付应是固定 denominator 的 coverage/readiness artifact，
而不是选中交易清单。
