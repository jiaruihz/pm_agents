# Weather Strategy Live-Test Selection Review

## 数据快照

- 数据源：本轮汇总只引用 `runtime/weather.db.fact_signal_candidates` / `fact_trades` 生成的已提交研究报告。
- 最新已复核报告：`2026-06-10-side-band-forecast-regime-v0.md`。
- CLOB coverage gate：`gate_pass=true`，`fact_trades_live_real.rows=1405`，`db_fill_cost_minus_fact_cost=0.0`。
- 本报告不改 N100/live 配置，不给未过三门的 live 动作。

## 一句话结论

现在没有适合开始真实 live 测试的新策略。

原因很直接：除了 all-YES underround 这个执行上很难落地的结构套利，其他 forecast-first / range / side-band 方向都没有同时通过 significance、baseline、forward 三门。更准确的动作是：把 `adjacent3 + forecast quality medium/low uncertainty` 做成 shadow/paper 观测线，而不是上真钱。

## 候选总表

| 方向 | 人话思路 | 最好证据 | 主要问题 | 三门 | 当前动作 |
|---|---|---|---|---|---|
| all-YES underround | 所有 YES 总价低于 1 时买一篮子，押市场互斥定价出错 | v1.0 proxy/executable 多阈值通过三门 | 多腿执行难、速度/滑点/partial fill/手续费风险高，用户已明确暂不实盘 | PASS/PASS/PASS | 只做工程 shadow，不作为当前 live 主线 |
| forecast-first adjacent2/3 | 买模型 mode 附近相邻 2/3 个温度档 | adjacent3 proxy 有正点估计，quality overlay 中 medium filter holdout ROI 约 `+33.5%` | executable 三门没做全；样本少，严格过滤会砍死样本 | FAIL/FAIL/FAIL 或 NA | shadow_candidate，仅记录 would-trade |
| tail fade / uncertainty | 模型认为尾部风险低、市场尾部贵，就卖尾部买内侧 | best rows 点估计很好 | holdout 常只剩 1-3 天，top5 stress 过不了 | FAIL/FAIL/FAIL | inconclusive，暂不 shadow 主线 |
| center / shoulders / butterfly | 押中心、肩部、尾部形状错价 | shoulders cheap 点估计强 | 样本只有几行；中心/尾部结构 proxy 超额为负或接近 0 | FAIL/FAIL/FAIL | inconclusive |
| side-band + forecast regime | 检查旧 side-band 早期赚钱是否可复制 | 旧 side-band proxy cost-proxy ROI `+6.4%` | top5 removed 约 `-0.0%`；train 赢家 holdout 反向，best holdout excess `-48.6%` | FAIL/FAIL/FAIL | inconclusive，不 live |
| forecast quality regime | 判断哪些 city-day 的 forecast 更可信 | adjacent3 命中/尾部 miss 有清晰信号 | 它不是交易策略；不能直接给 PnL | NA/NA/NA | 作为 planner 软过滤 |

## 当前最值得继续的东西

最值得继续的不是旧 `mid_price` 或旧 `side_band`，而是：

`forecast quality soft gate + adjacent3 range shadow`

人话解释：模型在某些 city-day 上对“最终温度会落在 mode 附近三档”确实有识别力。我们不应该马上真钱买，而应该先把这些 would-trade 在 live clock 下记录下来：当时盘口、spread、depth、是否可完整成交、最终结算、top5 贡献日。等它积累出 executable forward 证据，再谈真钱。

## 不建议 live 的原因

- `side-band`：旧形态赚过，但 cost-proxy 后只有 `+6.4%`，去掉 top5 后接近 0；clean test 的 holdout 明确反向。
- `tail fade`：看起来像策略，但样本集中到少数日期，最容易过拟合。
- `butterfly`：结构有道理，但目前只有 shoulders cheap 小样本好看，不能当策略。
- `adjacent2/3`：这是最像核心 alpha 的方向，但现在还缺 executable forward。

## 下一步实验规格

1. 建一个只记录、不下单的 `range_rv_shadow_journal`：每个 decision snapshot 写入 adjacent2/3 would-trade、forecast quality regime、market cost、spread、depth、orderbook timestamp。
2. 固定一条 shadow 规则：`adjacent3 + no_filter/medium_quality`，不要在 shadow 期调参。
3. 每天复盘一次：settled 后按 event_date cluster 更新 ROI/excess/top5 removed。
4. 只有当 shadow executable forward 同时满足三门，才讨论 tiny live test。

## 最终选择

- **real live test**：现在不选。
- **shadow/paper test**：选 `forecast quality soft gate + adjacent3 range`。
- **继续研究但不主线**：tail fade、side-band、shoulders cheap。
- **暂不实盘但保留工程验证**：all-YES underround。

