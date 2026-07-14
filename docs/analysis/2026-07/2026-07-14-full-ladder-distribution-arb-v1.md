# Full-Ladder Distribution Arbitrage v1

> 目标：换掉单档预测思路，穷举 complete-set / leave-one-out / any-k / mint-sell 表达，检查是否存在费用后、可执行、forward 的确定性结构利润。Research-only；zero notional。

## 数据与执行口径

- `3419` 个 PIT orderbook snapshot，覆盖 `2026-05-19..2026-07-14`；event 在城市本地日尚未结束。
- 只保留连续完整 ladder（含 top tail）、同 event 各腿 fetch span `<=30s`、每腿 top depth `>=5 shares`；固定 5 shares。
- entry 使用同 snapshot ask/bid 与官方 Weather taker fee；train `<2026-06-16`，forward `>=2026-06-16`；所有入选篮子 settlement audit 均为 exact-one-in-ladder。

## Forward 结果

| expression | opportunities | dates | 5-share cost | locked profit | ROI |
| --- | ---: | ---: | ---: | ---: | ---: |
| BUY all NO | 1 | 1 | $24.98527 | $0.01473 | +0.059% |
| BUY NO leave-one-out | 2 | 2 | $34.98670 | $0.01330 | +0.038% |
| BUY best any-k NO subset | 2 | 2 | $39.98195 | $0.01805 | +0.045% |
| BUY binary YES+NO | 0 | 0 | $0.00000 | $0.00000 | NA |
| mint then sell binary pair | 0 | 0 | $0.00000 | $0.00000 | NA |

- all-NO forward 只剩 Karachi 2026-07-05 一次，locked profit 仅 `$0.01473`；每腿增加 `0.1c` cushion 即归零。
- leave-one-out / best-subset forward 都只有 Tokyo 2026-06-20 与 Karachi 2026-07-05 两次，利润分别 `$0.01330` / `$0.01805`；`0.05c/leg` 级别的最小摩擦已足够清空。
- binary BUY 在 `976,102` 个可执行 condition observations 中 fee 后为 `0`；mint-sell 只有 Shenzhen 2026-06-07 一次 train 机会（+0.797%），forward 为 `0`。

## 为什么 all-YES / synthetic 不算新 alpha

- fee-aware top-of-book 筛选的 all-YES forward 有 `30` 次、点估 ROI `+0.759%`，但每腿 `0.5c` friction 后全样本机会归零，而且全腿 CLOB 成交不是 atomic。旧 270-basket 分母加官方 fee 后总体 ROI 为 -0.423%，两者共同说明这里只有瞬时 top-of-book pennies，不是可部署套利。
- NegRisk 可把 `NO_i` 原子转换为所有 `YES_j (j != i)`；因此 `sell NO_i + buy other YES`、`sell all NO` 等 synthetic 只是 all-YES/all-NO 的等价执行，不应重复记作独立策略。NegRisk conversion 可以 atomic，但随后多腿 CLOB fill 仍不 atomic。

## 结论

- **full-ladder 结构方向否决 live。** 最好的 NO-side forward 只有 1–2 个事件和 1–2 美分总利润，对最小价格漂移不稳；早期 train 的漂亮数字集中在 5/19–5/21 launch-stage 宽盘口，6/16 后不复现。
- 该搜索没有找到新的独立 alpha。后续不再扩展 synthetic 组合；策略研究应转向有时间优势、且能按 source/city 校准错误概率的单腿信息事件。

> frozen evidence: `/Users/deepsleep/projects/pm_agents/runtime/analysis_snapshots/full_ladder_distribution_arb_final_20260714.json`
