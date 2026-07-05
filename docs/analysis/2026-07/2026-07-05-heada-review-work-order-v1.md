# HeadA 审阅工单 v1（交 Codex 执行）

Generated: 2026-07-05
Status: `current-reference`（工单；证据权威性以引用的各 doc 为准）
背景输入：本日 HeadA 全链路审阅（执行链代码审 + canonical ROI + hot 子集 TP 重放 + ECMWF 采集回归定位）。
证据 doc：[hot 子集 TP 重放 v1](2026-07-05-low-price-yes-hot-subset-tp-replay-v1.md) ·
[数据审计+边界 v1](2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md) ·
[live case review v1](2026-07-05-heada-headb-live-case-review-v1.md) ·
[forward plan v1](2026-07-05-heada-headb-forward-plan-v1.md) ·
[city source fit v1](2026-07-05-low-price-yes-city-source-fit-v1.md)

## One-Line Read

**P0 是一个真的 live 数据有效性事故：Mac 接管后 ECMWF 历史误差缓存没拷进运行时 cache，
`paper_snapshot.py` 静默 fallback，7/04 起全部 47 城（含 31 个 ECMWF-assigned 城市）都在用 GFS 出信号
——当前 live 候选与 train 证据的 source 口径已错位三天。其余为观测性修复与研究结论落地，无策略层变更。**

---

## P0 ECMWF 采集回归（确定性修复，优先做）

### 现象（fact 层可复核）

```sql
-- fact_signal_candidates 按 event_date × forecast_source：
-- 7/01: ecmwf 461 / gfs 403（正常：31 城 ecmwf、49 城 gfs，每城固定模型）
-- 7/02: ecmwf 116 / gfs 757（崩塌开始，与 Mac 接管时点吻合）
-- 7/03: ecmwf 6   / gfs 835
-- 7/04+: ecmwf 0（全 GFS）
```

`fact_forecast_hourly_curves`（7/03 起建）也因此只有 `gfs` 行。

### 根因（已定位到行）

`weather_data_feed_service/weather_data_feed_service/legacy_weather_predict/paper_snapshot.py:668`
`compute_ecmwf_error_distribution` 依赖 `CACHE_DIR.glob("ecmwf_v4_{city}_*.json")`；
Mac 运行时 cache（`~/projects/weather_data_feed_service_runtime/cache/`）只拷了 67 个 `gfs_v4_*` 文件，
**零个 `ecmwf_v4_*`** → 返回 None → `all_models[city]` 静默 fallback 成 `gfs`（`paper_snapshot.py:1146-1167`），
连 `fetch_live_ecmwf` 都不会被尝试。ECMWF 缓存文件本地就有两份可用：
`pm_agents/runtime/weather_edge_v1/market_data/cache/ecmwf_v4/*.json`（N100 镜像）和
`~/projects/weather-predict/cache/ecmwf_v4_*.json`。

### 动作

1. 把 `ecmwf_v4_*.json` 拷入 Mac 运行时 cache（**flat 放置**，glob 不认子目录），重启 data feed loop
   （`scripts/ops/start_mac_weather_data_feed_loop.sh`）。
2. **静默 fallback 改显式**：ECMWF-assigned 城市 fallback 到 GFS 时必须打日志/告警（工程姿态：
   显式失败优于静默 fallback）。可加 loop 级 summary："ecmwf_assigned=31 ecmwf_active=N fallback=M"。
3. **污染窗口标注**：2026-07-02..修复日 期间 ECMWF 城市的 candidates 是 GFS 口径，与 per-city source
   policy 不符。在数据治理记录（LIVE_RUN_HISTORY / DATA_GOVERNANCE 或本工单回填）标注该窗口；
   分析这段"最新"数据时 source-fit 相关切片要剔除或分层。
4. 验收：修复后次日 `fact_signal_candidates` ECMWF 城市 `forecast_source='open_meteo_live_ecmwf'` 恢复
   ~450 行/日水平；`fact_forecast_hourly_curves` 出现 ecmwf 行（若 curves builder 仍只写 gfs，再修 builder）。

### 回答"每城要不要按自己的源算"

系统**本来就是每城固定模型**（`CITY_MODEL`：31 城 ecmwf / 49 城 gfs，按城市历史误差分布选定），
train 分母就是这个口径产生的——所以这不是要不要做的问题，是**修回来**的问题。
[city source fit v1](2026-07-05-low-price-yes-city-source-fit-v1.md) 进一步确认：source fit 作为
hard filter 没赢 baseline（`source_hot_clean` 点估更差），当前姿态 = per-city assigned source 出信号 +
source-fit 只做 shadow telemetry 解释层，不动 selector。

## P0.5 E4 双模型分歧的数据层前置（修完 P0 才有意义）

E4（"GFS/ECMWF 分歧大 → tail 更值钱"）需要**同城同日两个模型**的 forecast_max，而采集是每城单模型。
在 snapshot 生产侧把非 assigned 模型的 `forecast_max` 也拉一份（每 city-date 多 1 次 open-meteo 调用，
或用 open-meteo 多 model 参数一次拉齐），落进 snapshot/telemetry 字段（如 `forecast_max_f_alt_model`），
进 fact 层后 E4 才可测。**先积累 2-3 周，不写策略脚本。**

## P1 观测性修复（确定性，小改动）

1. **runner 日期窗口静默排除**：`low_price_yes_lottery_tiny_live.py` 的
   `effective_min_event_date = MAX(event_date)` 会静默排除较早 event_date 的候选且不写任何 blocked 记录。
   当前靠采集节奏巧合不丢票（D 日新鲜窗 ~12:30Z 关闭早于 D+1 行 ~15:00Z 出现），但无观测面。
   动作：把"因日期窗口被排除的匹配行数"计入 `latest_summary.json`（不改选择行为）。
2. **registry 更正**：[STRATEGY_REGISTRY](../../WEATHER_STRATEGY_REGISTRY.md) 写 price-tier sizing
   "2026-07-04 起"，canonical fill 显示 7/04 四张票（Busan/Manila/Paris/Helsinki）仍是 fixed-cash $0.8，
   **price-tier 实际 7/05 生效**（Shanghai 6 股 / Wellington 8 股）。sizing shadow 对比分界日用 7/05。
   另注意 tier 参考价是 maker 价非 ask（ask 9c/maker 7.5c 会落 6 股档）。
3. forward plan A0.1-A0.4（missing-token 告警、fact_refresh 监控、cold-share drift、blocked 去重口径）
   照旧执行，本工单不重复。

## P2 研究结论落地（更新文档口径，无 live 变更）

1. **A3 的 recover-stake@30c 候选降级关闭**：在当前 live 姿态分母（hot-only × price-tier × taker fee）
   上重放，delta vs hold = -16.9%（train）/-21.1%（full），paired date-block CI 全负。此前 +10.8% 来自
   全分母里的 cold 票，dist blocker 上线后证据不再适用。live 维持 hold-to-settlement，TP/stop 只留
   would-trigger telemetry。证据：[hot 子集 TP 重放 v1](2026-07-05-low-price-yes-hot-subset-tp-replay-v1.md)。
   动作：更新 [forward plan](2026-07-05-heada-headb-forward-plan-v1.md) A3 节 + registry 对应行。
2. **A2.1 连续 EV selector 预期下调**：hot 子集内 raw dist 分档胜率平坦（12.2/15.6/16.7/14.0/15.6%），
   连续距离梯度只存在于跨 hot/cold 边界处；与 continuous-ev v1、clean-ev v1 双双失败一致。
   as-of bias 基建（W0）保留（p_cal/解释层仍需要），但不再当 selector 替代候选卖。
3. **E-touch 预注册（shadow only）**：hot 票 bid 触及 0.30 后最终胜率 51.4%（37/72）、触及 0.20 后
   33.0%（38/115）——市场对已启动的 hot tail 重定价不足。若推进：A3 三态 telemetry 加
   `post_touch_hold_value` 字段即可覆盖，**不加仓、不改规则**，train 事后切片自知。
4. E3（overshoot ladder 全 book 重放）维持 forward plan 优先级不变，hot 输家 43% overshoot 仍是
   特征侧期望值最高的一条线。

## 不做（重申）

- 不动 selector / sizing / TP / size-up——全部等 ~7/16 E1/E-book gate（≥12 已结算活跃日）。
- 不因 live 14 张票 realized -30.5%（1/14 命中）动任何规则：14 张票命中率标准误 ±9pp，无信息量。
- 不把 E-touch / source-fit / 任何 train 切片直接写成 filter。

## 附录 A：P0 修复验证 + 污染影响量化（2026-07-05 16:40Z 回填）

**修复已验证生效**：`ecmwf_v4_*.json` 52 个已入 Mac cache；最新 snapshot（snapshot_20260706_0032）
432 ECMWF + 335 GFS records，比例恢复正常。待办残留：①fallback 显式告警仍未加（P0 动作 2）；
②下次 fact rebuild 后确认 `fact_forecast_hourly_curves` 出现 ecmwf 行。

**污染窗口错单反事实**（Open-Meteo Previous Runs API `ecmwf_ifs025` previous_day1 重建决策时 ECMWF forecast_max）：
16 张 live 票中 5 张是 ECMWF 城市用 GFS 决策，其中 **3 张在正确源下根本不是 hot-tail 票**：

| 票 | GFS 口径 dist | ECMWF 口径 dist | 正确源下的性质 |
|---|---:|---:|---|
| Ankara 7/03 30 @7.6c | +0.3C hot | **-0.6C** | forecast 30.6 在 bracket 内，非 tail |
| Dallas 7/03 98-99 @11c | +2.9F hot | **-2.1F** | ECMWF 报 100.1F，实为押"预报报高"的 cold 侧 |
| Busan 7/04 24 @4.8c | -0.4C cold | **-3.1C** | ECMWF 报 27.1C，深度 cold |
| London 7/03 28 @6c | +1.1C hot | +2.8C hot | 两口径都 hot，选票不变 |
| Helsinki 7/04 21 @15.1c | +0.6C hot | +2.5C hot | 两口径都 hot，选票不变 |

错误类别票成本 $2.54 / 总投入 $12.66（全输，但 hot 票基础命中率 ~15%，单票输赢仍是噪声；
要点是**类别错了**，不是结果输了）。错过侧（ECMWF 下会触发但 GFS 没触发的票）无法从 candidates 层重建，
需 S4 Previous Runs 全量重建才能补，暂记为未知缺口。

**E1/E-book forward 窗口污染（对 ~7/16 gate 有直接影响）**：7/04 起 shadow 全分母 22 个唯一候选中
**8 个（36%）是 ECMWF 城市**，其 `hot_tail_boundary_v1 / bracket_dist_br_v1` 标签基于错误 GFS forecast。
裁决动作：E1/E-book 评估时 ECMWF 城市行按"7/02..修复时刻"窗口剔除或单独分层；
这些城市的干净 forward 从 ~7/06 重新起算（GFS 城市不受影响，仍按 7/04 起算）。

**新假说（回填 case review P3）**：7 月 cold 占比翻倍（28%→60%）与 7/02 源污染时间重合——
GFS 替代 ECMWF 后 31 城的 forecast_max 系统性变化会直接改变 dist 标签分布。这是自然实验：
若修复后 cold-share 回落到 train 水平，P3 的"regime 漂移"大部分是采集回归假象，不是天气/市场变了。
把 cold-share 按 city-model 分组分别追踪（A0.3 的 drift 指标加一个维度）。

## 当前 canonical 数字（截至 2026-07-05 11:00Z，gate_pass=true）

| 口径 | 数值 |
|---|---|
| live 已结算（7/02-7/04，14 张） | cost $12.66 / realized **-$3.86**（-30.5%，1 胜 = Houston 7/03 +$8.01） |
| live 未结算 open cost（7/05，2 张） | $1.20（Shanghai 部分成交 + Wellington） |
| token 故障反事实（已结算 4 张） | 错过 +$25（2 胜，+360%）——管道损失仍 > 策略损失 |
| 冻结分母 hold 基线（hot 333 行） | train +35.1% CI [-1.6%,+76.4%] / full +41.8% CI [+9.5%,+77.0%] |

## 执行状态（2026-07-06 Codex）

已执行 / 已验证：

- P0 数据层：Mac targeted snapshot 已恢复 assigned source 口径，health check `snapshot_source_model.status=ok`，最新 snapshot `open_meteo_live_ecmwf=432 / open_meteo_live_gfs=335`，`source_model_summary.fallback_counts={}`；`fact_signal_candidates` 7/06 已有 ECMWF rows，`fact_forecast_hourly_curves` 7/06 已有 ECMWF rows。
- P0 显式 fallback：`paper_snapshot.py` 已输出 `source_model_summary`，fallback 非空时打印 `[forecast_source_warning]`；`weather_data_feed_prod_health_check.py` 已把 missing summary / fallback / assigned-active mismatch 作为 source-model health 状态。
- P1 日期窗口观测面：`low_price_yes_lottery_tiny_live.py` 已新增 `date_window_excluded_matching_rows` summary 字段，统计被 `effective_min_event_date=MAX(event_date)` 排除的匹配行数和 city-date dedupe 行数；不改变 selector。
- P1 registry 更正：`price_tier_6_8_10_shares` 实际生效日更正为 2026-07-05，且标注 tier 参考价是 maker reference price。
- P2 文档口径：forward plan / registry 已把 recover-stake@30c 在 hot-only live 分母上降级关闭；A2.1 连续 EV selector 从“优先替代候选”下调为解释层 / telemetry 候选；E-touch 仅 shadow。

污染影响补充量化：

- 新脚本：`scripts/analysis/forecast_quality/replay_low_price_yes_source_counterfactual_v1.py`。
- 输出：`docs/analysis/2026-07/generated/low_price_yes_source_counterfactual_v1/summary.json`。
- 真实 fills（7/02-7/05）：全量 settled 20 / wins 1 / hit 5.0% / PnL -$3.86；source-aligned settled 13 / wins 1 / hit 7.7% / PnL +$0.23；source-mismatch settled 7 / wins 0 / PnL -$4.09。
- candidates 层 source-corrected partial（只修 assigned error distribution，仍使用已存 forecast_max_f）：旧 selector 37→40，settled hit 12.5%→14.8%；当前 `dist>0` hot-tail 口径 22→27，settled hit 12.5%→15.8%。
- current Open-Meteo API forecast-max 近似（非 PIT，只量化 forecast max 改变方向）：旧 selector 37→36，settled hit 12.5%→16.7%；当前 `dist>0` hot-tail 口径 22→25，settled hit 12.5%→18.8%。

仍不做 / 保留：

- 不把 E4 双模型分歧写成 selector；只作为采集侧补 `alt_model_forecast_max` 的前置需求，积累 2-3 周后再测。
- 不因 ECMWF 修复后 counterfactual 变好而 size-up；7/02..修复时刻 ECMWF 城市 forward 行剔除或分层，干净 forward 重新累计。
