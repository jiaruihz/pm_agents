# 东京 / 赫尔辛基 / 阿姆斯特丹 Runtime Contract 实数审计 v1

Status: snapshot / design-input
Evidence cutoff: 2026-08-01 13:23 Beijing / 05:23 UTC
Scope: 实际 raw、canonical、zero-notional journal 与当前/生产 checkout 代码；不改变 collector、runner 或 live 行为

## 结论

“共享采集与 replay、城市插件、统一候选与执行”的总方向成立，但原方案还不能直接进入实现。三城实数串联暴露了四个必须先建的 contract：

1. observation 不只是一个 `temp_c`：Amsterdam/KNMI 是 10 分钟 measurement interval，且同一 provider item 会 revision；东京/赫尔辛基当前是 point observation。
2. `target_date` 是业务日期，不是物理文件分区。东京在本地 8 月 1 日时，official rows 一度仍写在 UTC 7 月 31 日 shard，按 `target_date/observations.jsonl` 取文件造成连续缺数。
3. source、official/settlement 与 market bracket 是不同 anchor。JMA 可以先跨档；把 source offset-0 book 当 official current bracket 会把正常领先状态报成 mismatch。
4. repo contract 与实际运行代码必须做 handshake。单测验证的是 develop working tree，但生产 checkout/loaded SHA 不同，实际 journal 仍跑旧 schema 和旧 one-sided 行为。

因此总体设计保留，但 Phase A 前新增 `Phase 0: deployed-contract census`，并把 revision、partition locator、multi-anchor ladder 和 schema/version handshake 提升为 P0。未完成这些项前，不开始三城 runtime 迁移，不改变 live。

## 审计范围与方法

- production manifest：canonical DB route healthy；同时存在 `process_checkout_head_drift` warning，生产进程主要运行于 `/Users/deepsleep/projects/pm_agents_prod`。
- raw window：Helsinki/FMI `2026-07-21..08-01`，Tokyo/JMA `2026-07-22..08-01`，Amsterdam/KNMI `2026-07-27..07-31`。
- existing replay：`research_three_city_first_seen_path_v1.py --start-date 2026-07-21 --end-date 2026-07-31 --min-prior-dates 3`，输出到临时目录，不改既有研究产物。
- runtime journal：`city_probability_shadow_v1` evaluations/intents/errors，截至 05:10 UTC。
- tests：city probability、三城 first-seen、KNMI、first-seen producers 和 shared `OrderRuntime`，共 `37 passed`。

## 1. 三城 raw 不是同一种 payload

按 observation timestamp 去重后的实际覆盖：

| city/source | raw rows | unique obs timestamps | duplicate factor | first-seen age p50 / p95 / max | 关键 payload 语义 |
|---|---:|---:|---:|---|---|
| Helsinki/FMI | 1,006 | 909 | 1.11× | 1.918 / 2.614 / 10.064 min | point `temp_c`；wind 100%，pressure 10.7% |
| Tokyo/JMA | 933 | 919 | 1.02× | 7.104 / 7.593 / 14.169 min | point `temp_c`；当前无 wind/pressure |
| Amsterdam/KNMI | 1,388 | 467 | 2.97× | 3.915 / 4.052 / 83.809 min | 10m interval end；`ta` + `tx`；可 revision |

三城 `target_date` 与各自 timezone 的 observation local date 在本窗均无 mismatch；Helsinki/Tokyo 也没有大于 30 分钟的 exact first-seen observation。问题集中在 Amsterdam 的 revision/backfill：

- 73 个 observation timestamps 出现多个 payload hash；
- 1,384/1,388 raw rows带 KNMI revision identity，revision kind 为 460 initial、924 update；
- 9 个 unique observation 的最早 capture age 超过 30 分钟；现有三城 loader 按 revision rows 统计时排除了 26 rows；
- `measurement_interval_end_utc` 和 `max_temp_c_past_10m` 覆盖 100%，说明它不能被当成普通瞬时 point observation。

设计影响：`EventEnvelope` 必须声明 `payload_kind=point|interval_summary`、measurement interval、native unit/precision、`event_role`、`revision_of_event_id` 和 material-state-change；不能只用一个通用 schema_version 加可选字段。

## 2. canonical 三城并未真正串通

05:11 UTC 的实数 replay 得到：

- raw matching rows `15,600`；collector-exact states `2,250`；OOF predictions `11,907`；
- canonical `weather_information_events` 中只有 KNMI `608` rows；FMI/JMA 为 `0`；
- fast-source 触发的三城 checkpoint 为 `0`；
- same-checkpoint PIT market baseline `0`，executable expression/fill `0/0`。

05:23 UTC canonical refresh 后，三城总 checkpoint 数变为 Amsterdam 1,011、Helsinki 123、Tokyo 112，但 source event counts 仍只有 KNMI 608；新增 checkpoint 并不证明 FMI/JMA fast-source lineage 已接入。这个审计过程中 derived DB 自身发生刷新，还说明研究产物必须记录 `canonical_build_id / DB mtime / observed_at`，否则同一命令的 coverage 会随 refresh 漂移。

因此三城研究代码可以读 raw 并产概率，但尚不能证明共享 fast-source checkpoint/replay/candidate 链已经成立。Amsterdam 被 canonical ingest，Tokyo/Helsinki 没有，并非同一 adapter 的小差异，而是 producer capability/deployed version 不一致。

## 3. 单测通过，但实际 runtime 仍持续丢分母

定向测试 `37/37 passed`；实际 `city_probability_shadow_v1` journal 只有：

- evaluations `66`：Tokyo 56、Helsinki 10；
- distinct source observations：Tokyo 28、Helsinki 5；
- zero-notional paper intents `3`，均为 Tokyo；
- evaluations 仍是旧 v1 shape，没有 `evaluation_status` 字段。

同期 errors 共 `1,160` poll rows。按相邻错误间隔超过 5 分钟折叠后是 12 个独立故障窗口：

| city | failure class | poll rows | incident windows | 直接原因 |
|---|---|---:|---:|---|
| Helsinki | one-sided / missing midpoint | 574 | 2 | 旧 deployed adapter 要求 two-sided，直接抛异常 |
| Helsinki | missing target-day book file | 321 | 1 | 本地日已切换，但新 shard 尚不存在 |
| Helsinki | schema/state shape | 21 | 2 | `running_max_c` 缺失或字段类型不符 |
| Tokyo | official/book bracket mismatch | 39 | 4 | source anchor 先于 official anchor 跳档 |
| Tokyo | one-sided / missing midpoint | 34 | 2 | midpoint 不存在即抛异常 |
| Tokyo | missing official target-day journal | 171 | 1 | 业务日期与 UTC 文件 shard 混用 |

这里 `1,160` 不是 1,160 个独立数据事故，而是 loop 每分钟重复记录同一故障；但它们都使当前 checkpoint 没有形成 evaluation，属于真实 evidence-funnel denominator loss。错误 journal 只有 exception message，没有 stage、input refs、checkpoint ID 和 traceback，也放大了排查成本。

设计影响：等待首报、文件未出现、one-sided、source/official 异步必须成为结构化 checkpoint 状态，而不是 adapter exception；telemetry 同时记录 poll count 与 incident identity。

## 4. Tokyo 证明 bracket anchor 不能只有一个

8 月 1 日 Tokyo active-ladder 的 500 个 `relative_offset=0 / NO / book_status=ok` rows：

- two-sided 379，bid-only 121（24.2%）；
- 446 rows 的 book reference 与 PIT official running-max bracket 相同；
- 54 rows（10.8%）不同，包括 `28→29`、`32→33`、`33→34`、`33→35`。

这些 mismatch 多数是 JMA 先跨档、routine METAR 尚未确认的正常 source lead，不应当作为数据错误。更重要的是 collector 只围绕 source anchor 抓 `-1/0/+1`：当 source=35、official=33 时，official current bracket 已在捕获集合之外，adapter 无法靠“换一行”修复。

设计影响：checkpoint 必须同时保存：

- `source_lattice_anchor`；
- `official_or_settlement_lattice_anchor`；
- `market_expression_anchor`；
- 每个 anchor 的 native unit、rounding/lattice profile 和 evidence timestamp。

book capture 应取各 anchor 所需 expression 的 union，或保存完整 ladder；`relative_offset` 必须相对声明的 anchor，不能是无上下文整数。

## 5. 生产代码与开发 contract 发生漂移

manifest 显示生产 checkout/loaded SHA 与 develop 不同。逐文件核对：

- Tokyo adapter 与 develop 一致；
- `core.py`、Helsinki adapter、city config 不一致；develop 已有结构化 one-sided/not-scorable 修复，但运行 journal 仍是旧行为；
- high-frequency producer 差异更大：develop 已加 information-event header，生产版本仍使用旧 first-seen/new-observation 写法；
- 两个不兼容 row shape 仍使用 `weather_city_probability_shadow_v1`，schema version 没有 bump。

所以“repo tests pass”不能作为 runtime contract 验收。需要每个 output row/summary 固定记录 source checkout SHA、loaded module SHA、config hash、model artifact hash 和 schema fingerprint；启动时 consumer 与 producer 做 contract handshake，不匹配则显式停在 `contract_incompatible`。

## 6. 代码层额外风险

### 6.1 历史序列的 PIT 截断不完整

Tokyo `_jma_history` 和 Helsinki `_fmi_history` 主要按 `observation_time <= source_obs_time` 截断；没有对每条 prior row 统一强制 `available_at/first_seen <= decision`。本窗 Helsinki/Tokyo 未发现 >30 分钟 late row，因此没有证据说现有 62 evaluations 已实际泄漏；但 Amsterdam 已真实存在 late revisions，复用同类逻辑就会把晚到修订带进早期 replay。

修订要求：所有历史 state 都由 event store 按 `available_at_utc <= checkpoint.as_of` fold，adapter 不再直接扫 raw journal 自己截断。

### 6.2 `latest.json` 与 `target_date/path` 不是 replay contract

Helsinki adapter 从全局 `observations/latest.json` 取 city row，再用 target_date 拼 book/forecast 路径；Tokyo 用 target_date 拼 official journal。跨日、等待首报或重放旧 checkpoint 时，这些 mutable latest/path assumptions 会失败。

修订要求：加入 `InputCatalog/DataLocator`，按 event identity/index 查询，不由策略代码猜物理路径；`latest` 只用于运维展示，不作为 deterministic replay source。

### 6.3 evaluation identity 没声明 market clock

当前 `evaluation_id` 基于 city/date/source_obs/bracket/side/model，不含 trigger event、book snapshot 或 policy clock。它事实上把每个 source observation 的首次成功评分锁住，但“first post-event book”和“decision-current book”没有被编码；若最早 book one-sided、稍后变 two-sided，最终记录的是第一次成功而不是预注册 clock。

修订要求：checkpoint ID 包含 trigger + as-of + input-event-set hash；model feature book 与 execution book 分开；dedupe 按声明的 market clock，而不是按 scorer 何时碰巧成功。

### 6.4 target 语义不统一

- 三城 first-seen research：`P(new source strict high within 30/60/120m)`，不是 settlement outcome；
- Tokyo runtime：current exact bracket `P(stay)`，同时输出 YES/NO；
- Helsinki runtime：current bracket overshoot/NO 概率；
- Amsterdam 当前没有同级 probability runtime adapter，另有历史 one-shot execution script。

修订要求：增加 `PredictionTarget` ontology，区分 `physical_path`、`settlement_outcome`、`market_expression`。只有已映射为 settlement expression probability 的输出可以生成 `SignalCandidate/TradeIntent`；physical-path head 只能作为上游 feature/model output。

## 7. 对总体方案的最终修改

保留原架构，但实现顺序改为：

```text
Phase 0 deployed contract census
  -> Phase A typed event/revision/partition/anchor contracts + golden raw fixtures
  -> Phase B event-store fold + InputCatalog + virtual clock
  -> Phase C Tokyo/Helsinki old-vs-new parity and multi-anchor ladder
  -> Phase D Amsterdam interval/revision adapter and collector liveness
  -> Phase E canonical checkpoint/candidate materialization
  -> Phase F zero-notional forward
  -> Phase G explicitly authorized per-instance execution migration
```

Phase A 的 gate 不再只是 unit tests：必须同时通过 develop fixture、deployed-runtime sample、schema fingerprint、cross-day rollover、revision replay、one-sided interval、source/official two-anchor 和 repeated replay parity。

## 8. 当前动作

- 总体设计：`revise_then_implement`。
- 三城 alpha/live：本审计不评价，不改变任何 live/shadow 授权。
- runtime migration：先完成 Phase 0/A，不直接抽象现有 adapter。
- production：本轮无部署、无重启、无配置变更、无真实订单。

## 9. 复现

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/analysis/market_structure_edge/audit_three_city_runtime_contract_v1.py \
  --start-date 2026-07-21 --end-date 2026-08-01
.venv/bin/python scripts/analysis/market_structure_edge/research_three_city_first_seen_path_v1.py \
  --start-date 2026-07-21 --end-date 2026-07-31 --min-prior-dates 3 \
  --out-dir <temporary-output-dir> --report <temporary-report-path>
```

第二条命令只读 raw/canonical/runtime journal；输出含 source shape、incident windows、Tokyo anchor 和 canonical coverage。第三条命令验证 physical-path probability/evidence funnel，不代表 market residual 或 live evidence。
