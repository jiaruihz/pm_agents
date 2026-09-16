# Tokyo JMA 多变量升温路径模型与盘口残差 v2

## 结论

Tokyo 的非温度气象特征对“JMA 报文出现后，未来 30/60/120 分钟 routine METAR 是否确认该温度 lattice”确有增量信息，但不能直接把这个概率当成 exact-bracket 的交易概率。

- weather head：**保留**。v1 在 211 个 2026 post-train 日期上，完整 METAR+JMA weather logit 相对温度路径的 date-equal Brier delta 为 `-0.00173 / -0.00163 / -0.00196`（30/60/120m）；全特征 HGB 为 `-0.00656 / -0.00597 / -0.00530`。
- v2：**按 horizon 路由**。首次跨档事件去重后，30m 与日终 break 使用校准多变量 head；60/120m 在 2025Q4 audit 出现过度自信，退回温度路径 head，不强迫 Tokyo 复用 Helsinki 的单一模型结构。
- market residual：**首版失败，禁止接 live**。2026-07-15..30 的同事件盘口/结算分母为 55 events / 11 target dates；模型 Brier `0.01644`，market `0.00324`，model−market `+0.01320`，target-date bootstrap 95% CI `[+0.00412,+0.02624]`。市场显著更准。
- counterfactual 5-share taker：4 个 first-city-day 正 edge，1 胜 3 负，fee-adjusted PnL `-$3.1519`，ROI `-38.66%`，95% CI `[-100.0%,+0.77%]`。

因此当前成果是 Tokyo weather-path probability feature，不是可交易 alpha。保留 zero-notional telemetry；不创建 plan/order/fill/exit，不修改任何 runner。

## 数据补全与时钟

历史窗口为 `2024-04-30..2026-07-30`：

- JMA Haneda AMeDAS 10-minute：118,344 rows；
- RJTT METAR：40,347 rows；
- 可构造状态：116,153 rows / 819 dates；
- 首次跨档事件：3,374 events / 744 dates。

本轮实际修复两处覆盖错误：

1. NCEI `47671099999_2025.csv` 只到 2025-08-25，已从 IEM 补 `2025-08-26..12-31`；
2. NCEI 尚无 2026 年度文件，已从 IEM 补 `2026-01-01..07-30` 的 RJTT METAR、dewpoint/RH、wind、QNH、cloud、visibility、weather。

JMA cache 完整性不再按“日期出现过一行”判断。JMA 的 `24:00` 会落到下一本地日；现在要求每日本地 rows ≥120，避免用一条午夜记录冒充整日覆盖。

历史 JMA/NCEI/IEM 只有 observation clock，不冒充 exact first-seen。盘口 replay 中：

- 38/94 events 使用 hash-verified collector exact first-seen；
- 其余 archive rows 使用 `observation_ts + 15m`，明确标记 `archive_reconstructed_plus_15m`；
- exact-only 的 28 settled score rows 上，model−market Brier 仍为 `+0.01217`，CI `[+0.00005,+0.03482]`，所以失败不是 archive 延迟单独造成的。

## v1：气象特征是否有用

特征按因果可见时间构造；同 timestamp METAR 不进入 JMA event 的 state，只能进入后续 label。

温度路径基线包括 JMA 10/30/60m slope、running max、lattice distance、strict-high age、warming run、prior METAR temp/gap/age、时钟/季节/solar elevation。

新增气象特征包括：

- JMA wind/gust/vector、30m wind change、10/30m precipitation；
- prior METAR dewpoint、RH、dewpoint depression；
- wind/vector、QNH 与变化；
- cloud fraction、ceiling、precipitation、visibility。

在 2026 的 211 个 post-train 日期上，全量状态同分母结果如下：

| horizon | temp-path Brier | full logit | full HGB | HGB delta vs temp |
|---|---:|---:|---:|---:|
| 30m | 0.09538 | 0.09365 | 0.08882 | -0.00656 |
| 60m | 0.07515 | 0.07352 | 0.06918 | -0.00597 |
| 120m | 0.06350 | 0.06155 | 0.05820 | -0.00530 |

解释性 logit 中反复出现的主特征是 dewpoint depression、JMA/METAR temperature gap、local peak clock、wind vector/change、cloud fraction。含义不是“风大一定升温”，而是这些变量与当前热量路径、海风输送和后续 METAR confirmation 共同决定条件概率。

## 结构审计与 v2

v1 有三个不能直接用于策略的问题：

1. 每 10 分钟一行造成同一 source cross 重复计数；
2. 30/60/120m 被迫共用模型家族；
3. `future METAR confirms JMA lattice` 是 source bridge，不是 Polymarket exact-bracket settlement label。

v2 将分母改为：当地 05:00–18:00，每个 `target_date × native-C lattice` 仅保留第一条严格高于 prior routine-METAR running max 的 JMA row。

时间切分：

- train：截至 2025-06-30；
- calibration：2025Q3；
- untouched audit：2025Q4；
- post-audit replay：2026-01-01 起。

2025Q4 audit 说明 Tokyo 不能机械复用 Helsinki 结构：

| target | v2 路由 | audit Brier | audit AUC | 相对 temp Brier delta |
|---|---|---:|---:|---:|
| confirm 30m | calibrated full/HGB blend | 0.19603 | 0.7206 | -0.00413，CI 跨 0 |
| confirm 60m | temp path | 0.13625 | 0.7289 | 0 |
| confirm 120m | temp path | 0.07568 | 0.7941 | 0 |
| final METAR break | calibrated HGB | 0.04266 | 0.9500 | -0.00479，CI 跨 0 |

60/120m 的 multivariate challenger 在 audit 变差，因此 safe selector 回退到温度路径。这个回退是模型选择，不是人为交易 gate。

## 盘口结果与典型案例

signal funnel：

- 94 first-cross events；
- 4 个 fee 后正 edge；
- 4 个 first-city-day counterfactual trades。

evidence funnel：

- 38 collector-exact events；
- 68 events 在 availability 后 45m 内有 full-ladder book；
- 55 settled same-row scores；
- 4 settled executable trades。

典型正确：

- 2026-07-17 09:20 local，JMA `30.6°C` 首次跨到 31，prior bracket 30。模型 `p(NO30)=0.993`，market 0.942，NO ask 0.959，最终 winner 32。5-share fee 后 `+$0.195`。这是明显持续升温，市场已经吸收绝大部分信息，剩余 edge 很薄。

典型错误：

- 2026-07-26 12:10 local（collector exact），JMA `33.2°C` 跨 33，prior bracket 32。模型 `0.832`，market 0.465，NO ask 0.50，最终 winner 32；5-share `-$2.563`。当时 METAR cloud fraction 0.875，且已经接近 peak clock；模型过度相信短期 temperature slope。
- 2026-07-21 12:10 local，JMA `34.6°C` 跨 35，模型 `0.465`，market 0.070，最终 winner 34；`-$0.470`。market 正确识别了 JMA instantaneous/10-minute path 与 settlement lattice 的 basis risk。
- 2026-07-22 16:10 local，模型仅 `0.255`，但 NO ask 0.06 仍形成表面正 residual；最终 winner 34、旧 bracket 34 YES 获胜，`-$0.314`。这说明“p 高于廉价 ask”不能替代 peak clock、forecast ceiling 与 settlement-source bridge。

## 还缺什么

下一版不应继续调当前 residual threshold，而应改变 label/结构：

1. 主 head 改为 `P(official settlement bracket leaves current exact bracket)`；METAR confirmation 只作中间多任务 head。
2. 加入严格 PIT 的 forecast peak clock、remaining heat、forecast ceiling margin 与 revision；当前历史模型没有可证明 first-seen 的 forecast archive，不能事后补入。
3. 单独学习 JMA→routine METAR→WU/market winner 的 basis，尤其是 JMA 10-minute local spike、晚间 peak、厚云/海风状态。
4. 等 collector exact + same-row book + settlement 扩到至少 30 target dates 后再冻结 forward；proper score 必须先胜 market，才评估 fee-adjusted expression。

本轮不增加 hard gate、不把 4 笔 replay 包装成策略，也不改变生产行为。

## 可复现产物

- v1 trainer：`scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_path_v1.py`
- v2 trainer：`scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_path_v2.py`
- market replay：`scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_market_v1.py`
- v1 artifacts：`docs/analysis/2026-07/generated/tokyo_jma_multivariate_path_v1/`
- v2 artifacts：`docs/analysis/2026-07/generated/tokyo_jma_multivariate_path_v2/`
- market artifacts：`docs/analysis/2026-07/generated/tokyo_jma_multivariate_market_v1/`
- tests：5 passed。

状态：`weather path feature retained / market residual v1 rejected / zero-notional only / no-live-change`。
