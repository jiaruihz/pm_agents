# Core Carry pullback add-maker v1

Status: `gross maker opportunity only / incremental attribution invalid / no-live-change`

## 结论

固定 Core Carry 入场和原 10-share taker 不动，把 5-share maker
风险预算静态挂在 entry ask 下方的反事实，在当前可比较的 live-era
窗口里只有**很短的 15 分钟 TTL**有正点估；30 分钟以上全部变负。
但这个 replay 的 baseline 是 10 taker only，而当前生产本来已有 5-share
maker，因此这些数字是 maker leg 相对纯 taker 的**毛机会**，不是新增
“补仓 maker”相对当前执行策略的增量 alpha。

主问题对应的 `entry ask - 7c / 15m`：56 个已结算信号中只成交 1 个，
即 Amsterdam 2026-08-10；原 taker 总 PnL `+$1.824`，candidate
`+$2.824`，增量 `+$1.00`。它证明 Amsterdam 的结构可执行，但只有一个
case，不能据此改 live。

事后网格中最好的是 `-2c / 15m`：7 个保守成交全部最终胜出，增量
PnL `+$4.40`，总 PnL 从 `+$1.824` 到 `+$6.224`，总 deployed-cost ROI
从 `+0.35%` 到 `+1.13%`。但 7 个成交全部来自最后 7 日期窗口，前 10
日期为 0 fill；而且 Amsterdam 已用于提出假设。即使 target-date bootstrap
CI 为 `[+$0.90,+$8.55]`，也不能把同窗选出的最优格点当 frozen-forward
显著性。

当前 v3 maker 初始价是
`min(best_bid+tick, best_ask-tick, retained-edge cap)`，随后最多两次向上
reprice；正常一档 tick 下通常已经在 `entry ask-1c`。只要该订单仍 active，
价格跌到 `-2c/-3c/-7c` 必然先穿过原 maker，所以不能再并行增加一个
5-share“补仓 maker”，否则既重复归因又超过 15-share 风险预算。

因此交易动作是：**不改 live，不把任何格点升为新 maker。** 下一次正确的
同分母比较必须是：A=current v3 maker lifecycle；B=同一 5-share 预算的
deeper-static replacement；C=current v3 + 在 source update 后 thesis 仍有效时
revalidated re-arm。比较 actual queue/order lifecycle 后再决定；30m 以上的
静态挂单已经明确不做。

## 固定分母与执行口径

- signal funnel：current raw first selected Core entries 62 个，2026-07-25..08-10，
  17 target dates、25 cities。
- evidence funnel：canonical settlement 56 个/15 dates；6 个旧 settlement gap
  （7/29–30）排除，不从 METAR 推断标签。56/56 已结算票都有 post-entry
  canonical book，62/62 token 均有 book。
- baseline：原 10-share taker effective cost（含实际 weather taker fee），持有到结算。
- candidate：额外 5-share resting maker，limit=`entry ask-discount`，无 reprice；
  只有 PIT `available_at_utc` 在 TTL 内、且完整盘口显示 `ask<=limit` 的可见
  ask 深度至少 5 股，才保守判 fill。单纯 future touch 只作 coverage diagnostic，
  不算成交；maker cost 按 limit 计、maker fee/rebate 均按 0。
- 盘口恢复：fill 后出现 `best bid >= entry ask`。`no qualifying pullback` 只表示
  未触到相应补仓价，不强称逐 tick 单调上涨。

历史 frozen 2026-06-02..07-08 的 136 个 Core entries 只有较粗 checkpoint，
没有同合同的 5 分钟 PIT full-book/queue 证据。它们保留为 probability/hold
基线，不用 hourly future-touch 伪造 maker fill，因此本报告的“全 Core”是
**具备可比较执行盘口的完整 live-era universe**，不是项目所有历史 signal。

## 相对 10-taker-only 的毛收益网格

| TTL | discount | fills (W/L) | maker增量PnL | candidate总PnL | candidate总ROI |
|---:|---:|---:|---:|---:|---:|
| 15m | 2c | 7 (7/0) | +$4.40 | +$6.224 | +1.13% |
| 15m | 3c | 6 (6/0) | +$4.15 | +$5.974 | +1.10% |
| 15m | 5c | 2 (2/0) | +$1.85 | +$3.674 | +0.70% |
| 15m | 7c | 1 (1/0) | +$1.00 | +$2.824 | +0.54% |
| 15m | 10c | 0 | $0.00 | +$1.824 | +0.35% |
| 30m | 2c | 10 (8/2) | -$3.65 | -$1.826 | -0.33% |
| 60m | 2c | 12 (9/3) | -$7.65 | -$5.826 | -1.02% |
| 120m | 2c | 14 (11/3) | -$6.90 | -$5.076 | -0.88% |

完整 5×4 网格在 frozen artifact。这张表不能与当前 live maker 叠加。
更深 discount 并不能修复长 TTL：30m 的
3/5/7/10c 增量分别为 `-$3.75/-$5.75/-$6.30/-$5.70`；60m 与 120m
所有格点也均为负。

## 两种盘口结构的分布

以收益最好的 `-2c / 15m` 看 56 个 settled entries：

- 回调并恢复：7（12.5%），全部最终赢家；城市为 Amsterdam、Chongqing、
  Helsinki、Shanghai×2、Taipei、Tokyo。
- 未回调到补仓价且最终赢家：45（80.4%）。这是 Core Carry 的主体，表现为
  直接上行或高位稳定，补仓 maker 不成交也不增加收益。
- 未回调到补仓价但最终失败：4（7.1%）：Chengdu、Lucknow、Manila、Singapore。
- 15m 内“回调后失败”：0。

以 Amsterdam 对应的 `-7c / 15m` 看：回调并恢复只有 1/56；51/56 是
未触补仓价的最终赢家，4/56 是未触价的最终失败。

TTL 延长以后结构反转：`-2c / 30m` 虽增至 10 fills，但把 Chengdu、Manila
两个最终失败也接下来，maker 增量立即转为 `-$3.65`；60m 又接到 Singapore，
降至 `-$7.65`。所以真正有效的不是“低价越跌越买”，而是**只捕捉入场后
极短时的流动性回撤**。较晚下跌更像 thesis deterioration/adverse selection。

Amsterdam 具体证据：入场 ask `0.87`；实际 raw 只有 10-share taker order，
没有 maker order，原因是当时 maker clock eligibility 未通过。14m50.9s 后
PIT full book 为
`0.74/0.79`，`ask<=0.80` 可见深度 8 股；约 62 分钟后 bid 恢复到至少
原 entry ask，最终 YES。若原 maker 当时成功 active，它应在跌到 0.80 前
就先成交。因此这笔是**原 maker 缺席/clock-rearm 的机会案例**，不是新深价
maker 的独立证据。5 股按 `0.80` 的 `+$1.00` 只保留作毛机会计量。

## 数据修复与影响半径

研究 loader 原先只读取 legacy `orderbook_snapshot_*`，且默认 roots 未包含
当前 canonical `market_books/batches`；这会漏掉 Amsterdam 8/10。现已同时
读取两种 batch 名、默认加入生产 current root，并以 `available_at_utc` 排序；
settlement join 增加 canonical `city+target_date+bracket` 精确回退。

修复后本次 coverage 为 1,694 files、62/62 tokens、11,305 matched book rows。
旧 archive-only 错误运行只有 48/62 tokens、4,511 rows，已保留但标为无效
对照，不用于结论。此前 `+3c` exit overlay 的 headline 从 `-$4.09` 变为约
`-$4.53`，方向不变，仍然拒绝该 exit；本 bug 没有触碰生产下单。

## 产物

- runner：`scripts/analysis/reheat_risk/core_carry_pullback_add_maker.py`
- conservative primary entry replay：artifact `primary_entry_replay.csv`
- full grid/denominator/CI：artifact `result.json`
- valid run：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/current_yes_core_carry_pullback_add_maker_v1/pullback_add_maker_20260811_v2`
- invalid archive-only comparison：同 family 下 `pullback_add_maker_20260811`
