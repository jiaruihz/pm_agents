# 跨城市 Intraday Contract Root Fix v1

Status: production producer + v2 zero-notional shadow deployed; process/raw/exchange verification passed; dashboard API has pre-existing JRS LaunchAgent permission blocker
Evidence cutoff: 2026-08-01 10:46:05 UTC
Scope: Phase 0 后的 schema、runtime identity、cross-day locator、one-sided/error denominator；不修改模型、threshold 或 live 授权

## 结论

Phase A 前的 consumer/runtime root-fix 已在 develop 落地：

- 新 output/config contract 为 `weather_city_probability_shadow_v2` / `weather_city_probability_shadow_config_v2`，旧 v1 不再继续写入新目录。
- 每条 evaluation/checkpoint/error/summary/intent 都带 output schema fingerprint 与 runtime identity：repo HEAD、tracked dirty、config hash、loaded entrypoint/core/adapter/InputCatalog module hash、8 个 model/spec artifact hash。
- v1 reader 只做显式 migration；历史缺失的 loaded identity 标为 `legacy_runtime_identity_unavailable`，不事后伪造。
- `JsonlInputCatalog` 以 decision/availability clock 和 producer shard timezone 找 physical input；`target_date` 只作 row filter。Tokyo 不再读 `target_date/observations.jsonl`，Helsinki 不再读 mutable `observations/latest.json`。
- official/source journals 改为 reverse PIT scan；forecast snapshots 按 producer physical clock newest-first 定位，修复初版递归扫 906MB archive 的性能问题。
- Helsinki/Tokyo one-sided 都返回 interval-censored `not_scorable`；expected waiting/anchor capture gap 写稳定 checkpoint/incident，不再进入 exception storm。
- 生产独有的 `weather_live_cross_observations_loop.py` 与 fast-lane contract 已收回 develop/Git；producer payload、每条 observation、notification 和 state 均携带 loaded code/config/schema identity，v2 consumer 启动时 fail-closed 校验上游 fingerprint。
- Tokyo active ladder 改为 source + official anchor 的表达并集，并记录 `capture_cycle_id`、`capture_anchor_values`、`capture_reasons`；consumer 按 official anchor 选择同一 PIT capture 中的 expression，不再假定 `relative_offset=0` 就等于官方档位。

production 已从 clean deployment worktree 的 commit `41806c874df169d327cffcbc2f41579a0b0bf38d` 完成切换；真实下单权限与 notional 均未扩大。

## 1. 新 contract

新配置：`configs/weather/city_probability_shadow_v2.json`。

启动 handshake：

```text
config schema == weather_city_probability_shadow_config_v2
output schema == weather_city_probability_shadow_v2
output schema fingerprint == 51a9b5a1...b1f
producer schema == weather_high_frequency_observations_payload_v2
producer schema fingerprint == 789563c9...bf1
producer latest identity == consumer declared upstream identity
declared artifact hash == actual loaded artifact hash
loaded config dict == config file contents
```

输出目录单独使用 `city_probability_shadow_v2/`，不会把新 rows 继续 append 到混合 v1 journal。Tokyo 的 previous-weather input 同时读取 v2 和 legacy v1，通过兼容 reader 归一化。

## 2. 历史影响半径与反事实

固定 Phase 0 cutoff 的 v1 evaluation 共 78 行：

| 项目 | 数量 | 修复后口径 |
|---|---:|---|
| legacy/current shape 共用 v1 | 78 | 全部可规范化为 v2 reader row |
| inferred/native scored | 72 | 保留原概率和 evaluation identity |
| structured not_scorable | 6 | 保留 one-sided 固定分母 |
| 可恢复真实 loaded runtime identity | 0 | 78 行全部明确标 `legacy_runtime_identity_unavailable` |

旧 errors 共 1,166 个 poll rows，逐类反事实如下：

| 旧错误 | poll rows | 修复后行为 | 是否恢复评分 |
|---|---:|---|---|
| Helsinki/Tokyo one-sided | 614 | interval-censored evaluation，`not_scorable` | 不造 midpoint；保留分母 |
| Tokyo missing official target-day path | 171 | InputCatalog 从 physical `2026-07-31` shard 读取 target `2026-08-01` | **171/171 path failure 可恢复**；最早/最晚 error 时已有 12/18 个 PIT official states |
| Helsinki missing `2026-08-01` book file | 321 | `missing_market_expression` checkpoint/incident | 0 个伪评分；当时文件确实未产出 |
| Tokyo official/book bracket mismatch | 39 | 按同一 PIT capture 的 official expression 重放 | 17 可恢复 scored；13 恢复为 one-sided `not_scorable`；9 因历史未采到相差两档的 expression 不能事后造 quote，未来由 source+official ladder union 修复 |
| Helsinki `running_max_c` KeyError | 20 | `awaiting_official_observation` checkpoint | 0；等待首个 status=ok official row |
| Helsinki pre-forward malformed value | 1 | forward-start 前直接不评分 | 0；不应进入 adapter |

合计 1,166/1,166 均有确定去向：614 进入固定 evaluation denominator、171 消除错误 path、380 变成真实 coverage blocker、1 个 pre-forward poll 不进入分母。没有把 coverage gap 包装成策略筛选。

机器可读 migration：`docs/analysis/2026-08/generated/city_intraday_contract_repair_v1/migration_summary.json`；规范化 rows 为同目录 `evaluations_v2_migrated.jsonl`。source raw 未修改。

## 3. 验证

当前真实 raw、临时 output 的 smoke：

```text
elapsed_sec=1.398
evaluated=4
scored=4
not_scorable=0
checkpoint_blockers=0
errors=0
new_paper_intents=1
orders_submitted=0
```

smoke 的 paper intent 为 zero-notional 临时文件，不是生产 intent/order。runtime identity 实际记录 core、Helsinki/Tokyo adapters、InputCatalog 和 8 个 artifacts 的 SHA256。

新增 producer/consumer handshake、Tokyo mismatch expression 和 source+official ladder union 回归后，相关定向测试为 `102 passed`（另有 2 个既有 pandas fragmentation warning）；producer 无网络 smoke 输出 v2 fingerprint、4 个 loaded module hashes，并在 state 中写入同一 contract。weather docs check 通过。

39 行逐条重放产物：`generated/city_intraday_contract_repair_v1/tokyo_anchor_gap_replay_v1.json`。这 39 行是 poll rows，不是 39 个独立 city-day；其中 9 行对应同一段 `official=33/source=35` 缺口，历史只有 34/35/36 books，所以保留为真实 coverage gap。

## 4. Production cutover 验证

- producer：PID `30243`，v2 schema fingerprint `789563c9...bf1`，首轮保留 65 rows 且 `new_observation_count=0`，未制造重复 notification。
- Tokyo ladder：PID `30697`，首个 v2 capture 同时记录 source/official anchors `{30,35}`，生成表达并集 `29,30,31,34,35,36`。
- city shadow v2：PID `31481`；完整循环 `evaluated=2, scored=2, blockers=0, errors=0, orders_submitted=0`，upstream producer instance 与 handshake 完全匹配。
- 旧 v1 shadow session 已停止；v2 仅写 `evaluations.jsonl`、`paper_intents.jsonl` 等 zero-notional artifacts，没有 live order file/client。
- 既有 downstream live fast-source 进程 PID `18670` 在 producer 切换后继续返回 `status=ok`；首轮 producer 没有新 event，因而没有 cutover-induced duplicate order。
- production manifest strict 通过；canonical DB identity healthy。dashboard 前端 `:5174` 页面壳返回 HTTP 200，但 LaunchAgent API 读取 JRS symlink 时触发 `sqlite3.OperationalError: unable to open database file`，strategy-runtime endpoint 因而返回 500；这是既有 JRS/LaunchAgent 权限问题，不能写成 API 验证通过。全局 health check 仍有切换前已存在的非目标 coverage warnings，不属于本次 v2 contract cutover。

### Tokyo off-hours stale error 修复

2026-08-01 13:02:40–14:06:32 UTC，Tokyo adapter 在本地 18:00 评分窗结束后仍先执行 book-age 检查，因 ladder 不再产出当前 capture 而逐周期写入 61 条 `Tokyo active current book is stale`。61/61 都是同一 off-hours 运行口径错误，不是 61 个 signal/event；期间新增 live order/fill 为 0，paper intent 也未增加，因此 notional、shares、fee、PnL delta 均为 0。

commit `81a7ca8d46de5b0b78c93d1a96ea141013756fee` 将 frozen Tokyo 06:00–18:00 本地评分窗判断前移；窗内 stale book 仍 fail-closed，窗外直接不评分。production v2 shadow 于 14:06:44 UTC 重载，连续两个周期 `errors=0`，历史 error journal 保留不删，计数稳定在 61。

## 5. 尚未关闭的项

- 历史 multi-anchor coverage：30/39 poll rows 可用既有 sibling books 恢复；其余 9/39 的缺失 quote 不允许事后补造，只能由新 ladder union 在 forward 中补齐。
- Amsterdam：仍无 active producer/model consumer，按原计划在 Phase D 恢复 ownership 后接 interval/revision adapter。
