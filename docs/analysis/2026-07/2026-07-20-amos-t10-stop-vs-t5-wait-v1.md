# AMOS previous-NO：T-10 早入止损 vs T-5 等待

## 数据快照

- 生成时间：2026-07-20 20:17 CST；输入扫描 `2026-07-08..2026-07-20`，实际 target dates `2026-07-09..2026-07-20`。
- 快源：JRS runtime `output/high_frequency_observations/*/high_frequency_observations.jsonl` 的 Busan RKPK / Seoul RKSI AMOS first-seen；routine label 来自 `output/source_events/*/sources.jsonl` 的 AviationWeather METAR。
- 盘口：`output/fast_source_stale_book/quote_snapshots.jsonl` 的 fresh previous-bracket NO bid/ask/depth；不使用 mid，不把 future touch 当 fill。
- 结算：`active_realtime_source_alignment_v1/daily_source_wu_settlement.csv` 的 WU/canonical bracket；Busan 7/20 用 weather-confirmed 32C provisional label。637 report-cycle rows 中 final-labeled 486、unsettled 151（23.7%）；固定 T-10 executable cohort 为 7 rows，其中 2/7 unsettled；`missing_bracket=0`。
- 记录：`21,530` source→next-METAR aligned rows，`637` report-cycle / previous-bracket states，T-10 executable signal cohort `7 rows / 4 dates`。
- 执行口径：固定 10 shares；T-10=`T-12..8` 内第一份 source-qualified fresh quote，T-5=`T-6..4` 内第一份 fresh quote；source age≤180s；入场 ask≤0.97 且 ask depth≥10。entry/exit 均按 Weather taker fee `shares×0.05×price×(1-price)`、总 fee round 5 decimals。
- 产物：[summary.json](generated/amos_t10_stop_vs_t5_wait_v1/summary.json) · [policy_rows.csv](generated/amos_t10_stop_vs_t5_wait_v1/policy_rows.csv) · [policy_summary.csv](generated/amos_t10_stop_vs_t5_wait_v1/policy_summary.csv)；脚本 `scripts/analysis/forecast_quality/research_amos_t10_stop_vs_t5_wait_v1.py`。

## 结论与动作

**统一策略暂不选 T-10 早入+止损；若只能在用户给的两种里选，Busan 选 T-5 等待。** T-10 早入的点估更高，但唯一可执行的 false/reversal 就是今天 Busan，反转时 best bid 深度只有 8 shares，无法证明 10 shares 能按显示价退出；到 T-5 时深度进一步降到 2.88。当前正点估建立在不可执行的 full-top-bid 假设和今天 Busan 下午的大 repricing 上，不满足 live 依据。

更合适的 shadow 仍是第三种：**相对 source trigger 等 5 分钟做 path revalidation，而不是固定等到 report T-5**。它会拒绝 Busan 上午、却能在下午 T-44 以约 0.23 入场；避免固定 T-5 的 0.87，同时不依赖事后止损流动性。

生产 family 保持 feature-only/shadow，不新增 live stop、不改 size。

## 固定分母与策略定义

同一 raw opportunity 是：在 T-12..8 首次出现 source-qualified cross，且 31/previous NO 能以 ask≤0.97、depth≥10 买 10 shares。

| policy | T-10 | source 在 T-6..4 仍 `>= X+0.5C` | source 已跌破 `X+0.5C` |
|---|---|---|---|
| A `wait_t5` | 不持仓 | T-5 ask仍可执行才买并持有到 final WU | 不交易，PnL=0 |
| B `dynamic_stop` | 立即 taker 买10 | 继续持有到 final WU | 第一次看到跌破就 taker hit NO bid |
| B-checkpoint | 立即 taker 买10 | 继续持有 | 到 T-5 才 hit bid，作为更慢止损 sensitivity |

`P(next METAR cross)` 只判断 source thesis；实际 PnL 仍用 `P(final WU leaves prior exact bracket)`。Busan 上午 next-METAR false、最终 NO win，两者不能互换。

## 双漏斗

### Signal funnel

| layer | grain | rows | dates |
|---|---|---:|---:|
| aligned source observations | source obs→next METAR | 21,530 | 12 target dates |
| report cycles | report-cycle / previous bracket | 637 | 12 |
| T-10 quoted source-qualified | report-cycle / bracket | 22 | 7 |
| T-10 10-share executable | fixed policy opportunity | 7 | 4 |

### Evidence funnel

| layer | rows | dates | gap |
|---|---:|---:|---|
| T-10 executable cohort | 7 | 4 | — |
| T-5 source+quote checkpoint | 7 | 4 | 0 |
| final PnL comparable | 5 | 3 | 2 unsettled |
| invalidated entries | 1 executable | 1 | Busan 7/20 |
| invalidation可按 best bid 全退10 | 0 | 0 | top bid depth=8；完整 book walk 未采集 |
| actual fills | 0 | 0 | research replay，不是实盘 fills |

## 赔率损耗：等待通常约 1c，今天 Busan 是极端例外

先看所有有 T-10 quoted signal、且到 T-5 source 仍存活的 paired quotes，不要求可执行，以免只看成交样本：

| city | paired rows / dates | median `T5 ask - T10 ask` | mean | 删除最大涨价后 mean |
|---|---:|---:|---:|---:|
| Busan | 7 / 4 | 0.0c | +7.44c | +0.02c |
| Seoul | 6 / 5 | +0.7c | +1.95c | +0.94c |
| 合计 | 13 / 7 | +0.2c | +4.91c | +0.98c |

Busan 的均值几乎全由今天下午 `0.35→0.87` 的 +52c 推动；去掉这一笔后，等待到 T-5 基本没有系统性赔率损耗。Seoul 的 T-10 优势更像 1c 左右，但仍只有 5 个可执行机会，且入场多在 0.89–0.967 的薄残值区。

宽分母还显示：22 个 T-10 quoted source signals 中，8 个 ask≥0.97；只有 7 个最终满足 ask/depth。市场多数时候已经把 source cross 定价进去，不是“早五分钟必然便宜”。

## 止损磨损与 Busan 反事实

| local time | action | fresh NO book | 10-share fee-adjusted result |
|---|---|---:|---:|
| 12:49:33 | T-10 early entry | ask `0.85×12.49` | cash cost `$8.56375` |
| 12:53:33 | source 首次明确跌破，dynamic stop | bid `0.83×8` | 若虚构10股都在0.83成交：`-$0.33430`；真实 top depth不足 |
| 12:54:33 | T-5 checkpoint stop | bid `0.77×2.88` | 若虚构10股都在0.77成交：`-$0.95230`；深度更差 |
| 12:54 | wait-T5 | source latest `31.1C` | 不交易，PnL=0 |
| 15:50:56 | 下午 T-10 early entry | ask `0.35×10` | final PnL `+$6.38625` |
| 15:54:56 | 下午 wait-T5 entry | ask `0.87×14` | final PnL `+$1.24345` |

所以“尽快止损”明显优于“等到 T-5 再止损”，但 10-share dynamic stop 也没有足够 top depth。实际 15-share 仓位更不能用显示 best bid 作为可成交退出价。

用全部 survivor quotes、删除最大 repricing 后的平均早入优势 `0.9833c/share`，对照今天 dynamic stop 的损耗 `3.343c/share`，early+stop 只有在 invalidation rate 低于约 `22.7%` 时才 break even；若拖到 T-5 才止损，单股损耗 `9.523c`，break-even invalidation rate 降至约 `9.4%`。这两个阈值都由唯一 invalidation 推出，只作 sensitivity。

## Fee-adjusted policy replay

| policy | cohort | known PnL rows / dates | trades | fee-adjusted PnL | ROI | 关键限制 |
|---|---:|---:|---:|---:|---:|---|
| wait T-5 | 7 | 6 / 3 | 4 | +$1.90970 | +6.80% | 1 个入场后 unsettled；invalidated/no-liquidity 为 no-trade |
| T-10 + dynamic stop（optimistic） | 7 | 5 / 3 | 7 | +$7.85510 | +19.46% | 假设10股都能按 top bid 退出，实际 false row depth只有8 |
| T-10 + dynamic stop（executable） | 7 | 4 / 3 | 7 | 不发布可比 ROI | NA | 唯一 stop 无法全退，删除坏 row 后的 ROI 有选择偏差 |

在 5 个 final-comparable rows / 3 dates 上，optimistic dynamic-stop 相对 wait 的平均 PnL delta 为 `+$1.18908 / 10-share opportunity`，date-block bootstrap CI `[+$0.23333,+$2.40425]`。但该统计不通过执行基准：唯一止损行 0/1 可全量退出；而且总 delta 的 `80.9%` 来自今天 Busan 两个 report cycles。删除今天 Busan 后只剩 Seoul 3 rows / 2 dates，平均 delta `+$0.37897`，仍远低于城市/策略结论门槛。

## 城市判断

- **Busan**：quoted checkpoint 有 2/9 invalidations；T-10 executable 只有今天两次，一次 false、一次 afternoon repricing。去掉下午极端 repricing 后，T-5 相对 T-10 几乎不变；early+stop 没有稳定赔率优势，却暴露退出深度。因此选等待/path-confirmation，不选 stop-rescue。
- **Seoul**：9 个 fresh checkpoints 中 0 invalidations；paired T-5 ask 通常比 T-10 贵约 0.7–1.0c。T-10 early 是合理的 zero-notional challenger，但只得到 5 个 executable / 3 个 settled final rows，不能 live；它也不是 Busan 的止损规则证据。

## Data integrity / 8环 / 三门

- PIT：source、quote 都按 local detect/fetch time；下一份 METAR 与 WU 只作 label。
- 执行：入场/退出都扣官方 taker fee；maker、mid、future touch 均未冒充 fill。缺完整 depth，因此 stop 容量环失败。
- 覆盖环：描述性、信号、execution、容量 sensitivity、反事实已覆盖；概率校准、组合相关、真实 fill、frozen forward 缺失。
- variants tried `K=4`（wait、early-hold、checkpoint-stop、dynamic-stop），未做 multiple-testing correction；bootstrap 仅描述同样本不确定性。

```text
significance=FAIL（可执行 paired stop row 缺失；optimistic CI 不算执行成立）
baseline=FAIL（止损无法按10-share fresh top depth执行）
forward=FAIL（3 dates final-comparable；未冻结 forward）
conclusion=inconclusive
```

在 2026-07-09..20 的 Busan/Seoul 固定 T-10 executable cohort，optimistic dynamic-stop 相对 wait-T5 的 PnL delta 为 `+$1.18908/10-share opportunity`（95% CI `[+$0.23333,+$2.40425]`），但唯一止损 0/1 能按 top depth 全退且 80.9% delta 来自今天 Busan；forward FAIL，结论 `inconclusive`，动作是不改 live、继续 trigger-relative 5-minute revalidation shadow。
