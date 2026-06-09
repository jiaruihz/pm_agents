# Weather Strategy Entrypoint

Status: current-source
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

Last updated: 2026-06-08

This is the first file to read before changing, operating, or analyzing the weather strategy.

For early live rollout history, known mistakes, and how to split local/N100 live PnL, also read:

- `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`
- `docs/WEATHER_CITY_POOL_DECISIONS.md`
- `docs/WEATHER_CLOB_ORDERBOOK_CAPTURE.md`
- `docs/WEATHER_REPO_BOUNDARY.md`

## Current Production Posture

- Production data truth is N100 `weather-predict`, not this local repository mirror.
- Live execution runs from N100 `pm_agent` at `/home/jiarui/projects/pm_agent`.
- Local `pm_agent` is for analysis, dashboard work, strategy development, and deployment staging.
- Local live execution should remain stopped unless explicitly requested.
- N100 live execution has a persistent pause switch. Check it before diagnosing orders.

Current live rollout policy:

```text
city_pool = t1_trading (weather-predict v4 has 22 cities; pm_agent live instances apply explicit allowlists below)
signal capture = scan latest 90 minutes of synced snapshots; executable window 22h <= hours_to_settle_now <= 26h
sizing_mode = notional
max_order_notional = 5.00
max_order_shares = 25.00
order style = maker-only GTC, post_only=True
```

2026-06-08 timing baseline 后的临时风控：`<T-22` 继续不进 live，
`T-26-28` 先从 live executable window 移除；`T-24-26` 暂保留但必须继续
做 forecast checkpoint / city x timing 复核。

N100 should run exactly two weather live strategy instances by default:

| strategy_instance | execution_policy | source_strategy_instance | Entry / edge gate |
|---|---|---|---|
| `mid_price_core_v1_25_75` | `mid_price_core_v1` | direct signal builder | global `0.25 <= price < 0.75`, `edge >= 0.10` |
| `mid_price_core_v1_side_band` | `mid_price_core_v1` | direct signal builder | YES `0.20 <= price < 0.45`, `edge >= 0.20`; NO `0.35 <= price < 0.65`, `edge >= 0.10` |

## weather_edge_v2 Research / Shadow Entry

`weather_edge_v2` is research/shadow only. It is not a live strategy and must
not be read as wallet cashflow or live-realized PnL.

Current artifacts:

| Artifact | Purpose |
|---|---|
| `scripts/analysis/blender_shadow/research_weather_edge_v2_filtered_operational_base.py` | Rerun raw/blend/side-band/basket comparisons on the current operational base: remove `Ankara/BuenosAires/Jeddah/Karachi/Moscow/Munich`, require `decision_hours_to_settle <= 28`. |
| `scripts/analysis/blender_shadow/build_weather_edge_v2_shadow_lineage.py` | Build per city-day/rule shadow lineage: rule_id, selected/rejected legs, market distribution, EV, CVaR20, leave-best-out EV, worst-case payoff, actual settled PnL, missed/avoided attribution. |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-filtered-operational-base-research.md` | Human-readable full comparison report. |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-shadow-lineage.md` | Human-readable shadow lineage summary. |
| `docs/analysis/2026-06/2026-06-08-weather-edge-v2-shadow-lineage.jsonl` | Full machine-readable city-day/rule lineage rows for forward-settled comparison. |

Dashboard view:

```text
http://localhost:5173/weather/research
```

API source:

```text
GET http://localhost:8000/api/research/weather-edge-v2/latest
```

The dashboard intentionally shows `weather_edge_v2` under Weather Research,
not under Strategies or Live. Until a canary is explicitly approved, the
Strategies/Live pages remain reserved for actual configured/live execution
metrics.

Refresh flow after new settled data:

```bash
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh
. .venv/bin/activate
python scripts/analysis/blender_shadow/research_weather_edge_v2_filtered_operational_base.py
python scripts/analysis/blender_shadow/build_weather_edge_v2_shadow_lineage.py
```

Canary still requires: CLOB gate and fill_id reconciliation pass, recent and
holdout filtered basket beat current side-band, top5-removed ROI does not
deteriorate, missed profit is no larger than avoided loss, walk-forward
positive folds stay at or above 60%, and at least one additional settled week
of forward shadow evidence.

Current pm_agent live allowlists:

| strategy_instance | allowed cities |
|---|---|
| `mid_price_core_v1_25_75` | Boston, LA, London, Miami, NYC, Phoenix, Shanghai, Tokyo, Warsaw |
| `mid_price_core_v1_side_band` | Boston, LA, London, Miami, NYC, Phoenix, Shanghai, Tokyo, Warsaw |

The 2026-06-09 v1_25_75 allowlist was narrowed to the same legacy core 9-city
pool as side-band. Expanded T1 cities such as `Chengdu/Guangzhou/Istanbul/
Lucknow/Madrid/Manila/Seattle/Singapore` remain in weather-predict collection,
paper/research, and settlement, but are not live-eligible in pm_agent by
default. This supersedes the 2026-06-08 17-city pm_agent allowlist.

`mid_price_core_v2_25_75` was stopped from live on 2026-06-06. Finding:
V2 execution improved fill-vs-plan on BUY_YES, but the grabbed `0.25-0.75`
YES opportunities were negative alpha (`target_date >= 2026-06-01`: V2
BUY_YES PnL `-22.64`, ROI `-19.45%`). Do not restart it by default; only
explicitly re-enable with `START_MID_PRICE_CORE_V2_25_75=1` for a named
shadow/live experiment after adding YES/city filters.

Operationally, this is one order pipeline parameterized by
`strategy_instance`, `execution_policy`, and entry-band config. Do not count
strategies only by `execution_policy`: two instances can share
`mid_price_core_v1` while using different signal gates and separate live dedup.
Start the intended production set with:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && scripts/ops/start_weather_three_strategy_instances.sh'
```

The script name is historical; by default it now starts two instances. V2 only
starts when `START_MID_PRICE_CORE_V2_25_75=1` is set.

## 当前城市池 v4 + pm_agent allowlists（2026-06-08）

城市池决策日志和完整证据见 `docs/WEATHER_CITY_POOL_DECISIONS.md`。
代码 source of truth 是 `weather-predict/city_pools.py` 的
`CITY_TRADING_CONFIG`。

**T1 完整城市列表（2026-06-06 city×side 配置，共 22 个）：**
Ankara, Boston, Chengdu, Guangzhou, Istanbul, Jeddah, Karachi, LA,
London, Lucknow, Madrid, Manila, Miami, Moscow, Munich, NYC, Phoenix,
Seattle, Shanghai, Singapore, Tokyo, Warsaw

**2026-06-06 city pool 变更：**
- Amsterdam 降级到 T2 / research only：三策略实例 all-history live PnL `-30.43`, ROI `-82.0%`。
- BuenosAires 降级到 T2 / research only：三策略实例 all-history live PnL `-28.11`, ROI `-49.6%`。
- V2 live 默认停止：主动抢单质量不是主要问题，`0.25-0.75` YES 信号负 alpha 是主要问题。

**保留的 2026-05-29 city×side 规则：**
- Madrid 重新进入 T1，但只允许 `BUY_NO`。
- Shanghai 保留 T1，但只允许 `BUY_NO`。
- Paris 降级到 T2 / research only。
- NYC 保留 T1，但 pm_agent live signal builder 暂时 hard-block `BUY_YES`（`WEATHER_LIVE_BLOCKED_CITY_SIDES=NYC:BUY_YES`）；`BUY_NO` 继续允许。
- 其余 T1 城市默认双侧。

Paris / Beijing / Chicago / Austin / Amsterdam / BuenosAires 均保留在 `FULL_CITY_CONFIGS`，因此是 T2
research-only，不是删除城市配置。

**2026-06-09 pm_agent live allowlist 覆盖层：**

- `mid_price_core_v1_25_75` 从 live allowlist 进一步收窄到 legacy core 9 城：
  Boston, LA, London, Miami, NYC, Phoenix, Shanghai, Tokyo, Warsaw。
- `mid_price_core_v1_side_band` 保持 legacy core 9 城，不包含上述弱城市。
- 这不是从 `weather-predict` 删除城市；这些城市仍继续用于采集、结算、
  paper/research 和 shadow 分析。
- 2026-06-08 N100 曾部署 `4149a13` 的 17 城覆盖层；2026-06-09 起以本文
  9 城覆盖层为准。

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

**历史双策略 A/B paper 对比（2026-05-26 起，已退役）：**
曾经每个信号同时生成 `mid_price_core_v1` 与 `maker_queue_v1` 两笔 paper order。
`maker_queue_v1` 已退役；当前 legacy paper runner 只生成 `mid_price_core_v1`。

**T1 完整城市列表（v2，共 20 个，历史记录）：**
Ankara, Boston, Chicago, Guangzhou, Istanbul, Jeddah, Karachi, LA,
London, Lucknow, Madrid, Miami, Moscow, NYC, Paris, Phoenix, Seattle,
Shanghai, Tokyo, Warsaw

`maker_queue_v1` is retired. Do not select it via CLI or environment variables.
Current default live execution policy branches are `mid_price_core_v1_25_75`
and `mid_price_core_v1_side_band`. `mid_price_core_v2_25_75` is stopped from
live by default as of 2026-06-06.

Execution policy is part of `strategy_config`, not part of signal generation.
For A/B tests, build signals once and branch after `signals`:

```text
same signal_id
  -> mid_price_core_v1 run/plan/order/fill
  -> mid_price_core_v2 run/plan/order/fill
```

Use `scripts/ops/weather_policy_branch.py --execution-policy mid_price_core_v2`
to run an execution-policy branch from the latest `mid_price_core_v1` signal
file. This keeps market/model opportunities identical while giving each policy
its own config/run/plan/order lineage.

Important: `city_pool=t1_trading` is the execution pool source of truth. Do not add a second hardcoded T1 city allowlist unless there is a new explicit design decision. If a T2 city appears in live signals or live orders, treat it as an upstream `city_pool` or live-cycle parameter incident, pause live, and investigate.

2026-06-08 exception: `scripts/ops/start_weather_three_strategy_instances.sh`
passes explicit per-instance allowlists. `mid_price_core_v1_25_75` is narrower
than weather-predict v4 T1 because recent raw/ECMWF degradation was concentrated
in `Ankara/Jeddah/Karachi/Moscow/Munich`; `mid_price_core_v1_side_band` keeps
the legacy core 9-city pool. Do not add a second city×model block unless a new
design explicitly proves it is needed; prefer changing the instance allowlist
first.

## Production Checks

Run N100 commands from WSL, not directly from Windows PowerShell:

```bash
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_status.py status --json'
```

## N100 pm_agent Git Deployment Record

As of 2026-05-31, N100 live execution runs from a real git worktree:

```text
worktree: /home/jiarui/projects/pm_agent
origin:   /home/jiarui/projects/pm_agent_repo.git
backup:   /home/jiarui/projects/pm_agent_pre_git_20260531T2145_gitcutover
```

Runtime-only paths were preserved across the cutover and must not be overwritten
by code deployment:

```text
/home/jiarui/projects/pm_agent/.env
/home/jiarui/projects/pm_agent/.venv
/home/jiarui/projects/pm_agent/runtime
/home/jiarui/projects/pm_agent/.claude
/home/jiarui/projects/pm_agent/.codex
```

If N100 does not have direct GitHub credentials, deploy code with a git bundle
as a git transport artifact: create the bundle from local `pm_agent`, import it
into `/home/jiarui/projects/pm_agent_repo.git`, then fetch/checkout in
`/home/jiarui/projects/pm_agent`. Do not copy individual code or config files
into the live worktree with `scp`/`rsync`.

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

2026-06-07 CLOB fill recovery incident: Polymarket public activity is not an
order-level authoritative fill source. It is account-level activity and can
misallocate fills across split child orders or partial fills. Correct live fill
recovery now uses this priority:

1. local `exchange_response.place.status='matched'` immediate fill
   (`makingAmount` / `takingAmount`);
2. authenticated CLOB order / trade data;
3. public activity only as a token/side/price/time matched fallback, capped by
   each submitted order's shares/cost.

Before publishing any live_real PnL/ROI/curve, run:

```bash
python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

`gate_pass=false` means stop and fix the fill cache / CLOB sync before making
strategy conclusions. Minimum publishable condition:
`gate_pass=true`, `missing_order_rows=0`, `over_order_keys=0`,
DB/cache fill_id difference is 0, and `db_fill_cost_minus_fact_cost=0`.
`live_real` row count changes as new real fills arrive, so do not hardcode a
historical count. Full incident report:
`docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md`.

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
