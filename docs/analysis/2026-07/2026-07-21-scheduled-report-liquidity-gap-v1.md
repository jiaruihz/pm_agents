# 定时报文窗口流动性空洞研究 v1

Status: `execution_shadow_candidate / no live change`

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源 | dense：`runtime/weather_edge_v1/remote_pm_agent/source_orderbook_timing/{sources,books}.jsonl`；current sanity：`/Volumes/jrs/weather_data_feed_service_runtime/{output/source_events, targeted_output/orderbook_snapshots, full_ladder_output/orderbook_snapshots}` |
| 数据快照时间 | dense 文件截止 2026-06-30；JRS source current 截止 2026-07-21 01:27:55（Asia/Shanghai），current report generated 01:30:17 |
| 记录行数 | dense raw book `3,807,384`，YES ok `1,756,348`，two-sided `685,451`；JRS current raw book `567,053`（首次 sanity run） |
| unsettled 占比 | `NA`：本轮主 target 是 PIT 流动性，不以 settlement 为 label |
| missing_bracket | `NA`：未 join settlement；不能发布 outcome PnL/ROI |
| 是否同步 / 重建 DB | 否；直接读取当前 JRS raw 与已有分钟级 timing journal，没有改 `runtime/weather.db` |

## 结论与动作

这个现象是真的，但正确表达不是“宽 spread 时直接买”，而是：**已有独立方向信号时，定时报文窗口可能提供 passive maker 的价格改善；taker 追单没有优势。**

- 跨 41 城、13 个独立 target dates、5,017 个可配对 report events，报文前 8 分钟相对更早的 `[-15,-8)` 窗口，活跃 exact-bracket YES token 的 spread 平均扩大 `0.237c`，date-block 95% CI `[+0.110c,+0.373c]`。
- 同期最薄一侧 top depth 平均减少 `1.00 shares`，95% CI `[-1.78,-0.37]`；双边报价率下降 `0.33pp`，95% CI `[-0.51pp,-0.20pp]`。这与做市撤单/缩量一致。
- 报文后 8–15 分钟，spread 相对报文前 8 分钟平均收窄 `0.522c`，95% CI `[-0.684c,-0.356c]`，说明空洞会恢复。
- `bid+1 tick` 相对 post-window mid 的 maker markout **理论上界**为 `+4.05c/share`，95% CI `[+3.66c,+4.47c]`；但没有 queue/fill 证据，不能当收益。
- 同一批事件若直接吃 ask，扣 Weather taker fee 后的 15 分钟 markout 为 `-1.56c/share`，95% CI `[-1.91c,-1.19c]`。因此不做“宽盘 taker”，不改 live。

动作：把它登记为 `execution_shadow_candidate`，下一步采集 full L2 + trade tape + queue-ahead 的 zero-notional shadow；只对**已经存在且在窗口前冻结的方向信号**做 execution A/B，不把报文时间或宽 spread 变成新的 alpha hard gate。

```text
significance=PASS（跨城市 liquidity H1）
baseline=PASS（同 token、同 report event 的 pre_far 配对基准）
forward=FAIL/NA（current JRS 只作稀疏 sanity，尚无 frozen queue/fill forward）
conclusion=shadow_candidate（execution only，不是 confirmed trading alpha）
```

## Target

```text
固定既有 P(exact outcome | PIT weather/path state)，检验定时报文前后的 execution cost / fill quality
是否相对同一 token 的普通时段改善，而不是用报文时间预测天气结果。
```

- physical target / exact-bracket semantics：方向信号仍预测最终最高温的 exact bracket；`X YES` 只有最终最高温正好为 X 才赢。定时报文窗口只是执行 overlay。
- grain：`source report event × target_date × city × YES/NO token × PIT book snapshot`；统计先聚合 token，再聚合 report event，避免同一报文的多 bracket 伪增样本。
- decision timestamp：方向概率必须在 `report_ts - 8m` 前冻结；盘口状态按实际 `book_fetch_end_utc/fetched_at_utc`。
- source anchors：同时对齐 nominal `source_report_ts_utc` 与本机 `first_seen_ts_utc`。主 H1 用 nominal report time，因为它事前可知。
- executable expression：`maker_limit = min(best_ask-tick, best_bid+tick, p_signal-min_edge)`；maker fee baseline 为 0，rebate 只作单列 upside。taker 使用官方 `0.05 * shares * price * (1-price)`。
- primary metric：真实/保守模拟 fill 后的 fee-adjusted edge 与 PnL delta；本轮只有 liquidity delta 和 15m markout，没有 settlement ROI。

## Data integrity / PIT

| 项目 | 值 |
|---|---|
| raw source and coverage | dense source events `18,231` / `42` 城 / `14` dates；dense books `3,807,384` rows |
| forecast issue/run/hash/age | 不用于生成新方向；未来 A/B 必须引用既有 `p_signal` 的 frozen lineage |
| source first-seen/cadence | aviationweather METAR；dense first-seen lag p25/p50/p75=`3.22/5.00/6.57m` |
| source-to-settlement basis | 本轮不把 METAR 当 settlement；Atlanta terminal false cross 仍是所有 source-event 方向研究的 negative control |
| book freshness / archive bias | dense timing journal 为分钟级 PIT；current JRS 为 15/30m 稀疏 sanity，不能独立判断 1–3m 撤单 |
| label availability | liquidity H1 完整；settlement、真实 maker fill、queue position 缺失 |

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| raw universe | raw book row | 3,807,384 | 13 book dates |
| YES ok | token snapshot | 1,756,348 | 13 |
| active token universe | token（cohort 内至少一次 two-sided mid 5–95c） | 1,607 tokens | 13 |
| report-aligned observations | token snapshot × report/detect anchor | 902,357 | 13 |
| paired H1 selection | report event（pre_far 与 pre_near 都有） | 5,017 | 13 |

这里没有“方向 signal selection”：本轮只验证 execution mechanism。后续必须从 `fact_signal_candidates` 取固定机会分母，不能只记录最终看对的方向。

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT source | unique report event | 18,231 | 14 | 42 cities |
| PIT quote | report event | 7,340 | 13 | 41 cities 有 book |
| paired liquidity H1 | report event | 5,017 | 13 | 要求 pre_far + pre_near |
| maker markout upper bound | report event | 4,506 | 13 | 无真实 fill/queue |
| settlement | exact bracket | 0 | 0 | 本轮未 join，不能报 ROI |
| actual fill | fill | 0 | 0 | zero-notional research |

## Wide-denominator sanity

### Nominal report time（事前可知）

| metric | paired events | mean delta | date-block 95% CI | 解释 |
|---|---:|---:|---:|---|
| spread，pre_near - pre_far | 5,017 | +0.237c | [+0.110c,+0.373c] | 报文前临近窗口变宽 |
| maker improvement，pre_near - pre_far | 5,017 | +0.237c | [+0.104c,+0.370c] | 可挂入 spread 的理论空间增加 |
| min top depth，pre_near - pre_far | 6,533 | -1.00 shares | [-1.78,-0.37] | 最薄侧缩量 |
| two-sided rate，pre_near - pre_far | 6,533 | -0.33pp | [-0.51pp,-0.20pp] | 整侧消失略增 |
| spread，post_far - pre_near | 4,648 | -0.522c | [-0.684c,-0.356c] | 报文后恢复 |

### First-seen time（我们真正拿到内容）

first-seen 前 8 分钟的 spread widening 只有 `+0.073c`，95% CI `[-0.002c,+0.137c]`，弱于 nominal report anchor；但双边报价率下降 `1.24pp`，随后 spread 收窄 `0.464c`。解释是市场按已知发布时刻提前防守，而我们当前 API first-seen 中位晚约 5 分钟；不能等本机看到报文才开始挂单。

### July current sanity（稀疏，不作 frozen forward）

JRS current 有 44 城 / 16 dates / 567,053 raw book rows，但 15/30m cadence 只给 nominal-report H1 留下 81 个完整 pre-window 配对：pre-report spread delta 为 `-0.017c`，CI `[-0.918c,+1.355c]`，无法复核细粒度 widening；post-report recovery 仍为 `-1.280c`，CI `[-2.122c,-0.349c]`。maker markout 上界 `+6.33c/share`，taker fee-adjusted markout `+0.60c/share` 但 CI `[-0.62c,+1.69c]` 跨 0。它支持 recovery/采集价值，不足以通过 forward execution gate。

## Ankara 个案

Ankara 的固定 METAR report minute 确认为 `:20/:50`。dense journal 有 `195` 个 report events / `10` dates，其中 `146` 个可做 pre-window spread 配对：

| metric | Ankara point | date-block 95% CI | 结论 |
|---|---:|---:|---|
| pre-report spread widening | +0.272c | [-0.096c,+0.650c] | 同方向但单城不显著 |
| pre-report min top-depth change | -3.82 shares | [-6.44,-0.98] | 缩量显著 |
| post-report spread recovery | -0.631c | [-1.482c,+0.061c] | 点估支持，CI 跨 0 |
| maker markout upper bound | +6.57c/share | [+4.65c,+9.13c] | 只是无 fill 上界 |
| taker fee-adjusted markout | -0.15c/share | [-1.60c,+2.04c] | 没有 taker edge |

JRS 2026-07-20 sanity 中，Ankara 活跃档在多次 `:20/:50` 前的典型 spread 为 `1–3c`，个别事件最薄 top depth 只有 `8–20 shares`；当前 15/30m cadence 对每个事件通常只能落到一个窗口，所以它只能支持个案复原，不能替代 dense paired denominator。

Ankara first-seen lag 在 dense window p50 为 `6.18m`；JRS current 约 `7.2m`，晚盘个别 `16:20Z/16:50Z` 报文在当前链里约 `+13.7m/+11.7m` 才看到。研究/执行必须同时存 nominal report 和 first-seen，不能把两者混成一个 trigger。

## 跨城市扩展

41 个城市都进入同一固定分母。未做 41 城多重检验校正，下面只用于 collector 优先级，不是城市 eligibility：Denver、Miami、Taipei、Wuhan、Warsaw、Beijing、Singapore、Busan 的 pre-report widening 点估和未校正 CI 为正。Ankara 的 spread CI 跨 0，但 depth 缩量通过。

当前不拟合城市专属阈值；collector 先覆盖所有有稳定 scheduled report minute 且存在活跃 exact-bracket book 的城市，再在 frozen forward 比较城市异质性。

## Expression / execution

| expression | events | metric | 95% CI | fill assumption |
|---|---:|---:|---|---|
| 直接 taker BUY existing direction | 4,506 | 15m fee-adjusted markout `-1.56c/share` | [-1.91c,-1.19c] | 当场 ask 可成交；结果为负 |
| `bid+1 tick` passive maker | 4,506 | 15m markout upper bound `+4.05c/share` | [+3.66c,+4.47c] | **假设成交，不可执行结论** |
| 普通时段 maker baseline | 5,017 | pre_far spread | paired baseline | 同 token、同 event |

真正要验证的不是“spread 能不能看见”，而是：

```text
E[value] = fill_prob * (p_signal - maker_limit)
           - adverse_selection_cost
           - missed_move_cost
           + maker_rebate_upside
```

其中 `fill_prob` 必须来自 trade tape + queue ahead，不能用 future best-ask touch。

## Frozen forward 预注册

- train choices frozen：窗口 `[-8m, first_seen+8m]`；基准 `[-15m,-8m)`；active mid `5–95c`；每个 report event 先跨 token 聚合。
- collector cadence：窗口外 30s，窗口内 1–2s；记录 full L2、trade tape、scheduled report、first-seen/hash、既有 `p_signal` 与 timestamp。
- queue model：新挂价前的 level size 全记 queue ahead；只有明确 trade volume 消耗完 queue ahead 才算 conservative simulated fill，cancel/depth disappearance 不算 fill。
- execution A/B：同一批 `fact_signal_candidates` 比较 normal entry、scheduled passive entry、scheduled taker；不改变方向 selector。
- forward primary：fill-adjusted net edge、capture rate、price improvement、1/5/15m adverse markout、missed favorable move cost；按 target_date block bootstrap。
- multiple testing：本轮跨城 K=41，未校正；forward 不选城市阈值，积累后统一做 FDR/层级模型。
- promotion blocker：至少 10+ 独立 dates、30+ conservative maker fills、相对 normal-entry excess CI > 0；真实 tiny-live queue audit 仍需单独授权。

## 8 环覆盖

| 环 | 状态 |
|---|---|
| 1 描述性绩效 | `NA`，没有 settlement PnL |
| 2 统计推断 | PASS，target_date block bootstrap |
| 3 信号判别 | 缺，方向信号保持外生 |
| 4 概率分布 | 缺，本轮不改 `P(outcome)` |
| 5 执行微结构 | PASS（spread/depth/PIT）；fill/queue 缺 |
| 6 容量 | 部分：top depth 有，queue/cancel/trade tape 缺 |
| 7 组合相关性 | 通过 date block 部分处理；城市同日相关仍需层级模型 |
| 8 基准/反事实 | PASS for liquidity；FAIL for executable fill |

## Bloodline placement

- shared data logic：scheduled report calendar 与 source first-seen 归 `weather_data_feed` source profile / event contract。
- feature layer：`seconds_to_scheduled_report`、`seconds_since_report`、`report_first_seen`、`spread`、`min_top_depth`、`queue_ahead`。
- `fact_signal_candidates`：只给既有 opportunity 增加 execution-context 字段，不另建平行策略事实表。
- shadow/collector runtime：zero-notional；未来若驻留 JRS，必须复用 `weather_jrs_tmux_env.sh` 的唯一 tmux 权限上下文。
- durable artifacts：可复跑脚本 `scripts/analysis/market_structure_edge/research_scheduled_report_liquidity_gap_v1.py`；generated CSV/JSON 在同名目录。
