# Tmin cross 触发口径与假突破率：结算同源 METAR-basis 历史重放

状态：`research replay / signal-level only / no price replay / 不改 frozen policy`

## 单轮 brief

- hypothesis（可证伪）：Tmin strict-cross 的"失败率"（结算落回刚离开的上一档）主要由观测源与结算
  基准的口径差与夜里时段结构决定；用结算同源 METAR 序列触发可消除 lane 假突破类失败，且失败率
  在 ~4 个月窗内可测出稳定基础比率。
- data scope：Seoul(RKSI)/Tokyo(RJTT)，target-date 2026-04-15..2026-08-20（市场存在性上限）；
  观测=IEM METAR 档案（30-min）；标签=gamma lowest-temperature 事件官方结算（backfill 215 城-日，
  交叉可标注 352/393）。全部为已结算历史（非 forward），样本已看过，只作机制定标。
- market capture scope/policy：N/A（无历史盘口，明确不做价格级回放）。
- collector budget：N/A（无新增采集）。
- acceptance gates：probability=报告 NO-loss 基础比率与按时段/月份切片（date-blocked bootstrap CI）；
  baseline=与重叠周现行 lane 触发对比；forward/execution=不在本轮。
- 唯一动作：offline replay（coverage/机制定标）。
- 不在范围内：live/shadow 变更、参数修改、其他城市、价格与 fee 回放。

## 结算标签对账（WU → METAR 档案）

- Seoul/Tokyo lowest-temperature 市场 resolution source 均为 **Wunderground Daily Observations 表**
  （RKSI / RJTT）。WU API 公共 key 被拒、页面客户端渲染不可抓。
- **IEM METAR 档案（含全部 30-min 报文）在 2026-08-12..20 的 18/18 夜完整复现两市官方结算标签**
  （all 与 hourly-only 子集一致）→ WU 表 ≡ METAR 报文流；标签可经 IEM 历史回补，无需 WU。
- 8/15 Seoul 反例定位：官方 METAR 流当夜最低 25（从未打 24），结算 25；我们 lane 里 `metar_temp_c`
  的 24.0 来自 AMOS 页内嵌字段，与官方 METAR 档案存在不一致 → **lane 内 metar 字段不得再当标签源**。
- METAR 报文为整度 → 无 0.1°C 边界取整歧义（10-min lane 的 23.4/23.5 问题在 METAR 基准下不存在）。

## 重放合同（镜像现行 cross 合同）

per local day（15:00Z..15:00Z）：METAR 温度 → `bracket=int(t+0.5)`；running-min 档位下降即
strict cross；表达=刚离开暖档 NO；**NO-loss ⇔ 结算==该档**。无价格/深度（历史 ask 不可得）。

## 结果（2026-04-15..2026-08-20）

| 城市 | 有 cross 夜 | cross 数(标注) | NO-loss 全体 | 首跨(rank1) | 后续跨 |
|---|---|---|---|---|---|
| Seoul | 112 | 208 (186) | **36/186 = 19.4%** CI[14.7,24.3] | 16.0% CI[9,23] | 23.3% |
| Tokyo | 104 | 185 (166) | **35/166 = 21.1%** CI[16.2,26.3] | 14.9% CI[8.5,22.3] | 29.2% |

按月：Seoul 19/26/19/15/17%（4-8月），Tokyo 16/18/18/25/28% —— 基础比率 ~20% 无干净季节趋势。

**按时段（当地时间，最强结构）**：

| 时段 | Seoul | Tokyo |
|---|---|---|
| 00:00–03:00 | 12% | **6%** |
| 03:00–06:00 | **27%** | **34%** |
| 18:00–24:00 | 28% | 31% |
| 06:00–18:00 | 22% (n=9) | 0% (n=10) |

## 含义

1. **机制基础比率首次定标**：即便用结算同源序列，~20% 的 cross 不钉住。保本 NO ask ≈ 1−loss率：
   深夜段(00-03) ≈ 0.88–0.94，凌晨段(03-06) ≈ 0.66–0.73。**flat cap90 在凌晨段按基础比率为负 EV**
   ——与观测周吻合（赢单 0.47/0.53 在便宜尾部，唯一输单 0.85 是 03:47 KST 凌晨跨）。
2. **时段是下一个政策维度**：00-03 的 rank-1 cross（Seoul 12%/Tokyo 6%）是最好人群；03-06 与傍晚
   ~1/3 失败。时间条件化的 cap/入场景比统一 0.90 更贴机制——留给下一轮假设，不在本轮改。
3. **重叠周对比**：METAR-basis 在 8/14..20 两市 14 cross 12 胜 2 负；Seoul 8/16 lane 的 NO@24 假突破
   （输单）在 METAR 基准下**不存在该信号**（官方 METAR 当夜未跌破 24.5）；两笔 METAR 失败（Seoul 8/15
   NO@25、Tokyo 8/16 NO@24）均为凌晨段 cross，与时段结构一致。
4. lane-basis 的假突破率**无法历史回放**（AMOS 实时端点无历史，journal 自 8/13）；重叠周 lane 4/7 夜
   夜低点取整低于结算（METAR 0/7 假突破）。lane 触发只能向前积累对照。

## 限制

- signal-level only：无历史 ask/fee → 无组合 PnL、无 cap90 漏斗重放；价格级问题仍由现行 shadow 攒。
- Tokyo 10-min AMeDAS 历史档案未接入（JMA portal 自动化未做）；本轮 Tokyo "全链" 以 RJTT METAR
  （与结算同源）承载，AMeDAS-lane 与 METAR 的差异只在 8 夜重叠窗验证过（7/7 一致）。
- 重放用 IEM 档案 PIT（事后档案），非 first-seen 时钟；对"cross 是否钉住"的标签级问题无影响，
  但不能直接推导可执行入场时刻。

## 产物

- `runtime/research/tmin_cross_metar_basis_replay_v1/`（raw IEM CSV、结果 JSON、重放脚本）
- gamma 结算 backfill：`cache/pm_history_lowest/`（215 城-日，4/15..8/20，幂等可重跑）
