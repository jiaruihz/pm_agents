# 绩效分析：WCIR admission-forward 全市场触发复盘（living）

> 窗口：各 profile 的 frozen-forward boundary — 2026-08-23 15:43:41Z
> 策略身份：`weather_city_probability_runtime_v3` / `weather.city_intraday_probability` / zero-notional admission forward
> evidence layer：current raw WCIR journal + canonical WCIR bridge + Polymarket exact market identity

## 结论与动作

这批就是此前持续积累的 admission forward，不另起分母。当前 8 个 active profile 共有
15,596 个 expression-checkpoint candidates，其中 3,848 个可评分、85 个真实触发 zero-notional
`TradeIntent`；80 个已按 `market_id + condition_id` 精确结算，44 胜，unit fee-adjusted PnL
`+0.4691`、ROI `+1.08%`，target-date block bootstrap 95% CI `[-20.36%, +19.89%]`。
点估接近零且区间很宽，不能升 live。

Amsterdam V9 的旧 side-scoped 表达产生了 11 个同日同 bracket 的晚到反向 intent。按 2026-08-23
已部署的 bracket-scoped policy 重放，历史 85 个 intents 保留 74 个；已结算部分从 80 个变为
70 个，组合 ROI 点估从 `+1.08%` 提到 `+4.83%`，但 CI 仍为
`[-19.45%, +23.50%]`。这证明 expression policy 确实污染了旧交易层，但不是 alpha 已确认。
新 policy 从 `2026-08-23T15:25:27Z` 起累计 8 个候选、0 个新 intent，尚无 untouched trade result。

```text
significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=inconclusive
```

动作：继续 zero-notional admission forward，不改 live。Amsterdam V9 只按新 bracket policy 继续积累；
Helsinki、Busan 不因当前点估加 gate 或升金额；Tokyo 与 cross-.7 先解决有效证据/触发覆盖，不把“0单”当保守胜利。

## 数据快照

| 项目 | 值 |
|---|---|
| raw 覆盖截止 | 18,080 bundle rows；17,932 unique WCIR candidates；2026-08-23 15:43:41Z |
| 本报告固定 active denominator | 15,596 candidates；Amsterdam / Busan / Helsinki / Tokyo；各自 frozen boundary 后 |
| canonical DB | `/Volumes/jrs/pm_agents/runtime/weather.db`；device `16777244` / inode `54444` |
| canonical build | `v2_event_checkpoint` 113,897 rows；148 selected；`fact_built_at_utc=2026-08-23T15:47:35.342065Z` |
| manifest | `critical=0`；`db_route=healthy`；1 个无关 tmux desired-state warning |
| 独立 target dates | selected 12 dates（8/12–8/23）；settled 11 dates（8/12–8/22） |
| unsettled | 5 intents，均为 8/23 尚未 closed 的 exact markets |
| actual order / fill / notional | 0 / 0 / 0；全部为 zero-notional shadow |
| outcome evidence | Gamma `market_id` 精确读取 263/263，无 fetch error；逐条要求 candidate `condition_id` 相等 |
| CLOB coverage gate | NA：没有 `live_real` fills，不发布账户 PnL |

canonical bridge 已在复盘前后两次做 bounded incremental apply；最后一次新增 8 candidates / 4 checkpoints /
4 events，`candidate_delta=0`。settlement 不使用 `(city,target_date,bracket)` 回退，避免 Tokyo Tmax/Tmin
同档串线。

## Target metric 与固定分母

- unit/grain：概率层为 unique `SignalCandidate` expression-checkpoint；交易层为首次 selected `TradeIntent`。
- PIT decision timestamp：candidate `decision_ts_utc`，每个 profile 严格使用原 frozen-forward boundary。
- universe：当前 production config 中 Amsterdam V9/cross-.7/market-offset、Busan、Helsinki、Tokyo 三个 head；旧 Amsterdam V7/prior-A 与旧 Helsinki heads 不混入。
- label：Polymarket closed binary outcome，exact `market_id` 且 `conditionId` 必须与 candidate 相等。
- price：`executable_cost` 已含官方 weather taker fee。V9 是 top-ask 1-share-equivalent；配置了 `selection_shares=5` 的 profile 为 5-share sweep 的 per-share effective cost。
- 主指标：同 rows model-vs-market Brier/logloss；交易层 unit fee-adjusted ROI。
- baseline：同一 candidate row 的 `market_p`；不是不同机会集合的 market-favorite 交易。
- inference：按 `target_date` block bootstrap 20,000 次；8 个 profile/slices 未作多重检验校正，所有切片只作诊断。

### 各 profile 的实际时间范围

总报告不是一个统一自然日起点；每个 profile 保留自己预注册的 frozen boundary，统一观察截止才是
`2026-08-23T15:43:41.173029Z`。

| profile | frozen boundary | 实际 candidate decision 覆盖 | target-date 覆盖 | selected target dates |
|---|---|---|---|---|
| Amsterdam V9 | 8/11 22:00Z | 8/12 08:34Z–8/23 15:43Z | 8/12–8/23 | 8/12–8/23，12日 |
| Amsterdam cross-.7 | 8/12 03:00Z | 无 candidate | 无 | 0 |
| Amsterdam market-offset V3 | 8/12 08:24Z | 8/13 11:14Z–8/23 14:14Z | 8/13–8/23 | 8/13–8/22，10日 |
| Busan | 8/12 02:00Z | 8/12 02:00Z–8/23 03:40Z | 8/12–8/23 | 8/12–8/22，10日 |
| Helsinki | 8/12 08:31Z | 8/12 08:42Z–8/23 15:41Z | 8/12–8/23 | 8/12–8/23，11日 |
| Tokyo pre-cross V2 | 8/12 00:00Z | 8/13 00:58Z–8/23 05:37Z | 8/13–8/23 | 0 |
| Tokyo state-entry V7 | 7/31 15:00Z | 8/02 07:08Z–8/23 08:59Z | 8/02–8/23 | intent disabled |
| Tokyo overshoot V2 | 8/02 00:00Z | 8/02 07:08Z–8/23 08:59Z | 8/02–8/23 | intent disabled |

## Signal funnel

| profile | candidates | scored | blocked | source triggers | selected | 触发频率 | selected dates |
|---|---:|---:|---:|---:|---:|---:|---:|
| Amsterdam V9 | 2,826 | 1,204 | 1,622 | 1,413 | 42 | 2.97% | 12 |
| Amsterdam cross-.7 | 0 | 0 | 0 | 0 | 0 | NA | 0 |
| Amsterdam market-offset V3 | 246 | 242 | 4 | 123 | 15 | 12.20% | 10 |
| Busan online market-prior | 6,344 | 474 | 5,870 | 6,343 | 12 | 0.19% | 10 |
| Helsinki bounded residual V2 | 1,150 | 880 | 270 | 570 | 16 | 2.81% | 11 |
| Tokyo pre-cross V2 | 32 | 3 | 29 | 32 | 0 | 0% | 0 |
| Tokyo state-entry V7 | 2,500 | 523 | 1,977 | 1,182 | 0 | 0% | 0 |
| Tokyo overshoot V2 | 2,498 | 522 | 1,976 | 1,181 | 0 | 0% | 0 |

这里的 `source triggers` 是各 profile 内的 source-event 分母，Tokyo heads 共享事件，不能横向相加。
blocked 主要不是策略主动拒单：V9 有 1,622 条 midpoint interval-censored；Busan 有 5,266 条
不在 pending-confirmation state、598 条 exact-NO book 非双边；Tokyo 两个旧 head 各约 1,326 条
one-sided interval，另各约 650 条 token/outcome mismatch。后两项是 evidence/identity coverage，应与 selector
本身分开。

### Tokyo 为什么是 0 个 intent

此前只报 `selected=0` 把两种完全不同的情况混在了一起：

| Tokyo head | candidates / scored | scored `would_enter` | `emit_paper_intents` | 0 intent 的真实原因 |
|---|---:|---:|---|---|
| pre-cross V2 | 32 / 3 | 0 | true | 29条 one-sided interval；仅3条可评分的 fee+5-share+reserve 后 edge 为 `-7.03/-4.87/-3.38pp` |
| state-entry V7 | 2,500 / 523 | 27 | false | 有正edge，但这个旧诊断 head 配置上禁止发 intent |
| overshoot V2 | 2,498 / 522 | 2 | false | 有2条正edge，但同样只记 candidate、不发 intent |

所以 Tokyo 不是“模型完全没看到机会”。真正 admission-enabled 的 pre-cross V2 是没有可执行正edge；
state-entry/overshoot 则是研究配置主动禁用 intent。若严格按各自 position scope 对已评分 `would_enter`
做诊断性 first-position replay，state-entry 是10个city-day、9胜、unit ROI `+7.44%`；overshoot 是2个
date×bracket、2胜、ROI `+65.62%`。这两组不是实际 forward selections，且其全分母 proper score 仍输market
（Brier delta分别`+.00541/+.00287`），不能把这个事后小样本补进85个intent headline，更不能据此打开live。

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| active raw candidates | expression-checkpoint | 15,596 | profile-dependent | raw journal authoritative |
| scorable PIT model + market | expression-checkpoint | 3,848 | 最多 17 settled dates | 11,748 blocked，原因见上 |
| policy selected | TradeIntent | 85 | 12 | 85/85 已进入 intent journal |
| exact settled selected | TradeIntent | 80 | 11 | 5 条 8/23 open |
| actual fill | fill | 0 | 0 | zero-notional by design |

## Probability / ranking quality

delta 定义为 `model - market`；负数才是模型更好。

| candidate | rows | model / market Brier | model / market logloss | Brier delta | block 95% CI | 判断 |
|---|---:|---:|---:|---:|---|---|
| Amsterdam V9 | 1,122 | .08706 / .08121 | .30504 / .24770 | +.00585 | [-.02435, +.03873] | market 点估更好 |
| Amsterdam market-offset V3 | 216 | .14761 / .15293 | .45855 / .45745 | -.00531 | [-.04243, +.03348] | Brier 点好、LL 点差，均未确认 |
| Busan | 474 | .05312 / .04840 | .16859 / .15807 | +.00472 | [-.00249, +.01465] | market 点估更好 |
| Helsinki | 732 | .10673 / .10790 | .32457 / .32644 | -.00117 | [-.00676, +.00604] | 微弱点好、未确认 |
| Tokyo pre-cross V2 | 3 | .37608 / .38879 | .94656 / 1.00158 | -.01271 | [-.03814, 0] | 只有3行，不可用 |
| Tokyo state-entry V7 | 523 | .09884 / .09344 | .31646 / .28638 | +.00541 | [-.02600, +.03328] | market 点估更好 |
| Tokyo overshoot V2 | 522 | .09651 / .09365 | .30401 / .28732 | +.00287 | [-.00851, +.01483] | market 点估更好 |
| 合计 | 3,592 | .09356 / .09048 | .30229 / .27655 | +.00308 | profile-mixed，不作单一CI | baseline gate FAIL |

合计 rows 少于 3,848，是因为尚未 closed 的 8/23 expressions 不进入 proper score。总体 model 不胜 market；
因此 selected ROI 即使为正，也只能算探索性交易表达结果。

## Fee-adjusted trade performance

以下是每个 intent 买 1 share 的可比 unit replay，不是美元实盘 PnL。

| slice | intents | settled | W-L | unit cost | unit PnL | ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| Amsterdam V9 旧表达 | 42 | 38 | 16-22 | 15.9555 | +.0445 | +0.28% | [-18.77%, +18.58%] |
| Amsterdam V9 新 bracket policy 历史重放 | 31 | 28 | 12-16 | 10.5816 | +1.4184 | +13.40% | [-24.34%, +44.94%] |
| Amsterdam market-offset V3 | 15 | 15 | 10-5 | 11.0519 | -1.0519 | -9.52% | [-44.01%, +17.27%] |
| Busan | 12 | 12 | 8-4 | 7.9448 | +.0552 | +0.69% | [-37.28%, +37.88%] |
| Helsinki | 16 | 15 | 10-5 | 8.5787 | +1.4213 | +16.57% | [-13.96%, +55.67%] |
| Tokyo / cross-.7 | 0 | 0 | 0-0 | 0 | 0 | NA | NA |
| 全部实际旧表达 | 85 | 80 | 44-36 | 43.5309 | +.4691 | +1.08% | [-20.36%, +19.89%] |
| 全部按当前 bracket policy 重放 | 74 | 70 | 40-30 | 38.1570 | +1.8430 | +4.83% | [-19.45%, +23.50%] |

side 拆分：已结算 YES 35 个、15 胜、ROI `-0.77%`；NO 45 个、29 胜、ROI `+2.06%`。
Helsinki 的正 PnL 主要来自 NO：6 个已结算、5 胜、ROI `+38.78%`，样本太小；market-offset 的
YES/NO 分别为 `-11.56%/-8.61%`，不是单侧掩盖。

### 盈利贡献，而不是只看净数

80个settled intents的gross winning PnL为`+11.2705`，gross losing PnL为`-10.8014`，最后只剩
`+0.4691`。净收益接近零是两边大额抵消，不是每个profile都稳定小赚。

| profile | settled / wins | gross gains | gross losses | net PnL | ROI | 占全部gross gains / losses |
|---|---:|---:|---:|---:|---:|---|
| Amsterdam V9 | 38 / 16 | +4.3815 | -4.3371 | +.0445 | +.28% | 38.9% / 40.2% |
| market-offset V3 | 15 / 10 | +2.2649 | -3.3168 | -1.0519 | -9.52% | 20.1% / 30.7% |
| Busan | 12 / 8 | +1.7032 | -1.6480 | +.0552 | +.69% | 15.1% / 15.3% |
| Helsinki | 15 / 10 | +2.9209 | -1.4996 | +1.4213 | +16.57% | 25.9% / 13.9% |

按当前 bracket policy 重放后，V9/Helsinki分别贡献`+1.4184/+1.4213`，market-offset回吐`-1.0519`，
Busan仅`+.0552`，合计`+1.8430`。但8/17和8/22两个最好target dates合计贡献`+4.3566`；实际旧表达
去掉这两日后剩余unit PnL`-3.8875`、ROI`-11.47%`，当前policy重放去掉两日后也约`-8.82%`。
这就是为什么headline正数不能外推。

## Helsinki 潜在 alpha 与 Amsterdam 模型诊断

2026-08-24北京时间做了一次只读补充检查，raw cutoff推进到`2026-08-23T17:13:42Z`：Helsinki/V9
各增加16个candidate，但没有新增intent或settled result，因此以下判断仍使用上面的固定settled分母，不把
新增未结算行混入headline。

### Helsinki：有潜力，但尚未确认

Helsinki是当前active profiles中唯一同时满足“forward交易点估为正”和“全评分分母proper score点估优于
market”的城市。732个已结算expression rows/11 dates上，model-market Brier delta为`-.001174`，
date-block CI`[-.00680,+.00575]`；logloss delta为`-.001872`。15个已结算intent/10个交易日为10胜，
unit ROI`+16.57%`，CI`[-13.96%,+55.67%]`。方向一致，但概率门和收益门都未显著。

收益结构仍很脆弱：NO为6笔5胜、unit PnL`+1.3972`、ROI`+38.78%`，占组合净PnL的98.3%；YES为9笔5胜，
PnL仅`+.0241`、ROI`+.48%`。移除单笔最大赢家后ROI降至`+6.60%`；移除两笔最大赢家后只剩
PnL`+.01935`、ROI`+.24%`。因此更准确的假设不是“Helsinki双边模型已盈利”，而是“FMI first-seen之后，
bounded market residual对exact-NO remaining-heat可能有小幅纠偏能力”。NO切片来自本窗诊断，不能反过来
关闭YES或改side gate。

这个假设有物理与统计上的可继续性：artifact只允许天气在market logit上做`c=.15`的有界修正，并且天气
calibration来自2025 OOF 51,451 rows/365 dates；8/2–11已看过压力窗与本次untouched forward的概率/交易
点估同号。反面证据是样本仍只有11个settled dates，所有16个intent edge都不超过2c、13个不超过1c，
中位edge仅0.36c，执行延迟足以吞掉全部优势。当前等级仍是`inconclusive / top-priority zero-notional
candidate`，不是live alpha。

### Amsterdam：V9和market-offset V3不是同一种失败

V9在1,122个已结算expression rows/11 dates上，Brier/logloss相对market分别恶化
`+.005846/+.057344`；天气innovation对实际residual的诊断斜率只有约`.36`，说明standalone天气修正平均
放大过头。尤其绝对修正3–15pp的rows明显拖累proper score。按当前bracket policy重放虽有28笔ROI
`+13.40%`，但移除两笔最大赢家后PnL为`-.00148`、ROI约`-.01%`；这不是可依赖的selector证据。

market-offset V3则不同：216个已结算rows/10 dates上Brier delta为`-.005313`，但logloss delta为
`+.00110`且CI跨0，说明平均概率还有可能的market residual；失败集中在被selector挑出的高置信尾部。
15个selected rows的model probability均值`.848`、market均值`.698`，实际实现率`.667`；selected-row
Brier delta为`+.02587`，fee后ROI`-9.52%`。也就是说，V3不是“完全没预测信息”，而是密集checkpoint上
训练出的correction在first date×bracket entry tail中过度自信，且交易表达没有把这种selection-conditioned
calibration单独验证。

因此Amsterdam的研究动作不是继续给V9加feature或从这10日追加price/edge gate：V9保留为dormant
weather comparator，不再直接当fair value；V3沿用同一model identity做预注册的同rows A/B，只比较
`market-only`、当前residual、用旧开发窗冻结的bounded/shrunk residual，并把checkpoint、transition、
state-entry/date×bracket三个grain分别按target_date等权评分。当前forward只作评测，不能用于选择cap、L2或
阈值；同时继续记录t0/+1/+3/+5/+15s 5-share execution survival。只有新的proper-score、selected-row
calibration、fee uplift和execution四层同向后才谈替换当前表达。

### Amsterdam 第一性原理 retraining 首轮（2026-08-26）

首轮已实际训练和重放，不是计划。固定历史分母为4/3–7/29的11,800个PIT price-reference checkpoints、
112个target dates；nested expanding OOF为6/1–7/29的6,136 rows/57日。market logit系数保持1，只允许天气
提供残差；训练目标把checkpoint、V5 path-regime transition、state-entry各按target_date等权后再各占1/3。
第一轮候选固定为2个feature set×3个L2×4个logit cap=24组。最初实现曾在price join后错误重算grain并
覆盖reference membership；该开发中结果已作废，现保留pre-evidence canonical membership，forward也复用
同一bracket+path-regime定义。这个bug没有进入production或live，相关10个测试已通过。

| model | 57日 multigrain Brier / logloss | 8/12–23 reused diagnostic Brier / logloss | 相对t0 market ΔBrier / ΔLL | edge02 交易 |
|---|---:|---:|---:|---:|
| incumbent V3 | .06448 / .20901 | .09092 / .29019 | +.00556 / +.02304 | 13笔8胜，ROI -8.95% |
| balanced grain | **.06211 / .19998** | .09024 / .28752 | +.00488 / +.02036 | 12笔7胜，ROI -8.36% |
| physical-only（去掉V9 disagreement） | .06329 / .20192 | **.08862 / .27916** | +.00326 / +.01200 | 12笔7胜，ROI -20.80% |
| t0 market | .07080 / .22386 | .08536 / .26716 | 0 / 0 | 同rows comparator |

balanced在June和July两个nested OOF都优于incumbent，且8月相对incumbent的checkpoint ΔBrier/ΔLL为
`-.00068/-.00267`，但date-block CI均跨0；physical-only的相对incumbent ΔBrier/ΔLL为
`-.00230/-.01103`，CI同样跨0。两种challenger的验证集最终都选择`L2=.01 / 无cap`，所以“强制bounded”
本身没有得到历史选择支持。8月三种模型仍全部输同rows t0 market，交易也均为负；physical-only虽有最好的
forward proper score，却挑中了更差的交易集合，进一步证明probability与selector必须分门验收。

结论是`research point improvement / market baseline FAIL / fee FAIL / no-live-change`。balanced和
physical-only均为`fixed_market_logit_offset_v2` research artifact、`live_eligible=false`，当前WCIR adapter
也不消费该schema。8/12–23已被此前诊断看过，只能作reused diagnostic；新的clean forward从各artifact
freeze时间开始，但当前`forecast_hourly_curves` collector stale，修复前不延伸、不选模。机器摘要位于
`/Volumes/jrs-archive/pm_agents/research/artifact_store/amsterdam_knmi_market_offset_balanced_bounded_v1/run=20260826_first_principles/comparison_summary.json`
（SHA-256 `17231cc3…f61`）。

### Amsterdam 当前最优模型封闭 tournament 与 final refit（2026-08-26）

在部署采集前又完成了一次封闭 tournament，避免把首轮两个 challenger 误称为“当下最好”。选择规则在看
8月结果前固定为8个feature sets × 3个L2 × 2个logit caps × 5个correction scales，共240个候选/fold；
主目标是checkpoint、transition、state-entry三grain按target_date等权后的logloss，Brier只作tie-break，ROI
不参与选模。候选同时包含raw market（scale=0）、train-only calibrated market、incumbent/balanced/physical
residual及market+weather interaction。

4–7月的封闭选择仍选中`weather_path_mechanism / L2=.01 / 无cap / scale=1`，说明首轮balanced方向确实是
这套候选空间里的weather champion，而不是遗漏了简单shrink或market calibration。6–7月expanding OOF为
6,136 rows/57日，champion multigrain Brier/logloss=`.06273/.20120`，raw market=`.07080/.22386`；7月
validation为`.05979/.19392`。已看过的8/12–23 outer development audit仍失败：484 rows/11日上champion
对t0 market的ΔBrier/ΔLL=`+.00488/+.02036`，CI分别`[-.02528,+.02909]`和
`[-.05045,+.07791]`；edge02为12笔7胜、fee后ROI`-8.36%`、actual fills=0。

因此“当前最好”必须分两层：**当前decision probability champion是raw pre-event market；当前weather
model champion是balanced market-residual**。前者是必须打败的基准，不是weather alpha；后者只获准完整
分母zero-notional shadow，不获准真实交易。选参结束后已将8/12–23的498个PIT prior rows/11日作为
`seen_outer_development_refit_only_not_forward_evidence`并入final refit；最终artifact训练12,298 rows/123日，
clean-forward boundary=`2026-08-26T12:00:15.867834Z`，SHA-256
`9e337219995b548e075021ea9ec7cb1404bd2ce876393b8a9f9ceb055b1b20f6`，`live_eligible=false`。runtime
adapter已兼容v2 schema并保留v1回放；随后以release `da87d3c1…befa0`部署到Mac production的既有
`weather_city_probability_runtime_v3` zero-notional实例，未开放订单。首条新artifact证据为12:10 UTC KNMI
event：bracket 24双边feature coverage 100%、0 missing，YES/NO概率`2.4076%/97.5924%`，两侧fee后edge
均为负而正确skip；runtime `errors=0 / orders_submitted=0`。canonical机器身份见
`/Volumes/jrs-archive/pm_agents/research/artifact_store/amsterdam_knmi_market_offset_champion_tournament/run=20260826_current_best/champion_manifest.json`。

## 入场时机、赔率与选择质量

| profile | 本地入场时间 range / median | effective cost range / median | model p median | market p median | fee后 edge median |
|---|---|---|---:|---:|---:|
| Amsterdam V9 | 07:14–18:14 / 12:44 | .0021–.9342 / .3309 | .5213 | .2825 | 6.45pp |
| Amsterdam market-offset | 11:14–16:44 / 12:44 | .4120–.9514 / .7399 | .8777 | .6950 | 7.18pp |
| Busan | 09:21–14:40 / 12:35 | .1667–.9953 / .7398 | .7915 | .7100 | 1.61pp |
| Helsinki | 06:33–16:13 / 12:43 | .0063–.9829 / .6317 | .6353 | .6000 | .36pp |

按各城市本地时间合并，实际settled entry分布为：

| 本地时段 | entries / wins | unit PnL | ROI | 主要构成 |
|---|---:|---:|---:|---|
| 00–09 | 1 / 0 | -.0021 | -100% | Amsterdam |
| 09–12 | 21 / 12 | -1.2239 | -9.26% | Amsterdam 14、Busan 4、Helsinki 3 |
| 12–15 | 49 / 29 | +2.2887 | +8.57% | Amsterdam 30、Helsinki 11、Busan 8 |
| 15–18 | 8 / 2 | -.6786 | -25.33% | Amsterdam 7 |
| 18–24 | 1 / 1 | +.0850 | +9.28% | Amsterdam |

12–15时段贡献了全部净正收益，但它同时占49/80 entries，且跨不同城市机制；现在只能描述，不能据此
制造统一时段gate。

赔率带诊断显示两端都值得继续观察，但现在不能据此新增 hard gate：

- cost `<.10`：15 个 intents，13 个已结算，0 胜，ROI `-100%`；其中 14/15 来自 Amsterdam V9。
- `.10–.30`：11 个已结算，4 胜，ROI `+60.26%`。
- `.30–.50`：11 个已结算，3 胜，ROI `-31.48%`。
- `.50–.70`：11 个已结算，8 胜，ROI `+18.85%`。
- `.70–.90`：17 个已结算，14 胜，ROI `+2.08%`。
- `>=.90`：17 个已结算，15 胜，ROI `-5.50%`。

低价 13 连败看起来醒目，但这些 intent 的模型概率也多为小概率，13 行不足以证明应设 price floor；
高价组即使 15/17 胜仍亏，说明 hit rate 不能代替 fee-adjusted EV。下一轮应预注册 price-band calibration
诊断并继续完整分母，不根据本次结果回改阈值。

edge带同样不单调：`0–1pp`为18笔、ROI`+29.54%`，`1–3pp`为12笔、ROI`-49.09%`，
`3–7pp`为23笔、ROI`+.18%`，`7–15pp`为12笔、ROI`-26.73%`，`>=15pp`为15笔、
ROI`+19.60%`。这是小样本与不同profile/赔率混合的结果，不支持简单提高edge threshold。

## 切 live 时的可成交性审计

会存在“shadow显示能买、真实下单时成交不了或成交后edge消失”的情况。当前85个intents全部zero-notional，
实际下单尝试数为0，所以没有经验fill rate；但盘口证据可以把风险分层：

| profile | decision snapshot 的5-share证据 | 时钟/短时压力测试 | 主要风险 |
|---|---|---|---|
| Amsterdam V9 | 42/42 t0 books均可完整扫5股；41/42首档本身>=5股，余下1条跨一档只增加0.18c/share | t0 ladder完成比source clock晚3.77–15.84s（median 4.95s）；+15s仍42/42可扫5股，但只有40/42仍过原edge门 | 不是没depth，主要是下单前价格重定价；当前candidate timestamp早于book完成 |
| Amsterdam offset V3 | 15/15 t0 books可完整扫5股 | 完成延迟3.39–9.65s（median 5.01s）；+15s仍15/15可扫，14/15仍过edge门 | pre-event feature book与t0 execution book已分开，但真实executor仍需再验价 |
| Busan | 12/12 的`executable_cost`来自真实5-share sweep；profile要求feature response<=10s、execution book age<=90s | 没有这12条统一的+1/+5/+15s post-decision execution tape；0次真实order | 6/12 edge<=1c，盘口一跳就可能失去EV；严格限价会unfill，追价会变负edge |
| Helsinki | 16/16 的`executable_cost`来自真实5-share sweep | source first-seen→selected book/decision为7.1–110.2s（median 33.5s）；profile仍允许book age<=900s、source→book<=120s；无真实order | 16/16 edge<=2c、13/16<=1c；现有freshness合同不够支撑live taker |

组合上22/85 intents的fee后edge不超过2c，45/85不超过5c。Amsterdam的+15s books说明5-share容量大多
存在，但也观察到单条最大价格恶化10.41c（V9）和12.73c（offset）；如果executor追价，这些intent即使成交
也不再是报告里的交易。Busan/Helsinki因为缺统一post-decision tape，连短时fillability偏差都还不能估。

因此切live前缺的不是“能不能发API订单”，而是执行合同：必须在真正提交前重新抓execution book，按实际
5-share sweep+fee重算edge；使用不可追价的marketable limit/FOK或FAK语义，深度/价格变化就skip；并先累计
t0后`+1/+3/+5/+15s`的同token盘口与模拟fill结果。尤其Helsinki现有900s book-age上限不能直接作为live
授权口径。否则会出现两种看似相反但都真实的情况：限价不追导致unfilled，或者追价成交但预期edge已经消失。

## Amsterdam 反向双选影响半径

旧表达的 11 个晚到方向是：8/12 `28 NO→YES`、8/13 `32 NO→YES`、8/14
`33 NO→YES`、8/14 `34 YES→NO`、8/16 `23 NO→YES`、8/18 `21 NO→YES`、
8/19 `21 NO→YES`、8/20 `20 NO→YES`、8/20 `21 NO→YES`、8/21 `20 YES→NO`、
8/23 `20 NO→YES`。

现在 exact market settlement 已覆盖前 10 个：晚到方向 4 胜6负、unit PnL `-1.3739`；8/23 一条仍 open。
所以 bracket policy 对已结算窗口的改善是移除 `-1.3739`，不是此前 city/date/bracket 近似 join 得出的
`+$3.5428`；旧数字已 superseded-for-decision-use。新 policy 部署后尚无 selected intent，不能把历史重放
当作新 policy forward。

## Forward 与稳健性

| 检查 | 结果 |
|---|---|
| frozen admission forward | 是；各 profile 原 boundary 保留 |
| target-date block bootstrap | 全部交易层 CI 跨0 |
| same-row model vs market | 总体 model Brier/logloss 均劣于 market；各 profile CI 未形成一致胜出 |
| current bracket policy untouched forward | 8 candidates / 0 intents / 0 settled，NA |
| multiple testing | 8 profiles/slices，未校正；不从最优切片升 live |
| settlement sensitivity | 只认 exact market identity；263/263 market metadata 可取，0 identity mismatch/error |
| execution sensitivity | 没有真实 order/fill；V9 与 5-share sweep profile 的 capacity basis 不完全相同 |

## 三门与残余风险

| 门 | PASS/FAIL/NA | 证据 |
|---|---|---|
| significance | FAIL | actual +1.08% CI `[-20.36%,+19.89%]`；policy replay +4.83% CI `[-19.45%,+23.50%]` |
| same-denominator baseline | FAIL | 合计 model-market Brier/logloss delta `+.00308/+.02574` |
| forward | FAIL | 最多 11 settled dates；新 bracket policy 0 settled intents；未到30日 admission gate |

残余风险：one-sided/interval-censored quote coverage、Tokyo token identity blockers、V9 top-ask 无 5-share
capacity、selected-only小样本、8/23 未结算、8 个 profile 的多重观察。当前不应新增价格、side、城市或时段 gate。

## Output routing

- family living doc / registry：已回写 `WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md` 与 `WEATHER_STRATEGY_REGISTRY.md`。
- canonical machine format：`review.json`（内含全部85条 selection rows、outcome 与11条 suppressed rows）。
- artifact root：`/Volumes/jrs-archive/pm_agents/research/artifact_store/weather_city_intraday_probability/admission_forward_review/2026-08-23`。
- living report：首次把所有当前 active admission-forward profiles 按 exact market identity 同分母收口；后续同口径更新本文件，不新增平行日期报告。
- superseded files removed：无；旧结论保留并标记 superseded-for-decision-use。
- `check_weather_docs.py`：本报告自身的 dated-report/link hygiene 已通过；全仓仍因既有 AGENTS/CLAUDE contract、两个未跟踪旧报告链接及 HEAD 已有394个 `docs/analysis` hard-code 超过 ceiling 393 而 FAIL，未改这些无关用户工作。

在各 profile frozen boundary 至 2026-08-23 的 15,596 个 expression-checkpoint 固定分母上，WCIR
model 相对同 rows market 的 Brier delta 为 `+0.00308`；85 个 zero-notional selections 的 fee-adjusted
ROI 为 `+1.08%`（95% CI `[-20.36%,+19.89%]`），forward FAIL，结论 `inconclusive`，动作是继续
zero-notional shadow、不改 live。
