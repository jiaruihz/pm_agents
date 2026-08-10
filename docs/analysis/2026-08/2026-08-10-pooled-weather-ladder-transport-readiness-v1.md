# Pooled Weather Ladder Transport Readiness v1

Status: **`historical BLOCKED_DATA / FORWARD_COLLECTION_RUNNING / no-fit / no-live-change`**

## 结论

用户已将主时钟从10/30/60s改为collector-native **15/30/60s**。冻结readiness时，Amsterdam、Helsinki、Tokyo 的 source event 历史存在，但没有一行同时满足：各城 adapter 的完整 native-lattice `ΔP_weather`、event-first-seen 的 full ladder，以及精确 pre/t0/+15/+30/+60s reconstructed book。固定 pooled M0–M3、expanding-date OOF、frozen forward、leave-one-city-out 和 settlement secondary head 因而没有被训练；本报告不补写虚构的历史回测，也不创建 selector 或策略。文末记录随后完成的共享forward collector部署。

## 固定合同

- pooled universe：Amsterdam / Helsinki / Tokyo；Busan / Seoul 只作 source-basis negative control。
- primary labels：15/30/60s `rung move - ladder common move`；terminal settlement 是独立 secondary head。
- `ΔP_weather = P_after - P_before`，before/after 分别投影到 full native settlement simplex，故整条 ladder 的 delta 和为 0。
- 同 rows 比较：M0 market level、M1 weather increment、M2 ladder dynamics、M3 weather×ladder；不复制 max/min selector，不选盈利城市。
- development 截止 2026-08-10；2026-08-11～17 保持 frozen forward。

## 实际覆盖

| city | material first-seen events | dates | exact WS feature rows/events | full-ladder complete events |
|---|---:|---:|---:|---:|
| Amsterdam | 1,027 | 10 | 0 / 0 | 0 |
| Helsinki | 695 | 9 | 80 / 16 | 0 |
| Tokyo | 714 | 10 | 0 / 0 | 0 |
| Busan control | 4,407 | 10 | 不进入拟合 | 不要求 |
| Seoul control | 7,048 | 10 | 不进入拟合 | 不要求 |

Amsterdam 的 KNMI collector 在冻结 manifest 时另有 2,881 events / 17,283 full-ladder REST captures / 10 dates，且15/30/60s三个时点全部存在，时间合同现已通过。剩余缺口是：Helsinki/Tokyo 没有对应的 event-aligned full ladder；WS subscription epoch 只覆盖 2–4 个 rungs，现有 materializer 仍输出 +10s 而不是 +15s，且只物化 Helsinki 的 3-rung hot strip。三城现有 runtime adapter 均未输出归一化的 full-ladder `P_before/P_after`。

因此联合可训练 rows、OOF rows、frozen rows、LOCO rows 和 settlement secondary rows 都是 **0**；没有可以诚实计算的 MSE/Brier/logloss/AUC/PnL。

## 终态与唯一动作

历史评估终态是 `BLOCKED_DATA`，因此停止建模和策略创建，live 未改、orders=0。恢复动作已经按原约束执行：在WCIR/shared data layer补齐三城full native-lattice weather delta与full-ladder exact 15/30/60s event materialization；没有拿binary adapter或hot strip替代。当前为`FORWARD_COLLECTION_RUNNING`，待形成三城同分母日期后只运行已冻结的M0–M3 evaluator一次。

复跑：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_full_ladder_first_seen_residual_v1.py \
  --mode pooled-ladder-transport-readiness
```

Artifact：`market_structure_edge/market_structure_edge_ladder_transport_v1/readiness_15_30_60_2026-08-10/{readiness.json,run_manifest.json}`。此前10s readiness artifact保留为被本次时钟修订取代的审计证据。

## 缺口补采与生产验证（2026-08-10）

上述历史readiness保持不改：当时联合rows确为0，也没有回测。缺口现已转为forward collector：

- 共享合同：`weather_data_feed/source_event_ladder.py`，schema `weather_source_event_ladder_transport_v1`。Amsterdam/KNMI、Helsinki/FMI、Tokyo/JMA统一为collector first-seen event；天气分布使用各城assigned forecast的canonical empirical error，再以source running maximum更新settlement-native lower bound。before/after分别归一化，整梯`ΣΔP_weather=0`。
- 盘口物化：`scripts/ops/weather_source_event_full_ladder_v1.py`，由现有`weather_market_books` session的`event-ladder` window承载；固定0/15/30/60s，记录`feature_book_snapshot_id`和各future evaluation snapshot id。它没有signal/intent/execution import，health固定`zero_notional=true, orders_enabled=false, order_count=fill_count=0`。
- 部署：production checkout `/Users/deepsleep/projects/pm_agents_market_books_ws_v2_prod`，SHA `ffdaef4c`。Amsterdam/KNMI、Helsinki/FMI、Tokyo/JMA各完成一个真实event；三者均11档、probability sum `1.0→1.0`、delta sum `0.0`，error samples分别354/354/736。每城0/15/30/60s四份snapshot均11 markets、22/22 books complete；t0 actual start offset分别3.858/1.465/0.763s，+60s分别60.229/60.326/60.235s。
- runtime正本：`/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_full_ladder_v1/`。本次只启动zero-notional data shadow，没有selector、SignalCandidate、TradeIntent或真实订单；旧历史仍不足训练，需等三城forward形成同分母日期后才运行冻结M0–M3。
