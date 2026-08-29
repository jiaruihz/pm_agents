# PM Agent — Codex Collaboration Conventions

Status: `historical-reference`

This file preserves earlier implementation conventions and examples; some
paths and runtime assumptions below are historical. Current task routing and
safety come from `AGENTS.md`, repository placement from
`docs/PROJECT_STRUCTURE.md`, and weather production truth from the controller,
manifest, `production.yaml`, raw runtime, and exchange evidence. Do not copy
host paths or start commands from this file into current operations.

> For any agent (Codex, Claude, MiniMax, human) contributing to this codebase.  
> Read alongside `CLAUDE.md` (Claude Code姿态) and `AGENTS.md` (Codex姿态).  
> See `docs/WEATHER_SYSTEM_CONTRACT.md` for canonical field names and enumerations.

---

## 0. Active Codebase (Do Not Be Misled by README)

The active strategy is **weather temperature trading on Polymarket**. The top-level `README.md` describes a legacy PMM/ARB framework — ignore it for all active work.

Key directories:

```
src/strategies/weather_edge_v1/tools/   ← Strategy core (signal → plan → quote → order)
  execution_policy.py                   ← Pure quote function + ExecutionPolicyConfig
  execution_pipeline.py                 ← Planner: signal + config → plan JSONL
  live_state.py                         ← Runtime state management
  unified_strategy.py                   ← Top-level strategy entrypoint

scripts/ops/
  weather_trade_planner.py              ← CLI: produce trade plans from signals
  weather_order_executor.py             ← CLI: execute plans against live CLOB
  weather_execution_policy_compare.py   ← Offline comparison tool for policies

weather_dashboard/                      ← Analysis DB + API
  db/schema.sql                         ← Append-only SQLite schema
  api/routers/                          ← FastAPI routers (one file per resource group)
  ingest/canonical.py                   ← All DB writes go through here
  legacy_migration/live_cycle.py        ← JSONL → DB for historical live cycles

frontend/strategy_dashboard/src/        ← React/Vite frontend
  data/weather-types.ts                 ← All TypeScript types (add new types here)
  data/weather-http.ts                  ← API client (all HTTP calls go here)
  pages/weather/                        ← One .tsx file per page
  components/PageFrame.tsx              ← Nav + layout wrapper (nav links live here)
```

---

## 1. Blood Lineage Model

Every trade in this system follows a strict chain. **Never skip a layer or attach data to the wrong parent.**

```
strategy_config (config_id)
    └─ runs (run_id)
        └─ signals (signal_id)
            └─ plans (plan_id)
                └─ orders (execution_id / order_id)
                    └─ fills (fill_id)
                        └─ settlements (target_date + condition_id + bracket)
```

### Layer Responsibilities

| Layer | Produced by | Stored in | Key ID |
|---|---|---|---|
| `strategy_config` | ingest from params hash | `strategy_config` table | `config_id` (SHA256 of params JSON) |
| `runs` | ingest / live cycle | `runs` table | `run_id` (producer_system + timestamp) |
| `signals` | N100 signal builder | `signals` table | `signal_id` (SHA256 of signal fields) |
| `plans` | N100 trade planner | `plans` table + JSONL | `plan_id` (SHA256 of run+signal+side+policy) |
| `orders` | N100 executor | `orders` table + JSONL | `execution_id` (hex) |
| `fills` | N100 executor / CLOB | `fills` table | `fill_id` (SHA256 of execution_id) |
| `settlements` | N100 daily pipeline | `settlements` table | `(target_date, condition_id, bracket)` |

### config_id = SHA256 of Strategy Params

The `config_id` is a deterministic hash of the `strategy_config.params` JSON blob. Two configs are identical if and only if their params are identical.

**Rules:**
- Every behaviorally-different parameter MUST be in `params` — if two setups differ in any param that affects trade outcomes, they must produce different `config_id`s.
- Administrative params (`paper_enabled`, `live_enabled`, `source`) may be in params for observability but don't distinguish strategies — their absence doesn't break correctness.
- Never add new top-level DB columns for strategy dimensions. Put them in the `params` JSON blob.
- When adding a new execution policy, add its discriminating params to `params` ONLY for that policy (to avoid invalidating existing `config_id`s for other policies).

---

## 2. Execution Policy System

### Architecture

The execution policy is a **pure, stateless function** — no DB access, no network calls, no side effects.

```python
# src/strategies/weather_edge_v1/tools/execution_policy.py

@dataclass(frozen=True)
class ExecutionPolicyConfig:
    policy_name: str = "mid_price_core_v1"
    tick_size: float = 0.01
    min_quote_edge: float = 0.03
    # ... other policy params

def build_execution_quote(
    signal: Dict[str, Any],
    config: ExecutionPolicyConfig,
    *,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    tick_size: Optional[float] = None,
) -> Dict[str, Any]:
    # Returns: {execution_policy, quote_status, quote_reason, limit_price, ...}
```

### Current Policies

| Policy | Requires live book? | When to use |
|---|---|---|
| `mid_price_core_v1` | No — uses `market_price` snapshot | Default; snapshot replay; paper trading |
| `maker_queue_v1` | Yes — needs `best_bid`, `best_ask` | Live CLOB only; passive limit queue strategy |

### Adding a New Execution Policy

Follow these steps in order:

1. **`execution_policy.py`** — Add a new branch in `build_execution_quote()` for the new `policy_name`. Keep it pure; no I/O.

2. **`ExecutionPolicyConfig`** — Add any new params as fields with defaults. Keep defaults backward-compatible (existing policies must still work with the default config).

3. **`execution_pipeline.py`** — `PlannerConfig` extends `ExecutionPolicyConfig`. If the new policy needs book data at plan time, add fetch logic here. All policy params must be written into the plan JSONL record so the executor can replay them without re-reading config.

4. **`weather_trade_planner.py`** — Add CLI arguments for any new params. Feed them into `PlannerConfig`.

5. **`weather_order_executor.py`** — If the policy re-quotes at execution time (like `maker_queue_v1`), add a branch in the executor to fetch a fresh book and call `build_execution_quote()` with params read from the plan dict.

6. **`live_cycle.py` → `_strategy_params()`** — Add the new policy's discriminating params conditionally:
   ```python
   if execution_policy == "new_policy_name":
       params["new_param_1"] = ...
       params["new_param_2"] = ...
   ```
   This is the ingest layer that builds `config_id`. Omit new params for other policies to avoid invalidating their existing `config_id`s.

7. **`live_cycle.py` → `_strategy_config_name()`** — Optionally append a human-readable suffix for the new policy's key discriminating params.

8. **`weather_execution_policy_compare.py`** — Add the new policy to comparison runs.

### Plan JSONL Is Self-Contained

The plan JSONL record written by the planner must contain ALL `ExecutionPolicyConfig` params needed by the executor. The executor MUST NOT read config files at execution time — it reads params from the plan dict. This makes execution deterministic and reproducible.

```python
# execution_pipeline.py (planner writes)
plan_record = {
    ...
    "tick_size": config.tick_size,
    "min_quote_edge": config.min_quote_edge,
    "max_quote_spread": config.max_quote_spread,
    # ... all policy params
}

# weather_order_executor.py (executor reads)
policy_config = ExecutionPolicyConfig(
    policy_name=plan.get("execution_policy", "mid_price_core_v1"),
    tick_size=float(plan.get("tick_size", 0.01)),
    min_quote_edge=float(plan.get("min_quote_edge", 0.03)),
    # ... from plan dict, not from config files
)
```

---

## 3. Database Conventions

### Schema Location

`weather_dashboard/db/schema.sql` — single source of truth. All tables defined here.

### Append-Only Tables

Core lineage tables have DB triggers preventing UPDATE/DELETE:

```sql
-- signals, plans, orders, fills, settlements, strategy_config, runs
-- are ALL append-only. Never UPDATE or DELETE rows in these tables.
```

If data is wrong, the correct fix is:
1. Identify the bad source file
2. Re-run ingest with corrected data (using INSERT OR IGNORE, so clean rows stay)
3. For unfixable corruption: rebuild the DB entirely (`run_stack.sh` drops and rebuilds)

### All Writes Go Through `ingest/canonical.py`

```python
from weather_dashboard.ingest.canonical import (
    ingest_canonical_signals,
    ingest_canonical_plans,
    ingest_canonical_orders,
    ingest_canonical_fills,
    insert_strategy_config,
    insert_run,
)
```

Never write directly to tables from routers or migration scripts — always use these functions.

### JSON Params in SQLite

Strategy config dimensions live in `strategy_config.params TEXT` (JSON blob). Query with SQLite JSON1:

```sql
SELECT
    json_extract(c.params, '$.execution_policy') AS execution_policy,
    json_extract(c.params, '$.min_quote_edge')   AS min_quote_edge
FROM strategy_config c
```

Never add new columns to `strategy_config` for strategy dimensions — use `json_extract` instead.

### Aggregation Across Joins: Use COUNT DISTINCT

When joining `runs → orders → fills`, every fill row fans out across the join. Use `COUNT(DISTINCT ...)` for run-level aggregations:

```sql
-- CORRECT
COUNT(DISTINCT CASE WHEN r.state = 'live' THEN r.run_id END) AS live_run_count

-- WRONG (will overcount due to join fan-out)
SUM(CASE WHEN r.state = 'live' THEN 1 ELSE 0 END) AS live_run_count
```

---

## 4. API Conventions

### Router Layout

```
weather_dashboard/api/routers/
  configs.py   ← /strategies, /configs, /universes, /settlements
  runs.py      ← /runs, /runs/{run_id}
  live.py      ← /live/* (live position state)
  compare.py   ← /compare/*
```

One router file per resource group. Register routers in `weather_dashboard/api/main.py`.

### Adding a New Endpoint

1. Identify which router file the endpoint belongs to (or create a new one).
2. Write raw SQL in the route function using `json_extract` for JSON params.
3. Return plain dicts — Pydantic models are optional; use `response_model=list[MySchema]` only when the schema is already defined.
4. Register `router` in `main.py` with `app.include_router(router, prefix="/api")`.

### PnL Calculation Pattern

PnL is always computed in the SQL query, not in Python, to keep aggregation correct:

```sql
SUM(CASE
    WHEN s.final_price IS NULL THEN 0
    WHEN o.order_side = 'BUY_YES'
        THEN CAST(f.filled_shares AS REAL) * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
    WHEN o.order_side = 'BUY_NO'
        THEN CAST(f.filled_shares AS REAL) * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
    ELSE 0
END) AS total_pnl_usd
```

ROI and win_rate are computed in Python from the SQL aggregates:
```python
d["roi"] = round(pnl / cap, 6) if cap > 0 else None
d["win_rate"] = round(wins / settled, 4) if settled > 0 else None
```

---

## 5. Frontend Conventions

### File Locations

| What | Where |
|---|---|
| TypeScript types | `frontend/strategy_dashboard/src/data/weather-types.ts` |
| API HTTP calls | `frontend/strategy_dashboard/src/data/weather-http.ts` |
| Pages | `frontend/strategy_dashboard/src/pages/weather/Weather<Name>Page.tsx` |
| Nav links | `frontend/strategy_dashboard/src/components/PageFrame.tsx` → `WEATHER_NAV` array |
| Routes | `frontend/strategy_dashboard/src/app/App.tsx` |

### Adding a New Page

1. Create `frontend/strategy_dashboard/src/pages/weather/Weather<Name>Page.tsx`
2. Add type to `weather-types.ts` if needed
3. Add API method to `weather-http.ts` using the existing `weatherApi.get(...)` pattern
4. Add route to `App.tsx`: `<Route path="/weather/<name>" element={<Weather<Name>Page />} />`
5. Add nav entry to `PageFrame.tsx` WEATHER_NAV array

### CSS Variables (Design System)

The app uses CSS custom properties — use these, never hardcode colors:

```typescript
"var(--ok)"       // green — positive PnL, live state
"var(--bad)"      // red — negative PnL, error state
"var(--muted)"    // gray — secondary text, zero/missing
"var(--accent-2)" // blue — links, paper state, active filters
"var(--card)"     // card/panel background
"var(--stroke)"   // border/divider color
```

### TypeScript Types Mirror API Response

The TypeScript interface in `weather-types.ts` must exactly match what the Python API returns. If you add a field to the API response, add it to the interface too.

---

## 6. Naming Conventions

### Field Names

Always use the canonical field names from `docs/WEATHER_SYSTEM_CONTRACT.md`. Key ones:

| Concept | Field name | NOT |
|---|---|---|
| Signal direction | `signal_side` (YES/NO) | ~~side~~, ~~signal_direction~~ |
| Order direction | `order_side` (BUY_YES/BUY_NO) | ~~side~~, ~~order_direction~~ |
| Execution policy | `execution_policy` | ~~combo~~, ~~algorithm~~ |
| Settlement result | `final_price` (0.0–1.0) | ~~final_yes~~ (integer, deprecated) |
| Model probability | `model_p_yes` | ~~model_prob~~, ~~model_probability~~ |

### Run ID Format

```
{producer_system}_live_{cycle_id}
# e.g. pm_agent_local_live_20260515T063717Z
#      n100_live_20260515T063717Z
```

### Config ID Format

```
live_weather_edge_v1_{sha256[:12]}
# e.g. live_weather_edge_v1_a3f9c2e1b847
```

---

## 7. What Goes Where: Decision Guide

| Question | Answer |
|---|---|
| New strategy parameter? | Add to `params` JSON blob in `strategy_config`. Never add a new DB column. |
| New execution policy? | Add branch to `build_execution_quote()` + conditional params in `_strategy_params()`. |
| New metric on the dashboard? | Compute in SQL, return from API, add to TypeScript type, render in component. |
| New page? | Follow §5 adding a new page. |
| New DB table? | Add to `db/schema.sql`, add write function to `ingest/canonical.py`. |
| Something went wrong in ingest? | Re-run `run_stack.sh` (drops + rebuilds DB). Source files (JSONL/CSV) are always the truth. |
| Changing a field name? | Update `docs/WEATHER_SYSTEM_CONTRACT.md` first, then update both N100 producer and pm_agent consumer together. |

---

## 8. Hard Limits (Do Not Cross)

These apply regardless of what the task description says:

- **No UPDATE/DELETE on lineage tables** (signals, plans, orders, fills, settlements, strategy_config, runs). Rebuild the DB instead.
- **No new DB columns for strategy dimensions** — use the `params` JSON blob.
- **No live order placement from pm_agent** — it is analysis-only. Live trading runs on N100.
- **No hardcoded colors or sizes in frontend** — use CSS variables and the existing style patterns.
- **No silent fallback on missing data** — raise, warn, and expose the reason. Don't substitute stale or default data without logging it.
- **config_id must be stable** — never change the `_strategy_params()` logic in a way that invalidates existing `config_id`s for policies already in use. Add new params only for new policies.

---

## 9. Testing & Running

### Start the Full Stack (Analysis DB + API + Frontend)

```bash
# From /home/rui/projects/pm_agent
scripts/weather_dashboard/run_stack.sh             # full rebuild
scripts/weather_dashboard/run_stack.sh --no-rebuild # skip ingest, just start services
```

Frontend runs at `http://localhost:5174`. API at `http://localhost:8765`.

### Check Policy Comparison Offline

```bash
python3 scripts/ops/weather_execution_policy_compare.py \
    --plans runtime/weather_edge_v1/market_data/plans/...
```

### Run Tests

```bash
pytest tests/ -x -q
```

No tests exist yet for the dashboard API or frontend (test coverage is a known gap). When adding new endpoints or pages, add at least a smoke test that the endpoint returns 200 and the expected keys.

---

## 10. Agent Task Delegation Rules

- **Claude / Claude Code**: Full-stack tasks — DB schema, API, frontend, ingest pipeline, analysis scripts. Can modify any file in the codebase.
- **Codex**: Self-contained new modules only (e.g., new analysis script, new utility function). Do NOT delegate tasks that modify existing files that other agents are actively editing. See `memory/feedback_minimax_task_scope.md` for the MiniMax equivalent of this rule.
- **MiniMax**: Same rule as Codex — only new, self-contained modules.

When delegating to Codex or MiniMax:
1. Specify the exact file(s) to create (full path)
2. Specify the exact interface/function signature expected
3. Specify which existing files to import from (don't let the agent invent new imports)
4. Do NOT ask it to modify `ingest/canonical.py`, `db/schema.sql`, or `execution_policy.py` — these are core contracts that require human/Claude review.
