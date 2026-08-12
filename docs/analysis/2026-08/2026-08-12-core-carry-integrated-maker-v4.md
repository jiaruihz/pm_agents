# Core Carry integrated maker v4

Status: `implementation verified / live unchanged / deployment authorization pending`

## 结论与交易动作

Core Carry 不应再叠加第二个 pullback maker。现有 5-share maker sleeve
同时承担初始排队、短时回调承接和最多两次主动提价；新天气信息到达后先撤单，
由 event-rescore 重新计算，post-update re-arm 继续 zero-notional。这样单信号仍是
`10 taker + 最多 5 maker`，最大 15 股，不改变 selector、Core 概率或 taker。

本次实现 profile
`split_taker_maker_event_validated_staged_no_fallback_v4`：

1. 初始 `bid+1 tick`，受 retained-edge 与 taker-improvement cap 约束；
2. 前 5 分钟保留 queue；5 分钟后最多一次 midpoint reprice；
3. 10 分钟后最多一次 near-ask reprice，总计最多两次；
4. replacement 使用计划的阶段目标，不再在下单器中悄悄改写为另一种报价；
5. replacement 永不低于原订单；决策后 bid/ask 任一漂移超过 1 tick 时不重挂；
6. 新 METAR/官方观测、forecast curve revision、exact bracket/token 变化均撤单；
7. 15 分钟 TTL 或下一份 source report 前 90 秒撤单；无 maker→taker fallback；
8. reprice stage、天气 state hash 与决策盘口完整写入 order lineage。

它是现有证据支持下的统一执行版本，不是已证明提高 ROI 的新 alpha。部署后仍需按
真实 maker intent、authenticated fill、paired price improvement、adverse selection
和 settlement PnL 复评，不能用 future touch 冒充成交。

## 为什么是这个组合

- 最近 28 个真实 maker root intents 有 15 个成交；已结算 maker 70 股贡献
  `+$1.80`、ROI `+2.85%`，相对同 signal taker 平均改善 `1.18c/share`。
  它增加绝对 PnL，但低于 taker-only `7.77%` ROI，不能靠盲目增量仓位优化。
- pullback replay 的 `entry ask-2c / 15m` 为 7/7、增量 `+$4.40`，但这些价格会先
  穿过当前 active maker；30m 已转为 `-$3.65`。因此正确表达是同一 5 股在短 TTL
  内保持有效，而不是再加 5 股深价单。
- post-update re-arm 的保守 fill 仍只有 Amsterdam 1 个；它继续 shadow，不进入 live。
- event-rescore 已能区分 observation、forecast revision 与 exact-bracket transition；
  v4 把这些事件用于撤销 stale maker，但没有把低样本 event selector 偷渡进下单。

## 生产 bug 与影响半径

v3 有两个相互叠加的 lifecycle bug：

1. `maker_last_reprice_stage` 在 plan 中生成，但没有写入 live order record；下一轮把
   已用过的 midpoint 当成未使用。
2. lifecycle 计划价进入 shared runtime 后，request builder 再按 fresh `bid+tick`
   重新定价；它只检查不超过计划上限，没有检查 replacement 是否低于 source。

当前 v3 共有 4 条真实 reprice order，其中 1 条 non-improving，逐条影响如下：

| UTC | city | source | plan | actual | v4 处理 |
|---|---|---:|---:|---:|---|
| 2026-08-10 21:42:43 | PanamaCity | 0.87 | 0.88 | 0.88 | 正常一次 midpoint |
| 2026-08-12 06:56:56 | Shanghai | 0.821 | 0.87 | 0.852 | 稳定盘口才按阶段目标；已实际成交 5 股 |
| 2026-08-12 14:42:15 | Jeddah | 0.77 | 0.83 | 0.78 | 记录 midpoint 已使用 |
| 2026-08-12 14:42:38 | Jeddah | 0.78 | 0.83 | **0.64** | 重复 midpoint 被阻止；即使进入 builder 也禁止向下重挂 |

Jeddah 的 0.64 单最终没有成交，14:52:12Z 按 TTL 撤销，因此已确认实际
fill/cost/PnL 影响均为 0；影响是撤掉了原 0.78 queue、浪费第二次 reprice，造成
潜在漏单。污染窗口和精确 order lineage 已保留在 raw journal，没有重写历史。

## 验证边界

- 当前 production health、strict manifest、canonical DB identity 正常；fill coverage
  gate 通过，1,464 个 live_real fill ids 无 missing/over-order，fee unknown 为 0。
- 定向测试覆盖 exact stage target、quote drift、禁止向下 replacement、reprice stage
  持久化、forecast revision 撤单、new observation/rebracket 撤单、两次上限和无 fallback。
- v4 尚未部署，当前生产仍为 v3。本报告不把实现完成写成收益确认。

参考：

- `2026-08-12-current-yes-core-carry-recent-live-performance-v2.md`
- `2026-08-11-current-yes-core-carry-pullback-add-maker-v1.md`
- `2026-08-12-core-carry-event-rescore-maker-forward-v1.md`
- `2026-08-06-core-carry-maker-microstructure-v1.md`
