# Tmax Distribution Edge：Tiny-Live 政策草案 v1（条件性，未批准上线）

Generated: 2026-07-05
Status: `policy-draft`——这是"如果要 tiny live 应该怎么上"的完整设计 + 上线前置门，**不是上线批准**。
当前族judgment仍是 `inconclusive_positive_signal / no live`（synthesis + 第一性原理评审 v1）。

## 0. 口径归属澄清（先纠正一个命名漂移）

`clean_edge02` / `city_source_edge02` / `clean_edge10` 是 **tmax_distribution edge 策略族**（独立
probability-engine track）的三个 shadow config，**不属于 HeadB**（HeadB = METAR rich-current
collapse / runway d1 YES reversal）。family map 的 do-not-mix 规则适用：本文只谈 tmax_distribution，
不与 HeadA 彩票仓、HeadB METAR reversal、regime-routed NO runner 混合。

## 1. 三个口径是什么、历史回测怎么说

2026-07-05 复核：原草案里的 `extension fwd（4 天）` 其实混入了上游 atlas winner
未回填的 6/27、6/28、7/01。当天又补齐 7/03-7/04 pm_history settlement 并重跑
atlas/P5/P6 后，7/03 也进入 `verified_forward`；当前 P6 feature/state layer 只覆盖到
7/03，所以 `extension_forward = 0`。7/04 不是未结算，而是尚未进入 atlas state rows。

| config | 模型 | edge 门 | 角色 | verified fwd（12 天） | extension fwd |
|---|---|---:|---|---|---|
| `tmax_dist_clean_edge02` | loo_no_city_source_blend | 0.02 | **clean mechanism primary** | 226 行/36 城，win 51.3%，avg ask 0.465，**ROI +10.5% CI [+0.8%, +20.0%]** | 0 行 |
| `tmax_dist_city_source_edge02` | mkt_city_source_blend | 0.02 | capacity / city-source 对照 | 236 行，+11.3% CI [-1.7%, +24.3%] | 0 行 |
| `tmax_dist_clean_edge10` | 同 primary | 0.10 | 高 edge 压力测试 | 33 行，+34.3% CI [-18.7%, +92.6%] | 0 行 |

**选型（第一性原理评审 §3.4 已预注册）：primary = `clean_edge02`，唯一 live 候选。** 理由：
- city_source 增量被 P3 消融判为疑似 city 记忆（去掉更好），不能当机制；它只留作 capacity sensitivity shadow。
- edge10 是 winner's curse 的典型形状（verified 点估强、n=31、CI 极宽），只作压力测试。
- city_source 点估略高但 CI 不如 clean 干净，且机制上更容易吃到城市/source 记忆。若上线时再从三者里挑，就是又一次选择偏差；primary 必须现在锁死。

## 2. 机制重述（政策的每一条都要能挂回这里）

**Alpha 本体 = 市场校准套利，不是天气预测优势。** 市场信息含量接近公开上限（weather_physical
只输 market 0.01-0.03 logloss），但对 **P(current bucket holds)** 存在可条件化的校准偏差：
锚切换盲区 + 度内小数位置盲区 + hazard 时间衰减三者的交集。表达方式：每个 city-date-hour state
对四个表达（current YES / current NO / d1 NO / d2 NO）算 `p_win − ask`，取最大且过门。

由机制直接推出的三条政策约束：
1. **edge 必须扣掉半 spread + 官方 fee 才是可采集的**——d2/高 ask 表达 EV≈0 的结构性原因就是
   ask 0.8-0.95 处半 spread 吞掉 0.02 级 edge。纸面 edge 门 ≠ 可执行 edge 门。
2. **max-edge 选择自带 winner's curse**——0.02-0.05 边际带 verified EV **-6.9%**。正解不是抬阈值
   而是不确定度收缩（E-D），live 前必须先落。
3. **风险单位是 city-day 不是行**——同一 city-day 多小时事件共享同一结算，PnL 按行独立统计低估相关。

## 3. Tiny-Live 政策设计（条件满足后照此上）

### 3.1 Selector（低自由度，全部机制推导）

```text
config          = tmax_dist_clean_edge02（冻结 primary；其余两 config 永久 shadow 对照）
selection       = argmax(p̃_win − ask)，p̃ = E-D 冻结的向 market 收缩概率（λ 离线定死）
edge gate       = p̃_win − exec_price − taker_fee(exec_price) ≥ 0.02
                  其中 taker_fee(p) = 0.05·p·(1−p)（Weather 官方），maker 成交按 0 fee 复核
                  ——即"fee-adjusted edge ≥ 0.02"，不另设新阈值；边际带被摩擦自然出清
entry price     = primary 不吃极低 ask 尾部；ask floor 需离线冻结，当前候选区间 0.20/0.25+
                  （0.2c/0.4c 这类票属于 lottery / stale-tail，不应混进中价位校准策略）
表达集          = 四表达；current YES 在 E-B（below/basis 修正）落地前 **°F 城市禁用**
                  （below 缺口使其 p_win 系统性高估 ~7pp，是公式偏差不是坏运气）
dedupe          = 每 city-day 第一条过门信号（现行 P6 policy），此后同 city-day 全部 blocked 记录
数据新鲜度      = state snapshot age ≤ 45min（obs cadence ~15-30min 决定）；book snapshot 时延必须记录
```

### 3.2 Entry 执行

```text
maker-first：bid+tick 挂单，TTL 60min（日内策略，hazard 随时间走，不能像 HeadA 挂半天）
taker fallback：仅当 maker TTL 过期撤单后，且 fee-adjusted edge 仍 ≥0.02 时按 fresh ask 吃
fresh-book cushion：fresh ask 超决策价 +2c 即 block（中价位票给 2c，比 HeadA 的 1c 宽一档）
```
与 HeadA 的差异根源：HeadA 的赢单 ask 决策后还会走低（不用抢）；本族是日内 hazard 策略，
时间本身消耗 edge，所以 maker 等待要限时。**METAR-reversal 的"maker 被逆向选择"结论不自动
适用本族，但方向存疑——live 首月 maker/taker 分账本记录，作为本族自己的执行实验。**

### 3.3 Sizing 与风险上限

```text
sizing          = 未冻结；当前更合理的候选是 fixed 5 shares（交易所最低 5 shares）或
                  ask-bucket capped shares，不是 naive fixed cash
daily cap       = 不使用 time-order daily cap。按触发顺序限额会系统性偏向早时区城市，
                  只能作为诊断反事实，不进入 selector
city-day cap    = 1 event（dedupe 已保证）
overlap tag     = 与 regime-routed NO tiny live 同 city-day 同向（current/d1/d2 NO 重合）时
                  照常下单但打 overlap 标；月度看两账本日 PnL 相关，若显著正相关再议合并风控
```

2026-07-05 sizing/cap 复核：P6 原始 ROI 是 1-share 口径，不是 fixed-cash 实盘口径。
在 verified forward 上，全部 city-day 首触发 226 行为 +10.5%；若机械套 `$10/天 = 每天前 10 笔`
的时间顺序 cap，会把时区靠后的城市系统性挡掉，所以它不是有效 selector。这个 cap 只能在
summary 里作为 diagnostic_daily_caps 反事实展示，不能用于 alpha 回测或 live 选择。同时
fixed-cash `$1/event` 会把极低 ask 票放大成彩票仓（例如 6/22 CapeTown current YES ask
0.004 的赢票会单笔主导 cash ROI），因此 primary 不采用 naive fixed cash。

补充 ask-floor + fixed 5-share replay（verified 12 天；每 city-day 第一条过门信号；不使用
daily cap；不使用未来排序）：`ask>=0.05` 为 203 笔 ROI +9.9%；`ask>=0.10` 为 195 笔 ROI
+9.6%；`ask>=0.20` 为 188 笔 ROI +11.1%，平均 15.7 笔/天；`ask>=0.25` 为 177 笔 ROI
+11.6%，平均 14.8 笔/天；`ask>=0.40` 为 155 笔 ROI +11.0%，平均 12.9 笔/天。低 ask 不是
直接证明坏，但它是另一种 lottery 结构：`ask<0.05` 的 23 行只靠一张 0.4c 赢票撑住，
`0.02-0.05` 桶全亏。因此 primary tiny-live 不应混入极低 ask；若要保留，必须单独建
low-ask lottery shadow sleeve。

### 3.4 Exit

```text
hold-to-settlement（当日结算，持仓寿命 1-10 小时）
无 TP / 无 stop——校准论题下中途 repricing 是噪声；TP/stop 只写 would-trigger telemetry
（HeadA 的 TP20 教训直接平移：任何 exit 研究必须区分 touch / resting fill / 可执行退出）
```

### 3.5 记账与血缘（复用现有基建，不新建）

- 订单走 `weather_order_executor.py`，journal 写 `runtime/weather_edge_v1/live/tmax_dist_clean_edge02_tiny_live_v1_orders.jsonl`
  （命名符合 7/04 修复后的 `live/<strategy>_orders.jsonl` ingestion 约定，自动进 fact_trades）。
- 每 cycle 写 P6 schema 的 selected+blocked 全量事件（含 ask size、snapshot 时延、p̃_win、fee-adjusted edge）。
- **city-day 净敞口视图**入 summary（评审 §2.4 的风险单位修正）。
- 发布任何 PnL 前过 CLOB fill coverage gate（既有硬口径）。

## 4. 上线前置门（全部满足才翻 live 开关；预计最快 2-3 周）

| # | 前置 | 为什么是硬门 | 状态 |
|---|---|---|---|
| G1 | **P7 实时 shadow runner 跑通 ≥7 天**（真 forward 事件流，非 P5 backfill） | 现在的 forward 发生器不存在；promotion gate 永远不可能过 | 未建 |
| G2 | **E-A spread 包络**：模型 logloss 打败 ask-side 归一化基线（不只 mid） | 打不过 ask-side ⇒ 技能在做市商区间内，EV 预期下调，live 无意义 | 未做（1 天离线） |
| G3 | **E-B below/basis 修正重放**：五桶胜负规则后 clean_edge02 dedupe EV CI 下界仍 >0 | 唯一能翻转 current YES 符号的已知系统误差（~7pp > 3×edge 门） | 未做（settlement 已补到 7/04，材料在库；P6 feature 只到 7/03） |
| G4 | **标签血缘审计**：atlas winner rejoin 后确认 extension 只剩未官方结算日期；新增 settlement 后做 observed vs official diff | 防止 observed-derived 与 official winner 混用，避免再次把数据滞后误判为策略证据 | 部分完成：atlas 已回填 753 行 winner；当前 P6 extension=0 |
| G5 | **E-D 不确定度收缩落地并冻结 λ**：0.02-0.05 边际带 EV 从 −6.9% 收敛向 0 | 不落它，live 会持续买进 winner's curse 边际带 | 未做（2 天，P6 双记录账本可离线重放） |
| G6 | ≥10 个新 settled forward dates 且 primary ≥80 events（文档既有 promotion gate） | 当前 12 天/226 events 来自 backfill/rejoin，不是真 P7 fresh-forward | 靠 G1 积累 |

**Kill/暂停条款（live 后）**：≥15 已结算活跃日 ROI<0；或 G3 重放翻转 current YES 符号；
或 maker/taker 分账显示系统性逆向选择（maker fill 的 EV 显著差于 taker）→ 暂停回 shadow，telemetry 保留。

## 5. 与既有 live 的边界

- 不动 regime-routed NO runner；本族独立 strategy family 记账（overlap 只打标不合并）。
- 不与 HeadA 共享 selector/sizing 结论（价位结构完全不同：0.42 vs 0.105）。
- 总敞口：$10/天 上限在 HeadA（~$5/天）+ regime NO 之外新增，三者合计仍在个位数美元/天量级。

## 复核入口

- 三口径回测：[P6 shadow telemetry](2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md) ·
  [synthesis](2026-07-03-tmax-distribution-research-synthesis-v1.md)
- 机制与证据债：[第一性原理评审 v1](2026-07-03-tmax-distribution-edge-strategy-first-principles-review-v1.md)（E-A~E-E 实验定义均在此）
- 事件账本：`generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv`
