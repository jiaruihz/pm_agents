# HeadA rain/convective forward shadow v1

## 结论

继续深挖后，当前最值得保留的不是“禁 GFS”，而是一个有物理含义、跨时间段方向一致的候选：

> 对原 HeadA `dist>0` exact-bracket YES 分母，只标记 D-1 forecast peak window
> `precipitation probability >= 50%` 的 `rain_convective` ticket。

它仍然只能是 **zero-notional forward shadow**，但已经比 source、城市、价格带等事后切片更接近可研究策略：

- 历史前半（非 PIT）：29 tickets / 15 dates，7 wins，fee-adjusted taker ROI `+116.6%`；
- 历史后半复现（非 PIT）：45 / 24，12 wins，ROI `+136.7%`；
- 最近 frozen PIT shadow：19 / 7，4 wins，ROI `+57.5%`；
- 最近 PIT 的 ECMWF 为 3/15、ROI `+50.9%`；GFS 为 1/4、ROI `+81.0%`。

历史后半按 `target_date` block bootstrap 的 ROI 95% CI 为
`[+21.7%, +269.4%]`，且相对非雨候选的 ROI uplift CI 为
`[+21.2%, +295.4%]`。最近 frozen PIT 的 CI 仍为
`[-100.0%, +179.0%]`，因此暂不升 live。

## 固定分母与时钟

- 原始 signal denominator：HeadA `dist>0`、ask `0.05..0.20`、D-1 entry 的 exact-bracket YES；
- selector 只使用 entry 时刻可知的次日 peak-window POP，不使用 target-day 实际天气；
- 历史 333 行的天气曲线是 archive 后补，不满足完整 PIT，只作机制发现与时间复现；
- 最近 84 行 / 8 target dates 是 frozen decision-time PIT；
- ROI 使用 fee-adjusted taker cost，便于做保守可执行评估；不把 public price-history proxy 当作可成交盘口。

## 三段时间检验

| block | all HeadA | rain rows / dates | wins | rain ROI | complement ROI |
|---|---:|---:|---:|---:|---:|
| 2026-05-06..06-02 historical discovery（非 PIT） | 146 | 29 / 15 | 7 | +116.6% | +47.8% |
| 2026-06-03..06-30 historical replication（非 PIT） | 187 | 45 / 24 | 12 | +136.7% | -12.5% |
| 2026-07-16..07-25 current frozen PIT | 84 | 19 / 7 | 4 | +57.5% | +4.9% |

最近 PIT 平均为每个全量 target date `2.38` 张 rain ticket，单日最多 8 张；
19/19 都有 direct best ask，median best-ask size `32.89 shares`，median spread `1c`。
这是容量描述，不是 fill 保证。

最近 8 天逐日结果如下；彩票收益明显不平滑：

| target date | all rows / wins / ROI | rain rows / wins / ROI |
|---|---:|---:|
| 07-16 | 8 / 2 / +87.8% | 1 / 1 / +464.8% |
| 07-19 | 5 / 1 / +99.4% | 1 / 0 / -100.0% |
| 07-20 | 11 / 3 / +91.3% | 1 / 0 / -100.0% |
| 07-21 | 9 / 0 / -100.0% | 3 / 0 / -100.0% |
| 07-22 | 15 / 2 / +6.5% | 2 / 0 / -100.0% |
| 07-23 | 11 / 0 / -100.0% | 0 / 0 / NA |
| 07-24 | 9 / 0 / -100.0% | 3 / 0 / -100.0% |
| 07-25 | 16 / 4 / +93.7% | 8 / 3 / +189.0% |

rain 的 +57.5% 主要由 07-16 与 07-25 两天贡献；去掉最大 cohort 日 07-25 后，
其余 11 tickets 仅 1 win、ROI `-33.4%`。这正是当前 block CI 跨 0、只能继续
forward shadow 的原因；不能把三段正点估写成稳定日收益。

## 为什么它比“禁 GFS”合理

GFS 在最近 8 天是 1/30、ROI `-73.2%`，但历史 GFS 是 16/111、ROI
`+21.3%`；最近的 GFS 强多源热共识也只有 1/20，说明问题不是“GFS 一家报错”。
若直接禁 GFS，会把历史已有赢家以及最近唯一的 GFS rain winner 一起删掉。

rain/convective 更像 forecast-error distribution 的 moderator：对流发生时钟会同时影响
辐射抑制、阵前增温、风向突变和尾部分布宽度。它不是“雨天一定更热”，而是 exact bracket
市场可能仍按过窄分布定价。后续应使用连续的 POP、cloud、wind、source bias 和
settlement-lattice distance 估计 `P(outcome)-market`，而不是永久使用一个二元天气 gate。

## 多源、bias 与实际升温路径带来的更新

1. entry 时把多源 forecast 直接混合进概率并没有帮助：最近 Brier 比原 rolling-bias baseline
   差 `+0.00461`；联合 multi-source + bias 差 `+0.00760`。不能把“模型更多”当成自动增益。
2. prior rolling bias 在历史 logloss/Brier 上有小幅改善；最近只对 ECMWF 有帮助，
   对 GFS 反而有害。因此 bias 应做 source/city/state 的连续校准，不是 source hard gate。
3. 补齐 7/24–7/28 canonical 后，target-day 12:00 的实际升温路径相对同刻
   contemporaneous market proxy 的 Brier 点估改善 `-0.00313`，
   CI `[-0.00836, +0.00114]`；gamma=0.25 的 logloss CI 略低于 0，
   但预先固定的 gamma=1 主检验未过。
4. 12:00 更新后未来 180 分钟价格的 signed move 为 `-2.70c`
   （CI `[-4.65c, -0.33c]`），与 weather delta 显著反向。它更像市场先过冲后回落，
   不能直接做 target-day 加仓信号。

在预注册的 rain/convective 子集内，market residual 的点估比全体 HeadA 更强：

| checkpoint | rows / dates | wins | Brier delta vs market | 95% target-date CI | logloss delta |
|---|---:|---:|---:|---:|---:|
| 09:00 | 12 / 7 | 2 | -0.01941 | [-0.09440, +0.00770] | -0.07628 |
| 12:00 | 12 / 7 | 1 | -0.01388 | [-0.07759, +0.00191] | -0.03421 |

这说明 `rain entry × warming innovation` 值得继续 frozen forward；但两组 CI 仍刚好跨 0，
且只有 12 张 exact ticket，不能据此挑 gamma 或恢复真实下单。

可检验的两阶段策略因此是：

```text
D-1 rain/convective HeadA candidate
        ↓
target-day 12:00 source-bias-adjusted warming innovation
        ↓
只在相对 fresh market 仍有 fee-adjusted residual 时 hold/add，否则只记录 exit shadow
```

当前 lifecycle direct-book 子集过小且 0 winner；已有 2c weather exit policy 虽比 hold 少亏，
仍显著差于 unconditional exit-all。因此两阶段策略未达到 executable upgrade。

## 执行口径：不全是 taker

- HeadA 当前生产姿态是 zero-notional shadow；若恢复执行，配置是 `maker-first_fraction=1.0`，
  不是纯 taker。
- 本报告 entry ROI 用 fresh ask + fee 的 taker 口径，是保守估值，不代表计划全部吃单。
- lifecycle 的 sell 用 direct bid、add 用 direct ask，均按 taker 评估；public
  `/prices-history` 只用于 lead/lag proxy，不当作可执行价格。

## 已落地的数据闭环与验收

- integrated-tail zero-notional shadow 已持续记录每张 ticket 的多源 calibration、source/city bias、
  D-1 cloud/POP/wind regime、target-day actual warming path、fresh book 和 feature-frame lineage；
- 2026-07-28 起 integrated-tail 每 5 分钟按 HeadA exact `condition_id` 从最新 snapshot 解析
  YES token，只抓当轮候选的 direct/proxy fresh bid/ask/top size/5c depth；首轮历史日 smoke
  6 rows 中 4 个 `ok`、1 个 closed-day token missing、1 个暂时 fetch failed，后续循环保留
  missing/failure 行而不把它们伪装成策略筛除；
- 2026-07-28 clean-checkout 缺 calibration CSV 导致 telemetry 全部 error 已修复，重启后
  6/6 rows `tail_telemetry_status=ok`、`pcal_v2_status=ok`、
  `forecast_source_calibration_status=ok`；
- rain selector 不改变原 HeadA eligibility，不下单，只作为预注册 shadow cohort。

下一次冻结验收在累计到 **至少 30 个新的 PIT target dates 且至少 80 张 rain ticket** 后执行：

1. 固定 selector，不再按城市/source/价格二次挑选；
2. probability score 必须相对同时间 fresh market 在 Brier 和 logloss 上均改善；
3. fee-adjusted ROI 与相对 complement uplift 的 target-date bootstrap 95% CI 均高于 0；
4. ECMWF/GFS 分别只做稳定性检查，不要求各自显著，但不允许单一 source 承担全部 PnL；
5. fresh-book coverage 至少 90%，maker queue/fill 与 taker fallback 分开报告。

若不过，保留连续 `rain intensity × bias × warming innovation` 概率建模数据，停止把
rain 二元 selector 当独立策略；不会再从少量失败票上追加新 hard filter。

## 产物

- `scripts/analysis/forecast_quality/research_heada_rain_convective_shadow_v1.py`
- `generated/heada_rain_convective_shadow_v1/temporal_scorecard.csv`
- `generated/heada_rain_convective_shadow_v1/source_scorecard.csv`
- `generated/heada_rain_convective_shadow_v1/target_date_bootstrap.csv`
- `generated/heada_rain_convective_shadow_v1/market_anchor_scorecard.csv`
- `generated/heada_rain_convective_shadow_v1/current_daily_scorecard.csv`
- `generated/heada_rain_convective_shadow_v1/summary.json`
- market-anchored lifecycle 见
  [heada-market-anchored-lifecycle-v1](2026-07-28-heada-market-anchored-lifecycle-v1.md)
