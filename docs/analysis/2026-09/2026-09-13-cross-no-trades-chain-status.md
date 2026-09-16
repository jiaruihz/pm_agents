# Cross NO 历史成交与链路检查（2026-09-13）

本次范围为当前 Cross NO：`strategy_id=live_weather_edge_v1_c16645cc1165`、`strategy_key=latency_arb.fast_source_prev_no`、`trade_class=live_real`。排除已归档 METAR.ws 快源试验 `cross_no_v2_metar_v1`。仅刷新数据与只读诊断，没有部署、改资金参数或重启实盘策略。

## 成交范围与产物

当前 canonical 可见历史：2026-07-09 21:46:08 至 2026-09-11 13:27:28（北京时间），242 个 fill、228 个订单、173 个 city/target_date/bracket 表达。累计买入成本 $1,674.706902；成本不是亏损。历史范围仅指该策略 canonical 与现存 raw/cache 可核对集合，不声称无限期交易所完整私有历史。

逐笔表：[cross_no_all_fills.csv](/Users/deepsleep/projects/pm_agents/runtime/analysis/cross_no_status_20260913/cross_no_all_fills.csv)，包含 fill/order/plan/signal identity、城市/档位、成交价格/份额/费用、北京时间/当地时间和 canonical 结算字段。

证据目录：`runtime/analysis/cross_no_status_20260913/`。初始 coverage/reconcile 是刷新前快照；最终结论使用 final 文件，禁止混用分母。

## 链路结论

- 当前 live runner `fast_source_prev_no_trial_v1`，PID 43557，loaded release `559d285ca9f111915ea4a80d2535bc40bb6c5722`；manifest/进程/最新 raw 心跳一致，live_enabled=true，status=ok。本次检查时刻为北京时间 9/13 凌晨，不外推全天 uptime。
- controller JRS context read/write probe healthy，DB route healthy，storage audit critical=0。canonical DB `/Volumes/jrs/pm_agents/runtime/weather.db`，device=16777244，inode=54444。
- 当前数据源和盘口心跳在更新。Amsterdam/Helsinki 最新机会因为 source 未超过 METAR running max 正常阻挡。日韩凌晨 source_missing 与 source collector 的 06:00–22:00 当地采集窗口相符，不能据此判故障。
- 全局 health 是 WARNING：未登记历史 worktree、inactive observation cache、Busan snapshot city coverage。釜山覆盖警告尚未闭合；本次没有验证全天无漏采。9/12 当前机会 shard 仅从 UTC 14:22 起可见，不把这个局部窗口当全天漏单分母。
- Helsinki 9/11 21:42 北京时间（当地 16:42），17 NO，提交 11.17 shares × $0.05 = $0.5585，被交易所 HTTP 400 明确拒绝，最低 marketable BUY 金额为 $1；无 order_id，不能计成交。其 execution journal 仍为 reconciliation_required，latest_continuity_error 保存 AmbiguousExecutionError。
- 根因定位到部署代码 execution_result_state 的 DEFINITIVE_NO_ORDER_ERRORS 未覆盖该 minimum-amount rejection。现有 reconciliation guard 按 live_key 限制该次 source observation，而非全局暂停。待核对键只有 1 个；本次没有改写执行 journal、解除 guard 或部署修复，因这会改变生产执行行为。
- 147/242 个 canonical fill 的 instance_id 为空；它们的 strategy_id/strategy_key 完整且相同。固定对账脚本按 instance 筛选只得到 95 笔，会遗漏 147 笔；本次以 canonical strategy_id 导出全部 242 笔，并保留原字段，不伪填 instance。该映射缺口尚未修复。

## 数据完整性自检

先完成 production manifest strict、controller health、storage audit，再通过登记的 canonical bounded one-shot 刷新 order → authenticated fill → fact_trades → gate。首次刷新 authenticated checked=69，filled=33，cancelled=26，still_open=10，errors=0；这里是全账户 72h reconciliation 窗口，不是 Cross NO 专属数量。

首次刷新全账户新增 1 fill；Cross NO 数量未增加，最新成交仍为 Seoul 9/11 13:27:28 北京时间。Gate 全账户 DB/cache=1702/1702，raw_not_in_db=db_not_in_raw=missing_order_rows=over_order_keys=0，fill-id mismatch=0，effective cost delta=0；matched taker zero fee without evidence=0；fee evidence exact=1184、estimate=66、maker_zero=452、unknown=0。Cross NO 费用证据 exact=196、estimate=46，估算不可称全为实付精确手续费。

刷新后发现 7 笔 missing_bracket，成本 $48.2164，覆盖 9/08–9/11。已用现有 backfill_weather_pm_history.py 补 4 城 × 4 日共 16 份 Gamma cache，保留 closed 和 exact condition/market identity，再交既有 canonical refresh ingest；不全量 rebuild。文档旧代理 7890 拒绝连接，使用当前 production market proxy 7896 后 16/16 成功、errors=0。最终结算物化验证见下方最终快照。

本次是数据补全及诊断，未实施决策代码修复，因此未做修复后的反事实决策重放，也不发布新策略绩效、扩仓或 promotion 判断。账户余额、全部开放订单和资金转账桥接不在本次逐笔成交刷新验收范围。

## 最终快照

结算补全与后续标准刷新均 exit=0。Cross NO **242/242 成交已结算，missing_bracket 从 7 降为 0**；成交份额、成本与订单数量未改变。此次 7 笔结算更新逐笔范围见 evidence 目录 pending_settlement.csv（刷新前名单），最终逐笔值见 cross_no_all_fills.csv。

最终 observed_at_utc=2026-09-12T17:45:23.123080+00:00；fact build max=2026-09-12T17:44:06.749314+00:00；fact_trades=5247 行；fact_signal_candidates=[183416, '2026-09-11T16:59:44.621655Z']。candidate 层未在本次 bounded fill refresh 中重建，不能把其 build 时间冒充当前 raw signal 新鲜度。

最终全账户 coverage gate PASS：DB/cache fills=1702/1702，缺失、超量、fill-id mismatch、effective cost delta 均为 0；完整 stage 结果保存在 *_final.json。

验收结论：历史成交导出及最新缺失结算补齐完成；当前主进程与主数据路径正常，但 Helsinki 单次拒单状态、instance 归属缺口及 Busan 覆盖告警仍未修复，因此整个链路不能称全绿。未改生产行为。
