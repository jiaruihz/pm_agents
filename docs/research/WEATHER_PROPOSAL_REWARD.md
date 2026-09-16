# Polymarket Weather Proposal Reward

状态：`historical audit complete / zero-notional hot-window shadow deployed / collecting / no private key / no live proposal`。

这是独立的 oracle/proposal strategy family；复用 weather source、城市时区和
EventEnvelope 血缘，但不与天气 CLOB alpha 合并 PnL，也不复用交易信号作为
proposal eligibility。

## 当前规则与合同（2026-08-11 13:00 UTC 快照）

- 8 月 11 日、12 日各有 59 个 high/low temperature events，每个 event 有 11 个
  独立 UMA requests；每档都可单独 proposal、单独领取 reward。
- 两日每天 57/59 events 明确写明：必须等 resolution source 发布 next-date 第一条
  data point；香港高/低温 2 个例外是等 HKO 发布指定日期数据。
- 当前 54/59 events 使用 Wunderground Daily Observations；Istanbul、Moscow、
  Tel Aviv 使用 NOAA/WRH，香港使用 HKO。不能用同一 WU trigger 覆盖全部城市。
- 每 request：custom bond `$250`、final fee `$250`、总 deposit `$500`、reward
  `$0.60`、custom liveness `900s`。当前 liveness 是 15 分钟，不是旧口径的 2 小时。

## 历史分母与结果

可复跑入口：

```bash
.venv/bin/python scripts/analysis/proposal_reward/research_weather_proposal_window_v1.py \
  --min-proposal 2026-03-01T00:00:00+00:00 \
  --cutoff 2026-08-11T13:00:00+00:00
```

输出：`runtime/proposal_reward/weather_proposal_window_v1/`。

- UMA 两个官方 Polygon OOV2 subgraph 中 88,906 个包含 `temperature` 的已 proposal
  requests；其中成功解析为 Polymarket daily high/low temperature 的为 83,715
  requests / 7,650 events。其余是别类 temperature question 或无法映射城市时区，
  没有静默进入主分母。
- 当前 `$0.60 reward / $500 deposit / 900s liveness / standardized next-date rule`
  cohort 为 10,692 requests / 972 events，覆盖 2026-07-23..2026-08-11；每个 event
  都是 11 requests。
- 第一笔 proposal 相对 next local midnight：p10 `00:02:01`、median `00:08:45`、
  p75 `00:54:24`、p90 `00:56:50`。500/972 在 00:10 前，219/972 在 00:45–01:00，
  34/972 在 01:00 后，最晚 `02:34:05`。
- 这不是“source 发布后仍有这么久”。与现有 WU first-seen collector 可重叠的
  92 city-days 中，proposal 相对 source observation valid time中位数 `+3.63m`，
  但相对本项目 60 秒 polling collector 的 first-seen 中位数 `-0.57m`，91/92
  已先于 collector 下一次 poll 提交。负值表示竞争者更早看到/轮询，不证明其在
  source 发布前提交。
- 速度差主要在 polling/execution，不是 HTTP RTT：这 92 个 city-days 的 WU fetch
  latency 中位数 `371ms`、p90 `566ms`；但相对 proposer 上链时间，collector 中位数
  晚 `34.3s`、p90 晚 `59.3s`。91/92 落后，其中 18 个落后 0–10 秒、21 个 10–30 秒、
  44 个 30–60 秒、8 个超过 60 秒；只有 Miami 2026-07-15 在 block timestamp 口径下
  早 `1.16s`。block timestamp 不是 proposer 广播时间，因此这一例只能视为接近同场，
  不能视为已证明可赢。
- 11 档 batch span 的 p10/p50/p90 都为 0 秒；top 3 proposer 占 99.79% requests。
  这是自动化 first-seen race，不是人工在凌晨慢慢挑档。
- 10,692 requests 中 0 dispute。10,691 已 settlement 的 proposal→settlement
  中位数 980 秒，p90 2,730 秒；`$500` 通常可跨时区轮转，但每次只能抢一个 request。

## 当前动作

有工程空间，但尚无人工执行空间。先做只读/zero-notional source-first-seen shadow：

```text
precompute all 11 outcomes
-> source-specific next-date first-seen
-> onchain request still unproposed
-> record hypothetical submit latency and winning proposer
-> 15m settle/capital-release replay
```

触发必须来自指定 resolution source，不用固定 `00:xx`。Denver 的历史 proposal
常在 01:44–02:34，属于值得 forward 核实的候选异常；在拿到同日 source publication
first-seen 前，不能把 observation valid time到 proposal 的差值当成真实可抢窗口。

当前生产 data-feed 的 observation 周期是 300 秒、strategy snapshot 是 600 秒，且
历史 `weather_wu_history_latency_monitor` 已不在 production manifest；这些链路适合天气
事实采集，不是 proposal 竞速链路。现已新增下述独立、只读 event-window watcher；它不
改变通用 data-feed cadence，也不复用 CLOB executor。

## Zero-notional hot-window shadow v1

可证伪假设：历史 `34.3s` 中位落后主要来自旧 60 秒整轮 polling，而不是 WU endpoint
结构性晚于专业 proposer；改为持久连接和 source-specific hot window 后，本地
`hypothetical_ready_at` 能在一部分独立 city-days 早于 winner block timestamp。

部署对象：

```text
instance: polymarket_weather_proposal_reward_shadow_v1
mode: zero_notional_shadow; submission_enabled=false; private_key_access=false
source: WU/weather.com historical API, native C/F retained, direct pooled HTTP
market metadata: Gamma default route; UMA proposal label: official Polygon OOV2 subgraphs
lineage: EventEnvelope -> DecisionContext -> deterministic ModelOutput
         -> SignalCandidate -> zero-notional ProposalIntent -> ProposalObservation
raw: output/proposal_reward_shadow_v1/{market_discoveries,source_triggers,
     event_envelopes,decision_contexts,model_outputs,signal_candidates,
     proposal_intents,proposal_observations}.jsonl + latest.json/state.json
```

首轮只覆盖 8 个代表城市：Seoul、Beijing、Shanghai、Madrid、NYC、LA、Denver、
Wellington。high/low 同站同日合并为一次 source poll；历史窗口内 Seoul 1 秒，常规城市
2 秒，Denver 5 秒，预算上限 6,500 source polls/day，预计约 5,100/day。只写 aggregate
poll counters 和状态转换，不逐次落空 payload；raw 计划保留 90 天，未获删除授权前不自动
prune。

四时钟为 WU observation valid time、collector first-seen、hypothetical-ready、winner
proposal block time；另留本项目 proposal observed/ingested clock。block time 不是对手广播
时间，因此“早 1 秒”不视为已能赢。先积累 7–14 个 target-date blocks；若优化后仍有
90% 以上 scorable city-days 在 winner block 后才 ready，停止该 expression。否则继续
shadow 测 RPC/broadcast headroom、gas 和净 reward，不由少量领先样本直接升 live。

2026-08-11 14:09 UTC 已由 production controller 启动首个 runtime，loaded build
`68056035ec324305c3e60a88fb06dce1a63f2c11`；首轮 Gamma discovery 为 8 城、21 个
station-date watches，health 明示 `actual_orders=0`、`actual_deposit_usdc=0`、
`submission_enabled=false`。首个 forward 热窗口为 Seoul target 2026-08-11，
`2026-08-11 15:00–15:06 UTC`。
