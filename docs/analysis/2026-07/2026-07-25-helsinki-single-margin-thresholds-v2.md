# Helsinki FMI 单次 Cross Margin 回放 v1

## 目标和口径

- target：比较第一次可用单次 FMI 信号相对旧 exact bracket 上沿 `+0.5/+0.6/+0.7/+0.8°C` 的准确率、时间和可执行盘口。
- grain：`target_date × previous exact bracket` 的首个信号；decision clock 为 source first-seen，不是 observation timestamp。
- 共同 gate：距离下一 routine METAR report clock 不超过 `20.0` 分钟；只改变 margin，不叠加连续确认。
- final label：最终 winning bracket 是否离开旧档；next label：下一份 routine METAR 是否立即离开旧档。
- book：合并 `fast_source_stale_book` 与 `source_event_ladder_repricing_shadow`；collector 首次 Helsinki/FMI detect 为 `2026-07-09T13:45:48.414916+00:00`。采集后没有 archive 的日期记 coverage gap。
- 执行：signal 后第一张可见 fresh priced quote 立即判断；top ask `≤0.97` 且 top depth `≥10.0` 就买 `10.0` 股，否则不交易。不等待后续价格或深度改善，不使用 120 秒 eligibility gate。
- fee/PnL：按第一张 quote 的 ask、官方 Weather taker fee 和 final settlement 计算；这是 research replay，不是 actual fill。

## 结果

### Signal 正确率

| 单次 margin | 采集后信号/日期 | final 正确率 | Wilson 95% 下界 | next METAR 正确率 |
|---:|---:|---:|---:|---:|
| +0.5 | 51/12 | 50/51 (0.9804) | 0.897 | 38/51 (0.7451) |
| +0.6 | 41/11 | 41/41 (1.0) | 0.9143 | 33/41 (0.8049) |
| +0.7 | 35/11 | 35/35 (1.0) | 0.9011 | 30/35 (0.8571) |
| +0.8 | 25/11 | 25/25 (1.0) | 0.8668 | 22/25 (0.88) |

### 第一张 fresh quote 直接执行

| 单次 margin | first-priced coverage | 当场可执行/日期 | 成交正确率（Wilson low） | principal | fee | PnL | ROI（date-block 95% CI） | quote延迟中位/最大 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| +0.5 | 28/51 | 8/7 | 8/8 (1.0; 0.6756) | $69.63 | $0.4154 | $9.9546 | 0.1421 [0.0673, 0.2286] | 39.244s/140.502s |
| +0.6 | 19/41 | 6/6 | 6/6 (1.0; 0.6097) | $53.42 | $0.2621 | $6.3179 | 0.1177 [0.0365, 0.2141] | 32.277s/63.561s |
| +0.7 | 12/35 | 4/4 | 4/4 (1.0; 0.5101) | $36.38 | $0.1476 | $3.4724 | 0.0951 [0.0322, 0.2349] | 37.111s/60.285s |
| +0.8 | 8/25 | 3/3 | 3/3 (1.0; 0.4385) | $26.7 | $0.1321 | $3.1679 | 0.1181 [0.0294, 0.3169] | 17.703s/51.517s |

完整逐事件见 `threshold_event_ledger.csv`；逐日 coverage 见 `collector_date_summary.csv`；汇总见 `threshold_summary.csv`。

## 可执行盘口对应日期

| margin | 第一张 priced quote 当场可执行日期（行数） |
|---:|---|
| +0.5 | 2026-07-09×2, 2026-07-14, 2026-07-17, 2026-07-18, 2026-07-20, 2026-07-22, 2026-07-23 |
| +0.6 | 2026-07-14, 2026-07-17, 2026-07-18, 2026-07-20, 2026-07-22, 2026-07-23 |
| +0.7 | 2026-07-17, 2026-07-18, 2026-07-20, 2026-07-23 |
| +0.8 | 2026-07-17, 2026-07-18, 2026-07-23 |

## 直接结论

- `120s` 已从交易 eligibility 中删除。runner 应当立即抓 book；历史 replay 只用第一张实际归档 quote，quote 延迟单列为执行质量，不据此筛交易。
- 不允许等待后续盘口改善：例如 `+0.5` 的唯一 final 错误（7/17 previous 24 NO）第一张 ask `0.16` 但 top depth 只有 `6.75`，严格 10 股口径不成交；不能等 389 秒后深度变成 `14.43` 再假装直接成交。
- 阈值结论只使用采集启动后的同分母行；没有 archive 的 signal 日期保留为 coverage gap，不从策略分母删除。
- signal 层 `+0.6/+0.7/+0.8` 都是 100%，但直接可执行层四档目前也全胜，样本只有 `6/4/3/2` 笔。`+0.5` 的已覆盖直接执行 PnL/ROI 最高，`+0.6` 的 signal 误判更少；现有 book coverage 不足以证明哪个 live 更优。

## 双漏斗

- signal funnel：Helsinki/FMI causal comparisons `1476` → collector 启动后首信号 → threshold 子集。
- evidence funnel：collector 后 signal → first-priced archive coverage → 第一张 quote 当场满足 `ask≤0.97 & depth≥10` → hypothetical direct fill。
- collector active target dates：`2026-07-09, 2026-07-14, 2026-07-15, 2026-07-16, 2026-07-17, 2026-07-18, 2026-07-20, 2026-07-22, 2026-07-23, 2026-07-24, 2026-07-25`。信号存在但 archive 不工作的日期是 coverage gap，不算策略筛除。

## 结论边界

这是同分母、多阈值探索性 replay；没有独立 frozen forward，且 direct-book coverage 薄。阈值只能决定下一阶段 shadow 对照，不能单凭本表修改 live。
