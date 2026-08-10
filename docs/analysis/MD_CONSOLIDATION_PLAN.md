# Weather 文档清理账本

Status: current-reference
Updated: 2026-08-10
Source of truth: no
Used by: `WEATHER_DOCS_INDEX.md`; `docs/analysis/` living docs

这不是策略结论或又一份文档索引，只记录文档负债的清理口径、已完成批次和下一批队列。当前研究结论以
`WEATHER_STRATEGY_REGISTRY.md`、对应家族 living doc 和最新 frozen/reset report 为准；生产状态以 controller、manifest
和 raw/exchange evidence 为准。

## 清理合同

整理一个历史报告前必须先读内容，并完成以下四步：

1. 锁定 owner living doc，提取仍成立的机制结论、证据窗口和失效边界。
2. 把耐久结论写入 owner；日期报告只保留当时 snapshot，不再声明“当前结论”。
3. 检查文档链接、代码默认路径、测试和 artifact consumer；存在消费者就保留。
4. 只有确认是重复、已被吸收或数字已失效，且用户已授权删除时，才删除/归档；`inconclusive` 和 dormant 不等于 dead。

禁止用“移动文件”“改索引状态”冒充内容整合，也禁止把旧 PnL、旧城市排名或旧 live 标签复制进 living doc。

## 权威归属

| 内容 | Owner |
|---|---|
| 概率、校准、model-vs-market | `model_vs_market.md` |
| 盘口结构、range/RV、repricing | `market_structure_edge.md` |
| fill、fee、成交覆盖、执行窗口 | `execution_quality.md` |
| 入场时钟与 city×timing | `entry_timing.md` |
| side 表达 | `side_alpha.md` |
| 城市池与 city-day portfolio | `city_selection.md` |
| observed path、reheat、current/d1 | `reheat_risk.md` |
| sizing 与价格带 | `sizing_entry_band.md` |
| blender/shadow | `blender_shadow.md` |
| live 历史表现 | `live_performance.md` |
| 钱包和账户对账 | `account_reconcile.md` |
| side flip、候选/成交血缘、数据缺口 | `data_integrity.md` |

WCIR、数据源、生产与 canonical contract 不写进上述研究 living doc，分别回到 WCIR 设计、data-feed、repo boundary、
system contract 和 ops runbook。

## 已完成的历史批次

- 2026-06-11：2026-05 报告迁入历史区；早期 UNKNOWN/maker-queue、旧 city pool、旧 PnL 数字不再作当前证据。
- 2026-06-11：部分 2026-06-03..09 重复或 invalidated 报告迁入历史区；near-binary、fill recovery、
  `missing_bracket` 污染窗口保留为失败史。
- 2026-06-16：observed-max 新研究归入 `reheat_risk`；`observed_max/` 只保留历史 producer/station-basis 邻接工作。
- 2026-08-05..06：大型 generated 产物迁到 JRS 内容寻址库；可精确重放的派生对象用 tombstone 清理，raw/canonical 不动。
- 2026-08-06：删除本文件原有 400 余行逐文件流水账；已执行的迁移由 git 历史、索引和 living doc 承担，
  不再维护第二份平行清单。
- 2026-08-06：完成“切片冒充全量”专项清理。D-1 的 5,561 rows 已还原为
  `42,705 artifact rows → 16,916 best-model rows → 5,561 May–Aug training slice`，修正 registry、5 份活跃报告、
  3 个 runner 与 summary metadata；Korea residual v1–v4 的“完整历史”改为明确的 runner 输入窗口。
  钱包公开 activity、three-city strategy-era 与 Tokyo policy replay 等保留“全量”时，均已有紧邻的 universe、起止或
  endpoint 限定，不等于项目全库。
- 2026-08-06：dated `current-reference` 从 72 份收敛到 8 份；保留项均被 `scripts/ops` 作为运行实例的冻结
  source/preregistration/provenance 合同引用，其余65份统一降为 `snapshot`。新增 docs check allowlist，防止日期报告重新
  膨胀为平行当前真相。
- 2026-08-06：city-day basket 删除11个已吸收文件：06-07 同 runner 的较早 baseline/distribution、除时间戳外完全
  重复的 walk-forward、已被 living doc 吸收的 optimizer/protocol，以及两份过渡 handoff；当前只保留06-08较新证据并
  由 `city_selection.md` 统一结论。blender 的过渡 handoff 已吸收进 `blender_shadow.md`；三份不同 denominator 的 producer
  报告继续保留为 snapshot evidence，不误删成“重复”。
- 2026-08-06：删除4份已吸收的早期交接/计划：06-08 综合审计交接、06-09 research-window handoff、06-10 observed-max
  原始交接与重复执行计划。三门 verdict 已在 `live_performance.md` / `data_integrity.md`，observed-max 的耐久机制与证据边界
  已在 `reheat_risk.md`；删除项仍可从 git 历史恢复，不再让旧 WSL/N100 提示词和“物理盖棺”措辞充当当前入口。
- 2026-08-06：删除 Fabel review prompt 与已退役的 Range-RV N100 shadow handoff。前者只是模型间提示词副本，后者的
  runtime 路径和 sync/start 命令已失效；METAR/forecast-tail 结果报告及 Range-RV 实验/forward 证据继续保留。
- 2026-08-07：合并 recovered runner reports。KNMI 的 train/holdout/July/batch 五份输出已由唯一 threshold
  report 吸收；Korea residual baseline/expanded v1-v4 已压成 data-backfill 总表的迭代账；Busan v7-v11/exit 已压成
  `busan_intraday_exact_no` 稳定模型架构的 experiment 账。7/11 single-snapshot 和 7/12 Tmax v3 初跑由后续完整重跑
  吸收；Tokyo/Helsinki source-event、stop/re-entry、stale-book 恢复快照由 strategy registry 的
  `metar_cross_prev_no` 事故/证据账与 canonical case reports 吸收。只删重复派生 Markdown，不删 raw、canonical、
  machine artifact 或可复跑 runner。
- 2026-08-07：current-YES 第一批压缩。删除已由 `reheat_risk.md` 吸收的 v16/v17/v19/v20/v21 旧 N100
  telemetry rollout/deploy 文档以及已被 living doc 取代的 3,390-word model-map。保留 v14 scorecard、v15 gate、
  v18 feature evidence 与 v22 supersession 作为不同证据层；旧 N100 PID、启动命令和 live 标签不再留作可误执行入口。
- 2026-08-07：删除两份无消费者的 Current-YES Codex preflight/prompt 对比报告；24 个 settled rows、三版 prompt
  事后选择、无 frozen forward、从未启用 live gate 的证据边界已并入 `reheat_risk.md`。保留 replay machine artifact，
  不再把 prompt 迭代当成两项策略研究。
- 2026-08-07：Core Carry 7/30 的 live-funnel 与 filtered-delta 已并入 residual taker A/B；三者共享同一
  OOF parent 与执行成本问题。保留唯一 A/B 结论和 generated 行级切片，删除两个无消费者的平行 Markdown。
- 2026-08-07：删除无消费者的 Helsinki 单城 reversal guard 报告，DST/observation-clock 根因已并入
  `reheat_risk.md`；删除 6/21 重复 feature-factory 快照，保留由公共 runner 消费且后来重建的
  `2026-06-16-reheat-feature-factory-v1.md` 作为唯一 factory evidence。
- 2026-08-07：修正 runtime registry 仍把 6/22 非 PIT climbing-NO 旧假设列为 high/proposed 的误导；来源改为
  7/14 expression-specific reversal replay，状态改为 low/blocked，并删除已在正文承认被取代的旧报告。
- 2026-08-07：合并 current-YES peak/future-break hazard V2/V3/V3.1。三份日期报告的固定分母、ROI/CI 和失败边界已
  写入 `reheat_risk.md`；producer 后续统一把 report/summary 写进对应 generated artifact 目录。V3/V3.1 runtime queue
  从 proposed 改为 superseded，不再让早期三日 tail 点估冒充当前 shadow 候选。
- 2026-08-07：完成策略 catalog 与 production authority 交叉审计。25 个 production managed runtime 中只有 2 个
  `expected_live`；历史 catalog 原有 9 个不在 production.yaml 却仍标 current live 的实例，已全部改为 stale/
  `expected_live=false`，并新增一致性测试，强制 catalog live 集合严格等于 production manifest live 集合。当前 controller
  health 为 JRS/context/data-feed healthy，唯一 manifest warning 是一个运行进程的 loaded SHA 与 checkout HEAD 漂移；
  未启停或重启任何生产进程。canonical DB 仍保留提交前的 catalog snapshot；必须在本批提交进入 production checkout 后
  再由 canonical refresh 同步，不能直接拿 dirty control checkout 改生产 DB。
- 2026-08-07：脚本入口第一批收口。`weather_strategy_launcher.py` 降为只读 catalog 兼容入口，历史 `start/stop` 和
  `reconcile --apply` 全部 fail closed；non-operational catalog 初次同步默认为 shelved/blocked，旧 enabled 行在生命周期
  变为 stale/superseded/deprecated 时自动收口。删除 3 个无消费者的 register 脚本、26 个已退出生产合同的直接
  start/stop 入口，并把 peak/future-break 三个版本文件名改为稳定机制 runner 名。历史设计文档已明确 DB-supervisor
  路线被 controller/manifest 取代，不再提供可执行旧命令。
- 2026-08-07：当前视图与研究 runner 第二批收口。Dashboard probe 不再因 catalog lifecycle 写着 shadow/live 就把历史
  实例列为当前运行，只显示 `expected_live` 或有真实 running runtime-state 的行；definition 的 live count 改按
  `expected_live`。low-price source-quality 与 high-price-NO 两组 runner 从 `_v1/_v2` 文件复制命名改为
  base/forward、residual-surface/physical-confirmation 稳定机制名。Helsinki remaining-heat v1-v7 经依赖审计确认是冻结
  artifact 串联而非纯复制，现阶段保留并标为后续模块化对象，避免破坏 replay/test 血缘。
- 2026-08-07：controller 外围入口第三批收口。删除 15 个已退出 production contract 的 shadow/monitor/patrol/TMAX
  常驻 wrapper，再删除整套 dormant `mac_weather_stack`、它唯一消费的 regime live start/stop、旧外置盘迁移和 N100
  proxy-failover，共 20 个历史 ops 文件。data-source materializer 的 stale-book 条目已改到当前 production wrapper/session；
  Dashboard/current docs 和回归测试同步更新。历史研究 runner 本体保留作复现，删除的是会被误执行成当前生产的入口。
- 2026-08-07：生产后验顺带修正 forecast health 假 critical。collector 合同明确允许 snapshot 复用仍新鲜的
  `cached_durable_curve`，旧 health 却强制 capture 与每轮 snapshot timestamp 完全相同，导致正常缓存复用被报
  `snapshot_mismatch`。现改为校验 archive path、city/target_date 与 forecast hash 的 durable lineage；真实错 path/hash
  仍 fail closed。修复时 91 个 city-target 全部核验通过，证明原 critical 为假报；随后 Open-Meteo 返回 HTTP 429，
  curve 超过 45 分钟后 controller 正确重新报真实 `forecast_hourly_curves:stale`。25 个 managed session 均在运行，
  但不能据此宣称数据语义健康；429 限流窗口需作为独立生产稳定性问题处理。
- 2026-08-07：无 owner shell 第四批收口。按 production/catalog/全仓消费者三重扫描删除 18 个 orphan：上一批入口
  遗留的 inner loop/stop/status、已 superseded 的 city-probability v2、两个未登记 JRS research wrapper，以及旧
  fast-observation/station-basis loop。对应 Python runner、研究证据和 runtime 数据不动；备份、Cloudflare、Telegram
  等非 weather production 工具明确排除，不借天气清理扩大范围。
- 2026-08-10：删除 Git 中 12 个 2026-02 遗留 runtime 日志、PID 和 shadow 输出；`.gitignore` 现在同时覆盖根目录
  `runtime` 目录与 NVMe compatibility symlink，并由 repo hygiene 检查禁止任何 `runtime/` 文件重新进入版本库。production manifest 同时开始把登记或运行中的
  脏 checkout 明确报为 warning，避免 `dirty_tracked=true` 被藏在 `findings=[]` 的健康结论里。

## 仍有效的历史边界

- 2026-06-07 前的 live PnL、胜率、城市/策略排名，若依赖旧 near-binary、缺 fill 或 `missing_bracket`，不得用于当前决策。
- paper/shadow、proxy ask、少量 selected trades 和运行中的 probe 不能证明 live alpha。
- 后来的 fee、fill、settlement 或 denominator 修正只作勘误；不得把旧 gross 结论继续留在 living doc 主结论中。
- 日期报告可以保留 lineage、失败模式和预注册规则，但当前动作只能在 owner living doc/registry 出现一次。
- 未限定的“全量历史 / 全部数据 / full history / long history”不再作为样本名；必须记录 `denominator_scope`、
  输入来源、覆盖起止和逐层过滤 funnel。artifact 全部 rows 也不自动等于项目全部历史。

## 当前清理队列

1. **dated current-reference 收敛**：日期报告原则上降为 snapshot；只有仍被运行合同直接消费的 protocol/entrypoint 可保留 current-reference。
2. **重复家族合并**：继续 current-YES/reheat；source-event recovered-report 批次已完成。
3. **历史脚本去重**：先抽公共 helper/config runner，再删除仅城市、日期、参数不同的入口；保留算法或数据合同真正不同的 producer。
4. **机器产物修复**：没有 frozen input inventory、producer revision 或 exact replay 的对象继续保留，不因文件大而删。
5. **非天气分离**：copy-trade、wallet 与硬件研究进入各自 owner，不继续混入天气结论索引。

每批交付必须报告：吸收了哪些结论、删除/保留了哪些文件、为什么安全、链接/测试结果，以及是否影响生产。
