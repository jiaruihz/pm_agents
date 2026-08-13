# Core Carry integrated maker v4 / dual-maker A/B v5 / post-update rearm v6

Status: `v5 live / v6 implementation verified / production rollout target`

## 结论与交易动作

交易决定已改为小额真实 A/B：Core selector、概率与 10-share taker 不动，单信号
增加两个独立 5-share maker sleeve。`maker_staged` 执行现有 queue→midpoint→near-ask；
`maker_pullback` 静态挂 `entry ask-2c`、15 分钟内不追价。两者独立 role、dedupe、
order lifecycle、fill/PnL attribution，共享 METAR/forecast/rebracket 失效时钟。单信号
最大 `10+5+5=20` 股；每日成本上限仍为 `$100`，没有同步放宽总日风险。

v5 profile 为 `split_taker_two_maker_event_validated_no_fallback_v5`；其中 staged arm
沿用并修复 v4：

1. 初始 `bid+1 tick`，受 retained-edge 与 taker-improvement cap 约束；
2. 前 5 分钟保留 queue；5 分钟后最多一次 midpoint reprice；
3. 10 分钟后最多一次 near-ask reprice，总计最多两次；
4. replacement 使用计划的阶段目标，不再在下单器中悄悄改写为另一种报价；
5. replacement 永不低于原订单；决策后 bid/ask 任一漂移超过 1 tick 时不重挂；
6. 新 METAR/官方观测、forecast curve revision、exact bracket/token 变化均撤单；
7. 15 分钟 TTL 或下一份 source report 前 90 秒撤单；无 maker→taker fallback；
8. reprice stage、天气 state hash 与决策盘口完整写入 order lineage。

pullback arm 固定 5 股、报价为 entry ask 下方 2c、无 reprice、无 taker fallback，
并写入实验标识 `core_carry_staged_vs_pullback_maker_ab_20260813`。它是经明确授权的
小额执行实验，不是已证明提高 ROI 的新 alpha。部署后仍需按
真实 maker intent、authenticated fill、paired price improvement、adverse selection
和 settlement PnL 复评，不能用 future touch 冒充成交。

v6 不改变 Core selector、模型、10+5+5 sizing 或现有 maker 的追价方式；只修正
maker 首次撞上 source-report blackout 后被永久 `skip_terminal` 的容量缺口。处理改为：

1. 旧天气 epoch 下不挂 maker，避免抢在新 METAR 前被反向选择；
2. 最多等待 60 分钟内的下一份 observation/forecast/exact-bracket state；
3. 新 epoch 到达后用冻结 Core v3 和新鲜十股 ask ladder 完整重评；
4. 只有 exact bracket/token 未变、Core taker net-EV 仍为正时才重新武装缺失的两条 maker；
5. maker cap 同时低于新 ask、原 taker ask，并保留至少 1c 模型 edge；仍为 post-only、
   无 taker fallback，且单 city-day 最大仓位保持 20 股；
6. 若新 epoch 不再正 EV，只记录评估并等待下一 epoch；超时或 token/bracket 改变后终止。

2026-08-13 Shanghai 是直接影响案例：13:31 local 的 Core 信号在 `0.88/0.91`，
10-share taker 因 CLOB read timeout 未成交，两个 maker 因已越过预期 13:30 METAR
时间而没有创建。13:41 新 METAR 后冻结 Core 重评为 `p=0.929867`、十股 net edge
`+1.5777c/share`；v6 反事实会分别创建 5-share staged 与 5-share pullback，二者
初始限价均为 `0.89`、cap `0.90`。这增加本应可执行的 maker 容量，但不是成交或
收益保证；真实效果仍按 authenticated fill、adverse selection 和 settlement 统计。

## 为什么是这个组合

- 最近 28 个真实 maker root intents 有 15 个成交；已结算 maker 70 股贡献
  `+$1.80`、ROI `+2.85%`，相对同 signal taker 平均改善 `1.18c/share`。
  它增加绝对 PnL，但低于 taker-only `7.77%` ROI，不能靠盲目增量仓位优化。
- pullback replay 的 `entry ask-2c / 15m` 为 7/7、增量 `+$4.40`，56 个 settled
  signals 中占 12.5%；30m 已转为 `-$3.65`。这只证明 15m/5-share 容量值得做
  有界 live A/B，不能把 future ask crossing 当真实 queue fill，也不能延长 TTL。
- 两个 maker 可能在同一次回调都成交；所以 v5 的目的同时包含扩大单信号仓位，
  不是把 pullback arm 伪装成与 staged arm 完全独立的历史 alpha。
- 旧 post-update re-arm 研究只有 Amsterdam 1 个保守 fill，不能支持扩大 selector；v6
  仅恢复已经通过 Core selector、却因 clock blackout 缺失的 maker child，不交易新 city-day。
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
- v5 上线前必须确认交易所无遗留 open order；上线后按两个 maker arm 分开报告
  submitted、fill、均价改善、adverse selection 与 settlement PnL。

参考：

- `2026-08-12-current-yes-core-carry-recent-live-performance-v2.md`
- `2026-08-11-current-yes-core-carry-pullback-add-maker-v1.md`
- `2026-08-12-core-carry-event-rescore-maker-forward-v1.md`
- `2026-08-06-core-carry-maker-microstructure-v1.md`
