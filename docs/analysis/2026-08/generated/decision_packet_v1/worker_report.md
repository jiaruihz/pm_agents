# Worker Report:decision packet / as-of 数据契约打地基(2026-08-21)

Worker:core carry 策略数据治理 worker;模型 builtin:bigmodel-coding-plan/GLM-5.3;全程只读生产数据,只写本目录;未改任何 /Volumes/jrs 文件、未改 tracked 文件、无 git 写操作、未触碰进程。
token/usage:worker 无法自报精确 token 计数(harness 不向 worker 暴露 usage API);本线程约 30+ 工具轮、上下文峰值约 100k token 量级,定性声明为中等消耗,精确数字以 coordinator 侧 telemetry 为准。

## 1. 读取数据源与行数(telemetry)

| 源 | 范围 | 行数/说明 |
|---|---|---|
| weather.db(mode=ro) | fact_trades 全表计数 5,068;instance 过滤 126 行(84 signal) | 全读 instance 子集 |
| weather.db | orders 7,494 / fills 5,122 | 仅 signal 关联子集查询 |
| weather.db | fact_signal_candidates 146,241 | 策略计数 + Chengdu 样例 |
| weather.db | tmax_v2_forecast_captures 289,750;fact_forecast_hourly_curves 254,289;tmax_v2_forecast_capture_metadata 289,750 | 聚合计数 + hash 查询 |
| pre_live_scores.jsonl | 4,735 行(任务描述时点 4,708;runner 仍在追加) | 全量扫 2 遍 |
| live_orders.jsonl | 377 行 | 全量扫 2 遍 |
| entry_attempts.jsonl | 129 行 | 仅读首行结构 |
| observations/<UTC日> | 11 天(亏损日±1) | 97,408 行 |
| market_books/batches/2026-08-20 | 全部 gz | 535,128 行 |
| paper_snapshots | 6 个 snapshot 文件 | 每文件 ~1,023 records |
| forecast_hourly_curves raw | 2026-08-16..19 四目录 | 7,756 行 |
| 3 个 latest summary json + 2 份 08-20/08-21 分析文档 | — | 段落级 |

## 2. 新发现的数据问题(除已知三项外)

1. **84/86 根因**:consult prompt 粘贴表实际 84 行,与 canonical 84 成交 signal 零差集;"86"是标题计数错误(84 成交 + 备注里两个高 p 触发未成交 Miami 0.994/Warsaw 0.976 被计入总数但从未写成行)。外部审阅解析正确。
2. **canonical forecast capture 链路 08-08 起断**:tmax_v2_forecast_captures 最后 snapshot 2026-08-08T16:45:58Z。
3. **forecast_hourly_curves raw collector 08-19 23:04 BJ 后停写**(2026-08-20 目录缺失)。
4. **paper_snapshots 保留止于 08-19 23:08 BJ**:Amsterdam 08-20 触发行引用的 snapshot_20260820_2043.json 不存在(引用未随保留合同)。
5. **观测 ingested_at_utc 自 2026-08-16 才有**:前三周 receive 时间不可分(可用 observation_cache_generated_at_utc 代理)。
6. **fact_forecast_hourly_curves.available_at_utc 100% NULL**(254,289/254,289)。
7. **live_orders.data_epoch_ts_utc 仅 47.2% 非空**(178/377)。
8. **8 个触发未成交 signal 全部 risk=passed 但 taker 单 error 且 place={}**(未达交易所):4 个 08-13 post_order_dispatch_unknown、3 个 order_request_builder_rejected、1 个 fresh_book_replan_rejected;这些 signal 从未出现在任何 PnL 表,只有 journal 可见。
9. **fact_signal_candidates 的 forecast_values_hash 不是决策时点曲线**(first_seen grain,decision_ts_utc=NULL;Chengdu 例:candidate 5b59… vs 决策快照 804a…),不能当 as-of 证据。
10. **Warsaw 08-18 触发前最近 checkpoint 退化**(ask=0.001,p=None):"最近负检查点"需带 quote 可用性判据。
11. 盘口批次内 exchange_book 最长陈旧 ~80 分钟(4,788.9s);批次时间(文件名 BJ)≠记录 snapshot_ts_utc(UTC)≠交易所 exchange_book_ts_utc,packet 引用时必须带 lag。
12. pre_live_scores 行数随 runner 运行增长(4,708→4,735),任何行数声明须带 as-of 戳;`no_order_placed=true` 在全部 92 触发行上为真,字段名有误导性(实为 pre-order 评分语义)。

## 3. 未完成项与原因

- 每个亏损 signal 的完整盘口 ladder 快照(market_books 逐 signal join)未组装:packet 已带 book_fetched_at/snapshot 指针,全 ladder 回放属后续研究任务。
- 08-18/08-20 共 9 个 pending 行的 canonical 结算刷新:settlement 管道写入,超出本 worker 只读边界(Warsaw/Amsterdam 外部链上确认输已按 coordinator 结论标注)。
- Chengdu 等亏损的反事实重放未做(任务范围为数据契约,不含策略重放)。
- forecast issue/run time 历史不可回填(源不暴露),已按 `unavailable_in_history` 标注并在 write_contract_design.md 给出生产改造建议。

## 4. 产出文件

均在本目录:`signal_ledger.csv`(92 行)、`signal_ledger_diff_report.md`、`timestamp_coverage_matrix.md`、`loss_decision_packets.json`(7 包)、`packet_gaps.md`、`write_contract_design.md`、`worker_report.md`。
