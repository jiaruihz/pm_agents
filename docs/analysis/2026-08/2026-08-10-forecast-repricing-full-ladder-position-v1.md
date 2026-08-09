# Forecast Repricing：full-ladder position policy v1

significance=FAIL（entry relative-markout 相对 M0 的 OOF/holdout CI 均跨 0）

baseline=market-level relative-markout M0

forward=NA（secondary reconstructed holdout，不是 collector-exact frozen forward）

execution=maker-fill-gated；conditional dynamic ROI `+13.13%`，actual fills=`0`

production: live_action=none; orders_changed=0

## 结论

已经把 `forecast_repricing_v1` 从固定 horizon 的研究 scorer 落成可加载的完整 position policy：

```text
D-1 forecast revision
  -> 所有 ladder rungs 同时评分
  -> NO_TRADE / POST_MAKER
  -> actual fill 才建立真实 position
  -> 30m full-ladder continuation score
  -> EXIT / HOLD
  -> 60m hard exit
```

策略复用 relative markout、M0、signed mode distance、邻档 curvature/propagation；没有复活已失败的 max/min selector。
Taker 路径在首轮完整回放为 `-44.08%`，已从 runnable policy 中关闭。当前入口是 maker-fill-gated：
未看到真实 fill 时只记录 pending/conditional path，不把 quote cross 冒充持仓或收益。

## 数据与固定分母

输入：`forecast_event_rungs.csv`，SHA-256
`8be4d7c0a0f209f692df7935f6cb99ba9ad8914ef55f518818b20dd62e5f3900`。

| funnel | rows/events | dates | 说明 |
|---|---:|---:|---|
| raw event-rungs | 28,038 | 41 | D-1/D-2 recovered union |
| D-1 rungs | 26,986 | 40 | position policy universe |
| D-1 forecast events | 2,666 | 40 | event 等权、date block |
| 30m direct-bid scoreable | 18,399 | — | 缺失保留为 evidence gap |
| 60m direct-bid scoreable | 18,086 | — | entry/exit label |
| secondary conditional positions | 39 | 6 | threshold 只在 development OOF 选择 |
| actual fills | 0 | 0 | 可实现性仍未验证 |

Development 是 24 个可评分 target dates；secondary holdout 是
`2026-06-26..2026-07-07` 中有标签的 7 个日期。历史 archive 的 provider first-seen 仍不完整，
所以这里不称 formal forward。

## 模型

Entry head 的 label：

```text
60m rung-relative bid move
= held rung bid move - ladder median bid move
```

Challenger：

```text
M0 market level / mode distance / neighbor shape / spread / depth
+ weather probability shock
+ weather mode shift / transport L1
+ rung-relative immediate response
+ neighbor propagation / lead-lag
+ weather shock × mode distance × neighbor propagation
```

Continuation head 在 30m 使用完整 ladder 的当前 relative markout、mode distance、邻档传播和原始 weather shock，
预测 `30m executable bid -> 60m executable bid` 的 fee-adjusted incremental value。预测值大于 0 才 HOLD，
否则 EXIT；60m hard exit。

Entry proper-score 增量尚未通过：

| split | MSE delta challenger-M0 | target-date bootstrap 95% CI |
|---|---:|---:|
| development expanding OOF | `+0.00000106` | `[-0.00000146,+0.00000353]` |
| secondary holdout | `-0.00000190` | `[-0.00000746,+0.00000248]` |

因此不能把 positive conditional ROI 解释为已确认的独立 weather alpha。

## Maker threshold 与 position replay

Maker threshold 不是价格带 hard filter。它只在 development expanding OOF 的预注册 quantile grid 内选择：
`predicted maker-conditional net value > 0.004981`；development 为 35 positions / 7 dates，conditional ROI `+6.97%`。

Secondary holdout：

| policy | positions | dates | conditional ROI | 95% CI |
|---|---:|---:|---:|---:|
| full-ladder dynamic exit | 39 | 6 | `+13.13%` | `[+1.50%,+18.89%]` |
| fixed 30m diagnostic | 37 | 5 | `+4.38%` | `[-1.64%,+6.06%]` |
| fixed 60m diagnostic | 39 | 6 | `+13.56%` | `[+1.77%,+19.09%]` |

Dynamic exit 保住了 conditional 正收益，但尚未胜 fixed 60m（约 `-0.43pp`）。它的价值目前是建立正确的实时
position interface，并收集每次 checkpoint 的继续持有判断；不是宣称已经找到最优退出模型。

## 可运行入口

训练/重放：

```bash
.venv/bin/python -m weather_model_evaluation.cli forecast-repricing-position \
  --input /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260809/forecast_event_rungs.csv \
  --output-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810
```

Zero-notional runner：

```bash
.venv/bin/python scripts/ops/weather_lmvm_forecast_repricing_shadow_v1.py \
  --position-policy-model /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810/position_policy.joblib \
  --position-policy-sha256 79e5947c7d88c384a4b082a449c22abd17b4a3ce42ad57e9af27ab0dcda42baa
```

Runner 输出：`decision_bundles.jsonl`、`trade_intents.jsonl`、`position_decisions.jsonl`、
`quote_markouts.jsonl`。`TradeIntent` 始终为 zero notional；POST_MAKER 后的 HOLD/EXIT 是
conditional position telemetry，直到真实 order/fill journal 证明成交。

### 当前 raw 接入实测

当前 `strategy_snapshots` 只携带 hot/partial books，不能直接当完整 ladder。Runner 现在按快照中的
`canonical_orderbook_source.archive_path`，只读联接同一批次的 `market_books` 与
`market_ladder_snapshots`，并逐腿验证 request/response/parse/available 时钟；不重新请求盘口，也不把缺档事件送入模型。

2026-08-10 one-shot current-raw smoke：

| item | count |
|---|---:|
| collector-exact joined full ladders | 231 |
| joined rungs | 2,541 |
| initialized forecast streams | 23 |
| incomplete ladder events blocked | 1,229 |
| plan / order / exchange calls | 0 / 0 / 0 |

这证明完整策略入口可以消费当前 raw 并建立持续状态；`1,229` 是证据覆盖缺口，不是策略 selector 筛除。
首次启动只有 baseline，不会把左截断前不存在的 forecast revision 伪造成交易信号。历史 fixture 另外验证了
`POST_MAKER -> conditional HOLD/EXIT` 的全链路。

Artifact：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810`
- model SHA-256：`79e5947c7d88c384a4b082a449c22abd17b4a3ce42ad57e9af27ab0dcda42baa`

## 当前动作

保持 zero-notional。先运行这个完整 policy，积累 forecast event、maker post/queue、actual fill 和每个 5 分钟
full-ladder position checkpoint。只有 actual-fill 分母足够，且 frozen forward 中 entry residual、fill-adjusted ROI、
dynamic-exit uplift 都通过，才讨论真实部署。
