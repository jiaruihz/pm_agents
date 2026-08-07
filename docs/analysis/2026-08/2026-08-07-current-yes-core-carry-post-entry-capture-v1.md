# Core Carry post-entry carry-capture v1

## 数据快照

- 数据源：Core raw `pre_live_scores/state_decisions`、PIT full-ladder orderbook、canonical `settlement_outcomes`。
- DB build：`2026-08-07T08:42:37.856550+00:00`；fact_trades `4948` rows。
- signal entries：`47`；canonical settled `33`；missing/open `14`。
- executable replay：`29` entries / `10` target dates。
- 本报告是 signal-policy replay，不是 actual live realized PnL；unsettled 不计入收益。

## 结论与策略动作

**保留 Core 入场不动，新增 `post-entry carry capture` 作为 zero-notional forward shadow。暂不改 live。**

执行语义：每份新官方报文到达后，重新读取原 YES token；若完整可卖 bid ladder 扣官方 exit fee 后，已比入场成本多锁定至少 `3c/share`，则全平；否则继续持有。没有 book 或深度不足只记 coverage gap，不触发 fallback。

在可执行同分母上，10 股 hold PnL `$+10.75`，candidate `$+16.37`，delta `$+5.62`；target-date bootstrap CI `[-6.42, +25.41]`。
触发 `21` 笔，其中 final loss `1`、winner `20`；saved loss capital `$8.93`，sacrificed winner profit `$3.31`。

这个点估改善主要来自 Lucknow：它在跨到 33 之前出现过足够深度的 90c 附近 bid，+3c carry capture 可以把最终全损改成小幅已实现盈利。它救不了所有错误：Singapore、Chengdu 和 Manila 在跨档前没有达到相同的 fee-adjusted 盈利退出条件。

## 阈值敏感性（同一已看过窗口）

| net gain floor/share | exits | candidate PnL | delta vs hold |
|---:|---:|---:|---:|
| 0c | 28 | $+11.27 | $+0.52 |
| 1c | 27 | $+11.56 | $+0.81 |
| 2c | 23 | $+15.92 | $+5.17 |
| 3c | 21 | $+16.37 | $+5.62 |
| 5c | 16 | $+8.76 | $-1.99 |
| 8c | 9 | $+9.76 | $-0.99 |
| 10c | 6 | $+10.19 | $-0.56 |

`3c` 是本窗口诊断后选出的 shadow operating point，不是 clean forward 结果；因此不能据此上线。

## 已跨档案例

| city | target_date | held | capture before cross | capture report |
|---|---|---:|---|---|
| Singapore | 2026-07-27 | 31 | False |  |
| Chengdu | 2026-07-27 | 29 | False |  |
| Lucknow | 2026-07-31 | 32 | True | 2026-07-31T10:00:00Z |
| Manila | 2026-08-07 | 28 | False |  |

## 证据门与下一步

- significance=`FAIL`；baseline=`PASS`；forward=`FAIL`；conclusion=`inconclusive`。
- 新天气字段不作为 entry hard gate：此前同分母 forward 没有增量。它们在这里负责定义“新报文/新状态”的 event clock；真正退出动作由可成交净收益决定。
- 下一步唯一动作：zero-notional forward 记录每个 Core position 的新报文、held-token 10-share sell ladder、would-exit 与后续 settlement；阈值冻结为 +3c，不再用同一窗口调参。
- runner 已实现于 `scripts/ops/weather_current_yes_core_carry_post_entry_capture_shadow_v1.py`；它固定 `zero_notional/no_order_placed/execution_calls=0`。

## 8 环

描述性=PASS；统计推断=PASS；信号判别=NA；概率分布=NA；执行微结构=PASS（archived full ladder）；容量=PASS 到 10 shares；组合=PASS（target-date block）；基准/反事实=PASS；frozen forward=FAIL。
