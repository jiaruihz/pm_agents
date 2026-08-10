# Forecast repricing anti-toxic maker / full-ladder completion v1

significance=FAIL
baseline=prior maker-fill-conditional ROI is not fillable evidence; 48 holdout quotes had 0 best-bid trade-throughs
forward=current raw smoke PASS for data/runtime contract; alpha forward NOT_STARTED
execution=zero-notional completion probe only; own fill, queue and D-1 WS tape coverage absent

production: live_action=none; orders_changed=0

## 结论

不能把旧的 maker conditional `+36.39%` 当机会：它挑到的是“价格不跌到我们的 bid、因此大概率不成交”的票。
在新的 bid/ask 窗口证据中，旧 selector 的 48 个 secondary-holdout quotes 有 `0/48` 出现 ask 跌到挂单价；
全体 D-1 rungs 中一旦出现这种明显 trade-through，98% 以上的 60m net markout 为负。提高 1 tick、挂在
spread 25%/50% 或 ask-1tick 都只提高有毒成交率，没有把条件收益转正。

因此本轮不冻结单腿 maker alpha。可运行版本改成严格的 `full_ladder_completion` zero-notional probe：

1. forecast revision 后只在完整 native ladder 上计算；
2. 选一档按当前 best bid 挂 maker；
3. 假设该档真实 fill 后，立刻以其余所有 YES 档的 executable ask 补齐等股 full set；
4. 入场要求 taker fee、每个 hedge leg 1 tick slippage 和 5-share depth 后，整套成本仍低于 `$0.99`；
5. 每个完整 ladder checkpoint 重算，margin/depth 恶化或机会移档立即 `CANCEL_MAKER`；
6. ask cross 只记 `POSSIBLE_FILL`，没有 own order/fill evidence 不开仓；真实 fill 后才允许输出条件 `HEDGE`。

这个结构不依赖被成交的单腿继续上涨，但仍有 maker fill→多腿 hedge 的延迟和 legging risk，不能称为无风险。

## 数据与固定分母

- 输入范围：`2026-05-21..2026-08-09`。
- 5,019/5,020 个跨代 snapshot inputs；6,003 个 forecast update events；62,444 个完整 ladder rungs。
- D-1：5,902 events、61,392 rungs、65 target dates。
- 60m bid/ask window scoreable：45,451 rungs；best-bid trade-through proxy：3,979（8.75%）。
- development：`2026-05-21..2026-07-28`，44 dates。
- secondary holdout：`2026-07-29..2026-08-09`，12 dates。
- trade-through 是未来 ask 跌到历史 best bid；它是明显 adverse path 的保守代理，不是 actual maker fill。

## 单腿 anti-toxicity head

模型把两件事分开：

- fillability：spread、bid/ask depth、ladder HHI/entropy、mode distance、邻档 spread/depth、时段和
  weather shock 预测 60m trade-through；
- toxicity：只在 development trade-through rows 上预测 fill 后 60m executable net value。

固定比较 linear 与 HistGradientBoosting，加上 8 个 development-only gate 组合。HGB 的 holdout
trade-through base rate `7.71%`，Brier `0.06197`、ROC-AUC `0.84156`，说明盘口形态确实能识别“什么单容易被打到”；
但这不是好消息：development 中 linear 选出的 10 个 proxy fills 全亏，HGB 对所有 candidate 的条件 value
都不判正。即模型学到的是 toxicity，不是可盈利 fill。

## Full-ladder completion 反事实

固定 entry buffer=`1c/set`，其余腿按 direct ask + official Weather taker fee + 1 tick/leg，固定 5 shares。

| period | selected quotes | trade-through + complete hedge | dates | locked ROI | negative margin rate |
|---|---:|---:|---:|---:|---:|
| development | 33 | 1 | 1 | `-11.45%` | `100%` |
| secondary holdout | 2 | 0 | 0 | NA | NA |

唯一 development trade-through 是 Houston：entry 时 completion margin 为正，但触价时 ladder mass 已迁移，
其余档同步变贵，5-share completion 变为负。这否定了“入场时看见 underround 就能在 fill 后安全补齐”的静态版本。

## 当前 raw smoke

修复 runner 默认输入：此前虽有新架构 parser，默认仍读历史 `full_ladder_output`，最新只到 8/7；现在默认只读
current canonical `strategy_snapshots/paper_snapshots`，历史 replay 必须显式传路径。

- current baseline：64 files、897 complete joined ladders、9,867 rungs、32 streams；latest decision clock
  `2026-08-10T15:45:00Z`；`zero_notional=true`、orders=0。
- 最近 128 current files：106 个真实 forecast revision pairs、1,155 个 scored rungs。
- 最大 fee+1tick completion margin `-4.72c/set`；`POST_MAKER=0`。正确动作是 `NO_TRADE`，不是放宽 gate。
- 当前 policy-valid WS selector 是 Amsterdam/Helsinki intraday hot strip，未覆盖跨城市 D-1 revision tokens；因此
  当前不能从 WS trade prints 建立本 family 的真实 queue/fill denominator。

## 可运行产物

- base panel：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_completion_20260810_tminus1/forecast_event_rungs.csv`
  - SHA-256 `266046445c3cbcb42f240ecc0cc782e3927429ca81cdc8e509e86a86e62f670d`
- policy artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_completion_position_20260810_tminus1/position_policy.joblib`
  - SHA-256 `7ce5355070fb23869af0b0c829e45dee2a0e81c887db6a981c6bef9ba7a61e1a`
- current smoke：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/completion_probe_current_smoke_v2_20260810`

Runner 能输出 `POST_MAKER / KEEP_MAKER / CANCEL_MAKER / POSSIBLE_FILL`，并在有 actual fill 时按当前完整 ladder
给出 `HEDGE` 或 `EXIT`。当前 artifact 的研究判定是
`historical_gate_fail_zero_notional_probe_only`：可跑、会 abstain，但没有 live 权限。

## 下一步唯一动作

要回答“真实 SELL flow 打到我们的 bid 后是否仍有 completion/relative-value 空间”，必须把冻结后的 D-1 candidate
tokens 接入 policy-valid WS subscription，并把 own post→queue ahead→SELL prints→partial/actual fill→同刻 hedge book
写进 append-only evidence。当前代码和 artifact 已准备好；扩生产 collector subscription 属于生产行为变更，需另行显式确认。
