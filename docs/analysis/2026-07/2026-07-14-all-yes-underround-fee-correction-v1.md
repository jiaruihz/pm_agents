# All-YES Underround Fee Correction v1

## 结论

旧的 `+3.16%` 是 gross ROI，不是可执行 taker ROI。固定原报告 `297` 个 strategy-candidate observations、其中 `270` 个 settled observations 后，官方 fee 为 `9.38511` unit，gross PnL `+8.27800` 变成 fee-adjusted PnL `-1.10711`；ROI 从 `+3.16%` 变为 `-0.42%`，target-date bootstrap CI `-0.68%`..`-0.19%`。

因此 `all_yes_underround_basket_v0` 的 taker 表达式应从“正收益候选”纠正为 **fee 后显著为负**，不能 live。`270` 个 settled basket 在 gross 口径全部为正；扣 fee 后仅 `69` 个为正，`201` 个翻为 non-positive。

## Bug 与影响半径

- 根因：`build_all_yes_underround_basket_facts_v0.py:410-413` 直接用 `unit_payout - total_yes_ask_cost` 生成 `unit_pnl`，`:180-181` 又直接汇总该 gross 字段；全链没有 fee 字段或 fee 曲线。
- 被污染窗口：candidate event dates `2026-05-21`..`2026-06-16`；已结算绩效窗口 `2026-05-21`..`2026-06-14`。
- 受影响旧判断：`2026-06-15-all-yes-underround-basket-facts-v0.md` threshold `0.02` 的 `+3.2%`，以及同表所有 threshold ROI，均为 gross-only。
- 高 threshold 行只保留为 post-hoc sensitivity，不能据此把阈值改成 `0.03/0.05` 后宣称找到 alpha；同一 event 在多个 snapshot 重复出现，且没有 all-leg atomic fill 证据。

## 固定口径

- 每腿 entry：marketable `BUY_YES` taker。
- fee：`shares * 0.05 * price * (1-price)`，每腿订单 round 到 5 decimals。
- 主 ROI 分母仍是旧报告的 summed fill cost，另在 JSON 给出 cash+fee denominator sensitivity。
- bootstrap block：`event_date`，`5000` reps，seed `20260714`。

## 可复查输出

- 逐 basket：`docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1/per_basket_counterfactual.csv`
- 逐 leg fee：`docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1/per_leg_fee.csv`
- 逐日：`docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1/daily_summary.csv`
- threshold sensitivity：`docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1/threshold_sensitivity.csv`
- machine summary：`docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1/summary.json`
