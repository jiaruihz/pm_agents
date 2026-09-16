# Core Carry actual-transport tail challenger v1

**结论：`no_incremental_tail_value`；不改 live。** 新字段在绝对值上标出一个高风险组，但没有比 Core 多抓到任何 overshoot。本报告以 tail capture 和仓位损益为主，不以第四位小数的 proper-score 差异作策略结论。

## 真正补入的字段

- 逐报 METAR 露点 1h/3h 趋势；风向、持续性、3h 转向及南北半球暖输送分量；
- 云量当前值/3h 变化、降水现象/小时降水、阵风 excess、3h 气压变化；
- 以上均要求 observation time 不晚于 decision time；再与 exact-bracket forecast exit margin、fresh-high 剩余太阳热量交互。

覆盖不是主要问题：完整 3h METAR path 为 `99.85%`，cloud transition `77.24%`，pressure transition `71.83%`；gust 只有 `2.97%`，已保留 missing，绝不把缺失解释成无阵风。

## 历史 forward：最高风险约 20% 是否抓到 overshoot

| 排序 | 高风险 states | 捕获 overshoot | recall | lift | winner 被标高风险 |
|---|---:|---:|---:|---:|---:|
| actual-transition challenger | 32/138 | 7/14 | 50.0% | 2.16x | 20.2% |
| frozen Core risk | 32/138 | 7/14 | 50.0% | 2.16x | 20.2% |

## 10→5 股风险预算 overlay

- Forward 固定 10 股：22 笔、1 负、PnL `$+9.53`。
- actual-transition：降仓 6 笔，其中抓到 1 个 loss；节省 loss capital `$4.23`，牺牲 winner profit `$3.54`，净 tail value `$+0.69`，PnL 变化 `$+0.69`。
- Core-risk 同覆盖基准：PnL 变化 `$+0.69`。
- 两个 overlay 降仓的是同 6 笔、同 1 个 loss 和同 5 个 winner；所以 `$+0.69` 完全是原 Core 风险排序已有的结果，不是新增天气字段贡献。

开发窗也更差：challenger 用 125 个高风险 states 抓到 `22/34` 个 overshoot，Core 只用 119 个就抓到 `23/34`；selected-entry overlay 的 PnL 变化分别为 `-$5.88` 和 `-$3.20`。这排除了“只是 forward 恰好重合”的乐观解释。

冻结模型中 `frontal_warm_transition`、`gust_warming_transport`、`falling_pressure_warm_transition` 被 monotone L2 直接压到 0；非零最大的 dry-warming、forecast-exit、hemisphere-warm-transport 和 dewpoint-surge 仍不足以改变 forward 的 tail 排序。

## 概率分数只作 secondary

- Challenger/Core Brier：0.075878/0.075182。
- Challenger/Core logloss：0.267848/0.265677。

## 证据边界与动作

- 这是已看过历史窗口的 diagnostic forward，不是新的 clean forward；selected loss 只有 6 个，不能直接改 live sizing。
- 阵风缺失不能当作无阵风；南北半球暖风分量只是粗 transport prior，尚不等于 city terrain。
- materiality rule=FAIL：absolute lift PASS，但相对 Core 的 tail capture 和 overlay value 都 FAIL。首轮实现只检查 absolute lift，曾误报 PASS；该判断 bug 只影响研究结论，没有部署或生产影响。
- 不保留这个 entry-time global residual / sizing overlay。真正值得继续的是把这些字段用于**入场后新 METAR 的 event-driven invalidation**：比较入场时状态与新报文的 dewpoint surge、风向转暖、云层清除/降水终止，而不是在同一入场 snapshot 上重复 Core/market 已有的排序。

大型产物：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/current_yes_core_carry_actual_transport_tail_v1/iem_actual_transition_monotone_tail_20260807`
