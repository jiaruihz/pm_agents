# CrossNO 分城市 trigger / order / fill 归因

## 结论

**你感觉到的“东京、首尔、釜山最近单多”只对了一半：最近 10 个 settled target dates，真正的增量集中在
Busan，Seoul 是 trigger 明显变多但 fill 只多 1 个；Tokyo 的十日 trigger 反而减少、fill 数不变。最近几天
Tokyo/Seoul 又密集出现，属于短窗聚集。**

对当前四个有可比历史的 live cities（Busan/Helsinki/Seoul/Tokyo），前十日到最近十日：

- first CrossNO expressions：`108 → 127`（`+19`）；增量完全来自 Busan `+12`、Seoul `+14`，被
  Tokyo `-6`、Helsinki `-1` 抵消一部分。
- actual settled fill expressions：`30 → 37`（`+7`）。用 `fills = triggers × fill conversion` 做两因
  Shapley 分解，约 `+5.4` 个来自 trigger volume，约 `+1.6` 个来自 conversion 改善。也就是**已有城市
  约 77% 的多成交来自天气/源路径跨过了更多档，约 23% 来自执行转化改善**。
- Amsterdam 8/10 才接入 live，8/11 新增 1 个 settled fill；它是 rollout 新增量，不能和前十日比较。
- 没有证据支持“以前大量信号因为系统慢而没抢到”。两窗口观测日期覆盖都完整，且未成交的大头是 ask
  高于 cap 或 CLOB `HTTP 200` 但没有 NO ask；明确 submit failure 只是少数事件。

因此归因是：**Busan/Seoul 的 trigger 增量主要是近期客观温度路径一次跨过更多 exact brackets；fast lane、
notification wake、fresh-book/depth execution 提高了抓住这些机会的能力，但不是 trigger 变多的主因。**

## 数据快照与口径

| 项目 | 固定值 |
|---|---|
| observed at | `2026-08-12 22:20` 北京时间 / `2026-08-12 14:20 UTC` |
| canonical DB | `/Volumes/jrs/pm_agents/runtime/weather.db`；repo 入口同 device/inode `16777247/54444` |
| canonical build | `MAX(fact_built_at_utc)=2026-08-12T13:46:29.407702Z`；settlement 到 `2026-08-11` |
| raw runner | `events.jsonl` 到 `2026-08-12T14:05:04Z`；`orders.jsonl` 到 `2026-08-12T13:43:53Z` |
| identity | raw `fast_source_prev_no_trial_v1`；canonical `strategy_id=live_weather_edge_v1_c16645cc1165` |
| rows | 2026-07-09..08-12 共 545 个 first expressions；104 个 settled live fill expressions；53,212 个去重 high-frequency observations |
| integrity | manifest `healthy`、0 findings；storage audit 0 critical/0 warning；CLOB fill gate `gate_pass=true`、1,463 DB/cache fills、0 missing/over-order |

统计 grain：

- trigger：第一次 `city × target_date × previous exact bracket` CrossNO expression，按 condition 去重；
- book/price：同一 trigger 后所有重复 event rows 只用于判断是否曾拿到 fresh book、ask≤city cap；不重复算信号；
- order：raw entry order record 与 unique CLOB order id；exit 独立排除；
- fill/PnL：canonical `fact_trades` 的 live-real BUY_NO expression，realized 只含 settled；
- 主窗口：`2026-07-23..08-01` 对 `2026-08-02..08-11`，各 10 个 target dates。

## 分城市结果

### 十日同窗 trigger → fill

| city | first expressions | submitted expressions | actual fills | fill conversion | accuracy | fee-adjusted PnL / ROI |
|---|---:|---:|---:|---:|---:|---:|
| **Busan** | `34 → 46` | `13 → 22` | `13 → 22` | `38.2% → 47.8%` | `97.1% → 91.3%` | `+$17.06/+12.73% → +$1.57/+0.77%` |
| **Seoul** | `25 → 39` | `2 → 3` | `2 → 3` | `8.0% → 7.7%` | `100% → 100%` | `+$1.72/+9.44% → +$5.35/+23.74%` |
| **Tokyo** | `28 → 22` | `8 → 7` | `7 → 7` | `25.0% → 31.8%` | `92.9% → 95.5%` | `-$3.19/-5.80% → -$4.02/-4.86%` |
| **Helsinki** | `21 → 20` | `8 → 5` | `8 → 5` | `38.1% → 25.0%` | `90.5% → 90.0%` | `+$9.27/+14.00% → -$6.78/-17.63%` |
| **Amsterdam** | `0 → 3` | `0 → 1` | `0 → 1` | `NA → 33.3%` | `NA → 100%` | `NA → +$0.23/+2.94%` |

城市结论：

- **Busan 是“单数变多”的主体。** 最近十日多 `12` 个 trigger、多 `9` 个 fill。观测 rows 反而从
  `6,113` 降到 `5,305`，所以不是多抓了 polling 样本；天气路径把更多相邻档依次跨过去。工程也有贡献：
  detect→runner p50 `2.509s → 0.444s`，book coverage `91.2% → 95.7%`，ask≤0.97 expressions
  `13 → 21`。但正确率下降且最近十日 ROI 近 0，说明多单不等于更优 regime。
- **Seoul 是“信号多、成交没有同比变多”。** trigger `25 → 39`，但 fill 只 `2 → 3`，conversion
  `8.0% → 7.7%`。最近十日 39 个信号里，24 个始终 ask>0.94，12 个没有 NO ask，只有 3 个成交。
  观测 rows `17,006 → 8,543`，覆盖仍是 10/10 日，因此 signal 增量更像天气跨档而不是数据量增长。
- **Tokyo 十日层面没有变多。** trigger `28 → 22`、fill `7 → 7`；只是最后五日从 `3` 个 fill
  到 `4` 个、8/12 又有 1 个未结算 fill，造成最近体感更密集。detect p50 `0.195s → 0.103s`、book
  coverage `71.4% → 81.8%`，说明工程变快，但没有创造更多十日 trigger。
- **Helsinki 没有增长。** trigger 基本持平、fill `8 → 5`；最后五日正确率回到 100%，属于天气实现
  变顺，不是成交覆盖上升。
- **Amsterdam 是新 rollout。** KNMI notification CrossNO 于 8/10 接入，first live signal 8/11；
  8/11 和 8/12 各有 1 个 fill，其中 8/12 尚未结算。这部分“最近多了”完全由新增城市造成。

### 为什么以前没有成交

这里用“同一 trigger 在整段 event 重试期间是否曾出现 book/price/order”判断，不把第一次没 ask 误判成漏单。

| city / window | trigger 未提交的主要归因 | 明确 submit failure |
|---|---|---:|
| Busan 前十日 | 18 ask>cap；3 始终无 ask | 0 |
| Busan 最近十日 | 22 ask>cap；2 始终无 ask | 2 raw records（1 maker GTD expiry、1 reprice 后 ask>cap） |
| Seoul 前十日 | 15 ask>cap；4 始终无 ask；3 个 7/23 shadow；1 个失败 expression | 3 repeated 401 records，集中同一 7/24 expression |
| Seoul 最近十日 | 24 ask>cap；12 始终无 ask | 0 |
| Tokyo 前十日 | 12 ask>cap；8 始终无 ask | 0 |
| Tokyo 最近十日 | 10 ask>cap；4 始终无 ask；1 failed expression | 1 个 8/5 order-manager-not-ready |
| Helsinki 前十日 | 4 ask>cap；8 始终无 ask；1 depth 不足 | 3 repeated trading-disabled records |
| Helsinki 最近十日 | 8 ask>cap；7 始终无 ask | 0 |

绝大多数 `missing_best_ask` rows 的 fresh-book 请求实际是 `HTTP 200/status=ok`，含义是当时 CLOB
没有可买 NO ask，不是 collector 没拿到数据。少量 network fetch failures存在，但 expression-level 几乎都能在后续重试恢复；
它们不是前后窗口成交差异的主因。

## 最近五日与 8/12 当日

最后五日对前五日：Busan `10→12` fills，Seoul `1→2`，Tokyo `3→4`，Helsinki `3→2`，另新增
Amsterdam `1`。所以“最近几天东亚单子更密”在短窗上成立，但幅度远小于十日观感，且主要仍是 Busan。

8/12 尚未结算的 raw/canonical 状态：

| city | trigger | submitted / canonical fill expressions | 当前解释 |
|---|---:|---:|---|
| Amsterdam | 3 | 1 / 1 | 新城市 rollout |
| Busan | 3 | 2 / 2 | 两个相邻档均出现可执行 ask |
| Seoul | 4 | 1 / 1 | 其余 3 个仍被 ask/no-ask 阻断 |
| Tokyo | 1 | 1 / 1 | 当日唯一 trigger 成交 |
| Helsinki | 2 | 0 / 0 | 无可执行 ask |

这一天 4 城共 5 个新 fill，确实会显著强化“最近突然单多”的主观体验；但它们尚未结算，不能进入正确率/PnL。

## 其他 CrossNO 城市

当前 controller 合同是：live `Amsterdam/Busan/Helsinki/Seoul/Tokyo`；shadow
`Ankara/Atlanta/Istanbul/Miami/SanFrancisco/Singapore/TelAviv`。

- Singapore 最近十日有 `17` 个 trigger，但当前是 shadow；窗口内仅遗留 1 个早期 live fill。
- Ankara、Atlanta、Istanbul、Miami、SanFrancisco、TelAviv 最近两个十日窗口均没有 qualifying
  CrossNO first expression。前四城存在观测日期覆盖，但没有满足当前 cross mechanism；不能说是漏单。
- HongKong、Shenzhen 只出现在启动期 legacy journal，不属于当前 generic CrossNO production city set。

## 工程时间线与最终归因

对本窗口最相关的已落地变化：7/21 JMA notification wake，7/22 visible-depth execution，7/23 Seoul
T-10 live、Tokyo single +0.7°C，7/24 Helsinki single +0.7°C 与 zero-fill retry，7/29 Korea observation
fast lanes，8/10 KNMI Amsterdam onboarding。它们改善了 latency、book retry、城市覆盖和 fill conversion。

但本次同窗证据显示：

1. Busan/Seoul 两个 trigger 增长城市的 high-frequency observation rows 没增加，反而减少；
2. 两窗都覆盖完整 10/10 target dates；
3. explicit submit failure 只解释极少数 miss；
4. Busan 的 `+9` fills 中约 `+5.2` 来自更多 trigger、约 `+3.8` 来自 conversion；Seoul 多 14 个
   trigger 只多 1 fill。

所以最终归因为：**近期天气路径是 trigger 和单数增长的第一来源；工程链使机会更快、更完整地变成订单，
但没有证据表明以前存在大量本应成交却因链路故障漏掉的 CrossNO。**

## 统计门与动作

- `significance=FAIL`：城市切片 K=14，本轮只作描述性归因，未做多重检验后的城市 alpha admission。
- `baseline=FAIL`：没有 same-row market probability proper-score baseline；正确率仍含 previous-NO base rate。
- `forward=NA`：Amsterdam/最近五日及 8/12 都是正在积累的 forward。
- `conclusion=inconclusive`；动作：**不因单数增多调整 live city pool 或 size**。继续按城市记录
  trigger→book→cap/depth→submit→fill，并优先观察 Busan terminal false cross 与 Seoul 低 conversion。

## 可复现

```bash
.venv/bin/python scripts/analysis/live_performance/weather_cross_no_city_trigger_attribution_v1.py
.venv/bin/python -m pytest -q \
  tests/research_tests/test_weather_cross_no_accuracy_attribution_v1.py \
  tests/research_tests/test_weather_cross_no_city_trigger_attribution_v1.py
```

产物写到 `docs/analysis/2026-08/generated/cross_no_city_trigger_attribution_20260812_v1/`；大型明细按
repo ignore policy 不提交，报告与可复现 runner 入库。
