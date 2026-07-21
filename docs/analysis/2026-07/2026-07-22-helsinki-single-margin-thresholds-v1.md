# Helsinki FMI 单次 Cross Margin 回放 v1

## 目标和口径

- target：比较第一次可用单次 FMI 信号相对旧 exact bracket 上沿 `+0.5/+0.6/+0.7/+0.8°C` 的准确率、时间和可执行盘口。
- grain：`target_date × previous exact bracket` 的首个信号；decision clock 为 source first-seen，不是 observation timestamp。
- 共同 gate：距离下一 routine METAR report clock 不超过 `20.0` 分钟；只改变 margin，不叠加连续确认。
- final label：最终 winning bracket 是否离开旧档；next label：下一份 routine METAR 是否立即离开旧档。
- book：合并 `fast_source_stale_book` 与 `source_event_ladder_repricing_shadow`；collector 首次 Helsinki/FMI detect 为 `2026-07-09T13:45:48.414916+00:00`。采集后没有 archive 的日期记 coverage gap。
- 同时报 decision 后 `120.0` 秒 fast SLA，以及旧 collector 一轮 `600.0` 秒内真正出现过的首个 priced/executable quote。
- 假设交易：top ask `≤0.97` 且深度 `≥10.0` 的 `10.0` 股 taker，扣 Weather fee；不是 actual fill。

## 结果

| 单次 margin | 采集后信号/日期 | final 正确率 | 有archive日期 | ≤120s priced/executable | ≤10m priced/executable | 10m口径PnL/ROI |
|---:|---:|---:|---:|---:|---:|---:|
| +0.5 | 41/10 | 40/41 (0.9756) | 7/10 | 19/6 | 20/10 | $7.6312 / 0.0926 |
| +0.6 | 33/9 | 33/33 (1.0) | 6/9 | 14/5 | 14/6 | $8.2037 / 0.1584 |
| +0.7 | 28/9 | 28/28 (1.0) | 6/9 | 10/3 | 10/4 | $1.5615 / 0.0406 |
| +0.8 | 20/9 | 20/20 (1.0) | 6/9 | 7/2 | 7/2 | $0.7617 / 0.0396 |

完整逐事件见 `threshold_event_ledger.csv`；逐日 coverage 见 `collector_date_summary.csv`；汇总见 `threshold_summary.csv`。

## 可执行盘口对应日期

| margin | ≤120s executable 日期（行数） | ≤10m executable 日期（行数） |
|---:|---|---|
| +0.5 | 2026-07-09, 2026-07-14, 2026-07-17, 2026-07-18, 2026-07-20×2 | 2026-07-09×2, 2026-07-14, 2026-07-16, 2026-07-17×2, 2026-07-18×2, 2026-07-20×2 |
| +0.6 | 2026-07-14, 2026-07-17, 2026-07-18, 2026-07-20×2 | 2026-07-14, 2026-07-17, 2026-07-18×2, 2026-07-20×2 |
| +0.7 | 2026-07-17, 2026-07-18, 2026-07-20 | 2026-07-17, 2026-07-18×2, 2026-07-20 |
| +0.8 | 2026-07-17, 2026-07-18 | 2026-07-17, 2026-07-18 |

## 直接结论

- 上一版的 `1/2` 只代表 120 秒内、且只读取后一套 archive 的结果，不是采集后总可执行数；该口径已纠正。
- `≤120s` 是新架构应追求的反应口径；`≤10m` 是旧 collector 实际完整一轮口径。后者能说明历史上盘口曾经可买，但不能假装成 source first-seen 时即可成交。
- 阈值结论只使用采集启动后的同分母行；没有 archive 的 signal 日期保留为 coverage gap，不从策略分母删除。
- 修正后 `+0.6` 在 120 秒内有 5 个可执行 expression、旧 collector 10 分钟内有 6 个；对应 final `33/33`，10 分钟口径假设 PnL `+$8.2037`。它仍是本轮 shadow challenger，但不因这次修正直接改 live。

## 双漏斗

- signal funnel：Helsinki/FMI causal comparisons `1185` → collector 启动后首信号 → threshold 子集。
- evidence funnel：collector 后 signal → 当日 archive coverage → priced quote → `ask≤0.97 & depth≥10` hypothetical fill；120 秒和 10 分钟分别列示。
- collector active target dates：`2026-07-09, 2026-07-14, 2026-07-15, 2026-07-16, 2026-07-17, 2026-07-18, 2026-07-20`。信号存在但 archive 不工作的日期是 coverage gap，不算策略筛除。

## 结论边界

这是同分母、多阈值探索性 replay；没有独立 frozen forward，且 direct-book coverage 薄。阈值只能决定下一阶段 shadow 对照，不能单凭本表修改 live。
