# Busan cross NO 2026-07-29 日内复盘 v1

## 结论

今天的超额收益主要来自一个罕见地适合 cross NO 的客观窗口：Busan routine METAR 最高温连续按
`36 → 37 → 38 → 39°C` 抬升，AMOS runway 快源在每档前后持续确认，同时盘口在 37、38 档明显来不及重定价。
系统改进的作用不是“创造了这段行情”，而是把它完整变成了持仓：`persistent_candidate_margin_v5`
避开旧版 terminal false cross，fresh-book + depth-adaptive taker/maker + hot retry 又救回了 38 NO
首轮盘口消失后的成交。

因此：

- **收益幅度以客观顺风为主**：今天三档加权成交价约 `0.649`，明显低于 v5 以来 Busan 已结算 live
  成交均价约 `0.887`。
- **三档都赶上不是纯运气**：确认、重试和 exact signed share cap 都按设计工作，尤其 38 NO。
- **不升级策略状态、不扩 size**：WU settlement 尚未发布，Busan source→settlement alignment 仍未过研究门；
  今天是极佳正案例，不足以证明 terminal false cross 已解决。

## 口径

- target slice：Busan、`metar_prev_no_exact` / `persistent_candidate_margin_v5`，2026-07-29。
- 今日执行 grain：actual fill / exact bracket；历史对照 grain：settled live market 与 active target date。
- 今日分母：3 个首次确认且进入 live execution 的 bracket；不是用结算后赢家倒推。
- 历史对照：v5 后 2026-07-18—27 的实际 Busan live fills；旧规则失败窗为 2026-07-11—14。
- 机会与成交分开：今日 signal funnel 为 3 个首次 bracket cross，evidence funnel 为
  3 个 fresh-book opportunity、3 个实际持仓、0 个 open order、0 个已发布 WU settlement。

## 今日逐档血缘

| bracket | 首次确认（Busan） | 快源 / routine METAR 状态 | 实际持仓 | 成本 |
|---|---:|---|---:|---:|
| 36 NO | 13:42:52 | AMOS 36.9°C；METAR running max 36°C | 15.00000 | $14.35000 |
| 37 NO | 14:40:00 | AMOS 38.0°C；METAR running max 37°C | 8.00000 | $5.20000 |
| 38 NO | 15:50:26 | AMOS 38.7°C；METAR running max 38°C | 13.81395 | $4.33395 |
| 合计 | — | 07Z routine METAR 后 running max 39°C | 36.81395 | $23.88395 |

逐档执行：

- 36 NO：10 shares @ 0.96 taker，5 shares @ 0.95 maker。
- 37 NO：8 shares @ 0.65 taker。
- 38 NO：0.15 的首轮可见深度在提交时消失，两次 taker 均为 0 fill；hot retry 随即挂
  7 shares @ 0.14，最终形成 5.81395 shares 的低价持仓；下一次 source event 又以
  8 shares @ 0.44 补齐剩余 cap。
- 三个 token 当前均无 open order；最新 CLOB best bid 约 0.999。

按 wallet position API 的最新 mark：

- current value：约 `$36.7955`
- 未结算 MTM：约 `+$12.9115`
- open-cost ROI：约 `+54.1%`
- 若三档最终均按 $1 结算，gross profit 约 `+$12.9300`

以上不是 realized PnL。官方 Weather taker fee 曲线下的机械估算约 `$0.2088`，但 canonical fill fee
coverage gate 当前未通过，因此不把估算后的 `$12.72` 当作已核准的 fee-adjusted live_real PnL。

## 为什么今天比以前好

### 1. 客观路径特别理想

今天不是一次刚刚越线后回落的单点 cross，而是连续数小时的单调升温：

`30 → 33 → 34 → 34 → 36 → 37 → 38 → 39°C`

routine METAR 每小时确认新高，AMOS 在档位附近有连续高频观测；39°C 打印后温度转为 38、37°C，
所以 36/37/38 三个 exact-bracket NO 在 observation layer 已经胜出。相比之下，7 月 11、13、14 日
旧版亏损分别来自 34.5→最终 34、31.7→最终 31、30.6→最终 30 这类 terminal false cross。

### 2. 盘口给了异常大的价格空间

v5 后历史成交多集中在高价 NO，Busan 7 月 18—27 日 14 个已结算 live market 的总成本约
`$153.415`、fee-adjusted PnL 约 `+$18.858`、ROI 约 `12.3%`，成交均价约 `0.887`。

今天一口气出现三个 bracket，且 37、38 NO 成交在 0.65、0.14/0.44，使加权成交价降到约 `0.649`。
因此今天的收益不是单靠“多赢两档”，而是市场在连续跨档时重定价明显落后。

今日未结算 MTM 约为此前 Busan v5 已结算累计利润的 `68%`；也是此前最佳单日
（7 月 22 日约 `+$4.677`）的 `2.76×`。这说明异常值主要来自价格和一次三档齐发，而不是已证明的胜率跃升。

### 3. 系统确实把机会执行完整了

新版做对了三件具体事情：

1. `persistent_candidate_margin_v5` 要求至少两个不同观测达到 `x.5`、其中一个达到 `x.7`，
   比旧版单点/末端 cross 更能抵抗 false cross。
2. 每次事件重新读 fresh book，并按可见深度拆 taker/maker；没有把 0.15 的陈旧 ask 当成已成交。
3. hot retry 与 exact signed share cap 配合：38 NO 首轮深度消失后仍恢复了 5.81395 @ 0.14，
   下一轮只补剩余份额，没有因为重试重复超买。

## 历史对照与研究状态

- 旧规则 7 月 11—14 日实际 live：总成本约 `$27.40`、PnL 约 `-$15.53`、ROI 约 `-56.7%`；
  三次主要失败均为 source terminal false cross。
- v5 后 7 月 18—27 日：9 个有成交日、14 个已结算 market，全部盈利，但样本仍小。
- 今天：3 个未结算 market；routine METAR running max 39°C 已使 36/37/38 NO 在观测层胜出，
  但仍须等待 WU canonical settlement。

三门判断：

- significance：**FAIL / 样本不足**，v5 已结算仅 14 markets、9 个成交日。
- baseline：**FAIL / 缺少同分母 market probability baseline**。
- forward：**仍为 running probe**，连续正向但不足以确认 alpha。

策略状态维持 `inconclusive / live trial`；不因今天大赚而扩仓。

## 数据质量说明

- production manifest `status=healthy`；仓库兼容入口与 JRS physical canonical DB 为同一 device/inode。
- 已执行 incremental dashboard refresh；N100 历史 rsync 不可用，但 Mac 当前生产 raw 正常。
- 刷新前 `fact_trades` 漏了 38 NO 的 5.81395 @ 0.14；authenticated CLOB sync 已直接回连正确的
  maker order `0x45de…8374d`，记录 5.81395 @ 0.14，与 wallet position 一致。
- 随后的 fact rebuild 在 JRS SQLite 大表索引扫描阶段持续 I/O wait；采样确认尚未进入 fact 表写入阶段，
  已请求中止。因此 canonical `fills` cache 已补，`fact_trades` 尚未重物化，不能把刷新说成全部完成。
- fill coverage gate 在刷新前 `gate_pass=false`，原因是全局仍有 unknown fee lineage 与 matched-taker
  zero-fee rows。因此本文只发布 wallet MTM 与 gross terminal estimate，不发布核准的 fee-adjusted realized PnL。

## 动作

1. live trial 参数和 size 保持不变。
2. 把今天标为 `monotonic multi-cross + stale-book hot-retry` 正案例，与 7 月 11、13、14 日
   terminal false cross 逐日对照。
3. 继续积累 source→routine METAR→WU native settlement basis、各档 first-seen lag、盘口重定价速度，
   再判断 v5 是真正消除了 false cross，还是只碰到了连续升温的有利样本。
