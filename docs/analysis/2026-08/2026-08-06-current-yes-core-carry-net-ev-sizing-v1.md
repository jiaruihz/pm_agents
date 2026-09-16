# Core Carry continuous net-EV sizing v1

Artifact routing: this report is the durable snapshot. New sizing runs require
a stable `--run-id` and write immutable machine output beneath the configured
JRS research artifact root; updating a report requires an explicit `--report`.

结论：`不升级 live sizing`。同一 136 个 frozen entry 上，连续 sizing 没有增加准确率（信号集合未变），也没有稳定增加美元 PnL。

| policy | full shares | full PnL | ROI | frozen-forward PnL | forward avg shares |
|---|---:|---:|---:|---:|---:|
| fixed 10 | 1360.0 | $+57.96 | +4.67% | $+15.00 | 10.00 |
| raw net-EV 5–15 | 1318.5 | $+62.21 | +5.24% | $+8.93 | 8.52 |
| Kelly score 5–15 | 1329.7 | $+64.99 | +5.35% | $+13.44 | 8.92 |

两条连续曲线只在 development dates 上校准到平均 10 股，然后原样应用到最后 8 个 frozen-forward target dates。信号、label、PIT quote 与 eligibility 完全相同；变化只有 shares。

- raw net-EV 的 full-window PnL 比 fixed 10 高 `$4.25`，但 frozen-forward 低 `$6.07`；paired target-date mean-daily PnL delta 95% CI `[-$0.74, +$0.98]`。
- Kelly score 的 full-window PnL 高 `$7.03`，frozen-forward 低 `$1.55`；paired CI `[-$0.36, +$0.86]`。
- tail 没改善：full-window worst-date PnL 从 fixed 10 的 `-$6.97` 恶化到 raw net-EV `-$12.03`、Kelly `-$10.83`。

局限：本轮用 frozen 5-share fee-adjusted effective cost 作逐股成本近似。136 行中 114 行的 top ask 本身覆盖 15 股；其余 22 行需要完整 15-share ladder 才能做严格容量结论。因此这足以否定“现有 net-EV 单变量立即升 live”，不用于批准 15 股实际下单。
