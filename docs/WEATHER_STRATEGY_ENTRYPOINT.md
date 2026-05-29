# Weather Strategy Entrypoint

Last updated: 2026-05-27

This is the first file to read before changing, operating, or analyzing the weather strategy.

For early live rollout history, known mistakes, and how to split local/N100 live PnL, also read:

- `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`
- `docs/WEATHER_CITY_POOL_DECISIONS.md`
- `docs/WEATHER_CLOB_ORDERBOOK_CAPTURE.md`

## Current Production Posture

- Production data truth is N100 `weather-predict`, not this local repository mirror.
- Live execution runs from N100 `pm_agent` at `/home/jiarui/projects/pm_agent`.
- Local `pm_agent` is for analysis, dashboard work, strategy development, and deployment staging.
- Local live execution should remain stopped unless explicitly requested.
- N100 live execution has a persistent pause switch. Check it before diagnosing orders.

Current live rollout policy:

```text
city_pool = t1_trading (v3, 24 cities — see docs/WEATHER_CITY_POOL_DECISIONS.md)
execution_policy = maker_queue_v1  [upgraded 2026-05-26; set via WEATHER_LIVE_EXECUTION_POLICY in N100 .env]
entry window = 0.25 <= price < 0.75
signal capture = scan latest 90 minutes of synced snapshots; executable window 22h <= hours_to_settle_now <= 28h
sizing_mode = notional
max_order_notional = 5.00
max_order_shares = 25.00
order style = maker-only GTC, post_only=True
```

## 当前城市池 v3（2026-05-27）

城市池决策日志和完整证据见 `docs/WEATHER_CITY_POOL_DECISIONS.md`。
代码 source of truth 是 `weather-predict/city_pools.py` 的
`CITY_TRADING_CONFIG`。

**T1 完整城市列表（2026-05-29 city×side 配置，共 24 个）：**
Amsterdam, Ankara, Boston, BuenosAires, Chengdu, Guangzhou, Istanbul,
Jeddah, Karachi, LA, London, Lucknow, Madrid, Manila, Miami, Moscow,
Munich, NYC, Phoenix, Seattle, Shanghai, Singapore, Tokyo, Warsaw

**2026-05-29 city×side 变更：**
- Madrid 重新进入 T1，但只允许 `BUY_NO`。
- Shanghai 保留 T1，但只允许 `BUY_NO`。
- Paris 降级到 T2 / research only。
- 其余 T1 城市默认双侧。

Paris / Beijing / Chicago / Austin 均保留在 `FULL_CITY_CONFIGS`，因此是 T2
research-only，不是删除城市配置。

## 2026-05-26 城市池 v2 变更（已被 v3 覆盖，paper ledger 生效）

基于 2026-05-08~05-24 ledger 数据，939 笔已结算交易的分析结论。

**T1 新增（8 城，T2→T1）：**
Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle
— BUY_NO ecmwf/gfs 胜率 67%~95%，ROI +20%~+55%（95% CI 下界均 > 0.53）

**T1 移除（完全屏蔽 paper order）：**
- Beijing → `EXCLUDED_CITIES`（ECMWF 结构性失效，BUY_YES 0/17，BUY_NO 全 edge bucket 均亏）
- Austin → `EXCLUDED_CITIES`（entry_price 中位 0.65，赔率结构不利，BUY_NO 58% 胜但 ROI -12.6%）

**BUY_YES 禁用城市（`BUY_YES_BLOCKED_CITIES`）：**
Ankara, Istanbul, Jeddah, Lucknow, Moscow（BUY_YES 胜率 0%~20%，ROI -42%~-100%）

**Paris 过滤收紧：** `abs_edge ≥ 0.30`（低于此阈值全部亏损）

**双策略 A/B paper 对比（2026-05-26 起）：**
每个信号同时生成两笔 paper order，各 5 shares：
- `mid_price_core_v1`：entry_price = last_trade_price（原有行为）
- `maker_queue_v1`：entry_price = no_best_bid / yes_best_bid（CLOB 盘口）
dedup key 包含 execution_policy，两笔订单互不干扰。

**T1 完整城市列表（v2，共 20 个，历史记录）：**
Ankara, Boston, Chicago, Guangzhou, Istanbul, Jeddah, Karachi, LA,
London, Lucknow, Madrid, Miami, Moscow, NYC, Paris, Phoenix, Seattle,
Shanghai, Tokyo, Warsaw

`maker_queue_v1` is implemented as a pluggable execution policy for dry-run comparison and controlled rollout. It prices maker orders from the current bid/ask, requires edge after an adverse-selection buffer, and is selected with `--execution-policy maker_queue_v1` or `WEATHER_LIVE_EXECUTION_POLICY=maker_queue_v1`. Keep the documented `mid_price_core_v1` live default until a deliberate rollout switch is made.

Execution policy is part of `strategy_config`, not part of signal generation.
For A/B tests, build signals once and branch after `signals`:

```text
same signal_id
  -> mid_price_core_v1 run/plan/order/fill
  -> maker_queue_v1 run/plan/order/fill
```

Use `scripts/ops/weather_policy_branch.py --execution-policy maker_queue_v1`
to run an execution-policy branch from the latest `mid_price_core_v1` signal
file. This keeps market/model opportunities identical while giving each policy
its own config/run/plan/order lineage.

Important: `city_pool=t1_trading` is the execution pool source of truth. Do not add a second hardcoded T1 city allowlist unless there is a new explicit design decision. If a T2 city appears in live signals or live orders, treat it as an upstream `city_pool` or live-cycle parameter incident, pause live, and investigate.

## Production Checks

Run N100 commands from WSL, not directly from Windows PowerShell:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_status.py status --json'
```

Health check:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_doctor.py --http-timeout 6 --sync-dry-run --require-live-loop --require-telegram-control'
```

Dry-run one live cycle without placing orders:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_cycle.py --dry-run-live --no-telegram'
```

Pause/resume:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_status.py pause --reason "manual review" --source claude'
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_status.py resume'
```

Telegram commands also exist:

```text
pause / 暂停
resume / 继续
status / 状态
```

## Main Code Paths

Live cycle orchestration:

- `scripts/ops/weather_live_cycle.py`

Signal construction from N100 snapshot mirror:

- `scripts/ops/weather_snapshot_signal_builder.py`

Planning and sizing:

- `scripts/ops/weather_trade_planner.py`
- `src/strategies/weather_edge_v1/tools/execution_pipeline.py`

Live/paper order execution:

- `scripts/ops/weather_order_executor.py`

Live pause state:

- `scripts/ops/weather_live_status.py`
- `src/strategies/weather_edge_v1/tools/live_state.py`

Health monitoring:

- `scripts/ops/weather_live_doctor.py`
- `docs/OPS_RUNBOOK.md`

Architecture and cleanup context:

- `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`
- `docs/WEATHER_EXECUTION_ARCHITECTURE.md`
- `docs/archive/WEATHER_EXECUTION_CLEANUP_PLAN.md` (归档，已完成)

## Runtime Files To Inspect

On N100 `pm_agent`:

```text
runtime/weather_edge_v1/live_cycle/*.json
runtime/weather_edge_v1/live_cycle/loop.log
runtime/weather_edge_v1/signals/live_*_signals.jsonl
runtime/weather_edge_v1/plans/live_*_trade_plans.jsonl
runtime/weather_edge_v1/live/live_*_orders.jsonl
```

Important live PnL note: `live_*_orders.jsonl` records submitted/error order
attempts, not actual fills. Real live CLOB fill-level PnL is in the dashboard
DB (`runtime/weather.db`) via `orders.venue='polymarket_clob'` joined to
`fills.status='filled'` and `settlements`; the relevant API logic lives in
`weather_dashboard/api/routers/live.py`. See
`docs/WEATHER_DATA_PIPELINE.md` before making live-vs-paper PnL claims.

Each live cycle summary now includes:

- `config`: exact live parameters used for that cycle.
- `contract_alerts`: invariant violations that should be visible in Telegram.
- `signals.snapshots`: all snapshot files scanned for catch-up signal capture.
- `planner.live_dedup`: skipped prior or same-run duplicate plans.
- `executor.balance_preflight`: CLOB balance/allowance gate when orders are eligible.

Expected `contract_alerts=[]` for a normal cycle.

## Research State

Current rollout is based on recent backtests and live debugging, not a final settled strategy.

Working conclusions as of 2026-05-16:

- T1 `25-75` price-window strategy is the active live rollout.
- `$5` fixed-notional sizing is the active sizing policy.
- Fixed shares remains a configurable alternative but is not the default.
- Lottery-style entries below `0.25` are a separate research subtopic, not part of current live execution.
- T2 remains useful for research and paper ledger analysis, but not live trading.
- Filled small test positions from earlier rollout mistakes are being left alone unless explicitly requested.

Before making research claims, sync the latest N100 mirror first:

```bash
cd /home/rui/projects/pm_agent
scripts/ops/sync_weather_remote.sh
```

Preferred settlement summary source:

```text
runtime/weather_edge_v1/market_data/research/t24_paper_ledger_summary.json
```

If that file is stale or missing, regenerate on N100 `weather-predict`:

```bash
ssh 192.168.0.200 'cd /home/jiarui/projects/weather-predict && python3 scripts/analysis/settle_t24_paper.py --source ledger'
```

## Historical Incident Notes（已解决，仅供参考）

> 详细历史记录见 `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`。

2026-05-15/16 调试结论（均已修复，当前配置已覆盖）：

- 早期 live 历史分布在本机和 N100 两个 `pm_agent`。所有真实成交都计入 wallet PnL，但策略评估必须按 run/config/source 分开。
- 2026-05-14（本机）：重复提交风险，非干净样本。
- 2026-05-15/16（本机）：使用了宽泛/非 T1 城市池（含 Wuhan）。
- 2026-05-16（N100）：legacy/debug live run，metadata 不完整，不是当前 rollout。
- **当前修复状态**：N100 live cycle 传 `--city-pool t1_trading`；`max_order_shares` 替代了有误导的 `max_position`；live summaries 已有 contract_alerts 告警。

## Open Design Items

Do not implement these casually during emergency live fixes:

- Pending opportunity catch-up: keep paused/failed opportunities for a short TTL, then revalidate against latest book before placing.
- Retry policy by failure type: only transient maker/no-resting-price/network errors should retry; balance/allowance/config/contract errors should alert instead.
- True cumulative exposure caps: should read current positions and open orders before planning/execution.
- Daily live-vs-paper reconciliation report.
- Dashboard integration for live status, contract alerts, and pending opportunity audit.

## Minimal Safe Change Workflow

1. Read this file and `docs/OPS_RUNBOOK.md`.
2. Check N100 status and pause state.
3. Make local changes in `/home/rui/projects/pm_agent`.
4. Run focused tests.
5. Commit and push.
6. Deploy only the touched files to N100, or use the agreed deployment script if present.
7. Run N100 `py_compile`, focused tests, doctor, and one `--dry-run-live --no-telegram`.
8. Keep live resumed only if doctor passes and `contract_alerts=[]`.
