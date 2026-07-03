# Regime-Routed NO：三个测量实验规格 v1

> 目的：不找新 alpha，先把回测地板定水平。三个实验全是**测量**，无参数可调，任何结果都会改写现有报告的可信区间。
> 上游审稿：`2026-07-02-regime-routed-no-strategy-review-v1.md`。
>
> 字段库存核对（2026-07-02）：
> - `fact_signal_candidates` 已有 `forecast_max_above_bracket_f`、`forecast_max_in_bracket`、`forecast_max_native`、
>   `forecast_peak_delta_hours_local`、`decision_snapshot_ts_utc` —— E1 核心自变量已在 canonical 层。
> - atlas state rows（`generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`，101 列）
>   有 `current_bracket`/`d1_no_bracket`/`unit`/`icao`/`running_native`/`final_max_native`/`final_winning_bracket`
>   和全套 expression ask/payoff。
> - `runtime/weather_edge_v1/wu_basis_us/.../source_orderbook_timing/sources.jsonl`（1.28M 行）是现成的
>   arrival-time 账本：`source_report_ts_utc` vs `local_detect_ts_utc`、`detected_after_report_sec`、
>   `max_temp_f_since_7am`（WU 风格官方站 max）。**覆盖仅 2026-06-27..06-29、11 个美国城市，采集器已停。**
> - `weather.db.ingestion_log.created_at_utc` 是 fact 重建时间，不是原始到达时间，**不可用作 PIT 时钟**。

---

## E2 — Settlement basis / off-by-one audit（先做，成本最低、杀伤最大）

### 定义
对每笔历史已结算 selected trade 和每行 atlas 候选，比较三个刻度下的 bracket 归属：
1. **决策时刻**：METAR native running max 的 bracket vs 按结算规则（WU 整数 °F / 0.5°C，per-city 舍入）换算后的 bracket；
2. **结算时刻**：METAR `final_max_native` 推出的 bracket vs `settlement_outcomes.final_price` 给出的市场真实获胜 bracket。
输出 off-by-one 率和"basis 一致 vs 不一致"子样本的 ROI 差。SF 案（METAR 21.0°C→70°F vs WU 结算 68-69）是存在性证明。

### 已有字段 / 缺口
已有：
- atlas rows：`running_max_c/f`、`current_native`、`running_value`、`current_bracket`、`unit`、`icao`、
  `final_max_native`、`final_winning_bracket`
- `settlement_outcomes`（weather.db）：per bracket `final_price` —— 市场侧结算真值
- `2026-06-14-settlement-source-registry-v0.md`：每城结算站/规则登记
- `theta_no_wu_obs_patch_v*` generated 目录：部分城市 WU 观测 patch
- `wu_basis_us/sources.jsonl`：6/27–29 美国城市的 WU 风格 `max_temp_f_since_7am` 首见值

缺：
- **历史逐时 WU 结算站序列**（大多数城市、大多数日期没有）——历史部分只能用"METAR + per-city 舍入规则"近似，
  近似误差本身要在输出里报告；
- per-city 舍入规则表的机读版（registry 是文档，需固化成 `weather_data_feed` 里的 config）。

### 分母锁定
两层，都不做重新选样：
- (a) 冻结 v2 denominator：`generated/regime_routed_no_mechanism_split_v2/trade_details.csv`（263 行，5/20–6/26），
  按 city/target_date/decision_hour join；
- (b) 全量 atlas 已结算 state rows（hours 10–21）作背景率。
加 SF 类账户 activity 单（不在 fact_trades 的真实成交）单列。

### 输出表
`generated/settlement_basis_audit_v1/basis_audit_rows.csv`：
`city, target_date, decision_hour_local, icao, unit, running_native_metar, running_settlement_units,
bracket_metar, bracket_settlement, decision_bracket_agree, final_bracket_metar, final_bracket_market,
final_bracket_agree, basis_delta_deg, trade_selected, payoff, stake_profit_usd`

`basis_audit_summary.csv`：per city × source：`n, decision_off_by_one_rate, final_off_by_one_rate,
roi_agree, roi_disagree, roi_gap`。

### 对 live/shadow 判断的影响
- off-by-one 率 ≥5% 且 disagree 子样本 ROI 显著为负 → basis 归一成为 **live 前置硬边界**（允许的 hard gate：
  已知无效数据），basis 不稳城市从 runway 路由摘除直到有结算源 feed；
- ≈0 → 关闭这条质疑，SF 修复保留即可，历史回测数字不必打折。
- 任一方向都改写 v1/v2/v3 所有 ROI 表的脚注。

### 只能 forward 双写的部分
**决策时刻 WU 实际显示的 running max**（WU 历史页会事后修订，首见值不可回溯）。需要把 `wu_basis_us` 采集器
重启并扩到全部 36 城，作为 settlement-source first-seen 账本长期跑。6/27 之前的决策时刻 basis 永远只能近似。

---

## E1 — Escape margin calibration（定义 runway 路由的机制边界）

### 定义
在候选粒度上做校准曲线：`P(打穿当前档) = f(escape_margin)`，其中
`escape_margin = forecast_max(结算单位, per-city 舍入后) − current_bracket_upper`。
再与市场隐含概率（current-bracket NO ask）比较得每桶 EV。假设只有一条、事先注册：**escape 率对 margin 单调**。
不搜阈值、不挑城市。

### 已有字段 / 缺口
已有：
- canonical：`fact_signal_candidates.forecast_max_above_bracket_f` / `forecast_max_in_bracket` —— 直接是自变量；
- atlas rows：`forecast_max_native/f`、`current_bracket`（parse upper）、`current_bracket_no_ask/payoff`、
  `gfs_forecast_max_native` / `ecmwf_forecast_max_native`（source 稳健性）、`forecast_join_status`；
- 结算真值：`settlement_outcomes` / atlas `final_winning_bracket`。

缺：
- per-city 结算单位换算/舍入规则机读表（与 E2 共用，E2 先落）；
- 6/17–6/23 的 forecast peak clock 是事后 backfill（atlas 口径限制自认）——这些行**不能剔除也不能混入**：
  单独 flag 一列 `forecast_backfill_tainted`，主曲线不含，附录单列。

### 分母锁定
全量 atlas 已结算 state rows 中 `day_regime ∈ {day_open_runway, day_marginal_runway}`、
`forecast_join_status` 有效、decision_hour_local ∈ [10,16]（峰前）——**候选粒度，不是 selected trades**，
避免选样偏差。日期冻结 5/20–6/26。拟合/验证切分沿用 6/21 冻结线。

### 输出表
`generated/escape_margin_calibration_v1/calibration_by_margin.csv`：
`margin_bucket(≤-1, (-1,0], (0,1], (1,2], >2 结算度), decision_hour_bucket, n_rows, n_city_days,
escape_rate, escape_rate_ci_low/high(date-block bootstrap), avg_current_no_ask, market_implied_escape,
ev_per_dollar, backfill_excluded_n`

`calibration_by_city.csv`：同结构 per city（只报 n≥20 的城市，其余合并 other）。
`monotonicity_check.json`：Spearman + isotonic 残差，train/forward 两段各一份。

### 对 live/shadow 判断的影响
- 预期结果：margin <1 结算度的桶 EV 为负 → 这条线成为 runway 路由**定义的一部分**（机制边界 hard gate），
  Seattle 型进场直接消失；margin 以上区间给 soft weight 一个有物理量纲的输入，替换现在手写的
  `route_multiplier`。
- 若连 margin 1–2 度的桶都没有对 ask 的正 EV → runway 路由整体被市场定价充分，**无限期停留 shadow**，
  资源转向 capped/d2 和 basis alpha。
- 若 forward 段单调性破坏 → 说明 6 月下旬预报偏差 regime 是主导变量，E1 的输出改为按
  forecast-bias 先验分层重跑（衔接 6/30 bias 工作），仍不引入新参数。

### 只能 forward 双写的部分
历史已结算数据基本够用（这是三个实验里历史可答性最高的）。唯一 forward-only：**live 决策时刻实际拿到的
forecast payload 与 archive 的逐单一致性**（parity 验证）——需要 runner 落 accepted-candidate 特征 payload
（case review 已列 still-needed），从今天起双写，历史部分承认不可考。

---

## E3 — Arrival-time PIT replay（双时钟，量化"PIT 税"）

### 定义
把 replay 的信息集从 valid-time（观测发生时间 + 90min 容忍）换成 arrival-time（数据实际可见时间），
重算 label（day_regime / intraday_state / peak clock）和入场，报告决策改变率和 ROI 差。这个差就是当前
所有回测数字应缴的"PIT 税"。

分两段：
- (a) **历史下界估计**：用 `wu_basis_us/sources.jsonl`（6/27–29，11 美国城市）测每 source 的
  `detected_after_report_sec` 分布（样本里见到 Austin ~31min 的延迟），把 p50/p90 延迟作为滞后注入全量
  replay——这是敏感性 bound，不是精确重放，输出必须如此标注；
- (b) **forward 精确账本**：扩展采集器到 36 城 + forecast 首见 + 订单簿快照，runner 每次决策落
  visible-payload hash，之后的评估一律用 arrival-time 账本。

### 已有字段 / 缺口
已有：
- `sources.jsonl`：`source_report_ts_utc`、`local_detect_ts_utc`、`detected_after_report_sec`、
  `source_fetch_start/end_utc`、raw METAR、payload_hash —— schema 正确，覆盖不足；
- market snapshot 的 `snapshot_ts_utc`（= fetch 时间）对订单簿侧是天然 arrival-time。

缺：
- 6/27 之前**所有**观测/预报的到达时间（永久缺失，只能 bound）；
- 非美国城市、IEM cache 的到达延迟分布（IEM 比 aviationweather 更迟，历史无记录）；
- forecast 发布时间 vs 可见时间（forecast 首见采集不存在，需新建）；
- 采集器本身已于 6/29 停止——**重启是本实验的第 0 步**。

### 分母锁定
与 E2 相同的冻结 v2 denominator（263 行）+ 全量 atlas 候选行。滞后注入按 source×city 抽样
`detected_after_report_sec` 分布（seed 固定），每行观测的可见时间 = report_ts + 抽样延迟；label 重算逻辑
复用 `research_regime_routed_no_expression_v1.load_states()` 上游的 feature factory，不 fork 新口径。

### 输出表
`generated/arrival_time_pit_replay_v1/delay_distribution.csv`：
`source, city, n, delay_p50_sec, delay_p90_sec, delay_max_sec`

`pit_tax_summary.csv`：
`clock_variant(valid_time | lag_p50 | lag_p90), rows, label_changed_pct, entry_changed_pct,
dropped_rows_pct, roi, roi_delta_vs_valid_time, weighted_roi, weighted_roi_delta`

`pit_tax_by_route.csv`：同结构 per route_leg（runway / capped_d2 受观测新鲜度影响不同，分开看）。

### 对 live/shadow 判断的影响
- lag_p90 下 ROI 塌掉 → 现有全部回测对 live 无信息量，live 判断只能等 forward 账本积累；
  同时说明 intraday_state 这类高频 label 在慢 feed 下不可执行，路由应降级到只用慢变量（day_regime + margin）。
- ROI 稳健（税 <5 个点）→ PIT 质疑降级，主要 blocker 回到入场时点（route-specific timing wf，E4 线）。
- 无论哪个方向：以后所有报告的 ROI 都加一列 "arrival-time taxed"。

### 只能 forward 双写的部分
到达时间本体。6/27 之前永远是分布假设，不是数据；非美国城市在采集器扩容前同样如此。**行动项：重启并扩容
`wu_basis_us` 采集器（全城市 + forecast 首见），这是 E2/E3 共同的 forward 依赖，一天不跑就少一天账本。**

---

## 执行顺序与依赖

1. **第 0 步（今天就做）**：重启 + 扩容 first-seen 采集器（E2/E3 的 forward 依赖，纯采集无策略风险）；
   runner 落 accepted-candidate 特征 payload（E1 的 parity 依赖，case review 已列 still-needed）。
2. **E2**（历史部分 1–2 天工作量）：产出 per-city 舍入规则机读表，供 E1 复用。
3. **E1**（依赖 E2 的规则表）：产出 margin 机制边界 + soft weight 的物理量纲输入。
4. **E3a**（滞后注入 bound）：随时可做；**E3b** 随采集账本积累逐周补精确值。

三个实验共享同一个冻结分母（mechanism_split_v2 trade_details + atlas 候选层），互不引入新选样。

---

## 附：三个可独立 shadow 的新 alpha 方向（不并入当前 live runner）

1. **Settlement-source first-seen 基差 alpha**：SF 失败的反向利用——当 METAR 显示跨档而 WU 官方站不会打印
   （或反之）时，用错源的参与者在边界附近错价；只在 first-seen WU max 确认后按结算源方向记 shadow 单。
   基建即 `wu_basis_us`（`source_basis_rmk_proxy_opportunities.jsonl` 已有雏形）；与 E2 共用采集器。
   独立账本、独立分母，不碰 regime 路由。

2. **Forecast 修订重定价**：run-stamped forecast max 上/下修 ≥1 结算度后，订单簿吸收有滞后（
   `2026-06-26-forecast-update-time-repricing-v0` 已有初步证据）。只用"修订差符号"一个特征，shadow 记录
   修订首见时刻 ± 前后盘口。天然 forward-only（依赖 forecast 首见采集，即 E3 第 0 步的副产品）。

3. **Mature-fade 尾部 NO 独立小册**：router v3 里 +70.5% 的 `cheap_stale_tail_current_no` 只有 9 行，
   现在挂在 v3 里只会污染主策略归因。拆成独立 shadow 策略：峰后 ≥2h + decline 确认 + ask ≤0.35，
   固定规则冻结，攒到 n≥50 再判。它的机制（市场对已死盘残余 NO 定价懒惰）与 runway 完全正交，
   适合独立证伪。

三条共同纪律：各自独立分母、独立 shadow 账本、规则先冻结再攒样本；任何一条都不给现有 live runner 加分支。
