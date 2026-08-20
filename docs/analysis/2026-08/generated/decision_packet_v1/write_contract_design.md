# pre_live_scores 写入合同扩展设计稿(v1,纯记录,不改下单逻辑)

## 目标
让每个未来 signal 的 decision packet 可从 journal 一行自举重建,不再回扫全量 obs/book/forecast 文件。

## A. 触发行新增字段(positive_taker_ev 行,增量、可空)

1. last_negative_checkpoint 快照(冻结引用,非重算):
   `lneg_ts_utc, lneg_model_p, lneg_ask, lneg_bid, lneg_blockers_json, lneg_quote_usable(bool)`
   取值=同 (city,target_date) 内 ts<触发行的最近一条评分;`lneg_quote_usable` 标退化盘口(ask 空或 <0.01),避免 Warsaw 式退化行误导。
2. 三套时间戳(全部为“决策时已加载值”的直接落盘,不新增网络调用):
   - 观测:`obs_last_obs_utc`(已有 source_report_ts_utc,改名别名)、`obs_ingested_at_utc`、`obs_row_id`(=observation_history_id);
   - 盘口:`book_exchange_ts_utc`、`book_available_at_utc`、`book_capture_id`;
   - 评分自身:已有 created_at_utc/as_of_ts_utc,补 `data_epoch_refs_json`(feature_row_id + snapshot_file + book/obs id 的指针集合)。
3. forecast 元数据指针(核心是 hash,一切 join 的键):
   `forecast_values_hash, forecast_first_seen_utc, forecast_receive_source, model_init_utc_estimated, model_init_basis, forecast_valid_from_local, forecast_valid_to_local, forecast_lineage_status`
   source 为 open-meteo 时 `forecast_issue_ts_utc` 允许为 NULL 并显式带 `issue_unavailable_reason=source_response_does_not_expose_run_timestamp`。
4. 订单衔接(live_orders 侧同步):`data_epoch_ts_utc` 补齐为必填(当前仅 47.2% 非空);写入 trigger 行的 signal_ref(city|date|bracket)便于双向对账。

## B. 新增独立 journal(可选,若不愿加宽 pre_live_scores)
`decision_packets.jsonl`:仅触发行各写一条,字段=上述 A 全集+触发特征摘要+订单引用(execution_id 列表)。append-only、不可变,与 pre_live_scores 同目录。

## C. 为什么不改变任何下单逻辑
1. 全部字段来自决策时已在内存中的对象(observation cache 行、book snapshot、forecast frame 已含 ts/id/hash),只是序列化落盘,无新 I/O、无新计算路径。
2. 字段全部增量可空,旧 reader 不读即不受影响;无任何字段参与 EV/eligibility/sizing/risk 判断。
3. 写失败策略:packet 字段写失败不阻塞评分与下单(记 run_alert),因为它是记录扩展而非 gating——与“缺数据应显式失败”不冲突:交易输入(obs/book/forecast 本体)的缺失行为不变,packet 只是把这些输入的“指针+时刻”多抄一份。
4. lneg 快照在锁定首触发时一次性物化,后续 checkpoint 不重算,保证 packet 不可变。

## D. 验收
1. 对任一新 signal,仅凭 decision_packets.jsonl(+canonical orders/fills)即可生成与本次手工拼装等价的 packet,零文件回扫。
2. 三套时间戳字段在 30 天窗口 100% 非空(除 issue_ts 显式带不可得原因)。
3. 回放校验:用新字段重建的 weather_asof/book_asof 与回扫文件结果逐 signal 一致(抽样 ≥20)。
