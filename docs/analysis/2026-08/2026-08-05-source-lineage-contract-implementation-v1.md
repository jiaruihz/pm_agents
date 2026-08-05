# Weather source lineage contract 实现 v1

## 数据快照

- 生产 manifest / health：2026-08-05 检查均为 healthy；本次只读生产 raw，没有 sync/rebuild/restart/deploy。
- 生产 raw：`/Volumes/jrs/weather_data_feed_service_runtime`。
- forecast 独立 smoke：Shanghai，exact Open-Meteo Single Runs，6h conservative common-run lag。
- full-ladder 只读 smoke：`snapshot_20260805_1117.json`，946 rungs / 86 event ladders。
- source observation / TAF 复核：现有 append-only source-event raw 已包含四时钟、event id、revision parent 与 payload hash；没有改写生产 runtime。

## 结论与动作

已把 provider/event identity、collector clocks、content hash、batch/snapshot identity 和 producer build 拆成通用合同，并接入 forecast、forecast curve、orderbook/full ladder 与 settlement archive。source observation / TAF 的不可变 source-event 链原本已满足合同，不再给派生 latest cache 重复伪造 first-seen。代码已验证，**尚未部署或重启生产 collector**；因此现有生产 raw 仍保持旧 schema，不能把本地 smoke 当成生产已积累新 lineage。

下一生产动作必须走 `weather-strategy-deploy`：git-first 重载 `weather_data_feed_jrs` 与 `weather_full_ladder_capture`，再核对首批 raw、进程 SHA 和下游兼容。本报告不授权该动作。

## 各链路合同

| 链路 | provider/source identity | collector/PIT clocks | content/capture identity | 状态 |
|---|---|---|---|---|
| forecast generic endpoint | run timestamp 明确为 unavailable | fetch/detected/first-seen/available/ingested | raw hash、capture、batch、build | 保留，但 strict contract 为 blocker |
| forecast exact Single Runs | `model_key + forecast_run_at_utc + exact request endpoint/hash` | 同上；run age 与 target lead 单列 | provider-run revision 与 same-run content revision 分离 | 已实现；无 fallback 才认 identified |
| multi-model batch | model values + missing model list | batch available clock | mean/median/q25/q75/min/max/spread/IQR、assigned-minus-consensus、batch hash | 已实现 |
| forecast hourly curve | run 若源可证则保存，否则 null | detected/first-seen/available | values hash、capture/batch、information event、build | 已实现 |
| source observation / TAF | observation report / TAF issue+valid window | existing four clocks | information_event_id、revision parent、payload/raw hash | 原链已满足；保留 |
| derived observation state | source/station/report 指回 immutable source-event；不重复定义 event first-seen | 只保留派生 cache 的 generated/fetched clock | 不另造 observation event identity | 无需重复加；source-event 为权威 lineage |
| orderbook token response | token + fetched response | detected/first-seen/available | raw hash、book_capture_id、build | 已补 |
| full exact ladder | city/date/event + rung manifest | snapshot collection/available | snapshot_capture_id、book_snapshot_id、rung hash、normalized distribution | 已补；probability 与 two-sided book completeness 分开 |
| settlement archive | city/date/bracket/source path | canonical available；原始 first-seen=null | source file hash/mtime、build | 已补；明确 `late_backfill_first_seen_unknown` |

orders/fills 不需要 `forecast_run_at_utc`；它们继续使用既有 plan/order/execution/fill identity 和交易所时间，不能复制 forecast 字段冒充交易 lineage。

## Forecast 真实 smoke

- 6h lag 请求到明确 run `2026-08-04T18:00:00Z`。
- Shanghai × 2 target dates × 5 models：10 个 run-aware rows，10/10 strict contract `identified`。
- 同批保留 `single_run_request_endpoint`、request hash、raw hash、source fetch start/end、first-seen、batch ID。
- generic latest endpoint 同时保留，但 provider run 继续为 null / blocked；没有用 estimated cycle 填充。

默认使用 6h conservative common run，目标是稳定获得可比较的同步多模型批次。后续可以另加 latest-per-model capture，但必须作为不同 batch class，不能与 synchronized consensus 混分母。

## Full-ladder 真实 raw 只读 smoke

- 946 rungs → 86 city×target×event manifests。
- native lattice complete：86/86。
- probability distribution scorable：86/86。
- two-sided book complete：10/86。

这说明“市场概率可归一化”与“所有档位均有双边可执行盘口”不能共用一个 complete 标志。新合同分别保存；其余 76 个 checkpoint 保留 blocker，不静默丢行。

## 验证

- forecast、source lineage、ladder、service 与 canonical schema 合并回归：105 passed。
- forecast exact API smoke：PASS。
- source observation / TAF raw census：PASS；已有 information_event_id、available/detected/first_seen、payload/raw hash 与 revision parent。
- full-ladder current raw annotation smoke：PASS。
- Python compile：PASS。

## 影响半径

- 历史 forecast rows 的 provider run 覆盖仍不可追溯修复；不能用估算周期回填。历史继续标 blocked。
- 新代码部署后才开始积累真实 run-aware forward 数据。
- settlement 历史 first-seen 仍未知；新增 hash/mtime 只支持内容与文件版本审计，不把 mtime 当 provider/collector first-seen。
- 本次没有改变 signal eligibility、概率、订单、资金或 live execution。

significance=NA；baseline=NA；forward=NA；conclusion=`data_contract_implemented_not_deployed`。
