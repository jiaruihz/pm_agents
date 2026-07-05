# HeadA / HeadB Forward Plan v1（实验方向 · 计划 · 待完善点）

Generated: 2026-07-05
Status: `current-reference`（计划文档；各项结论权威性以对应 living doc 为准）
输入：[数据审计+边界 v1](2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md) ·
[live case review v1](2026-07-05-heada-headb-live-case-review-v1.md) ·
[tail review v1](2026-07-02-low-price-yes-tail-research-review-v1.md) ·
[family map](2026-07-03-tail-strategy-family-map-v1.md)

## 总原则（两条 Head 通用）

1. **固定分母 + 预注册 + fresh forward 裁决**；train 事后切片只定假说不定规则；6/21-6/30 已烧，不再参与任何选择。
2. **管道健康优先于策略调优**——case review 证明 5 天里最大的损失来自 token 解析（错过 2 个大赢家 ≈$25），不是选票。
3. 确定性修复直接做；策略层变更一律 pre-register → shadow → gate。
4. do-not-mix：HeadA 的 TP/maker 结论不适用 HeadB，反之亦然；两者不合并、不并入 regime-routed NO runner。

---

# HeadA `forecast_tail_low_price_yes`

## 当前姿态（2026-07-05）

tiny live：`edge>=0.20 + fee-adjusted>=0.15`、ask 5-20c、`dist>0` blocker、maker-first（1c taker cushion）、
`price_tier_6_8_10_shares`、hold-to-settlement（TP20 已停用）。shadow v2 带
`hot_tail_boundary_v1 / bracket_dist_br_v1 / book_state_v1 / pcal_v2_*` 全量 tag。
评估窗 2026-07-04 起。forward 预期（蒙特卡洛）：命中率 11-14%，ROI 中心 +10~+20%、上限 +37.6%、下限 ≈0。

## A0 确定性修复（现在做，不需要预注册）

| # | 项 | 依据 | 状态 |
|---|---|---|---|
| A0.1 | missing-token 从静默 block 改为告警 + token_cache 对次日市场预热 | case review P1：永久错过 2 个大赢家 | timeout/failover 已上，告警/预热待做 |
| A0.2 | fact_refresh 健康监控接进 runtime monitor | P2：stale 门误伤 7 张票源于刷新节奏 | 待做 |
| A0.3 | cold-share 日度 drift 指标进 shadow summary | P3：cold 占比 7 月 ~60% vs train 28% | 待做 |
| A0.4 | blocked journal 消费口径统一为"candidate×blocker 首见去重" | 原始行数虚高 ~100 倍 | 写进本计划，分析侧遵守 |
| A0.5 | 7/03+ settlement 及时性（链路日跑）+ 5/18 源头缺口记录 | 反事实/结算依赖 | 链路已通，保持 |

## A1 Forward 裁决窗（7/04 起，中间不看不调）

**在裁决的三个预注册假说：**

| 假说 | 裁决数据 | 判定标准 |
|---|---|---|
| E1 `dist>0` 边界（已进 live blocker，forward 验证其正确性） | shadow 全分母 hot vs cold 子集 | hot ROI > cold（paired date-block）且 hot CI > 0 |
| E-book：stale-quote 幻觉 vs 注意力真错价 | `book_state_v1` × live 成交率 / blocked-cushion 反事实 | thin_wide/missing 候选按决策价±1c 可成交率 ≥60% 且 feasible 子集 forward ROI ≈ train（≈0） |
| maker-first 执行假设 | fill 时延/滑点/部分成交记录 | 持续零不利偏差（当前 12 票全绿） |

**Gate（沿用 W3）**：≥12 个已结算活跃日（预计 ~7/16 到量）+ 上述 CI 条件 + top-trade-removed>0。
- **过门** → `dist>0` 从 blocker 固化进 selector 文档口径；可谈 $3-5/票（仍需 A2.4 部分成交模型先行）。
- **不过** → 回 `inconclusive`；telemetry 保留；cold 票不恢复（它是论题矛盾修复，不随 gate 回滚）。
- **kill**（≥15 日且 ROI<0 或 hot 层命中率按 CI 低于 avg ask）→ selector 废弃，telemetry 喂 current-NO 侧反向风险特征，研究不清零。

## A2 研究线（不依赖 forward 窗口，可并行开工）

| # | 实验 | 内容 | 前置 |
|---|---|---|---|
| A2.1 | **E2 连续 EV selector**（优先） | W0 三件套：as-of 滚动 station-bias（每决策日只用 T-1 前误差史，≥60 天起报）+ 欧洲 13 城 bias 补全 + `bracket_distance_f` 物化进 builder；然后 `p_cal(adj_dist, bias_asof) - ask ≥ θ` 替代双阈值，θ 按日均 3-6 票在 train 定一次不扫格；验收=decile 单调 + selected CI>0 + paired excess vs 冻结 v1 CI>0（pcal_v2 失败的那一门） | 无，可立即开工 |
| A2.2 | E3 表达实验 | hot 触发 city-date 上决策时刻全 book 重放：单票 vs +1 格 ladder vs "+"封顶档；官方 fee、maker/taker 双口径。背景：hot 输家 43% 是 overshoot（热对了格子买矮了） | paper_snapshots 全 book 解析 |
| A2.3 | E4 双模型分歧特征 | candidates 每城 source 固定（train 覆盖 3/383），需从 forecast cache 层物化同城同日 GFS vs ECMWF 分歧；假说：分歧大 → tail 更值钱 | forecast cache 可及性确认 |
| A2.4 | **部分成交/时延模型**（size-up 前置） | case review P5：$0.8 要 2-4 段跨 6-11h 吃完。回测从"瞬时全额"改为 maker 队列模型（按 fill 分段实测校准），重放 $3/$5/$10 票的可实现 ROI | live fill 样本继续积累 |
| A2.5 | W5 组合角色 | tail YES 袖 vs regime-routed NO 账本日度 PnL 相关；若 bust 日显著负相关，sizing 挂 NO 敞口（日成本 ≤ NO 日均敞口 5-10%）而非独立 absolute size | 两边 forward PnL 需 ≥3-4 周 |

## A3 Exit/Stop 线（全部 shadow，无 live 变更计划）

- TP/stop would-trigger telemetry 持续积累三态：`saved_loss / capped_winner_regret / missed_touch`。
- 唯一可辩护的 TP 候选是 recover-stake @30c（delta_vs_hold +10.8% CI>0，但为 touch 口径），等 forward 三态数据再评。
- strict dead stop：期望无改善、只微降尾部；tiny 阶段无资金占用压力，其价值在 size-up 后才存在。
- 任何 exit 研究必须区分 touch / resting-fill / 可执行退出（TP20 教训固化为口径纪律）。

## A4 明确不做

- 不 size-up（A1 gate + A2.4 双前置之前）；
- 不切 pcal_v2 / source_aware_v3 / city_diag 任何 selector；
- 不加 time_stop / late_salvage（各窗口更差）；
- 不因短窗口 PnL 正负动规则（15 天窗口正负号只有 ~2:1 证据强度）。

---

# HeadB `metar_reversal / rich_current_collapse_d1_yes`

## 当前姿态（2026-07-05）

zero-notional shadow。PIT rejoin 后 B4 d1 YES：70 rows/29 dates，hold +30.8% 但 CI [-33.0%, +105.7%] 跨 0、
top5-removed -17.2%、recent rows=0 → `inconclusive_positive_signal_keep_shadow`。
live shadow 2 天：触发 ~1.5 city-day/天（NYC 7/03、Lucknow/Helsinki 7/04）。

## B0 确定性修复

| # | 项 | 依据 |
|---|---|---|
| B0.1 | 采集预算保证触发时刻抓书（1/3 触发 `orderbook_budget_exhausted`） | 触发时无书 = case 作废 |
| B0.2 | 3 个触发 case 的表达正确性回填（d1 是否命中），等 7/03-04 结算入库 | 每个真实触发都是稀缺样本 |
| B0.3 | 触发 journal 加 fresh book 多档快照（不只 top-of-book） | B1 回放需要档位深度 |

## B1 回放 v2：深度感知执行（HeadB 最优先研究）

Case review 的硬事实：**触发时 d1 ask top-of-book 中位 8 shares ≈ $1.4**，回放的"taker 按 ask 全额成交"不成立。

- taker 按档位深度吃单模拟（$1 / $3 / $5 三档 notional 的实际成本曲线）；
- obs 同龄化：回放决策用 obs_age 中位 ~19min 的滞后观测，不用打印时刻（否则高估速度优势）；
- 维持 Single Runs PIT 口径（backfill 污染修复后的 rejoin）；
- 重报 B4 + false_fade 的 hold ROI/CI/top-removed——**若深度调整后点估显著缩水，这条线降级**。

## B2 状态频率与 trigger-hunger 监控

- 每日记录候选状态频率：runway_one_step / runway_conceded / fake_runway / 触发数；
- 判据：策略沉默时区分"状态消失（天气/市场 regime）" vs "alpha 失效"；
- **纪律：不为了创造交易而放宽阈值**（0.40 等冻结值只做 shadow 对照，不调）。

## B3 表达矩阵（corrected tail_reversal_expression_matrix）

family map 定义的 next durable experiment：同 city-date-snapshot 分母上，
market-belief state（current_live / conceded / neutral）× runway state × realized path（no-break / d1 / d2 / high-tail），
比较 d1 YES / d2 YES / high-tail YES / current NO / basket。
已知边界：conceded current → d1 YES 差（不代表 skip-over tail 死）；skip-over 的 d2/high-tail 表达 open。
canonical-settled PnL 与 unsettled telemetry 分开报。

## B4 Live 门槛（明确）

HeadB 上 live 需要**同时**满足，缺一不可：
1. fresh-forward 状态频率证明触发不是 pre-6/21 的历史残影（当前 2 天 ~1.5/天，需 ≥3-4 周）；
2. B1 深度感知回放后 CI>0 且 top-removed>0；
3. 执行可行性实测（多档 taker 成本 ≤ 边际的可承受比例）。

**不做**：不并入 HeadA 彩票仓；不套用 HeadA 的 TP20/maker 结论（B4 replay 显示 TP 伤、maker 逆向选择）；
heat_death 分支不 live（首条规则 CI 跨 0）。

---

# 共享基础设施（两条 Head 都依赖）

| # | 项 | 说明 |
|---|---|---|
| S1 | orderbook 采集正式化 | Mac 采集已接管（cadence ~17min），但 6/28-6/29 缺口永久；N100 修复或 Mac 转正需决策 |
| S2 | settlement 链日跑 | 7/03+ 及时入库；5/18 源头缺口已记录为 survivorship 脚注 |
| S3 | case review 周度化 | 把本次"五段血缘+假设记分板"做成脚本，每周自动产出（blocked 去重反事实 + fill 执行段 + HeadB 触发 case） |
| S4 | forecast backfill PIT 修复 | Previous/Single Runs API 重建（影响 HeadB 历史证据与 atlas 层，HeadA 分母已隔离） |

# 时间线（粗）

```text
本周 (7/05-7/11)：A0 全部 + B0 全部 + A2.1(E2/W0) 开工 + B1 回放 v2
~7/16：HeadA forward gate 首次到量（≥12 已结算活跃日）→ 裁决 E1/E-book
7 月下旬：A2.2/A2.3 + B3 表达矩阵 + S3 周度 case review 例行化
size-up / HeadB live：无日期——由 gate 触发，不由日历触发
```
