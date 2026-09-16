# Alpha Capital Agent — 首轮只读 Shadow Pilot

```text
disposition=NO_NEW_CAPITAL
selected_markets=5
confirmed_eligible=5
central_edge_ge_3pp=4
conservative_edge_ge_3pp=0
capital_plan=DATA_BLOCKED
execution=NO_ORDER
```

本轮在隔离临时目录中完整跑过 `市场发现 → 两轮增量确认 → 价格盲研究 →
盘口/费率修正 → 资金门控 → 替换判断`。没有修改 production config、runtime DB
或进程，也没有签名、提交或模拟任何真实订单。

## 结果

- Gamma 首轮取得 5 个 events / 24 个 nested markets；9 个为 active/open，6 个通过
  pre-book screen，按 one-market-per-event 去重后研究 5 个。
- 两次盘口批次间隔 342.44 秒；5 个候选全部从 `NEAR_ELIGIBLE_WATCH`
  转为 `ELIGIBLE_UNRESEARCHED`。这段窗口没有发现从不合格重新转为合格的新市场。
- 5 个市场的价格盲研究共使用 24 个来源。按中央概率有 4 个看似超过 3pp；按
  保守概率下界、真实 ask 和 taker fee 计算，0 个超过 3pp。因此本轮不会投入新资金。
- 当前盘口很薄：候选 YES book 的双边 top depth 只有约 $0.20–$14.28；即使中央
  edge 看起来不错，也不应绕过不确定性与可执行深度直接下单。

| 市场 | 最优方向 | 研究区间 `p_yes` | all-in ask | central edge | conservative edge | 结论 |
|---|---:|---:|---:|---:|---:|---|
| China × India military clash by Dec 31, 2026 | YES | 7%–23% | 8.000% | +5.000pp | -1.000pp | WATCH |
| NATO/EU troops fighting in Ukraine by Dec 31, 2026 | YES | 7%–30% | 5.500% | +10.500pp | +1.500pp | WATCH |
| Macron out by Dec 31, 2026 | NO | 0.5%–5% | 95.094% | +3.106pp | -0.094pp | WATCH |
| Kraken IPO by Dec 31, 2026 | YES | 5%–30% | 17.564% | -2.564pp | -12.564pp | NO_EDGE |
| UK election called by Dec 31, 2026 | YES | 10%–22% | 8.294% | +7.706pp | +1.706pp | WATCH |

费率按 Polymarket 当前官方公式 `shares × feeRate × price × (1-price)`、每股五位
小数取整；Politics/Finance 使用 0.04，两个 geopolitics 市场为 0。参见
[Polymarket Fees](https://docs.polymarket.com/trading/fees)。

## 账户与替换判断

公开账户同步取得 81 个正仓位快照，其中 64 个尚未 redeemable、17 个 redeemable；
公开快照的 initial value 合计为 $2,424.83，current marked value 为 $2,331.08。
这里的差额/`cash_pnl` 字段不是 realized PnL，也不是可用现金。

正式 replacement ranking 仍为 `DATA_BLOCKED`：公开 API 无法证明 free cash、reserved
cash、open/cancelled orders；已有持仓还没有同口径的价格盲 `q_cons` 与 sell depth。
安全审批也拒绝把账户派生 token IDs 再发给外部 CLOB endpoint，本轮没有绕过。即便
不考虑这些缺口，本批次 0 个新机会通过 conservative edge gate，所以默认动作仍是
不替换已有持仓。

## Freshness、幂等与复核

- Gamma 两轮间隔 363.42 秒；Gamma 与对应 book 的跨源时差分别为 282.35 秒和
  261.37 秒。独立 review 指出原脚本未显式约束此时差，现已增加 300 秒 freshness
  SLO，超限会 fail closed。本轮两批均通过；正式 scheduler 应把 Gamma 与 book
  back-to-back 采集，以进一步收紧该时差。
- 重复 materialize 后 projection 数保持 `10 observations / 10 transitions /
  10 next evaluations / 5 admission episodes / 1 capital plan`，且只有 1 个 plan ID。
- 独立只读 review：1 个 medium finding（跨源 freshness）已修复，无其余阻断项。
- 完整相关测试在外层 macOS 环境复跑：`761 passed`。内层 Codex 沙箱首次有 3 个
  `sandbox-exec` canary 因不允许嵌套 sandbox 失败；外层复跑后全部通过。

## 证据位置

原始响应、盘口、研究结果和隔离 DB 保留在：
`/private/tmp/polymarket-alpha-pilot/aca-taste-20260830-0CeFjs`。

本目录的 `manifest.json` 只封存非敏感汇总与原始工件 hash；账户持仓明细和 token
IDs 没有复制进仓库。
