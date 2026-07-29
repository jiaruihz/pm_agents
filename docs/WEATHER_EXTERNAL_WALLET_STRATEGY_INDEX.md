# Weather External Wallet Strategy Index

Status: current-reference

Updated: 2026-07-29

Source of truth: external-wallet research register, not production strategy truth

本文件长期登记外部 weather 账户的地址、完整策略判断、证据边界和下一步研究方向。
单个 bracket 交易只作为 chronology；策略归因必须先合并同一 `city × target_date`
的完整互斥 ladder。

## 已完整研究

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
- 核心市场：没有单城集中；最新样本 effective city count 23.73，第一城市 Busan
  仅 8.96%，覆盖全球 WU 机场站。
- 完整策略判断：target day 根据观测下限、forecast support 和市场分布，买一段
  连续 exact-bracket YES strip。常先铺接近等 shares 的 bounded-range 底仓，再
  对中心档加权；主要持有到 settlement。不是多个独立 YES 信号，也不是纯
  observed-floor threshold、纯 underround 或传统双边做市。
- 入场证据：最新 50 个完整 events 中 49 个为连续 YES strip，平均 4.52 档、
  覆盖 ladder 41.09%；最新资金 100% target day，89.76% 在当地 10–18 时。
  历史精确抽样 30 个 target dates 中 27 个为 YES strip，结构长期稳定。
- PIT 证据：可对齐 BUY cost 中 44.46% 的所买 bracket 在当时 running max 上方，
  32.67% 包含 current，只有 6.13% 在下方；source age 中位数 33.78 分钟，
  `<=10m` 仅 6.14%。更像 forecast/market distribution，而非抢单一快源。
- 执行证据：最新样本 1,303 个独立 transactions，中位秒级间隔为 0 秒，
  69.35% `<=2s`，峰值 43 tx/min；同时间覆盖的 public trades 推断约 27.48%
  maker。执行 bot 高可信，可能采用 maker-assisted accumulation，但几乎不 SELL，
  因此不是双边 MM。
- 盈利证据：官方 WEATHER leaderboard 截至 2026-07-29 为
  ALL `+$31,581.99 / $3.076m volume`（PnL/volume 1.03%），MONTH 0.94%，
  WEEK 1.05%。最新两日 39 个已结算 exact events 重建 ROI +7.11%，但只有
  2 个 target dates，不能作显著性或 forward 证据。
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

## 使用边界

- 外部钱包公开 activity 是成交后公开数据，不能证明其私有信号或下单时可见盘口。
- `public cashflow + position value` 是外部账户重建口径；已结算研究只纳入 Gamma
  已确认 winner 的 event，不能与本账户 canonical `fact_trades` 混用。
- 本索引记录的是研究线索，不是 live allowlist。任何复制或新策略仍须通过
  significance、same-denominator baseline、frozen forward 三门。
