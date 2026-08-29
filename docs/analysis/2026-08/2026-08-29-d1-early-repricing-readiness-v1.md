# Weather 研究：D-1 early repricing readiness v1

Status: `collector-deployed / awaiting-first-unseen-run / formal-forward-blocked / no-live-change`

## 单轮 brief

- hypothesis（可证伪，一句话）：D-1 18–24h 的 response-complete provider-run consensus revision
  会在随后 60 分钟推动 exact-bracket market mass 同向重分配，并留下覆盖双边 fee、spread 与可执行
  depth 的 single-YES / narrow-YES-strip residual。
- data scope（city/source/target-date，含或不含已查看样本）：34 城、target_date
  `2026-08-06..2026-08-31`；capture 审计窗口为
  `2026-08-05T07:15:57.703070Z..2026-08-29T13:46:54.417898Z`。当前全部是已查看的
  legacy earliest-observed rows；D-2、D0、钱包 fill-trigger 均不进入本轮。
- market capture scope/policy：只读现有 canonical REST market books、full-ladder snapshots 与历史
  paper snapshots；不扩 WS subscription。
- collector budget：请求 cadence、34 城、5 模型、retention 与 scheduler 均不变；只重载 response-complete
  clock release。
- acceptance gates：同 rows market proper-score baseline；60m 为 frozen primary horizon；target_date
  block bootstrap；direct ask entry、future bid/convergence exit、双边官方 fee、fresh depth；clean development
  至少 30 个 settled target dates 后才冻结模型，之后另以 untouched forward 评审。
- 动作：`coverage audit + minimal collector clock deploy`。
- 不在范围内：live/shadow、下单、D-2/D0、城市/阈值搜索、WS 扩采、改写旧 JSONL。

## Readiness（先填；`BLOCKED` 时停止于 coverage/机制诊断）

| 项目 | READY / BLOCKED | 证据 / 缺口 |
|---|---|---|
| PIT state + four clocks | `READY` | production collector 已从 `e943e956…d0d5f` 切到 `761a16d8…bd40`；首轮新增 1,020 rows 全部保存 request/response clocks，clock-order violation=0。它们是已见 run 的重复 poll，因此 append-only 保留旧 status；首个 unseen provider run 才会写 `collector_response_complete` |
| canonical DB / build identity | `READY` | strict manifest 通过；physical DB=`/Volumes/jrs/pm_agents/runtime/weather.db`，device/inode=`16777244/54444`；storage identity audit healthy |
| fresh market quote + depth | `BLOCKED` | 399,228 checkpoints 中 54,116 complete，legacy 60m 可评分 3,150；但与 response-complete signal 相交为 0，formal executable=0 |
| settlement / label coverage | `BLOCKED` | legacy settlement-complete events=12,688；clean response-complete events=0 |
| independent target dates | `BLOCKED` | clean=0；legacy 60m diagnostic=23 dates，不能充当 forward |
| clean frozen-forward status | `BLOCKED` | forward provider-run events=0，尚无 freeze boundary |
| selective capture policy/version + subscription coverage（若使用 WS） | `N/A` | 本轮不扩 WS；现有 REST/full-ladder 只作 coverage audit |
| incremental-book reconstruction parity（若使用 WS） | `N/A` | 未使用 incremental WS book |
| sampling grain / message weighting（若使用 WS） | `READY` | provider-run transition × city × target_date；同一 market transition 独立归因，target_date 为 bootstrap block |

## 结论与动作

collector blocker 已修复，但 formal fixed A/B 仍保持 `BLOCKED`。已在旧 production lineage
`e943e956…d0d5f` 上构造最小 release `761a16d80f130b780fe65827ede4ca303548bd40`，
production pin commit=`d23199d6`；controller 于
`2026-08-29T16:33Z` 完成 stop→registered start。未带入 workspace 后续 market-demand/WS 功能，
请求 cadence、城市、模型和 order path 均未改变。

首轮落盘 1,020 rows（其中 D-1 510），request/response clock completeness=`1,020/1,020`，
clock-order violation=`0`。这些 rows 对应部署前已见过的三个 provider runs，所以
`run_first_observation=false` 且 status 继续为旧 `collector_exact`；这是 append-only 正确结果，
不能为了造 clean 样本回写 first-seen。clean epoch 的收集能力已经上线，首个 unseen provider run
才会产生第一条 `collector_response_complete`。之后累计至少 30 个 clean settled target dates，
再运行固定 60m A/B；当前不改 shadow/live。

## Deployment acceptance（2026-08-29 16:33 UTC）

- loaded release：`761a16d80f130b780fe65827ede4ca303548bd40`；session pane PID
  `65490 → 22332`，child capture PID=`23419`。
- pre/post manifest：baseline 32 sessions，post missing=`0`；target、JRS context、data-feed
  semantics、canonical DB identity 均 healthy。唯一 warning 是三个未注册旧 persistent worktrees，
  不在本次清理授权范围。
- raw acceptance：rows `916,140 → 917,160`；新增 `1,020`，clock complete `1,020`，
  ordering violation `0`，new run-first observations `0`（均为既有 run 的复 poll）。
- execution isolation：orders `7,576 → 7,576`、fills `5,185 → 5,185`；
  `instance_id=weather_forecast_run_capture_v1` 的 orders 始终为 `0`。
- rollback：把 `forecast_capture` pin 恢复为
  `e943e956a13526b241e29d5192c89b662bc70d5f`，再由同一 controller restart。

## Target

```text
估计 P(final exact bracket | response-complete provider-run revision, PIT state)，
并检验 60m repricing residual 是否优于同一时点 market 且可覆盖执行摩擦。
```

- physical target / exact-bracket semantics：settlement-native full ladder；single YES / narrow strip 是
  expression，不把 touch、快源跨档或钱包 SELL 当 settlement truth。
- grain / universe：D-1 provider-model-run transition × city × target_date × exact rung；primary checkpoint
  为当地 target_date 前 18–24h。
- decision timestamp：`response_received_at_utc` / `available_at_utc`，不得使用 request-start 或重复 poll。
- label / settlement source：canonical exact settlement；当前 legacy complete=12,688，formal clean=0。
- executable expression / fee：t0 direct ask 买入，convergence 或 60m fresh bid 卖出，双边官方 fee；
  narrow strip 逐腿 depth/accounting，不使用 midpoint 或 future-touch maker 假设。
- primary metric / market baseline：同 rows Brier/logloss/RPS 与 market baseline；随后比较 fee-adjusted
  PnL/ROI 和 target-date block CI。ROI 不参与选模。

## Data integrity / PIT

| 项目 | 值 |
|---|---|
| raw source and coverage | forecast capture 911,176 rows；market checkpoints 399,228；34 城 |
| forecast issue/run/hash/age | 43,588 unique provider-run keys，867,588 duplicate deliveries collapsed |
| source first-seen/cadence | 38,976 provider-run transitions；D-1 16,198；primary 18–24h 3,968 |
| source-to-settlement basis | 12,688 legacy settlement-complete events；0 clean response-complete events |
| book freshness / archive bias | 389,672 canonical books、388,504 exact event-time clocks；legacy/paper 只作诊断 |
| capture policy / rollout-valid window / subscription set | release `761a16d8…bd40` 于 `2026-08-29T16:33Z` loaded；clock-valid rollout window 已开始，但 formal run-first window 从下一 unseen provider run 起算 |
| WS baseline snapshot + delta reconstruction / gap status | N/A |
| eligible checkpoints vs observed frames/messages | pre-book 14,332；post-book 15,514；60m scoreable 3,150；formal joined=0 |
| label availability | legacy 12,688；formal clean=0 |

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| raw universe | forecast delivery | 911,176 | 26 target dates |
| mechanism candidate | D-1 provider-run transition | 16,198 | 26 |
| primary checkpoint | D-1 18–24h transition | 3,968 | 25 |
| first clean event/city-day signal | response-complete transition | 0 | 0 |
| selected | frozen 60m expression | 0 | 0 |

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | legacy D-1 transition | 16,198 | 26 | 全部 `legacy_provider_run_earliest_observed` |
| PIT quote | transition with pre-book | 14,332 | 25 | clean intersection=0 |
| 60m repricing label | scoreable legacy transition | 3,150 | 23 | incomplete distribution 11,095；missing/late post 1,953 |
| settlement | complete legacy transition | 12,688 | 22 | clean intersection=0 |
| executable expression | direct ask→future bid | 0 | 0 | formal signal clock 缺失 |
| fill | actual fill | 0 | 0 | coverage-only run，未下单 |

## Wide-denominator sanity

Legacy-only 机制诊断不能当 alpha：60m 独立 market transition 为 1,914/23 dates，方向一致率
`54.23%`、mean/median directional rung shift `+0.949/+0.350pp`。primary D-1 18–24h 的
consensus-median revision 为 89/20、方向一致率 `61.80%`、mean/median `+2.074/+1.032pp`；raw
model revision 为 314/22、`57.01%`。这些点估没有 clean response clock、market proper-score baseline、
bootstrap CI 或 executable expression，只说明值得继续采集，不支持 shadow/live。

## Model / residual

| candidate | rows | logloss | Brier | calibration | delta vs market | date-block CI |
|---|---:|---:|---:|---:|---:|---|
| response-complete D-1 60m residual | 0 | N/A | N/A | N/A | N/A | N/A |
| legacy consensus 60m diagnostic | 89 | N/A | N/A | 未评估 | 未评估 | 未计算 |

### Microstructure ablation（使用 WS 时必填）

N/A；本轮没有启用或扩大 WS，不能从现有 REST markout 推断 queue/print alpha。

## Expression / execution

| expression | executable rows | dates | fee-adjusted ROI | excess | 95% CI | fill assumption |
|---|---:|---:|---:|---:|---|---|
| single YES / narrow strip，t0 ask→60m bid | 0 | 0 | N/A | N/A | N/A | 不作 fill 假设 |

## Frozen forward

- train choices frozen：仅冻结机制语义与 primary 60m horizon；threshold/model 未冻结，因为 clean
  development 尚未开始。
- forward dates/results：0 dates / 0 rows。
- multiple testing：5/10/30/90m 只作 diagnostic；formal headline 固定 60m。sigma grid 尚未产生可评分 row。
- unresolved blockers：等待首个 unseen provider run 写入 `collector_response_complete`；累计 30 个
  clean settled development dates；随后另起 untouched forward。

## 影响半径

- 受影响窗口：`2026-08-05T07:15:57.703070Z..2026-08-29T13:46:54.417898Z`。
- 受影响数据：911,176 raw rows、43,588 provider-run keys、16,198 D-1 transition candidates；其中
  primary 18–24h 为 3,968。所有 D-1 events 都只能归为 legacy，formal forward 为 0。
- 反事实订单：错误订单=0、错误 fills=0、PnL 污染=0；本轮与旧 collector 都是 coverage-only。
- 错过证据：16,198 个 D-1 transition candidates（primary 3,968）无法进入 formal research。旧响应
  完成时钟从未落盘，因此不能逐条精确重建，只能保留为 legacy development，不能伪造回填。
- 修复生效边界：`2026-08-29T16:33Z` 后的每次成功 HTTP response 已保存完整 clocks；已见 run
  仍保留旧 first-seen/status，下一 unseen run 才进入 formal clean denominator。

## Bloodline placement

- shared data logic：复用 `weather_model_evaluation/d1_revision_repricing.py` 与
  `scripts/analysis/forecast_quality/build_d1_d2_run_aware_dataset_v1.py --revision-repricing`；不新建平行 runner。
- feature layer：provider-run first-seen、model/consensus revision、full-ladder PIT market residual。
- `fact_signal_candidates` fields：未来 head 需保存 provider model/run、request/response/available clocks、
  revision、market snapshot identity、60m markout 与 blocker；本轮未写 canonical fact。
- shadow/collector runtime：最小 clock release 已加载；只改 forecast capture source collector，
  未启用 market-demand/WS、未改 shadow/live 或 order path。
- docs/index/registry update：本报告 + `forecast_repricing.md` + wallet living doc + registry/index。

## 可复现证据

- command：`.venv/bin/python scripts/analysis/forecast_quality/build_d1_d2_run_aware_dataset_v1.py
  --revision-repricing --run-id d1_early_repricing_start_20260829T1414Z --report
  /Volumes/jrs-archive/pm_agents/research/artifact_store/active/d1_d2_run_aware_dataset_v1/d1_early_repricing_start_20260829T1414Z/report.md`
- canonical machine result：`summary.json`，schema
  `d1_forecast_revision_market_repricing_research_v2`，SHA-256
  `26cc0acd8aa075426a690adc594e9e6b97a7a514ede3369435e04d79fd4c6b43`。
- human artifact：`report.md`，SHA-256
  `268975366e0c4517a704bde52547b35ca2322bf92587c803da8a730e55eafb5c`。
- tests：release checkout `38 passed`（clock contract/capture/cache + D-1 revision repricing）；
  production config suite `95 passed / 2 failed`，两项失败均是既有 core-carry test 硬编码旧 SHA，
  与 forecast_capture pin 无关。
- independent review：fresh read-only `luna_verifier`（固定 gpt-5.6-luna / medium）发现两项：
  缺 clocks 仍可能误标 response-complete、legacy upgrade 可能覆盖更强 status。已分别改为 fail-closed
  clock validation 与 `setdefault`，补回归后 38/38 通过；review rollout usage telemetry 未暴露。
- production：strict pre/post manifest、storage/DB identity 与 controller health 通过；新增 raw
  clock completeness `1,020/1,020`、ordering violations=`0`；orders/fills delta=`0/0`。

## Output routing（交付前填）

- family living doc（必须更新）：`docs/analysis/forecast_repricing.md`、
  `docs/WEATHER_EXTERNAL_WALLET_STRATEGY_INDEX.md`。
- registry/index update：`docs/WEATHER_STRATEGY_REGISTRY.md`、`docs/WEATHER_DOCS_INDEX.md`。
- canonical machine format（只能选一种 CSV/JSON/JSONL/Parquet）：JSON（`summary.json`）。
- JRS artifact manifest（完整 rows/model/image）：
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/d1_d2_run_aware_dataset_v1/d1_early_repricing_start_20260829T1414Z/`；
  大 CSV 只作 supporting replay rows。
- dated snapshot justification：独立、可复用的 production-lineage readiness 与影响半径证据。
- superseded files/runner removed：N/A；保留现有 runner，未删除任何 artifact。
- `check_weather_docs.py`：`FAIL`。本次链接目标均存在，但 workspace-wide checker 仍被既有
  `AGENTS.md`/`CLAUDE.md` 合同缺口、其他未跟踪日期报告、research script/entrypoint ceiling 阻塞；
  本报告在未提交前也按“not git tracked”列出。未为刷绿而扩大本任务范围或改写他人未跟踪内容。
