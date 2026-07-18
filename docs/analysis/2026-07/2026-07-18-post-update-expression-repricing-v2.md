# 观测更新后 Ladder Expression Repricing v2

生成日期：2026-07-18

状态：`continue_collector`；research / zero-notional only；不改 live、不部署、不下单。

## 大白话结论

这次研究的确不是 `cross 后买 T-1 NO`。`T-1 NO` 只用来当信息时钟；真正比较了更新后的 `T YES`、`T NO` 和 `T+1 YES`。

现在还回答不了“更新后固定交易哪条腿”。在 Helsinki/FMI、Tokyo/JMA 两个主城市和 Istanbul/MGM 负面对照上，canonical 已结算且 first-seen 后有 direct ask 的同一批事件只有 **4 个全局 target dates**：固定全买 `T YES` 扣官方 fee 和 1c buffer 后 ROI `-28.9%`，固定全买 `T NO` `-1.36%`，固定全买 `T+1 YES` `-9.27%`。也就是说，generic cross 后三条腿都不是策略。

物理上目前最像样的候选路由是：更新到 `T` 后，forecast ceiling 已不高于 `T+0.25°C` 时偏 `T YES`；ceiling 仍高于 `T+1.25°C` 时偏 `T NO`；中间区间观察 `T+1 YES`。但这个**事后定义的**干净路由在 Helsinki+Tokyo 只有 `34` 行 / `4` 天，fee+buffer ROI `+2.61%`，去掉最好一天立刻变成 `-2.35%`。不能冻结成 shadow 策略。

模型层也给出同样答案：天气/path 单独没有打赢 market；加入 current/d1 sibling 几何后，current-stop Brier 从 market 的 `0.1047` 小幅降到 `0.0908`，overshoot 从 `0.1566` 降到 `0.1341`，但只有 4 天，而且把 OOF 概率实际转成 `T YES/T NO/T+1 YES` 三选一后 ROI 是 **-31.6%**，去掉最好一天 `-61.8%`。`T+1 YES` 尤其失败：market Brier `0.1054`，weather/path `0.1816`，weather+geometry `0.1636`，明显不如 market。

所以当前动作是：继续采 `T/T+1` sibling direct book 和 PIT path；不启动 expression shadow。下一轮最值得复核的是少数城市的 **ceiling-vs-new-T current-leg router**，不是 cross-NO，也不是固定 d1 YES。

## 研究对象与数据快照

目标概率：

```text
P(final = T | source update reached T, PIT state)       # current stop
P(final > T | source update reached T, PIT state)       # overshoot
P(final = T+1 | source update reached T, PIT state)     # d1 stop
```

执行表达：`T YES`、`T NO`、`T+1 YES`；`T-1 NO` 仅度量旧信息吸收。

- Mac raw：`/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow`。
- 本次读取 `221` raw events、`10009` quote rows；event file mtime `2026-07-18T18:22:39Z`，quote file mtime `2026-07-18T19:02:42Z`。
- canonical DB：`runtime/weather.db`，fact built `2026-07-18T10:55:16Z`；settlement 截止 `2026-07-17`。
- 数据覆盖足够回答当前 collector 窗口，未同步、未 rebuild。
- 事件 dedup grain：第一条 `(city, target_date, source, newly_reached_T)`；重复 poll 和 Busan 式同档 repoll 不重复计。
- first-seen 后 horizon：`0/30/60/120/300s`，只有实际落在容差内的 quote 才保留。
- fee：`0.05 * price * (1-price)` per share；执行另加 `1c/share` buffer；容量只认 direct top ask size。

## 城市冻结

城市没有按目标腿 ROI 选择。

| 城市 | 角色 | source 关系 | events / active dates | settled | median source obs→first-seen | median first-seen→首个支持盘口 | H0 三腿完整 |
|---|---|---|---:|---:|---:|---:|---:|
| Helsinki | primary | FMI same-station alternate feed | 17 / 5 | 15 | 462s | 35.8s | 17 |
| Tokyo | secondary | JMA same-airport proxy | 22 / 4 | 22 | 457s | 37.2s | 20 |
| Istanbul | negative control | MGM same-airport proxy | 6 / 5 | 5 | 1325s | 25.7s（仅支持事件） | 3 |

Istanbul 的 source lag 和 sibling coverage 明显更差，只作负面对照。Moscow/HKO 仍没有 authoritative first-seen→sibling-book episode，没有强行加入。

## 双漏斗

### Signal funnel

| stage | unit | rows | dates |
|---|---|---:|---:|
| raw collector | raw event rows | 221 | 6 |
| frozen three cities | raw event rows | 46 | 5 |
| first city-date-source-T | events | 45 | 5 |

### Evidence funnel

| stage | unit | rows | dates |
|---|---|---:|---:|
| PIT peak clock / forecast ceiling / METAR state | events | 44 | 5 |
| canonical settlement label | events | 42 | 4 |
| H0 direct `T YES + T NO + T+1 YES` | events | 40 | 5 |
| actual fills | fills | 0 | 0 |

缺盘口和结算都只是 coverage gap，没有当策略筛选条件。

## 同 rows 概率比较

OOF 是按全局 `target_date` 留一块，beta-smoothed empirical probability。所有 ablation 与 market 使用完全相同 rows；这只是小样本诊断，不是已冻结模型。

| target | rows / dates | market Brier / logloss | event-only | city/source | weather/path/source | weather/path + market geometry | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| current stop | 36 / 4 | .1047 / .3479 | .1580 / .4970 | .1618 / .5066 | .1207 / .3944 | **.0908 / .3435** | 只有加盘口几何才微弱超过 market |
| current NO payout | 36 / 4 | .1047 / .3484 | .1580 / .4970 | .1618 / .5066 | .1207 / .3944 | **.0908 / .3435** | 与 current stop 互补，同样很薄 |
| overshoot `final>T` | 36 / 4 | .1566 / .4793 | .2016 / .5931 | .2054 / .6029 | .1449 / .4459 | **.1341 / .4403** | market `T NO` 对 proxy false-cross 只是近似 baseline |
| d1 stop | 37 / 4 | **.1054 / .3392** | .1697 / .5226 | .1748 / .5351 | .1816 / .5510 | .1636 / .5080 | market 明显胜出，拒绝 d1 表达 |

这里有两个不能越过的边界：

1. current 的改善只在 weather/path 与盘口几何组合后出现，天气单独不行；不能说找到了纯天气 alpha。
2. overshoot 的市场近似值来自 direct `T NO` mid；proxy source 可能最终落回 `T` 以下，所以它不等于纯 `P(final>T)`。执行时 `T NO` payout label 仍按 `final != T` 单独计算。

## 真实 ask、fee、buffer 与容量

这是全 rows 描述性 replay，不按结果选行：

| expression | settled executable rows / dates | avg ask | win rate | fee+1c ROI | top ask size≥5 / ≥10 |
|---|---:|---:|---:|---:|---:|
| `T YES` | 39 / 4 | .2362 | 17.95% | **-28.91%** | 39 / 28 |
| `T NO` | 37 / 4 | .8063 | 81.08% | **-1.36%** | 35 / 28 |
| `T+1 YES` | 39 / 4 | .2376 | 23.08% | **-9.27%** | 37 / 24 |

分城只有 Tokyo `T NO` 是正点估 `+2.29%`，但仍是同 4 天中的单城切片；Helsinki `T NO` 为 `-7.74%`。这不能作为按城市 ROI 选 Tokyo 的依据。

### 两个执行反证

- 可解释的 ceiling router：Helsinki+Tokyo `34` 行 / `4` 天，ROI `+2.61%`；去最好日 `-2.35%`。点估不稳定。
- date-block OOF 三腿最高正 edge router：`34` 个已结算选择 / `4` 天，ROI `-31.6%`；去最好日 `-61.8%`。其中 d1 YES 选择大量亏损，说明“概率切片看起来有结构”没有转化成可执行策略。

因此 actual first-seen 时点不存在已经验证的 fee-adjusted residual。

## Sibling 异步更新

- `45` 个去重事件中，H0 current mid 覆盖 `40`；30/60/120/300s 依次只有 `26/22/12/12`。
- 以 sibling mid 相对 H0 首次移动至少 2c 定义 reaction，找到 `5` 个异步案例、`40` 个同步或未动反例。
- **5 个案例全部来自 2026-07-14**；不是跨日期重复机制。
- Helsinki 7/14 的三个例子分别出现 d1 先动、current 后动和仅 current 动，方向也不一致；Tokyo 有一个只 d1 动，Istanbul 有一个只 current 动。
- 大多数早期 Tokyo 更新是 `T-1 NO` 已接近吸收，但 current/d1 在支持的 horizon 内都没有移动 2c。完整反例已逐条输出。

这只能说明 collector 能捕捉 sibling 非同步，不能说明非同步本身可交易。episode 没有 event 前盘口，且 300s 覆盖只有 12 个事件，不能估计稳定 update order 或相对价值回归。

## 关键 coverage gaps

1. 当前 collector 实际 schema 只有 `t_minus_1/source_round/source_plus_1`，**没有 T+2**；工单中 T+2 只能记缺口。
2. episode 从 first-seen 后创建，**没有 event 前 direct sibling book**；H0 是 detect 后首个支持 quote，不是 pre-event quote。
3. 没有 same-city/local-time non-cross sibling panel，因此 matched non-cross baseline 缺失。
4. rich weather/path 中 cloud、rain、wind、humidity、最近 1h/3h trend、plateau/pullback 不在 event ledger；当前只使用 PIT peak clock、forecast ceiling relative to newly reached T 和 pre-event METAR state。
5. settled evidence 只有 4 个全局 target dates；无法做可靠 expanding calibration、date bootstrap CI 或 frozen forward。
6. 本轮只有 top-level capacity，没有多档 5/10-share VWAP，也没有真实 fills。

特别纠正：forecast ceiling 必须以**新观测已经达到的 T**为基准。若继续用 `forecast_max - stale METAR max`，会机械混入快源领先距离并制造漂亮假 alpha；本轮脚本已改成 `forecast_max_native - source T`。

## Verdict 与下一步动作

最终动作：`continue_collector`。

- 不启动新 expression shadow；不改任何 live runner。
- `T+1 YES` 当前按 `reject_expression` 处理：proper score 输 market、全 rows fee+buffer 为负。
- `T YES/T NO` 保持 research：只保留 ceiling-vs-new-T 和 sibling geometry 的连续 residual，不能变成阈值 hard gate。
- collector 下一版若要真正回答 asynchronous ladder，需要补 event 前 snapshot、T+2、rich PIT path/weather、固定 30s cadence，并同时采 same-city/time non-cross panel。
- 至少再积累 10 个新的 settled active dates 后，冻结 Helsinki/Tokyo 的 continuous probability model，再看 proper score 和 direct execution；不得把本轮 4 天重新用作 forward。

## 可复跑产物

- 脚本：`scripts/analysis/market_structure_edge/research_post_update_expression_repricing_v2.py`
- 生成目录：`docs/analysis/2026-07/generated/post_update_expression_repricing_v2/`
- 核心文件：
  - `event_expression_horizon_panel.csv`
  - `proper_score_summary.csv`
  - `state_target_summary.csv`
  - `execution_summary.csv`
  - `physical_router_summary.csv`
  - `exploratory_oof_policy_summary.csv`
  - `asynchronous_cases.csv`
  - `asynchronous_counterexamples.csv`
  - `funnels.csv`
  - `coverage_gaps.csv`

复跑命令：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_post_update_expression_repricing_v2.py \
  --out-dir docs/analysis/2026-07/generated/post_update_expression_repricing_v2
```
