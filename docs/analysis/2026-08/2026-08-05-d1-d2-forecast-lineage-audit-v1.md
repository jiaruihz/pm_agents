# D-1 / D-2 forecast lineage 与完整 ladder 审计 v1

Artifact routing: this is the durable snapshot. New audit runs require a
stable `--run-id` and write immutable machine output beneath the configured
JRS research artifact root; the runner does not overwrite this report unless
an explicit `--report` path is supplied.

## 数据快照

- raw runtime：`/Volumes/jrs/weather_data_feed_service_runtime`；窗口 `2026-07-27..2026-08-05`。
- forecast_versions：405,731 rows / 47 cities / 12 target dates。
- forecast_hourly_curves：61,118 rows / 617 immutable files。
- full-ladder：324 snapshots；只读审计，没有 sync/rebuild/production change。
- production manifest healthy；production health 的 Helsinki pre-cross session critical 与本次 D-1/D-2 raw coverage 无关，但本报告不发布 canonical alpha 结论。

## 结论

当前 collector 已经解决了一半问题：append-only 多模型 version hash、fetch/available clock、assigned-model curve first-seen 和当前 full-ladder 都存在。但它**还不能支撑干净的 D-1/D-2 run-aware 回测**：provider run timestamp 为 0 覆盖，previous run/revision 与 multi-model summary 只是可派生、没有作为稳定字段物化；D-2 forecast 有数据，但同期 D-2 market ladder 为零。

所以不冻结上一版 pooled/hierarchical 模型。下一步先增加明确 model-run collector 与稳定 checkpoint contract，再训练 partial hierarchy。

## 字段覆盖

| required field | current evidence | coverage/status |
|---|---|---:|
| forecast issue/run time | `forecast_versions.forecast_run_at_utc` | 0.0% |
| first-seen / available | curves collector-exact + versions available clock | 100.0% / 100.0% |
| lead hours | target/snapshot 可计算；paper snapshot 仅 estimated | 未稳定物化 |
| model run age | 依赖真实 run timestamp | 不可计算 |
| previous run forecast | version sequence 可派生 | explicit 0 rows |
| run-to-run revision | 37,314 content revisions 可派生，但不是 provider-run revision | explicit 0 rows |
| multi-model mean/median/spread | 37,926/37,926 captured-at groups 可派生 | persisted 0 groups |
| D-1 native-lattice ladder | full-ladder states=15,024 | 100.0% |
| D-1 simultaneous market distribution | complete ladder + market probability | 100.0% |
| D-2 forecast versions | horizon=2 rows | 117,652 |
| D-2 native-lattice / market | full-ladder states=0 | NA |

## 正确修复

1. 新增 run-aware single-run capture：明确请求 `model_key + run_ts`，保存 source fetch、detected、first-seen、available 四时钟和 raw hash；live endpoint 的 `12Z estimated` 不再冒充 issue/run。
2. 在同一 append-only row 物化 `previous_run_ts`, `previous_run_forecast_max_f`, `run_to_run_revision_f`，同时保留 content-version revision，二者不能混名。
3. 新增稳定的 `batch_capture_id`；每个 `batch_capture_id × city × target_date` 物化全模型 values、mean、median、q25/q75、min/max spread、assigned-minus-consensus，并保存模型缺失列表。现有逐模型 `capture_id` 继续保留为 row identity。
4. full-ladder checkpoint 保存 event-level rung manifest/hash、native-lattice completeness、normalized mid distribution、book snapshot id；缺 rung 进入 blocker，不静默丢 state。
5. D-2 只在交易所确有完整同期 ladder 后进入 probability-vs-market 评测；现阶段 D-2 forecast 只能训练 weather accuracy，不能声称 market residual。

## Partial hierarchy v2（数据修好后）

结构固定为：`source/lead pooled residual shape + shrunk city×source mean bias + shrunk scale correction`。city bias 只修 center，不让单城重新学习整条 tail shape；scale 需要更高样本量并向 source/lead pooled variance 强收缩。多模型 consensus/spread、run age、revision 作为连续特征，market 只进入独立 residual head。

候选达到以下条件前不冻结：同 rows 同 checkpoint 至少不劣于 pooled proper score；相对 market 的 Brier/logloss gap 大幅收敛且没有城市灾难尾；在未参与开发的 target-date forward 保持同号。交易 ROI 只作第二层诊断。

## 双漏斗与状态

- signal funnel：forecast version rows 405,731 → horizon-1 137,462 / horizon-2 117,652。
- evidence funnel：full-ladder D-1 states 15,024 → native-complete 15,024 → market-complete 15,024；D-2 market states 0。
- significance=NA；baseline=FAIL；forward=NA；conclusion=`inconclusive_data_contract_repair_required`；actual fills=0；不改 live。
