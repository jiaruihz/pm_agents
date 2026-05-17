# Weather Strategy Entrypoint

Last updated: 2026-05-17

This is the first file to read before changing, operating, or analyzing the weather strategy.

For early live rollout history, known mistakes, and how to split local/N100 live PnL, also read:

- `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`

## Current Production Posture

- Production data truth is N100 `weather-predict`, not this local repository mirror.
- Live execution runs from N100 `pm_agent` at `/home/jiarui/projects/pm_agent`.
- Local `pm_agent` is for analysis, dashboard work, strategy development, and deployment staging.
- Local live execution should remain stopped unless explicitly requested.
- N100 live execution has a persistent pause switch. Check it before diagnosing orders.

Current live rollout policy:

```text
city_pool = t1_trading
execution_policy = mid_price_core_v1
entry window = 0.25 <= price < 0.75
sizing_mode = notional
max_order_notional = 5.00
max_order_shares = 25.00
order style = maker-only GTC, post_only=True
```

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
- `docs/WEATHER_EXECUTION_CLEANUP_PLAN.md`

## Runtime Files To Inspect

On N100 `pm_agent`:

```text
runtime/weather_edge_v1/live_cycle/*.json
runtime/weather_edge_v1/live_cycle/loop.log
runtime/weather_edge_v1/signals/live_*_signals.jsonl
runtime/weather_edge_v1/plans/live_*_trade_plans.jsonl
runtime/weather_edge_v1/live/live_*_orders.jsonl
```

Each live cycle summary now includes:

- `config`: exact live parameters used for that cycle.
- `contract_alerts`: invariant violations that should be visible in Telegram.
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

## Recent Incident Notes

2026-05-15/16 live debugging found:

- Early live history is split across local `pm_agent` and N100 `pm_agent`. All real fills count for wallet PnL, but strategy evaluation must split by run/config/source. See `docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`.
- Local target-date 2026-05-14 had duplicate/over-submission risk and is not a clean strategy sample.
- Local target-date 2026-05-15 and 2026-05-16 used a broad/non-T1 city universe. Wuhan belongs to this local early-live wrong-universe bucket.
- N100 target-date 2026-05-16 is a legacy/debug live run with incomplete metadata/guardrails, not the same identity as current rollout.
- Current N100 live cycle now passes `--city-pool t1_trading`.
- Size below 10 shares was not a fill bug; it came from fixed-notional sizing: `size = max_order_notional / limit_price`.
- `max_position` was misleading in this live path, because planning checked against `current_position=0`. The active config now uses `max_order_shares` for the per-order share cap. True cumulative position caps should be implemented separately with current position plus open-order context.
- Live summaries now alert on non-T1 rows, notional drift, wrong sizing mode, wrong entry window, executor errors, and command failures.

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
