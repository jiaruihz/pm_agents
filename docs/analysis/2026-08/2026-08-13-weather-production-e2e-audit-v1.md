# Weather 生产端到端审计（2026-08-13）

## 一页结论

生产控制面、JRS context、canonical DB identity 和 storage audit 均健康；同步与 bounded canonical refresh 已成功。账户/成交 coverage gate 为 `pass`：`1,464/1,464` 个 effective live fills，DB/cache fill-id 差异、missing-order、over-order、未知 fee lineage 均为 `0`。全历史 effective fill cost 为 `$4,827.4771`，fees `$17.6468`，已结算 fee-adjusted PnL `-$77.7704`；authenticated 可用 USDC 为 `$485.16119`，open orders / reserved 为 `0 / $0`。因没有期初现金和外部 transfer/redemption ledger，现金桥接不能闭合，不用 PnL 猜差额。

本轮修复了两处当前口径错误。账户脚本原先直接累计 physical fills，把 39 个 alias fills（`$279.6770`）和 15 个 excluded fills（`$9.6890`）计入，同时漏用两笔 `+$0.5900` price correction；同窗口 actual fill cost 因此从错误的 `$5,116.2531` 修正为 `$4,827.4771`，差额 `-$288.7760`。settlement matcher 原先让旧 token-grain `missing_bracket` 抢先于新 exact-market `settled` correction；修复后完成 5-fill 定向重放，open cost 减少 `$49.5130`、新增 realized PnL `+$9.81361`，且未改订单、fill、fee 原始证据。

## 当前未结算敞口 `[UNSETTLED]`

| city / target | side / bracket | fills | cost / fee | mid / bid / last-fill PnL | 状态 |
|---|---|---:|---:|---|---|
| Amsterdam / 8-12 | BUY_NO / 27 | 1 | `$9.60 / $0.01920` | `+$0.39 / N/A / N/A` | exchange market 仍 open |
| Jeddah / 8-12 | BUY_YES / 39 | 1 | `$8.62 / $0.05947` | `+$1.37 / +$1.37 / N/A` | exchange market 仍 open |
| Shanghai / 8-12 | BUY_YES / 27 | 2 | `$13.11 / $0.05088` | `N/A / N/A / N/A` | exchange market 仍 open |
| **合计** | 3 markets | **4** | **`$31.33 / $0.12955`** | mid 仅覆盖 2/4=`+$1.76` | open orders=`0` |

估值时间最多到 `2026-08-12T17:04:14Z`，且覆盖不全，不能把 `+$1.76` 当完整未实现 PnL。最大单 market/city 为 Shanghai `$13.11`（41.84%）；全部风险集中在 target_date `2026-08-12`。4 个 fills 均已过 target date，但对应 3 个市场在本次 authenticated/API 核验时尚未关闭。

## 最近 30 个 target_date lineage

窗口为 `2026-07-15..2026-08-13`：`131,422` candidates（v1 `32,657`、v2 `98,765`）、`620` signals、`1,041` plans、`1,390` order records、`299` live fill rows / `277` executions。50 个 duplicate execution aliases 已由 canonical adjustment 排除；逐单核验未发现 side mismatch、orphan fill、share-cap/over-order、settled-PnL-NULL，确认错单/错 fill 为 **0**。

确认的漏提交为 **141 attempts / 6 expressions / attempted notional `$792.90` / actual fills `0`**：138 次 7月15日 GTD expiration 被拒（当前 180 秒修复重放为 138/138 可构造有效 expiry）、2 次 7月16日 post-only cross（当前代码会刷新 book 后 bounded reprice）、1 次 Request exception。它们没有 exchange order id，无法诚实反推“若接受后一定成交”；因此 missed fills/PnL 标为不可识别，而不是按事后结算虚构收益。另有 211 个 accepted historical orders 最终未成交、当前 authenticated open orders 为 0，属于正常 resting/cancel/expire 结果，不算漏单。

canonical 仍保留 119 个历史 live orphan plans（7月16–28，迁移去重污染；52/64 signals 可连到另一真实 order）。当前迁移代码自 8月4日起已在写 plan 前按 physical order 去重；本轮不删除 append-only 历史行，审计统一标记并排除。13 个 BUY order records 缺同 side candidate，仅涉及 12 个同一 Singapore fast-source rejection 链和 1 个 simulated tmax 行，成交影响为 0。40 个 v2 `policy_selected` rows 是 research/zero-notional candidate，无 TradeIntent/order 不算漏单。

完整的 1,390-order 逐条清单（含 submitted/fill size、cap、exchange rejection、settlement、plan gap 和 candidate gap）位于：`/Volumes/jrs-archive/pm_agents/research/artifact_store/weather_production_e2e_audit/2026-08-13/lineage_30_target_dates_v1.json`。

## 验证

- canonical refresh、5-fill settlement replay、8月11日起 signal partition replay：exit `0`。
- final coverage gate：`gate_pass=true`；effective fills `1,464`，cost `$4,827.477067`，fee evidence `exact 971 / maker_zero 441 / estimate 52 / unknown 0`。
- 相关测试与文档检查结果见交付消息；本审计没有改变 live 策略参数、进程或订单。
- patched builder 已用于当前 canonical 定向重放；注册的 `control_plane` production release pin 未推进，因为该部署属于需另行显式确认的生产变更。当前数据已修正，但 pin 推进前 scheduled builder 尚未取得这项防复发代码。
