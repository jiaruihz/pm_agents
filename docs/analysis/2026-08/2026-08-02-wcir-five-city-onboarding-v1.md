# WCIR 五城接入与 JRS 权限异常审计 v1

Status: production recovered; WCIR five-city zero-notional runtime active
Date: 2026-08-02
Framework: `weather_city_intraday_runtime_v1` / Weather City Intraday Runtime (WCIR)
Strategy family: `weather.city_intraday_probability`
Execution mode: zero-notional; no exchange client

## 结论

Amsterdam、Busan、Helsinki、Seoul、Tokyo 已登记到同一个 WCIR v3 config。Helsinki/Tokyo 保留冻结概率 adapter；
Amsterdam/Busan/Seoul 在没有冻结 probability artifact 和 settlement-expression mapping 前使用 `coverage-only` adapter，
只写 typed checkpoint blocker，不生成伪概率、`SignalCandidate`、`TradeIntent` 或订单。

以后新增城市必须新增 WCIR `CaptureProfile + city adapter + deployed contract census + regression fixture`；不得新建
collector、replay clock、order/fill/PnL 或 settlement 旁路。框架名已同步进入 `AGENTS.md`、`CLAUDE.md`、设计、研究规范、
strategy registry 和 runtime instance registry。

## 实际 raw census

| 城市 | source/payload | deployed raw | 本次接入行为 |
|---|---|---:|---|
| Amsterdam | KNMI `ta` point + 10-minute `tx` interval + revision | 1,552 rows；244 revision；454 material；2026-07-30T02:03:51Z..2026-08-02T02:54:11Z | common live-cross producer 增加 `knmi/Amsterdam`；coverage-only |
| Busan | AMOS runway point group | 7,184 rows；截至 2026-08-02T02:56:19Z | 同 timestamp runway group；coverage-only |
| Seoul | AMOS runway point group/revision | 19,778 rows；截至 2026-08-02T02:56:19Z | preferred runway + group min/max + revision lineage；coverage-only |
| Helsinki | FMI point | existing frozen weather/market adapters | shared decision bundle/candidate/intent |
| Tokyo | JMA point + multi-anchor ladder | existing frozen market-aware adapters | shared decision bundle/candidate/intent |

真实 raw adapter smoke 得到：Amsterdam `measurement_interval_revision`、2 event roles；Busan
`point_group_revision`、1 event；Seoul `point_group_revision`、2 events，preferred `31.5°C`、group `31.3..31.5°C`。
因 producer 于 02:57Z 后停止，三城均正确分类为 `source_observation_stale`，没有 exception 或 candidate。

## 测试与 bug 修复

- 城市 runtime 全相关：55 passed。
- JRS entrypoint/high-frequency/Korea feature：30 passed。
- 新 coverage adapter 定向组：24 passed。
- JSON parse、Python compile、bash syntax、`git diff --check` 通过。
- 测试发现并修复 blocker identity bug：动态 `source_age_seconds` 原会令同一 event 每分钟产生新 blocker；现 identity 只由
  profile、event/revision 和 blocker 状态组成，第二轮 `written_blockers=0`。

## 生产异常与影响半径

canonical tmux server 从 2026-08-02 02:57 UTC 起失去 `/Volumes/jrs` 写权限：write probe 返回
`Operation not permitted`；live-cross fast lane 停止，Korea compatibility collector 累计 3,740 条 PermissionError，
data-feed snapshot/forecast/orderbook freshness 同时失败，LaunchAgent `weather-canonical-refresh` last exit=1，manifest=critical。

该窗口 `fast_source_prev_no_trial/orders.jsonl` 无新增行；最后订单仍是 2026-08-01 10:32 UTC，因此已确认的
order/fill/notional/fee/PnL delta 均为 0。未能产生的 source candidates 属 coverage gap，不能反事实声称为漏单或收益损失。

System Settings 显示 pinned `tmux` 的 Full Disk Access 仍为 on，说明现有 server 需要维护重建以重新获得 TCC/JRS context。
同 socket 包含 active live runner；生产 cutover 必须先记录/恢复完整 session 拓扑，再依次验证 write probe、producer freshness、
WCIR journals、restart dedupe、zero orders 和 strict manifest。

## 维护恢复结果

2026-08-02 07:07 UTC 进入已授权维护窗口。重启前认证 CLOB `open_orders=0`，并保存 canonical tmux 的
18 sessions / 19 panes 及完整启动命令。pinned `/opt/homebrew/Cellar/tmux/3.6b/bin/tmux` server 重建后，
JRS write probe 立即通过；18/19 拓扑原样恢复且无 restore error。随后启动统一 `live_cross_observations` producer 和
WCIR v3：observations 于 07:07:56Z 恢复、source events 于 07:08:08Z 恢复、live-cross 于 07:08Z 后持续更新，
snapshot/orderbook/forecast curves 于 07:13:14Z 完成首轮新鲜发布。

生产运行中另发现 Helsinki 当日 FMI 历史不足四条时误抛 `RuntimeError`，形成每分钟同一 incident 的 error row。
根因已在 `8a5a1794` 修为 `insufficient_pit_source_history` 结构化 checkpoint blocker；15 个定向测试通过。
production 标准 WCIR session 当前运行其后代 clean SHA `8d53ac1a`，保留该修复；最新周期 `errors=0`、
`orders_submitted=0`，framework/strategy identity 分别为 `weather_city_intraday_runtime_v1` 与
`weather.city_intraday_probability`。统一 producer 在独立 clean worktree `pm_agents_wcir_prod@8a5a1794`，避免其他
城市研究 checkout 切换污染 producer identity。

canonical refresh 完成且 coverage gate 退出 0；LaunchAgent `last exit code=0`。最终 strict manifest 为
`warning`、无 critical、DB route healthy；唯一 warning 是既有 18 个共享 tmux session 尚未全部登记到 instance registry，
不是本次 JRS 权限或 WCIR freshness 故障。维护前后认证 open orders 均为 0，fast-source order journal 仍为 256 行，
最后一行时间 2026-08-01 10:32:18Z；因此事故与维护窗口确认 order/fill/notional/fee/PnL delta 全为 0。
