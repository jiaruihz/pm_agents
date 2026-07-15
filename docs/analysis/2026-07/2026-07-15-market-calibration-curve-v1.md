# Market Calibration Curve v1（全分母：静态 taker 模式还剩多少空间）

Status: `research_snapshot`
Generated: 2026-07-15 · script `scripts/analysis/market_structure_edge/research_market_calibration_curve_v1.py` · data `docs/analysis/2026-07/generated/market_calibration_curve_v1/summary.json`

## 开头直接回答

- **问题**：两个月研究没有一条 confirmed alpha，是跑偏了还是模式本来就不存在？本报告用全分母回答后半句：atlas PIT 状态行（49 个结算日 × 36 城 × 5 种盘口表达，53,587 行）上，市场 mid 隐含概率 vs 实际结算频率的完整校准曲线，叠加 taker 摩擦（半价差 + `0.05*p*(1-p)` fee）。
- **主结论**：**市场 mid 在几乎所有价格带校准偏差 < 1.5c，taker at ask 的 fee-adjusted edge 在全部 expression × 价格带上非正（多数显著为负）**。"反复出现点估为正、CI 跨 0、fee 修正转负"不是研究方法失败，是一个已经把公开天气信息定价到摩擦成本以内的市场的正常样貌。静态 taker 价格模式这个搜索空间基本可以关闭。
- **两个真实存在的系统性弯曲**（方向与经典 favorite-longshot bias 一致）：
  1. **低价 YES 高估**：pooled (0,0.02] bias -0.002 CI[-0.003,-0.001]；lottery (0.2,0.35] bias -0.091 CI[-0.163,-0.010]。低价彩票 YES 是 bias 的**错误一侧**，与 P0 lottery live ROI -3.564% 相互印证。
  2. **高价 favorite 低估**：pooled (0.98,1.0] bias +0.003 CI[+0.001,+0.004]；**d1 YES mid (0.8,0.9] bias +0.076 CI[+0.035,+0.118]、(0.9,0.95] +0.054 CI[+0.018,+0.075]**。但除 d1 高 mid 外，其余弯曲幅度都小于半价差，只对 maker 侧有意义。
- **唯一可能穿过 taker 摩擦的格子**：d1 YES（running max 上一档 exact bracket）在 yes-mid ≥ 0.80。聚焦 probe 见下；**结论 `inconclusive_positive_signal_shadow_only`，不 live**。
- **lottery NO (0.2,0.35] 反向格子**：first-row ROI +2.9% CI[-8.1%,+13.5%]，forward 9 dates 转 -9.0%，**不成立，弃**。

## 数据与口径

- 输入：`intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`（14,368 city-date-hour 状态行，2026-05-19..07-08）。本分析只用盘口报价 + settlement 标签，不用 forecast/peak-clock 特征，因此不受 forecast backfill PIT 污染影响；但 6/21 前盘口行属 Single Runs PIT backfill rejoin 血缘，forward（6/21+）更干净。
- 5 种表达展开为 53,587 个 (state, expression) 长行：current YES / current NO / d1 NO / d2 NO / lottery YES。YES mid 对 NO 报价取 `1 - (no_ask+no_bid)/2`；YES taker 执行价 = `1 - no_best_bid`（镜像 book）。
- 标签：`final_winning_bracket` exact-bracket 语义（`d1_hit` = 结算正好落在 d1 档，factory `add_truth`）。
- CI：target_date block bootstrap 95%。fee：`0.05*p*(1-p)`/share。
- **evidence funnel 注意**：分母是"atlas 有报价且有 settlement 的状态行"，book missing 行天然不在内；d1 NO bid size 不在 CSV 列中，执行深度未验证，是 probe 的 open item。

## 校准曲线（pooled，节选）

| yes-mid 带 | n | dates | mid | freq | bias | 95% CI | 半价差 | fee@mid |
|---|---|---|---|---|---|---|---|---|
| (0, 0.02] | 19,529 | 49 | 0.005 | 0.003 | -0.002 | [-0.003,-0.001] | 0.002 | 0.0002 |
| (0.02, 0.05] | 3,641 | 48 | 0.033 | 0.026 | -0.007 | [-0.014,+0.001] | 0.014 | 0.0016 |
| (0.1, 0.2] | 3,474 | 49 | 0.148 | 0.139 | -0.009 | [-0.026,+0.009] | 0.031 | 0.0063 |
| (0.35, 0.5] | 3,463 | 49 | 0.422 | 0.429 | +0.007 | [-0.010,+0.024] | 0.051 | 0.0122 |
| (0.8, 0.9] | 1,513 | 47 | 0.855 | 0.868 | +0.013 | [-0.020,+0.045] | 0.028 | 0.0062 |
| (0.9, 0.95] | 1,385 | 48 | 0.929 | 0.943 | +0.014 | [-0.003,+0.030] | 0.019 | 0.0033 |
| (0.98, 1.0] | 7,504 | 48 | 0.995 | 0.998 | +0.003 | [+0.001,+0.004] | 0.003 | 0.0002 |

低端一致偏负（longshot 高估）、高端一致偏正（favorite 低估），单调穿越 0——教科书式 favorite-longshot bias，但除 d1 高 mid 格子外幅度均 < 摩擦。时段切片（<12 / 12-15 / ≥16）形状不变，无某时段独立可交易弯曲。taker edge 全表见 summary.json：60 个 expression×ask-band 格子中 **0 个 fee-adjusted CI 显著为正**，最好的格子也只是贴 0（current YES 0.9-0.95：-0.0015）。

## 聚焦 probe：d1 YES，mid ≥ 0.80（预注册式复检）

语义：市场已经把"升温再打穿正好一档"定价到 80%+ 时（几乎都在 10:00-17:00，heating 已确认），买 d1 YES（taker，`1 - d1_no_bid`，均价 ~0.92）。

| slice | rows | dates | win | ROI (fee-adj) | 95% CI |
|---|---|---|---|---|---|
| all rows | 296 | 48 | 0.959 | +2.18% | [+0.04%, +4.47%] |
| **first row / city-date（诚实分母）** | 218 | 48 | 0.945 | +2.24% | [-0.64%, +5.19%] |
| train < 6/21 | 162 | 33 | 0.932 | +0.73% | [-2.97%, +4.33%] |
| forward ≥ 6/21 | 56 | 15 | 0.982 | +6.62% | [+3.47%, +9.89%] |

- 广度好：33 城，24/27 个 n≥3 城市为正；亏损集中在 plateau/overshoot 型城市（Taipei -38%、Madrid、Lucknow、Wellington、Amsterdam、Jeddah）——失败模式是 overshoot 到 d2 或 current 顶住，物理上可解释，但**不预注册城市剔除**。
- 诚实读法：去重分母 CI 跨 0；train 弱、forward 强的组合既可能是"6/21 后盘口血缘更干净/盛夏 heating 更可预测"，也可能是运气。发现路径是先扫 ~50 个校准格子再聚焦（d1 (0.8,0.9] bias z≈3.5，粗略 Bonferroni 下仍显著，但仍属事后选择）。
- **该格子与 residual_high_price_no（d1 NO 95-99c）不冲突**：那是市场几乎确定不会再打穿时收 NO 残值，本格子是市场几乎确定正好打穿一档时跟 YES，两者分母不重叠。

## 入选行物理画像与失败模式（post-hoc 诊断，2026-07-15 补）

first-row 分母 218 行的中位画像：本地 14 点、1h 升温 +1.8°F、running max 刚打印 ~12 分钟、`remaining_heat_native` ≈ 1.11（正好一档）、forecast peak delta ≈ 0。即入选时刻 = 升温路径活跃、预报剩余热量恰好一档、市场已把 d1 定价到 ~0.92。

- **失败几乎全是 overshoot，不是 stall**：12 个 loss 中 11 个 `skip_over_d1`（结算跳过 d1 落到更高档），仅 1 个 current 顶住。loss 行的 `remaining_heat_native` 中位 1.67 vs win 1.11——预报剩余热量明显超过一档时，d1 会被跳过。
- **edge 集中在 ask 0.85–0.95**：0.80-0.85 带 +2.9%、0.85-0.90 带 +6.3%、0.90-0.95 带 +3.2%、**0.95-1.0 带 -0.9%**。>0.95 后残差小于摩擦——这解释了为什么 residual_high_price_no（95-99c 区）从来只有薄利：favorite 低估的可收割区间在 0.85-0.95，不在 0.95+。
- 以上均为事后切片。据此**预注册次级假设 `d1_yes_high_mid_v1.1`**（与 primary v1 并行 shadow，由 forward 裁决，不回改 v1）：v1 规则 + `ask <= 0.95` + `remaining_heat_native <= 1.3`（overshoot guard）。

## 实操血缘与时序审计（2026-07-15 补）

回测每一行的确切来源：盘口 = `runtime/weather_edge_v1/market_data/orderbook_snapshots/`（**~16 分钟一轮 poll**）；小时决策行取该小时内最后一轮 poll，quote 不前向填充（factory `iter_orderbook` hourly dedupe tail(1)）。观测 = IEM/METAR 官方链（m3 observed detail + patches），fast source **未接入** running-high（与既有审计一致）。

- **触发时刻观测年龄**：中位 ~32 分钟、67% > 30 分钟、>45 分钟仅 0.9%——恰好一个 METAR 周期（30 分制城市 ~31min；美欧小时制 ~38-41min）。即**回测本来就是拿一个报文周期旧的观测在交易**，+2.2%/+6.6% 的成绩已经内含这个滞后；live 只要 obs age ≤ 45min 就是 parity，不是新 filter。
- **obs 陈旧与输赢无相关**（win 31.9min vs loss 30.9min）；0.95+ 高价带 obs 略旧（35.9 vs 31.9min、>30min 占比 72% vs 64%），"高价带部分是 post-cross 污染（温度已踩进 d1 而我们的 METAR 没看到）"的假设方向存在但证据弱——shadow 记 telemetry，不 gate。
- **真快源城市（Tokyo/Busan/Singapore，JMA/AMOS/MSS 在产）：19/19 全胜、ROI +7.8%**，无证据"市场用快源抢跑"伤害本策略；亚洲亏损集中在无快源的台北（overshoot 气候）。d1 身份误判的机制风险仍真实存在（30 分钟盲窗内可能已跨档），live 缓解 = obs 新鲜度 parity + 同刻快源读数/current 盘口一致性作 telemetry。
- factory join 覆盖 68%（其余触发行在 factory 输出窗口外），obs-age 结论基于 148/218 行。
- **深度数据其实存在**：factory 上游已捕 `quote_best_bid_size`/`depth_bid_5c`，只是 atlas CSV 未导出——shadow runner 直接消费上游字段即可补上执行深度证据。

**shadow runner 设计要点**：双轨记账——(a) hourly-parity 轨照抄回测口径（该小时最后一轮 poll、每 city-day 首触发）作为 promotion 证据轨；(b) per-poll 轨（16 分钟）作 telemetry，量化更早触发的价格/胜率差异，不混入 promotion 分母。记录字段：NO bid size、spread、obs age、同刻快源读数、current/d1/d2 全套盘口、结算标签。

## Verdict 与动作

Contract: significance=MARGINAL(all-rows CI>0, dedup CI 跨 0); baseline=同价 taker 全表为负、本格子为唯一正; forward=15 dates CI>0 但短; conclusion=`inconclusive_positive_signal_shadow_only`

1. **关闭静态 taker 面扫描**：本报告给出全分母证据，与 2026-07-14 reset 结论一致且更彻底——不是"还没扫到"，是"扫完了，除一个格子外都是负的"。
2. **d1_yes_high_mid_v1 进 zero-notional shadow**（规则冻结如上：mid≥0.80、first qualifying hour、taker at `1-no_bid`、fee 官方口径、无城市/时段/天气附加 filter），走既有 promotion gate（≥10 独立结算日、绝对 CI 与 baseline excess CI >0）后再谈 tiny-live。深度（NO bid size）需在 shadow 里补记录。
3. **lottery（低价 YES）方向站在 longshot bias 错误一侧**：全样本校准直接支持收缩/反向审视 P0 probe，与其 live -3.564% 一致。
4. **favorite 侧 +0.3~1.4c 低估只对 maker 有意义**：与 reset 的"下一层只研究 maker fill/queue/spread capture"衔接——mid 校准 + 单边 bias 意味着 maker 在 favorite 侧挂单同时收 half-spread 和 bias，核心未知量只剩逆选成本，需要 fill/tape 证据，不是回放能回答的。
