# D-1 raw coverage 与 27 dates / 168 OOF 体检 v1

production:
live_action=none
orders_changed=0

## 结论

旧 D-1 tournament 只有 **27 个 target dates / 168 个 OOF states**，并不等于项目只拥有 27 天历史。它是以下五层连续收缩的结果：

```text
legacy paper baskets（2026-05-20..2026-07-23）
  └─ builder 固定 holdout=2026-06-17..2026-07-23
      └─ 1,774 forecast snapshots / 28 dates / 47 cities
          └─ fixed-model assignment：1,255（只覆盖 34/47 城）
              └─ settlement + ladder + market scoreable：694（invalid ladder=561）
                  └─ D-1_18_24_first：279 / 27 dates
                      └─ 前 10 dates 作 expanding train：168 OOF / 后 17 dates
```

因此：

- **27 dates** 主要是 builder 日期窗、raw 日期空洞、13 个未映射城市和 561 个无效 ladder 共同造成；
- **168 OOF** 是对 279 个主 checkpoint 再保留前 10 个日期作训练后的评测设计，不是另一次数据丢失；
- 已有 legacy basket 可以扩大 reconstructed development 样本，但不能被包装成严格 true-run PIT；
- 严格 provider-run + first-seen 的可审计历史实际上从 **2026-08-05** collector 上线后才开始，旧数据缺少的真实 run timestamp 不能靠同步或猜测修复。

## 本次已完成的修复与重跑

不是 canonical fact DB 没有重建。真正的问题是旧 research artifact 固定了窄日期窗，并把历史 CSV 的 `is_best_model` 错当成当前城市模型权威；此外 runner 把所有 ladder 异常合并成一个计数。

本次已实际完成：

1. 用已有 May–July basket 重做 conservative single-run forecast：
   - 旧：1,774 snapshots / 28 dates / 47 cities / 8,870 model rows；
   - 新：**3,550 snapshots / 50 dates / 48 cities / 17,750 model rows**；
   - 2026-06-11..16 找不到合同要求的共同五模型 run，明确记为 blocker，没有用更老 run 或 daily cache 伪补。
2. 训练和测试统一改用 `weather_data_feed.assigned_forecast_models.CITY_MODEL`：
   - authoritative history assignment 52 城；
   - test 48/48 城全部映射，`unassigned_cities=0`；
   - legacy `is_best_model` 路径保留为显式复现选项。
3. 重新读取 canonical `settlement_outcomes` 与历史 snapshot，物化同分母 ladder states：
   - all-policy：3,550 assigned → **1,844 scoreable states / 48 cities**；
   - primary `D-1_18_24_first`：**763 states / 49 dates / 48 cities**；
   - invalid ladder=1,706，全部是 `ladder_structure_error`；duplicate=0、parse=0、winner-not-in-ladder=0、missing-market-mid=0、missing-settlement=0。
4. 重跑 weather-only：primary date-equal logloss 为 market **1.4800**、pooled **2.3272**、hierarchical **2.4422**、city-only **2.7648**。扩分母没有改变 weather-only 明显落后 market 的结论。
5. 重跑 nested expanding OOF market residual：
   - prepared primary=763 states / 49 dates；前 10 dates 训练；
   - OOF=**571 states / 39 dates / 47 cities**；
   - locked robust-tail weather-only W0 logloss=1.77120，market=1.46075，Δ=+0.31045（95% CI +0.24018..+0.38998）；
   - market logloss=1.46075；点估最佳 V04=1.45933，Δ=-0.00143；
   - 95% CI=-0.00417..+0.00103，Bonferroni CI=-0.00497..+0.00172，均跨 0；
   - `baseline_gate_pass=false`，不能声明击败 market，也不进入 execution/shadow。

固定 artifacts：

- `/Volumes/jrs/pm_agents/research/artifact_store/active/d1_expanded_reconstructed_20260806/forecasts`
- `/Volumes/jrs/pm_agents/research/artifact_store/active/d1_expanded_reconstructed_20260806/hierarchy`
- `/Volumes/jrs/pm_agents/research/artifact_store/active/d1_expanded_reconstructed_20260806/tournament`
- [expanded market rerun](2026-08-06-d1-expanded-reconstructed-market-rerun-v1.md)

结论分两层：历史 full-ladder/raw 数据确实比旧报告使用得多，现已重接；但旧 snapshot 并不等于每个 checkpoint 都有连续完整 native lattice。provider `run_at + first_seen` 更不能从旧 first-seen hash 反推出，严格 clean forward 仍只能从 2026-08-05 起积累。

## 旧 tournament 分母复原

| 层 | 数量/范围 | 原因 |
|---|---:|---|
| `D-1_12_18` basket inventory | 2,071 snapshots / 56 dates | 覆盖约 2026-05-20..2026-07-23 |
| `D-1_18_24` basket inventory | 2,015 snapshots / 55 dates | 同上；不是全部项目历史 |
| backfill builder 日期窗 | 2026-06-17..2026-07-23 | `backfill_d1_single_runs_v1.py` 的默认 `HOLDOUT_START/HOLDOUT_END` 硬限制，May 20–Jun 16 被主动排除 |
| reconstructed forecast rows | 8,870 | 1,774 snapshot jobs × 5 models |
| unique forecast snapshot jobs | 1,774 / 28 dates / 47 cities | 日期缺 2026-07-03 与 2026-07-08..15 等 basket/raw 空洞 |
| assigned-model snapshots | 1,255 | legacy best-model assignment 只唯一覆盖 34/47 城，519 个 snapshot 未进入后续 |
| all-policy scoreable | 694 / 27 dates / 34 cities | settlement missing=0；market mid missing=0；invalid ladder=561 |
| primary `D-1_18_24_first` | 279 / 27 dates | tournament 的主口径 |
| expanding OOF | 168 / 17 dates | 前 10 个 target dates 只作训练，不计 OOF |

`forecast_rows` 的 28 个日期为 2026-06-17..07-23 内的非连续日期；primary 最终可评分日期为 Jun 17–28、Jun 30、Jul 1–2、Jul 4–7、Jul 16–23，共 27 天。Jun 29 虽进入 forecast universe，但 primary ladder 不可评分。

## 2026-05-05..2026-08-06 实际数据层

| 数据层 | 实际覆盖/能力 | 是否可用于严格 D-1 |
|---|---|---|
| legacy daily error cache | 训练 artifact 可追到 2024-05-01；旧报告中原始 42,705 rows / 52 城，best-model 后 16,916 rows | 只能用于长期 residual/bias 开发；没有严格 first-seen 与真实 provider run |
| legacy paper baskets | 当前已登记 inventory 约 2026-05-20..2026-07-23 | 可扩大 reconstructed holdout；lineage 仍是 `single_run_reconstructed_conservative_12h_lag` |
| `forecast_versions` | 2026-07-27..2026-08-05 审计窗：405,731 rows / 47 城 / 12 target dates | available/hash 有，真实 provider run timestamp 覆盖 **0%** |
| hourly curves | 同一审计窗：61,118 rows / 617 immutable files | assigned curve first-seen 有；真实 provider run timestamp **0%** |
| old full-ladder | 同一审计窗 324 snapshots / 15,024 D-1 states | 可作 D-1 market checkpoint；旧报告的 100% 是合同/归一化口径，不等于每档都有 strict two-sided executable mid |
| exact-run standalone collector | 2026-08-05 起；34 城、5 模型、D-1/D-2；精确请求 run，0 fallback | **是**。row 已有 run/first-seen/available/lead/run-age/hash/revision/capture IDs |
| current exact-run research intersection | 首次 repricing materialization：20,944 raw rows、4,828 batches、784 material batches、305 complete material batches | 当时 settlement-complete=0；不能评分最终 weather probability |
| strict current D-1 ladder | 同次 materialization：1,030 checkpoints，293 个 strict complete | 有一部分；strict complete 不能用旧 fallback completeness 代替 |
| D-2 market ladder | 0 | D-2 可做 weather accuracy；market residual / ROI 明确 blocked |

当前 standalone exact-run append-only 文件仍在增长；首行是 2026-08-04 12Z run 于 2026-08-05 first-seen，最新抽样已经覆盖 2026-08-05 18Z run 与 D-2 target。collector 最近一轮依次请求最近四个 run：较旧 run 有 complete/partial 数据，最新 2026-08-06 00Z 在 02:02Z 尚未发布而 blocked。`latest.json` 只覆盖写最后一个候选，因而会显示 `capture_status=blocked`，不能代表这一轮四个候选全部失败。

## 根因分类

| 类型 | 是否存在 | 影响 |
|---|---|---|
| 数据未同步 | 不是 27/168 的主因 | canonical JRS 与既有 basket/artifact 已能解释数字；标准 market-only sync 可能补近期镜像，但不能创造历史 provider-run timestamp |
| 未物化 | **是** | standalone `forecast_run_capture` 已写 rows/batches，但 `forecast_enrichment/forecast_run_rows_v2.jsonl`、`forecast_batches_v2.jsonl`、contract state 未形成统一生产输入；strict checkpoint 与 settlement join 仍靠 one-shot research |
| builder 筛选 | **是，主要原因之一** | Jun 17 起始窗排除了 May 20–Jun 16；34/47 城 mapping 丢 519 snapshots；前 10 dates 又从 OOF 排除 |
| 原始字段缺失 | **是，且不可逆** | Aug 5 之前的 versions/curves 没有真实 provider-run timestamp；禁止用 estimated 00/06/12/18Z 回填 |
| ladder/parser/market shape | **是** | assigned 1,255 中 561 个 invalid ladder；需要逐 reason 拆分 raw rung 缺失、native lattice 语义和 parser 失败，不能继续静默丢行 |
| raw 日期空洞 | **是** | Jul 3、Jul 8–15 等没有进入旧 reconstructed universe；需区分 basket 根本不存在与未同步 |

## 现有 collector / builder 是否足够

| 组件 | 判断 | 缺口 |
|---|---|---|
| `forecast_run_capture.py` | 对未来 D-1/D-2 true-run lineage **基本足够** | `latest.json` 应汇总本轮四个 candidate attempts / recent-complete，而不是只显示最后一个 newest-run blocker |
| full-ladder collector | 对 D-1 研究 **部分足够** | 需要 exact-run city-target 对应的 checkpoint coverage/blocker；strict two-sided complete 只有部分；D-2 ladder 仍无 |
| `build_d1_d2_run_aware_dataset_v1.py` | 作为手动 join **足够起步** | 需要显式传 rows、batches、settlements、ladder JSON(L)；没有增量 materializer/scheduler；现有 repricing 产出 ladder CSV，与 builder 的 JSON/JSONL reader 直接不兼容 |
| `research_d1_d2_weather_only_v2.py` | **不足以训练模型** | 当前只统计 readiness，A–G 的 logloss/Brier/RPS/calibration 全写 `None`；达到 30 dates 后也只会变成 `ready_for_inner_train`，不会自动拟合 |

## 可执行修复与重跑顺序

1. **先扩大 legacy reconstructed development，但保持证据标签不变。** 将 backfill 窗口改为现有 basket 的 2026-05-20..2026-07-23，复跑同一 runner；报告新增 dates/states，同时仍标 `single_run_reconstructed_conservative_12h_lag`，不得进入 clean forward。
2. **补齐 47 城 assignment 审计。** 对 13 个未唯一映射城市分别输出 `missing / duplicate / unsupported`；能从已冻结 city-model policy 唯一解析的再加入，不能解析的保留 blocker，禁止静默丢 519 rows。
3. **拆解 561 个 invalid ladder。** 固定输出 per-reason、city/date/checkpoint 清单：raw rung 缺失、bottom/top 语义、重复/断档 native value、token quote 缺失、parser error。只有 parser/物化错误可重放恢复；真实 raw 缺档只记 coverage gap。
4. **建立一个增量 clean materializer。** 输入 standalone `forecast_run_rows.jsonl` / `forecast_batches.jsonl`、canonical settlement、full-ladder checkpoint；输出稳定 JSONL dataset + signal/evidence funnels。将当前 ladder CSV 改为同时产 JSONL，或给 builder 增加显式 CSV adapter。
5. **每个 exact-run complete batch 物化 ladder/settlement blocker。** D-1 缺 event、缺完整 native ladder、缺 strict book、未结算分别记 evidence blocker；D-2 无 market 不影响 weather-only label，但继续禁止 market residual。
6. **实现真正的 A–G trainer。** 复用当前 preregistered model table 与 target-date block split，但 readiness 之后必须实际拟合并写 proper scores、calibration、city disaster tails、bootstrap；当前脚本不能承担这一步。
7. **修 collector health summary。** 保留 fail-closed exact-run 行为，只把健康产物改成 `attempts[] + latest_successful_run + newest_candidate_status`，避免“新 run 尚未发布”覆盖掉同轮已成功采到的较旧 run。

建议的两条研究轨互不混用：

- **立即可重跑：** legacy expanded reconstructed tournament，用于模型/特征筛选与学习曲线；
- **只能随时间积累：** 2026-08-05 起的 clean true-run forward，用于正式 weather-only gate、之后才是 market residual。

## 审计边界

本次执行过标准 `market-only sync`，但同一 JRS 卷上的压缩 rsync 出现严重 I/O 争用；在至少新增 158 个 snapshot、推进到 2026-08-05 后主动停止无界同步。没有跑 canonical DB 全量 rebuild，因为 27/168 的根因不在 canonical fact 缺失。随后通过 canonical JRS tmux context 完成 May–July forecast 回填、settlement/lattice 重物化和两组模型重跑。未部署、修改 collector、策略、订单、city pool、sizing 或 execution policy。因此“May 5–19 没有 legacy basket inventory”只表示当前登记 artifact 中没有，不代表所有历史介质绝对不存在副本。
