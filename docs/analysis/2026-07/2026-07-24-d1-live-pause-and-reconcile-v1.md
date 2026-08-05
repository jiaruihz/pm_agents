# d1 YES high-mid：暂停实盘与对账 v1

> 执行时间：2026-07-24 08:51 UTC 左右
> 对象：Mac `pm_agents_prod` / `d1_yes_high_mid_live_v1`
> 动作：用户授权暂停新增真实下单；不平现有已成交仓位。

## 结果

- 已终止 JRS 专用 tmux session `d1_yes_high_mid_live_v1`。原进程 PID `33807`，实际参数含
  `--live --confirm-live`；停后 session 与该 runner 进程均不存在。
- 已把 canonical control plane 改为：instance `desired_status=paused`、`expected_live=0`；
  strategy status=`dormant_research_live_paused`。d1 机制研究保留，不再允许新增实盘订单。
- 交易所认证查询：账户开放订单 `0`，d1 使用过的 17 个 token 的开放订单 `0`；无需撤单。

## d1 专属账本（canonical `fact_trades`）

| 口径 | fills / dates | cost | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|
| clean policy，已结算（剔除 7/19 source-history 事故） | 14 / 8 | $60.9250 | -$16.1273 | -26.47% |
| 7/19 事故持仓（不计策略 alpha） | 5 / 1 | $24.9350 | +$0.0640 | +0.26% |
| 全部已结算（仅作现金事实，不作策略口径） | 19 / 8 | $85.8600 | -$16.0633 | -18.71% |

clean policy 未结算 4 fills 都是 7/24 target date，成本 `$17.1600`、fees `$0.0385`。它们仍是
`[UNSETTLED]` position，不进入 realized PnL；最后 DB mid 估值为 `-$10.1850`，但估值时间仅到
`2026-07-24T06:50:52Z`，不能当作当前可实现现金损失。

| city | bracket | shares | fill cost | last mid MTM |
|---|---:|---:|---:|---:|
| Chengdu | 39 | 5 | $4.005 | -$1.605 |
| KualaLumpur | 32 | 5 | $4.900 | -$2.775 |
| Shanghai | 36 | 5 | $4.200 | -$2.975 |
| Shanghai | 36 | 5 | $4.055 | -$2.830 |

## Fill / cash 对账

- 正式 CLOB coverage gate：`gate_pass=true`。
- canonical 与 raw CLOB cache：各 `1,132` distinct fills，双向缺失均为 `0`，cost delta=`0`；
  无 duplicate / synthetic fill、无 over-order。
- fee lineage：`exact=763`、`maker_zero=344`、`estimate=25`、`unknown=0`；无 matched-taker 的
  未解释零 fee。
- 认证 collateral balance 为 `$408.095573`，但这属于全账户余额；没有同一时点的期初余额、赎回和外部转账流水，
  因此不能把它硬桥接为 d1 的独立净值。

账户汇总脚本原先误用 raw fill price 比较 append-only price adjustment 后的 `fact_trades`，会把 `$0.59`
修正误报为 coverage failure。本次已改为：raw DB fills 对 raw cache，effective adjusted fills 对
`fact_trades`，并重新运行通过。

## 结论

`d1 YES mid>=0.80` 的当前实盘表达已暂停。继续保留 d1 的 full-ladder / exact-landing 概率研究和
zero-notional telemetry，但不继续调阈值或添加事后 regime gate；只有新信息在同分母 proper score 上先赢市场，
才重新讨论独立 live sleeve。
