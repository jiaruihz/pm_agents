# d1_yes_high_mid：live/shadow 链路 vs 回测链路 Parity 审计 v1

Status: `snapshot`
Date: 2026-07-15
Runner: [scripts/ops/d1_yes_high_mid_shadow_v1.py](../../../scripts/ops/d1_yes_high_mid_shadow_v1.py)
Backtest: [scripts/analysis/market_structure_edge/research_market_calibration_curve_v1.py](../../../scripts/analysis/market_structure_edge/research_market_calibration_curve_v1.py) · 策略 doc [2026-07-15-d1-yes-high-mid-strategy-v1.md](2026-07-15-d1-yes-high-mid-strategy-v1.md)

## 目的

确认 shadow runner 的入场条件、价格口径、d1 身份、结算标签与回测**逐条一致**，不引入回测没有的额外过滤或条件。审计中发现 **2 处漂移，已修**；另有 3 处**结构性差异**（性质不同，已如实标注，不是 bug）。

## 一、逐条 parity 表（trigger / 价格 / 标签）

| 维度 | 回测 | runner | verdict |
|---|---|---|---|
| d1 身份 | factory `add_state_siblings`：`tail_distance==1` 的 NO 档（F 档宽 2°→`ceil((low−run)/2)`，C 档宽 1°→`round(low−run)`），running 先 `round_half_up` | **直接 import** factory 的 `Bracket/parse_bracket/bracket_contains/round_half_up`；`tail_distance` 同公式 | ✅ 同源，历史重放 5/5 精确匹配 |
| current 档 | YES 档包含 `running_value`，specificity(ranged)优先 | 同：`bracket_contains(running_value)`，ranged 优先 | ✅ 一致（仅整数边界轻微歧义，不影响 d1） |
| 触发阈值 | `d1_yes_mid >= 0.80` | `d1_yes_mid >= 0.80`（`--mid-threshold` 默认 0.80） | ✅ 相同 |
| mid 公式 | `1 − (d1_no_ask + d1_no_bid)/2` | 同 | ✅ identical by construction（2/2 数值核对） |
| 入场成交价 | taker `1 − d1_no_bid` | 同 | ✅ 相同 |
| fee | `0.05*p*(1−p)`/share on ask | 同 | ✅ 相同 |
| 去重（promotion） | 每 city-date 首个满足行（按小时升序取最早） | 每 city-date 首个满足 poll（最早时刻）→ `track=promotion` | ✅ 语义一致（最早触发） |
| 结算标签 | `final_winning_bracket`（`settlement_outcomes` `final_price≥0.99` 唯一赢家）== d1 档 | 同：`settled_bracket`（唯一 `final_price≥0.99`）== d1 档 | ✅ 相同 |
| 附加过滤 | **无** city/hour/weather filter | **无** city/hour/weather filter | ✅ 相同 |

## 二、审计中发现并修复的 2 处漂移

1. **obs_age 硬门槛（多出来的过滤）**。初版 runner 加了 `obs_age <= 45min` 硬 gate，但回测**没有任何 obs-age 过滤**（回测触发行 obs age 最大 60min，0.9% 超 45min 也全部计入）。这会让 shadow 的 promotion 分母比回测更小、悄悄改变结论。
   **修复**：取消 backtest-band 内的排除；只挡**病理性陈旧**（>120min，回测里从不存在、只可能因 live obs feed 卡死出现），并对每条记录打 `obs_age_in_backtest_band`（≤61min）flag 供 forward 分层。触发**条件**回到与回测完全相同的 `mid≥0.80`。
2. **d1 平局取档**。当 exact 档与 ranged 档同时 `tail_distance==1` 时，factory 取**最高 NO ask** 那档（ask 降序后 drop_duplicates）；初版 runner 取 dict 首个。
   **修复**：runner 改为在 `distance==1` 候选里取最高 NO ask，和 factory drop_duplicates 语义对齐（自测通过：exact `85` vs ranged `85-86` 并存时选 `85-86`）。

## 三、结构性差异（性质不同，非 bug，已标注）

1. **采样节奏**：回测 = 每 local decision-hour 取**该小时最后一轮 poll**的离线状态；live = 逐 poll、首次跨 0.80 online 触发。0.85–0.95 带价格移动慢，差异小，但存在。**结论**：shadow 的 promotion 证据以**自身 live-sampled forward 记录**为准，回测是先验假设、不是 promotion 分母。runner 同时写 per-poll telemetry 轨量化两者差。
2. **running-max 来源**：回测用 factory observed detail（IEM/METAR patch 链）；live 用 `observations/latest.json`（同一 METAR 官方链，实时）。同物理量、同链路，实时版更新更快。
3. **城市覆盖**：回测 36 城全 ladder；live 当前 `snapshot-targeted` 仅 ~5 城（见下）。这是**数据可得性**差异，不是过滤差异——覆盖变宽后 promotion 分母自动补齐，runner summary 的 `coverage_note` 显式标注 `narrow_targeted_coverage` vs `full_ladder`。

## 四、parity 证据（可复跑）

- 公式 parity：回测触发行上 `1−d1_no_bid` / mid 与 runner 公式 **2/2** 数值一致。
- anchoring parity：取 2026-07-08 本地原始快照重建 ladder，喂 runner `find_current_and_d1`，其选出的 d1 档与回测 atlas `d1_no_bracket` **5/5 精确匹配**（7 城重叠、5 城有 d1）。
- 自测：`tail_distance` F@85=1 / C@28=1；平局取最高 ask 档。

## 五、采集恢复的安全发现（未盲执行）

要把 live 覆盖从 ~5 城恢复到 36 城需切 `snapshot-full`。审计 `start_mac_weather_data_feed_jrs_tmux.sh` 发现：该 committed 启动器**未设** `WEATHER_DATA_FEED_SNAPSHOT_COMMAND`（默认 targeted），且把 `HIGH_FREQUENCY_OBSERVATIONS_ENABLED` 默认设 **0**，而当前运行的 feed 这些是**开着**的（live 快源/hko 头在消费）。→ **用该启动器盲重启会关掉 live 头依赖的高频观测**。因此不盲重启生产 feed；安全路径是独立的全 ladder orderbook 采集 loop（不碰 live feed），作为单独一步执行/确认。

## 六、漂移是否改变之前的结论？——不改变（已核）

两处漂移**都只在 runner（live/shadow）里，回测脚本不含它们**，已逐项验证：
1. `research_market_calibration_curve_v1.py` grep `obs_age/age/freshness` = 空 → 回测从未加 obs 门槛。
2. 回测 d1 口径直接用 atlas `d1_no_ask/d1_no_bid`（factory 已按最高 ask 定档）→ 本就是"修复后"口径。
3. 重跑 probe 数字逐位不变：all-rows +2.18% CI[+0.04%,+4.47%]、first-row +2.24% CI[-0.64%,+5.19%]、forward +6.62% CI[+3.47%,+9.89%]。

方向上：漂移让 live 本来比回测**更严**（多 45min 门槛 + d1 可能选错档），修复是把 live **对齐**回测。因此校准曲线的全部结论（静态 taker 面关闭 / d1_yes_high_mid 为唯一候选 / `inconclusive_positive_signal_shadow_only` 不 live）**均不变**。唯一新增的 forward-only 差异是 live 允许 45–120min obs（回测 population 最旧 60min），已用 `obs_age_in_backtest_band` flag 分层，只在 shadow 自身 forward 记录里体现，不回改回测。

## 结论

shadow 的**入场条件、价格口径、d1 身份、结算标签与回测逐条一致**，两处早期漂移已修，剩余为已标注的结构性差异。漂移仅在 runner、回测结论不变。promotion 以 shadow 自身 forward 记录为准。**当前不 live。**
