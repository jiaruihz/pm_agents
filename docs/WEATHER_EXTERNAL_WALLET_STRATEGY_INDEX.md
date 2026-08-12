# Weather External Wallet Strategy Index

Status: current-reference

Updated: 2026-08-12 low-frequency peer cohort

Source of truth: external-wallet research register, not production strategy truth

本文件长期登记外部 weather 账户的地址、完整策略判断、证据边界和下一步研究方向。
单个 bracket 交易只作为 chronology；策略归因必须先合并同一 `city × target_date`
的完整互斥 ladder。

## 全钱包结论矩阵

这里是当前人工入口。后面的长段只保留三条代表性机制的完整说明；其余逐钱包报告是
时间点证据，不应再各自充当“当前策略结论”。所有钱包共同的结论是：历史 selected fills
可以证明某个账户曾经盈利，却不能提供我们的未选择机会分母、私有概率、挂单队列或同刻
可执行 baseline，因此没有一条可以直接复制成 live 策略。

| 钱包 / 家族 | 已吸收的耐久机制 | 当前可复用部分 | 当前状态 / 详细证据 |
|---|---|---|---|
| 2026-08-12 新 26 地址低频 cohort | 低交易笔数、小 event capital 的 sparse exact-bracket selector；首选 `fildoro` D-1 NO，次选 `0xfc179…886a` D-2/D-1 NO | 接现有 D-1 probability + first_seen/PIT；每 city-day 最多一个 fee 后 max-EV YES/NO，并与 market/full-distribution/0x43cb strip 同分母比较 | `research-only / 26 complete / no-live-change`；见下文耐久结论，机器明细见 JRS manifest `weather_wallet_peer_scan_20260812_v1.json` |
| `yourthos` | Seoul/RKSI source-event 后动态切换 current NO、current YES 与 upper YES，并用 NegRisk/SELL 释放资金 | source-event 后重算整条 ladder；钱包方向只作 confirmation/veto | `inconclusive / 秒级执行不可跟单`；见下文 |
| `Gptball` | Chengdu/ZUUU 日内 path-state router，NO pass-through 后切 current YES | 单城 source/basis adapter、状态切换而非单腿模仿 | `inconclusive / research-only`；见下文 |
| `0x43cb` | 全球 target-day 连续 YES strip：range 底仓 + 中心加权，主要持有到结算 | full-ladder bounded-support probability 与 basket accounting | `historically profitable / transferable alpha unproven`；见下文与 [架构推断](analysis/2026-08/2026-08-05-research-43cb-city-model-architecture-v1.md) |
| `0x919698…d934` | current/neighbor YES/NO + 高确定性 NO + maker/SELL 的动态 ladder | 完整 ladder 和主动退出的研究样板 | `historically profitable / copying inconclusive`；[机制](analysis/2026-07/2026-07-29-research-wallet-0x919698-weather-strategy-v1.md) · [绩效](analysis/2026-07/2026-07-29-performance-wallet-0x919698-weather-v1.md) |
| `WeatherHK2` | 华南区域 D-1 库存，D0 随路径在相邻 YES/NO 间换档并 SELL/MERGE | 区域 source-basis、库存状态机 | `research-only`；[生命周期](analysis/2026-07/2026-07-30-research-weatherhk2-strategy-lifecycle-v1.md) · [案例](analysis/2026-07/2026-07-30-lineage-weatherhk2-five-case-v1.md) |
| `jjavi` | D-1/D0 欧洲概率分布与相邻档轮动 | 分布更新和主动退出机制 | `mechanism reference / execution complex`；[全历史对照](analysis/2026-07/2026-07-31-lineage-weather-wallet-remaining-four-v1.md) |
| `badatmath` | 提前建立宽 YES distribution 与 cheap-tail convexity | 概率分布和 tail sizing 研究 | `mechanism reference / 不复制高频执行`；[全历史对照](analysis/2026-07/2026-07-31-lineage-weather-wallet-remaining-four-v1.md) |
| `HighTempTation` | target-day 高速 NO inventory 后近确定性退出 | 只作 terminal latency 上限 | `non-copyable latency benchmark`；[五钱包对照](analysis/2026-07/2026-07-30-performance-weather-wallet-five-way-v1.md) |
| `MidYes56b` | target-day mid-price YES selector / 路径换档 | 只作 selector case source | `inconclusive / 收益集中`；[全历史对照](analysis/2026-07/2026-07-31-lineage-weather-wallet-remaining-four-v1.md) |
| `LMVM` | D-2/D-1 单档 YES，短持仓主动 repricing SELL，极少依赖 settlement | 固定 horizon 的 repricing 生命周期 | `signal unknown / mechanism reference`；[profile](analysis/2026-08/2026-08-04-lineage-lmvm-repricing-profile-v1.md) |
| `neo7777` | 多城市 mixed YES/NO，repricing 与 settlement hold 混合 | position-state 拆分方法 | `mixed account / 不归为 LMVM-like`；[profile](analysis/2026-08/2026-08-04-lineage-neo7777-repricing-profile-v1.md) |
| `balthazar` | full-NO-set + NegRisk conversion / SPLIT / MERGE | full-set conversion accounting | `structural research only`；[新增五钱包总账](analysis/2026-07/2026-07-31-lineage-weather-wallet-new-five-v1.md) |
| `opopv.` | D-2 起长期 mixed inventory、maker-style 累积与双向换手 | 库存/退出状态参考 | `historically positive / too complex to copy`；[profile](analysis/2026-08/2026-08-04-lineage-opopv-repricing-profile-v1.md) |
| `macau.weather` | 香港集中式路径交易 | 与 WeatherHK2 做区域机制对照 | `concentrated / unstable`；[新增五钱包总账](analysis/2026-07/2026-07-31-lineage-weather-wallet-new-five-v1.md) |
| `0x496f…` | D-2/D-1 多 NO ladder、长时间 inventory、conversion/MERGE 和 SELL | 完整库存与 NegRisk 会计 | `historically profitable / baseline unavailable`；[profile](analysis/2026-08/2026-08-04-lineage-wallet496f-repricing-profile-v1.md) |

统一研究结论：优先复用的是 `full-ladder probability + position state + executable
exit accounting`，不是地址跟单。钱包报告中的价格带、城市偏好和持仓时长都是描述性
切片，除非在我们的全机会分母、PIT book 和 frozen forward 上重现，否则不进入 eligibility。

## 已完整研究

### 2026-08-12 低频 cohort

- 分母：从 WEATHER leaderboard 的 ALL / MONTH / WEEK 各 250 行排除既有研究对象，
  profile 120 个新地址，筛出 26 个做完整历史；26/26 完成，合计 87,026 条
  weather activity、13,667 个 cashflow-complete portfolios，另有 5 个 portfolio
  缺 Gamma metadata，作为 coverage gap 保留。
- 首选机制 `fildoro`（`0x180e62e6f035dbf69118a2306df25d28762129df`）：
  跨城市 D-1 NO exclusion，802 events / 104 target dates / 30 cities；每 event
  中位 1 笔、成本 `$43.47`，selected-fill ROI `+7.89%`，target-date block CI
  `[+5.19%, +11.06%]`，去掉 top-5 盈利事件仍 `+5.53%`。
- 第二 challenger `0xfc17946b7bedc82eb11329e67d0f2d3a76c1886a`：D-2/D-1
  NO-only，81 events / 36 dates / 27 cities；中位 1 笔、成本 `$48.83`，
  selected-fill ROI `+19.72%`、CI `[+2.81%, +32.60%]`，但独立日期较少。
- 次级机制只作 control：LA D-1 single-YES、跨城市 D-1/D0 single-YES、D0
  sparse selector、Kuala Lumpur 单城 router 和 0x43cb bounded strip；不各建 runner。
- 耐久研究动作：复用同一个 D-1 runner，在同一 PIT city-day 分母比较 market、当前
  full-distribution、sparse NO、sparse YES、bounded strip 五臂；每 city-day 最多表达
  一个 fee 后 max-EV exact YES/NO，至少等 15 个全新 settled target dates。
- 边界：上述 ROI 是钱包 selected fills，没有我们的全机会分母、私有信号、未成交单和
  同刻可执行 baseline；只能作为 expression prior，不能用地址成交触发或升级 live。
- 机器明细已归档到
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/manifests/weather_wallet_peer_scan_20260812_v1.json`；
  原始 immutable snapshots 和 per-wallet replay 仍在
  `/Volumes/jrs-archive/pm_agents/research/external_wallet_weather/`。

### yourthos

- address：`0x4f1164d1531b8fb77919df285c576628318553b0`
- 核心市场：Seoul / Incheon，结算站 RKSI；高度集中，不是广撒网。
- 完整策略判断：以 RKSI/AMOS source event 驱动的日内方向交易，加
  NegRisk conversion 和确定性资金释放；不是传统双边做市，也不是 99¢ carry。
- 主要表达：早期以 current NO 表达“还会跨下一档”，后期以 current YES
  表达“running max 已锁定”，并用 next/upper YES 管理尾部。必须把同城同日
  多档合并后理解，单看一腿会把动态换仓误判成长期看多或看空。
- 入场证据：30 个 target dates；91.8% BUY cost 在 target day，81.4% 在当地
  10–18 时。7 个有完整 first-seen 的 current-NO 日期中，6/7 首次交易在新
  AMOS snapshot 后 30 秒内，但只有 1/7 已发生 persistent integer cross。
  更符合“新观测到达后重算 next-cross / final-exact 概率”，而非“跨档后无脑买 NO”。
- 退出证据：SELL cash 的 95.1% 在 `>=0.99`，主要是跨档后 NegRisk conversion、
  卖确定性 YES、提前释放 collateral；失败尾腿低价清理或到期。
- 自动化：独立交易中位间隔 22 秒，23.5% `<=2s`，峰值 25 tx/min。
  `bot execution` 高可信，完全自动信号为中高可信，仍可能人工设 regime/预算/启停。
- JRS 全历史快照：`20260729T145856Z`，44/44 UTC days、1,944 全账户
  activity rows、1,817 weather rows、30/30 event metadata、330 Gamma markets，
  已生成完整 ladder lifecycle analysis。
- 描述性绩效：29 个 cashflow-complete settled dates 中 24 胜、5 负，
  public cashflow `+$2,539.18 / $62,222.29 BUY cost`，turnover ROI `+4.08%`；
  target-date bootstrap 95% CI `[-18.35%,+23.96%]`。平均亏损约为平均盈利
  3.55 倍，最大回撤 `$5,649.25`，结论 `inconclusive / shadow_candidate`。
- 可复制性：不建议直接跟单；钱包公开 activity 是成交后信息，而且最有价值的
  AMOS edge 已在秒级执行。可把其动作作为外部 confirmation/veto 特征。
- 研究方向：
  1. RKSI 全 ladder PIT posterior，每次 AMOS first-seen 同时更新
     `P(cross in 30/60m)` 与 `P(final exact)`；
  2. 加 RKSS spatial gradient、remaining heat、running-max age、海陆风/云雨状态；
  3. 钱包动作做 A/B/C/D zero-notional overlay，不作主 trigger；
  4. 固定全观测分母、真实 ask/depth/fee，至少 15 个 forward target dates。
- 完整报告：
  - [交易全貌复盘](analysis/2026-07/2026-07-29-research-yourthos-rksi-complete-trade-replay-v1.md)
  - [触发条件与自动化推断](analysis/2026-07/2026-07-29-research-yourthos-rksi-trigger-and-automation-inference-v1.md)
  - [自建跨档概率 vs 跟钱包](analysis/2026-07/2026-07-29-research-rksi-cross-probability-vs-wallet-follow-v1.md)
  - [yourthos vs Seoul cross-NO 四次完整生命周期](analysis/2026-07/2026-07-29-research-yourthos-vs-cross-no-lifecycle-v1.md)

### Gptball

- address：`0xcac70909a505ed6f28b1b59a79bcc99ff22937d8`
- 核心市场：China，尤其 Chengdu / ZUUU；86.94% BUY cost 在 Chengdu，
  中国大陆占 99.99%，effective city count 仅 1.31。
- 完整策略判断：成都日内温度路径状态交易。先用 current/upper NO 表达
  pass-through 或排除某档；随着 running max 上移，再切换到 current YES
  锁定 final exact。不是传统做市，不是单方向 NO，也不是 99¢ carry。
- 入场证据：72.85% BUY cost 在 target day；其中 63.17% 在当地 14–18 时；
  79.97% 成交在 20–80¢，`>=95¢` 只有 3.36%。PIT 可对齐资金中
  current YES 50.77%、current NO 20.15%。
- 退出证据：48.78% event 有主动 rebalance，但 SELL 仅 38/610 trade rows；
  典型行为是多笔 taker 加仓、少数大额卖出有利腿，核心腿也会留到 settlement。
- 自动化：550 个独立 transactions，中位间隔 11 秒，49.0% `<=10s`，
  峰值 18 tx/min；maker 推断仅 0.33%。执行 bot 高可信，非 market making。
- 已结算描述性绩效：全 weather 39 events / 16 dates，turnover ROI +16.51%，
  target-date block-bootstrap 95% CI `[-5.62%, +35.67%]`；Chengdu
  16 events / 16 dates为 +15.20%，CI `[-10.22%, +35.52%]`。
  显著性未通过，且无同分母 market baseline 与 frozen forward，结论 `inconclusive`。
- 可复制性：比 yourthos 的秒级 AMOS source-event 更可能有分钟级研究价值，
  但不能复制单腿；它会在同一天从 NO 切 YES。钱包动作只宜作 state-router 的
  外部特征。
- 研究方向：
  1. Chengdu/ZUUU 全 ladder `state-switching landing router`；
  2. 同时估计 current invalidation、next-cross、final exact，按 residual 选择表达；
  3. 接 running max、remaining heat、forecast peak、云雨/风、附近站与更快中国源；
  4. 做无钱包、钱包 confirmation、反向/延迟 placebo 的同分母 A/B；
  5. 真实 ask/depth/fee，至少 15 个 frozen-forward target dates。
- 完整报告：
  - [Gptball 成都完整策略复盘](analysis/2026-07/2026-07-29-research-gptball-chengdu-complete-strategy-v1.md)

### 0x43cb：全球 YES-strip / bounded-range accumulator

- address：`0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df`
- 全历史证据层：已绕过 activity 5,500-row 上限，覆盖账户最早公开记录
  2025-10-28 至 2026-07-29；全账户 353,096 rows、weather 212,340 rows、
  2,989 events、18,392 conditions。Gamma 完整 ladder 2,987/2,989；
  唯一缺口为已下架的 Austin / Dallas 2026-07-09，已单列，未伪装完整。
- 核心市场：没有单城集中；全历史 effective city count 32.90，第一城市
  Shanghai 仅 6.68%，top-3 cost share 18.35%，覆盖 50 城全球 WU 机场站。
- 完整策略判断：target day 根据观测下限、forecast support 和市场分布，买一段
  连续 exact-bracket YES strip。常先铺接近等 shares 的 bounded-range 底仓，再
  对中心档加权；主要持有到 settlement。不是多个独立 YES 信号，也不是纯
  observed-floor threshold、纯 underround 或传统双边做市。
- 入场证据：全历史 99.98% BUY cost 在 target day，86.81% 在当地 10–18 时；
  event 首笔中位 11:59，cost-weighted fill 中位 14:05。2,917/2,989 是多档
  YES strip，YES 占 BUY cost 99.71%，连续 strip share 98.46%；档位中位 6，
  ladder coverage 中位 54.55%。
- PIT 证据：可对齐 BUY cost 中 44.46% 的所买 bracket 在当时 running max 上方，
  32.67% 包含 current，只有 6.13% 在下方；source age 中位数 33.78 分钟，
  `<=10m` 仅 6.14%。更像 forecast/market distribution，而非抢单一快源。
- 执行证据：每 event unique BUY transactions 中位 34，约 7 个 `>60s` bursts、
  5 个 `>5m` sessions，first→last BUY 中位 169 分钟；执行 bot 高可信。
  SELL 仅 329 events / 645 rows，proceeds 只占 BUY cost 0.55%，主退出仍是 settlement。
- 生命周期证据：first BUY→REDEEM 中位 13.93h，last BUY→REDEEM 10.60h；
  88.62% events 无主动 SELL 直接进入 settlement。range base fraction 中位
  54.74%，modal overweight 45.26%。3–4 月宽 8 档/重中心/早入场，6–7 月缩到
  5–6 档、common base 升到 60–70%，策略存在版本漂移。
- 盈利证据：2,966 个 cashflow-complete portfolios / 113 dates 的 public
  cashflow PnL `+$26,443.56 / $544,942.37 BUY cost`，turnover ROI +4.85%，
  target-date block-bootstrap 95% CI `[+4.07%, +5.64%]`。这是 selected wallet
  fills 的描述性盈利，不是 opportunity alpha。官方 WEATHER leaderboard 为
  ALL `+$31,581.99 / $3.076m volume`（PnL/volume 1.03%），MONTH 0.94%，
  WEEK 1.05%。
- 可复制性：机制比单城快源更可迁移，但必须同时取得完整 ladder 的 ask/depth、
  official fee 和逐腿 fill；复制单腿会把 bounded-range 变成裸 exact risk。
- 研究方向：
  1. `feasible-support YES strip / bounded-range residual` 全 ladder 头；
  2. 比较 observed-floor、forecast-support、market-only 和 wallet-overlay 四臂；
  3. 底仓与中心加权分开评估，报告 min-band payout / basket cost；
  4. maker fill/queue 与未原子成交风险单列；
  5. 至少 15 个 frozen-forward target dates，按 target_date bootstrap。
- 完整报告：
  - [0x43cb 全球 YES-strip 完整策略复盘](analysis/2026-07/2026-07-29-research-43cb-global-yes-strip-complete-strategy-v1.md)
  - [0x43cb 全历史 ladder 生命周期 v2](analysis/2026-07/2026-07-29-research-43cb-full-history-ladder-lifecycle-v2.md)
  - [0x43cb 三个典型 city-day 的逐 phase 时间线](analysis/2026-07/2026-07-29-research-43cb-current-strategy-case-timelines-v1.md)

## 使用边界

- 外部钱包公开 activity 是成交后公开数据，不能证明其私有信号或下单时可见盘口。
- `public cashflow + position value` 是外部账户重建口径；已结算研究只纳入 Gamma
  已确认 winner 的 event，不能与本账户 canonical `fact_trades` 混用。
- 本索引记录的是研究线索，不是 live allowlist。任何复制或新策略仍须通过
  significance、same-denominator baseline、frozen forward 三门。
