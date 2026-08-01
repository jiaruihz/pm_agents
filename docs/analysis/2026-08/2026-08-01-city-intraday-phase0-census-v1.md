# 跨城市 Intraday Runtime Phase 0 Census v1

Status: Phase 0 complete / Phase A implementation gate closed
Evidence cutoff: 2026-08-01 05:48:16 UTC
Scope: Helsinki、Tokyo、Amsterdam 实际 producer/consumer、checkout/config/code hash、raw shape 与异常样本；只读，无部署、重启或下单变化

## 结论

Phase 0 已完成：三城运行链已动态盘点，9 个 deployed samples 已冻结并通过同一个 contract validator，实际代码/config 与 row schema fingerprint 已固化。总体架构可以继续，但 Phase A 不能直接开始抽公共 runtime；必须先关闭 runtime identity、schema migration 和 cross-day locator 三项 outstanding P0。one-sided 行为在 cutoff 前已经由当前 zero-notional runtime 修复，但旧/新 rows 仍共用 v1 journal，所以它的 migration 还没有收口。

`phase0_complete=true` 不等于现有 runtime 已正确。当前 `phase_a_implementation_gate_pass=false`，表示 census 本身验收完成，同时明确拦住带病抽象。

后续状态：consumer/runtime root-fix 已在 develop 完成但尚未部署，见 [contract root-fix v1](2026-08-01-city-intraday-contract-root-fix-v1.md)。本 Phase 0 snapshot 的 production 判断保持 cutoff 原样，不用后续本地代码反写历史事实。

机器可读结果：[`phase0_census.json`](generated/city_intraday_phase0_census_v1/phase0_census.json)。冻结样本位于 `tests/fixtures/weather_city_intraday_phase0/`，生成与复验脚本是 `scripts/analysis/market_structure_edge/weather_city_intraday_phase0_census.py`。

## 1. 实际运行链

production manifest 在 cutoff 时为 `warning`，canonical DB route 为 `healthy`：兼容入口与 `/Volumes/jrs/pm_agents/runtime/weather.db` 同 device/inode。生产相关 checkout 是 `/Users/deepsleep/projects/pm_agents_prod`，HEAD `00bf846b3c1e7d0ce59fa8817cd5825194c4a0e3`；manifest 同时发现另一个运行进程报告 loaded SHA 与 checkout HEAD 不同，因此 runtime identity 不能只看工作树。

| city | source producer | book producer | model consumer | actual payload / ownership |
|---|---|---|---|---|
| Helsinki | PID 17547 `weather_live_cross_observations_loop.py` | PID 22235 active ladder | PID 25904 city probability shadow | FMI point observation；live-cross owner |
| Tokyo | PID 17547 `weather_live_cross_observations_loop.py` | PID 34181 active ladder | PID 25904 city probability shadow | JMA point observation + multi-anchor；live-cross owner |
| Amsterdam | none | none | none | KNMI interval/revision historical raw；当前无 active owner 和同级 probability runtime |

对 Helsinki/Tokyo 已固化 producer、observer、runtime core、city adapter、config 的 census-time SHA256；Helsinki 4 个 model artifacts、Tokyo 2 个 model + 2 个 spec artifacts 的实际 SHA256 均与 config 声明一致（8/8 match）。运行进程没有输出 loaded module/config/artifact hash，所以这些 hash 只能证明“cutoff 时磁盘内容”，不能证明进程内存中加载的精确内容；这是 `P0-RUNTIME-IDENTITY`，不能用 repo test 替代。

## 2. 冻结的 deployed contract samples

| fixture | 验证的实际语义 | schema fingerprint 前缀 |
|---|---|---|
| `helsinki_point_observation` | FMI point、first-seen、天气字段稀疏性 | `6a19a07a4fb5` |
| `tokyo_point_observation` | JMA point、约 7 分钟 first-seen | `21b5189160ee` |
| `amsterdam_interval_initial` | KNMI 10m interval initial | `e8573616417f` |
| `amsterdam_interval_revision_pair` | 同 provider item 的 initial→material revision lineage | `ec4253f24d34` |
| `helsinki_one_sided_book` | 有效 bid-only book，不是缺数据 | `6a80405d33e6` |
| `tokyo_one_sided_book` | 有效 bid-only book，不是缺数据 | `6a80405d33e6` |
| `helsinki_cross_day_partition` | target 8/1 row 物理写在 UTC 7/31 shard | `9eb8cba3f906` |
| `tokyo_cross_day_partition` | target 8/1 row 物理写在 UTC 7/31 shard | `9eb8cba3f906` |
| `tokyo_multi_anchor_mismatch` | PIT official anchor 28、source/book anchor 29 | `3d4ab23242fb` |

validator 同时验证 required fields、one-sided 状态、revision parent、cross-day shard 和 multi-anchor mismatch。所有 fixtures 保留原文件、line number、source-file SHA256 与 compact record fingerprint，后续 contract 变更可以直接回归。

cutoff 时 runtime journal 有 78 条 evaluation：68 条 legacy rows 缺 `evaluation_status`，10 条 current rows 已有结构化 status。历史 one-sided exception 为 614 个重复 poll rows，最后一条在 `05:31:06 UTC`；current shape 已保存 6 条 `one_sided_market_probability_interval`（3 个 distinct source observations），不再丢出固定分母。这证明行为修复已生效，同时也直接证明同名 v1 schema 已混入不兼容 shape。

## 3. 口径错误的修复顺序

### 立即修：Phase 0 后、Phase A 公共抽象前

以下三项不等到城市迁移后再补；它们组成下一批 root-fix，修完并回放 9 个 fixtures 后才能打开 Phase A gate：

1. `P0-RUNTIME-IDENTITY`：每个 producer/consumer output 固定 loaded repo/module/config/artifact/schema fingerprint，启动做 handshake；解决“磁盘代码、进程加载代码、同名 schema”无法对账。
2. `P0-SCHEMA-VERSION`：deployed 与 develop 的 evaluation shape 分开 version，提供显式 migration；禁止继续共用 `weather_city_probability_shadow_v1`。
3. `P0-PARTITION-LOCATOR`：引入 InputCatalog/DataLocator，按 event identity 查询；`target_date` 不再拼物理 shard，跨日 waiting 状态不再形成 missing-file error storm。

`P0-ONE-SIDED` 的 runtime 行为已完成：bid-only/ask-only 现在写结构化 `not_scorable` 并保留固定分母。剩余工作并入第 2 项 schema migration：迁移/分层旧 614 个 poll errors 与 68 个 legacy evaluations，避免研究读取时把两种 shape 混算。

### Phase A contract 修复

- `P0-MULTI-ANCHOR`：事件/checkpoint 明确 source、official/settlement、market-expression 三个 anchor；`relative_offset` 必须声明基准，book capture 取所需 expression union。
- `P0-REVISION-EVENT`：typed `point|interval_summary` payload、revision parent、`available_at` fold 和 late revision replay；Amsterdam 不再套 point observation 语义。

### Phase A→B 数据链修复

- `P0-CANONICAL-SOURCE-LINEAGE`：FMI/JMA information event 进入 canonical checkpoint/candidate lineage，并锁 canonical build identity；不能只看三城 checkpoint 数就声称 fast-source 串通。

### Phase D 城市恢复

- `P0-AMSTERDAM-LIVENESS`：恢复 KNMI collector ownership/freshness，再接 interval/revision adapter。当前没有运行 producer，所以不在本轮擅自启动第二套 collector。

生产部署与 contract 代码修复分开：上述 root-fix 应立即在 Phase A 前完成；涉及重启/实例行为的部署仍按 `weather-strategy-deploy` 走 git-first、单实例、显式确认。本轮没有改变生产。

## 4. 验收证据

```text
phase0_complete=true
phase_a_implementation_gate_pass=false
fixture_count=9
fixture_validation_errors=0
declared/actual artifact hashes=8/8 match
Phase 0 validator tests=5 passed
three-city relevant regression suite=43 passed
one-sided behavior=current runtime fixed; journal migration pending
production changes=0
```

复验：

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict \
  --json-out /tmp/weather_phase0_production_manifest.json
.venv/bin/python scripts/analysis/market_structure_edge/weather_city_intraday_phase0_census.py \
  --production-manifest /tmp/weather_phase0_production_manifest.json
.venv/bin/pytest -q tests/research_tests/test_weather_city_intraday_phase0_census.py
```
