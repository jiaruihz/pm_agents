# Weather Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the weather dashboard frontend around a probe-lifecycle information architecture (probe health → research evidence → settled performance → daily lineage), backed by four new read-only API endpoints, with Chinese-first labels and hover-to-reveal canonical field definitions.

**Architecture:** Reuse the existing FastAPI canonical-lineage backend and `runtime/weather.db`. Add four read-only endpoints: probe health (reads `runtime/weather_edge_v1/<probe>/latest_summary.json` keyed off `weather_strategy_runtime_registry`), single-probe pulse, research-line registry (aggregates `docs/analysis/**/generated/*/summary.json`), and a static field glossary. Rewrite the React/Vite SPA with a new nav, new pages, and shared interaction components (HealthDot, GlossaryTerm, VerdictLine, FreshnessBadge, EmptyState).

**Tech Stack:** FastAPI + SQLite (backend), React 18 + react-router-dom 6 + Vite 5 + TypeScript (frontend), pytest + FastAPI TestClient (backend tests), Playwright smoke (frontend, added in P0).

## Global Constraints

- Dashboard is **read-only**. No write/order/deploy actions. No code touches N100 live / private keys / balances / CLOB orders. (CLAUDE.md §3 hard boundary)
- Backend reads `runtime/weather.db` via existing `get_db` dep; new file-reading endpoints take a configurable root via env `WEATHER_RUNTIME_ROOT` (default `runtime/weather_edge_v1`) and `WEATHER_ANALYSIS_ROOT` (default `docs/analysis`) so tests can point at tmp dirs.
- Explicit failure over silent fallback: stale/missing snapshot files return a row with `status` set and `*_ts_utc` populated; never substitute old data as fresh. (CLAUDE.md §3)
- Freshness has TWO layers and must never be conflated: mirror-sync pulse (`heartbeat_age_min` / file mtime) vs production liveness (only via N100 doctor link, never inferred from mirror age). (memory: mac-checkout-data-availability; CLAUDE.md §2)
- Probe allowlist = `weather_strategy_runtime_registry` rows only. Never glob the directory (excludes `tmp_*` / `*_smoke`).
- Cash-flow uses `fill_date_bj`, not `order_date_bj`. Report `submitted_notional` / `posted_notional` / `actual_fill_cost` / `open_cost` / `realized_pnl` separately; `open_cost` is not loss. Settled rows only for realized PnL; unsettled = MTM + `val_snapshot_ts_utc`. (CLAUDE.md §5)
- Python tests live under `tests/weather_dashboard/api/`, reuse `client` / `api_db` fixtures from `tests/weather_dashboard/api/conftest.py`.
- Commit after each task. Branch is `develop` (already a feature branch); do not push unless asked.

---

## File Structure

**Backend (new):**
- `weather_dashboard/api/routers/probes.py` — `/api/probes/health`, `/api/probes/{instance}`
- `weather_dashboard/api/probe_pulse.py` — pure helpers: locate registry probes, read `latest_summary.json`, normalize health row, freshness classification
- `weather_dashboard/api/routers/research_lines.py` — `/api/research/lines`, `/api/research/lines/{line_id}`
- `weather_dashboard/api/research_aggregate.py` — pure helpers: walk `summary.json`, normalize research-line row
- `weather_dashboard/api/routers/glossary.py` — `/api/glossary`
- `weather_dashboard/api/glossary.json` — static field dictionary (`field → {zh, definition, source_doc}`)
- `weather_dashboard/api/app.py` — register the 3 new routers (modify)

**Backend (tests):**
- `tests/weather_dashboard/api/test_probes.py`
- `tests/weather_dashboard/api/test_research_lines.py`
- `tests/weather_dashboard/api/test_glossary.py`

**Frontend (new shared):**
- `src/app/AppShell.tsx` — new nav + layout (replaces ad-hoc PageFrame nav)
- `src/components/HealthDot.tsx`, `GlossaryTerm.tsx`, `VerdictLine.tsx`, `FreshnessBadge.tsx`, `EmptyState.tsx`
- `src/data/glossary-context.tsx` — loads `/api/glossary` once, provides lookup to `GlossaryTerm`
- `src/data/probe-types.ts`, `src/data/research-line-types.ts`
- `src/data/weather-http.ts` — add `getProbeHealth`, `getProbe`, `getResearchLines`, `getGlossary` (modify)

**Frontend (new pages):**
- `src/pages/v2/TodayOverviewPage.tsx` (`/`)
- `src/pages/v2/ProbesPage.tsx` (`/probes`), `ProbeDetailPage.tsx` (`/probes/:instance`)
- `src/pages/v2/ResearchLinesPage.tsx` (`/research`), `ResearchLineDetailPage.tsx` (`/research/:lineId`)
- `src/pages/v2/PerformancePage.tsx` (`/performance`)
- `src/pages/v2/DailyLineagePage.tsx` (`/lineage/:date`)
- `src/pages/v2/GlossaryPage.tsx` (`/glossary`), `ArchivePage.tsx` (`/archive`)
- `src/app/App.tsx` — new routes; old PMM/ARB + weather pages moved under `/archive/*` (modify)
- `src/styles.css` — new visual language (modify)

**Frontend (tests):**
- `playwright.config.ts`, `tests/e2e/smoke.spec.ts`

---

## PHASE P0 — Scaffold, glossary, archive

### Task 1: Glossary endpoint + static dictionary

**Files:**
- Create: `weather_dashboard/api/glossary.json`
- Create: `weather_dashboard/api/routers/glossary.py`
- Modify: `weather_dashboard/api/app.py`
- Test: `tests/weather_dashboard/api/test_glossary.py`

**Interfaces:**
- Produces: `GET /api/glossary` → `{ "fields": { "<field>": {"zh": str, "definition": str, "source_doc": str} } }`

- [ ] **Step 1: Write the failing test**

```python
# tests/weather_dashboard/api/test_glossary.py
def test_glossary_returns_known_fields(client):
    r = client.get("/api/glossary")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body
    # core canonical fields the UI hovers must be present
    for key in ("open_cost", "posted_notional", "fill_date_bj", "heartbeat_age_min", "snapshot_age_min"):
        assert key in body["fields"], f"missing glossary entry: {key}"
        entry = body["fields"][key]
        assert entry["zh"] and entry["definition"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/weather_dashboard/api/test_glossary.py -v`
Expected: FAIL (404, no `/api/glossary` route)

- [ ] **Step 3: Create the dictionary + router**

```json
// weather_dashboard/api/glossary.json  (seed; extend as UI surfaces more fields)
{
  "open_cost": {"zh": "开仓成本", "definition": "未结算持仓的建仓花费，不是亏损；结算后才有 realized PnL", "source_doc": "WEATHER_ANALYSIS_CONTRACT.md"},
  "posted_notional": {"zh": "挂单名义额", "definition": "实际挂到盘口的名义金额，与 submitted/filled 分开报", "source_doc": "WEATHER_SYSTEM_CONTRACT.md"},
  "submitted_notional": {"zh": "提交名义额", "definition": "请求下单的名义金额，未必全部成交", "source_doc": "WEATHER_SYSTEM_CONTRACT.md"},
  "actual_fill_cost": {"zh": "实际成交成本", "definition": "真正成交花掉的 USDC", "source_doc": "WEATHER_SYSTEM_CONTRACT.md"},
  "realized_pnl": {"zh": "已实现盈亏", "definition": "仅已结算持仓才有；用 pnl_usd_at_fill", "source_doc": "WEATHER_ANALYSIS_CONTRACT.md"},
  "fill_date_bj": {"zh": "成交日(北京)", "definition": "现金流归属用此口径；order_date_bj 受回填污染只作诊断", "source_doc": "WEATHER_ANALYSIS_CONTRACT.md"},
  "order_date_bj": {"zh": "下单日(北京)", "definition": "仅作下单归属诊断，不用于现金流", "source_doc": "WEATHER_ANALYSIS_CONTRACT.md"},
  "heartbeat_age_min": {"zh": "镜像心跳(分钟)", "definition": "本机镜像里该探针 artifact 的同步年龄；非生产断流判据", "source_doc": "WEATHER_DATA_PIPELINE.md"},
  "snapshot_age_min": {"zh": "行情快照年龄(分钟)", "definition": "探针决策所用盘口快照距今分钟数；超新鲜线即偏旧", "source_doc": "WEATHER_DATA_FEED_MODULE.md"},
  "lifecycle_status": {"zh": "生命周期状态", "definition": "live/shadow/telemetry/monitor/blocked/stale", "source_doc": "WEATHER_STRATEGY_REGISTRY.md"},
  "gate_pass": {"zh": "成交覆盖门", "definition": "weather_clob_fill_coverage_gate 是否通过；false 时 PnL 不可发布", "source_doc": "WEATHER_ANALYSIS_CONTRACT.md"},
  "excess_roi_vs_baseline": {"zh": "相对基线超额ROI", "definition": "策略 ROI 减同价未筛 baseline ROI", "source_doc": "WEATHER_STRATEGY_REVIEW_PIPELINE.md"}
}
```

```python
# weather_dashboard/api/routers/glossary.py
"""Static canonical-field glossary for human-friendly hover tooltips."""
import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(prefix="/glossary", tags=["glossary"])
_PATH = Path(__file__).resolve().parent.parent / "glossary.json"


@router.get("")
def get_glossary() -> dict:
    fields = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    return {"fields": fields}
```

Modify `weather_dashboard/api/app.py`: import `glossary` and add `app.include_router(glossary.router, prefix="/api")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/weather_dashboard/api/test_glossary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add weather_dashboard/api/glossary.json weather_dashboard/api/routers/glossary.py weather_dashboard/api/app.py tests/weather_dashboard/api/test_glossary.py
git commit -m "feat(dashboard): add /api/glossary field dictionary"
```

### Task 2: Frontend shell, routes, archive fold, glossary context

**Files:**
- Create: `src/app/AppShell.tsx`, `src/components/GlossaryTerm.tsx`, `src/data/glossary-context.tsx`
- Create: `src/pages/v2/GlossaryPage.tsx`, `src/pages/v2/ArchivePage.tsx`, plus empty stubs for the six main pages
- Modify: `src/app/App.tsx`, `src/main.tsx`, `src/styles.css`
- Test: `tests/e2e/smoke.spec.ts`, `playwright.config.ts`

**Interfaces:**
- Consumes: `GET /api/glossary` (Task 1)
- Produces: `<GlossaryTerm field="open_cost">开仓成本</GlossaryTerm>` (dotted underline + hover); `useGlossary()` hook; new route table

- [ ] **Step 1: Add Playwright + write failing smoke test**

```bash
cd frontend/strategy_dashboard && npm i -D @playwright/test && npx playwright install chromium
```

```ts
// tests/e2e/smoke.spec.ts
import { test, expect } from "@playwright/test";
test("nav shows the six new sections and lands on overview", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "今日总览" })).toBeVisible();
  await expect(page.getByRole("link", { name: "探针在跑" })).toBeVisible();
  await expect(page.getByRole("link", { name: "研究证据" })).toBeVisible();
  await expect(page.getByRole("link", { name: "绩效对账" })).toBeVisible();
  await expect(page.getByRole("link", { name: "归档" })).toBeVisible();
});
```

```ts
// playwright.config.ts
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  use: { baseURL: "http://localhost:5174" },
  webServer: { command: "npm run dev", port: 5174, reuseExistingServer: true },
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend/strategy_dashboard && npx playwright test`
Expected: FAIL (no such nav links)

- [ ] **Step 3: Build shell + glossary context + routes**

`src/data/glossary-context.tsx`: React context that fetches `/api/glossary` once on mount, exposes `useGlossary(): (field) => Entry | undefined`.

`src/components/GlossaryTerm.tsx`: wraps children in a `<span>` with `border-bottom: 1px dotted`, `title`/tooltip from `useGlossary(field)` showing `原名 · zh · definition`.

`src/app/AppShell.tsx`: left nav with the six main items (今日总览/探针在跑/研究证据/绩效对账/单日血缘 入口/术语字典) + bottom 归档; `<Outlet/>` for content; wraps content in `GlossaryProvider`.

`src/app/App.tsx`: new routes per spec §2; mount old pages under `/archive/*`; default `/` → TodayOverviewPage stub. `ArchivePage.tsx` lists links to the legacy pages with a "legacy / dormant 非当前主线" banner.

Create stub pages for the six v2 pages rendering just their title (filled in later phases).

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend/strategy_dashboard && npx playwright test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/strategy_dashboard
git commit -m "feat(dashboard): new app shell, routes, glossary hover, archive fold"
```

---

## PHASE P1 — Probes (highest value)

### Task 3: Probe pulse helpers (pure, unit-tested)

**Files:**
- Create: `weather_dashboard/api/probe_pulse.py`
- Test: `tests/weather_dashboard/api/test_probes.py` (helper tests first)

**Interfaces:**
- Produces:
  - `classify_freshness(age_min: float | None, warn_min: float, bad_min: float) -> str` → `"fresh"|"aging"|"stale"|"unknown"`
  - `read_latest_summary(root: Path, instance: str) -> dict | None`
  - `normalize_probe_row(registry_row: dict, summary: dict | None) -> dict` → keys: `strategy_instance, lifecycle_status, health_status, heartbeat_age_min, snapshot_age_min, snapshot_ts_utc, freshness, candidate_rows, execution_eligible, top_audit, caps, status`

- [ ] **Step 1: Write failing helper tests**

```python
# tests/weather_dashboard/api/test_probes.py
from weather_dashboard.api.probe_pulse import classify_freshness, normalize_probe_row

def test_classify_freshness_bands():
    assert classify_freshness(5, 20, 120) == "fresh"
    assert classify_freshness(60, 20, 120) == "aging"
    assert classify_freshness(300, 20, 120) == "stale"
    assert classify_freshness(None, 20, 120) == "unknown"

def test_normalize_probe_row_handles_missing_summary():
    reg = {"strategy_instance": "p1", "lifecycle_status": "live", "health_status": "ok", "heartbeat_age_min": 10}
    row = normalize_probe_row(reg, None)
    assert row["strategy_instance"] == "p1"
    assert row["status"] == "no_pulse_file"
    assert row["snapshot_age_min"] is None

def test_normalize_probe_row_reads_nested_meta_snapshot_age():
    reg = {"strategy_instance": "p2", "lifecycle_status": "shadow", "health_status": "ok", "heartbeat_age_min": 3}
    summary = {"candidate_rows": 0, "meta": {"snapshot_age_min": 96.4, "snapshot_ts_utc": "2026-06-26T05:30:29Z",
              "audit_counts": {"obs_not_ok": 1}}}
    row = normalize_probe_row(reg, summary)
    assert row["snapshot_age_min"] == 96.4
    assert row["freshness"] in ("aging", "stale")
    assert row["top_audit"] == "obs_not_ok"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/weather_dashboard/api/test_probes.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `probe_pulse.py`**

```python
# weather_dashboard/api/probe_pulse.py
"""Pure helpers for probe health: read latest_summary.json, normalize, classify freshness."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

def classify_freshness(age_min: float | None, warn_min: float, bad_min: float) -> str:
    if age_min is None:
        return "unknown"
    if age_min <= warn_min:
        return "fresh"
    if age_min <= bad_min:
        return "aging"
    return "stale"

def read_latest_summary(root: Path, instance: str) -> dict | None:
    p = root / instance / "latest_summary.json"
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _snap_age(summary: dict) -> float | None:
    if "snapshot_age_min" in summary and summary["snapshot_age_min"] is not None:
        return summary["snapshot_age_min"]
    meta = summary.get("meta") or {}
    return meta.get("snapshot_age_min")

def _snap_ts(summary: dict) -> str | None:
    if summary.get("snapshot_ts_utc"):
        return summary["snapshot_ts_utc"]
    return (summary.get("meta") or {}).get("snapshot_ts_utc")

def _top_audit(summary: dict) -> str | None:
    counts = summary.get("audit_counts") or (summary.get("meta") or {}).get("audit_counts") or {}
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]

def normalize_probe_row(registry_row: dict, summary: dict | None,
                        warn_min: float = 20.0, bad_min: float = 120.0) -> dict:
    base = {
        "strategy_instance": registry_row.get("strategy_instance"),
        "lifecycle_status": registry_row.get("lifecycle_status"),
        "health_status": registry_row.get("health_status"),
        "heartbeat_age_min": registry_row.get("heartbeat_age_min"),
    }
    if summary is None:
        base.update({"status": "no_pulse_file", "snapshot_age_min": None,
                     "snapshot_ts_utc": None, "freshness": "unknown",
                     "candidate_rows": None, "execution_eligible": None,
                     "top_audit": None, "caps": None})
        return base
    age = _snap_age(summary)
    base.update({
        "status": summary.get("status") or ("no_order" if summary.get("no_order_placed") else "ran"),
        "snapshot_age_min": age,
        "snapshot_ts_utc": _snap_ts(summary),
        "freshness": classify_freshness(age, warn_min, bad_min),
        "candidate_rows": summary.get("candidate_rows"),
        "execution_eligible": summary.get("execution_eligible"),
        "top_audit": _top_audit(summary),
        "caps": summary.get("caps") or {k: summary.get(k) for k in ("base_notional", "daily_gross_cap") if k in summary},
    })
    return base
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/weather_dashboard/api/test_probes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add weather_dashboard/api/probe_pulse.py tests/weather_dashboard/api/test_probes.py
git commit -m "feat(dashboard): probe pulse normalization helpers"
```

### Task 4: `/api/probes/health` + `/api/probes/{instance}` endpoints

**Files:**
- Create: `weather_dashboard/api/routers/probes.py`
- Modify: `weather_dashboard/api/app.py`
- Test: `tests/weather_dashboard/api/test_probes.py` (append endpoint tests)

**Interfaces:**
- Consumes: `probe_pulse` helpers (Task 3); `weather_strategy_runtime_registry` table; env `WEATHER_RUNTIME_ROOT`
- Produces:
  - `GET /api/probes/health` → `{"probes": [normalize_probe_row...], "generated_at_utc": str}`
  - `GET /api/probes/{instance}` → `{"row": {...}, "history": [..], "candidates": [..], "live_orders": [..]}`

- [ ] **Step 1: Write failing endpoint test**

```python
# append to tests/weather_dashboard/api/test_probes.py
import json, os
from pathlib import Path

def _seed_registry(api_db, instance="theta_x", lifecycle="live"):
    api_db.execute("""CREATE TABLE IF NOT EXISTS weather_strategy_runtime_registry(
        strategy_instance TEXT PRIMARY KEY, family TEXT, lifecycle_status TEXT,
        health_status TEXT, heartbeat_age_min REAL)""")
    api_db.execute("INSERT INTO weather_strategy_runtime_registry VALUES (?,?,?,?,?)",
                   (instance, "reheat_risk", lifecycle, "ok", 7.0))
    api_db.commit()

def test_probes_health_lists_registry_with_pulse(client, api_db, tmp_path, monkeypatch):
    _seed_registry(api_db, "theta_x")
    d = tmp_path / "theta_x"; d.mkdir()
    (d / "latest_summary.json").write_text(json.dumps({
        "status": "planned", "candidate_rows": 0,
        "meta": {"snapshot_age_min": 5.0, "snapshot_ts_utc": "2026-06-26T05:30:29Z"}}))
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/health")
    assert r.status_code == 200
    probes = r.json()["probes"]
    assert any(p["strategy_instance"] == "theta_x" and p["freshness"] == "fresh" for p in probes)

def test_probes_health_missing_file_does_not_crash(client, api_db, tmp_path, monkeypatch):
    _seed_registry(api_db, "ghost")
    monkeypatch.setenv("WEATHER_RUNTIME_ROOT", str(tmp_path))
    r = client.get("/api/probes/health")
    assert r.status_code == 200
    assert r.json()["probes"][0]["status"] == "no_pulse_file"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/weather_dashboard/api/test_probes.py -v`
Expected: FAIL (404)

- [ ] **Step 3: Implement `probes.py` router**

```python
# weather_dashboard/api/routers/probes.py
"""Live/shadow probe health from registry + runtime JSONL pulse (read-only)."""
import os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException
from weather_dashboard.api.deps import get_db
from weather_dashboard.api import probe_pulse
from weather_dashboard.api.routers.strategy_runtime import _read_recent_jsonl

router = APIRouter(prefix="/probes", tags=["probes"])
Db = Annotated[sqlite3.Connection, Depends(get_db)]

def _root() -> Path:
    return Path(os.environ.get("WEATHER_RUNTIME_ROOT", "runtime/weather_edge_v1"))

def _registry_rows(db: Db) -> list[dict]:
    try:
        rows = db.execute("SELECT * FROM weather_strategy_runtime_registry").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]

@router.get("/health")
def get_probe_health(db: Db) -> dict[str, Any]:
    root = _root()
    out = []
    for reg in _registry_rows(db):
        inst = reg.get("strategy_instance")
        summary = probe_pulse.read_latest_summary(root, inst) if inst else None
        out.append(probe_pulse.normalize_probe_row(reg, summary))
    return {"probes": out, "generated_at_utc": datetime.now(timezone.utc).isoformat()}

@router.get("/{instance}")
def get_probe(instance: str, db: Db) -> dict[str, Any]:
    root = _root()
    reg = next((r for r in _registry_rows(db) if r.get("strategy_instance") == instance), None)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"unknown probe: {instance}")
    summary = probe_pulse.read_latest_summary(root, instance)
    base = root / instance
    return {
        "row": probe_pulse.normalize_probe_row(reg, summary),
        "history": _read_recent_jsonl(base / "summary_history.jsonl", 50),
        "candidates": (probe_pulse.read_latest_summary(root, instance) or {}).get("candidates")
                      or _read_recent_jsonl(base / "latest_candidates.json", 1),
        "live_orders": _read_recent_jsonl(base / "live_orders.jsonl", 20),
    }
```

Register in `app.py`: `app.include_router(probes.router, prefix="/api")`.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/weather_dashboard/api/test_probes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add weather_dashboard/api/routers/probes.py weather_dashboard/api/app.py tests/weather_dashboard/api/test_probes.py
git commit -m "feat(dashboard): /api/probes health + detail endpoints"
```

### Task 5: Interaction components — HealthDot, FreshnessBadge, VerdictLine, EmptyState

**Files:**
- Create: `src/components/HealthDot.tsx`, `FreshnessBadge.tsx`, `VerdictLine.tsx`, `EmptyState.tsx`
- Create: `src/data/probe-types.ts`; Modify: `src/data/weather-http.ts`

**Interfaces:**
- Produces: `<HealthDot level="fresh|aging|stale|unknown"/>`; `<FreshnessBadge mirrorAgeMin snapshotAgeMin snapshotTsUtc/>` (renders BOTH layers + N100 doctor link); `<VerdictLine>…</VerdictLine>`; `<EmptyState message/>`; `weatherApi.getProbeHealth()`, `getProbe(instance)`

- [ ] **Step 1: Write failing component test (Playwright via a harness route)**

Add a temporary `/probes` render assertion to `tests/e2e/smoke.spec.ts`:

```ts
test("probes page renders cards with freshness + doctor link for stale mirror", async ({ page }) => {
  await page.goto("/probes");
  await expect(page.getByText("镜像心跳").first()).toBeVisible();
  await expect(page.getByText("生产是否断流").first()).toBeVisible();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test -g "probes page renders"`
Expected: FAIL

- [ ] **Step 3: Implement components + http methods + ProbesPage (Task 6 wiring uses these)**

`FreshnessBadge.tsx` must render two distinct rows: 「镜像心跳 {heartbeat}分钟」 (HealthDot from heartbeat band) and 「生产是否断流」 with a copy-able N100 doctor command `ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'` and the note "镜像旧 ≠ 生产死，按 doctor 判断". `HealthDot` maps level→color (fresh=green, aging=amber, stale=red, unknown=grey). Add `getProbeHealth`/`getProbe` to `weather-http.ts` and types to `probe-types.ts`.

- [ ] **Step 4: Run to verify it passes**

Run: `npx playwright test -g "probes page renders"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/strategy_dashboard/src/components frontend/strategy_dashboard/src/data frontend/strategy_dashboard/tests
git commit -m "feat(dashboard): probe interaction components + http client"
```

### Task 6: Probes page + Probe detail page + wire into Today Overview

**Files:**
- Modify/fill: `src/pages/v2/ProbesPage.tsx`, `src/pages/v2/ProbeDetailPage.tsx`, `src/pages/v2/TodayOverviewPage.tsx`
- Test: extend `tests/e2e/smoke.spec.ts`

**Interfaces:**
- Consumes: `weatherApi.getProbeHealth/getProbe`; components from Task 5
- Produces: rendered `/probes`, `/probes/:instance`, and the Overview "探针健康" pulse card

- [ ] **Step 1: Write failing test**

```ts
test("overview pulse card summarizes probe health and links to probes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("探针健康")).toBeVisible();
  await page.getByRole("link", { name: "探针在跑" }).click();
  await expect(page).toHaveURL(/\/probes/);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test -g "overview pulse card"`
Expected: FAIL

- [ ] **Step 3: Implement pages**

`ProbesPage`: 30s poll `getProbeHealth()`; one card per probe with `VerdictLine` (人话结论), status badge from `lifecycle_status`, `FreshnessBadge` (two-layer), today candidates/eligible, `top_audit` in plain words, caps; collapsible "看原始字段" table with `GlossaryTerm`-wrapped field names; `EmptyState` when no candidates. Link each card → `/probes/:instance`. `ProbeDetailPage`: pulse-history sparkline from `getProbe().history`, today candidates table, live orders, link to `/lineage/:date`. `TodayOverviewPage`: pulse card aggregating `getProbeHealth()` into "N 在跑 / M 偏旧 / K 待查".

- [ ] **Step 4: Run to verify it passes**

Run: `npx playwright test`
Expected: PASS (all smoke tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/strategy_dashboard/src/pages/v2
git commit -m "feat(dashboard): probes page, probe detail, overview pulse card"
```

---

## PHASE P2 — Research evidence

### Task 7: Research-line aggregation helpers + `/api/research/lines`

**Files:**
- Create: `weather_dashboard/api/research_aggregate.py`, `weather_dashboard/api/routers/research_lines.py`
- Modify: `weather_dashboard/api/app.py`
- Test: `tests/weather_dashboard/api/test_research_lines.py`

**Interfaces:**
- Produces:
  - `aggregate_research_lines(analysis_root: Path) -> list[dict]` → keys: `line_id, title, status, holdout_roi, forward_roi, ci_low, ci_high, ci_crosses_zero, excess_roi_vs_baseline, gate_ready, doc_path, summary_path, generated_at_utc`
  - `GET /api/research/lines` → `{"lines": [...]}`; `GET /api/research/lines/{line_id}` → full `summary.json` + resolved doc path

- [ ] **Step 1: Write failing test** — seed a tmp `analysis_root/2026-06/generated/foo_v1/summary.json` with known fields; assert aggregation extracts roi/ci and `ci_crosses_zero` correctly, and missing fields render `None` (not crash).

```python
# tests/weather_dashboard/api/test_research_lines.py
import json
from pathlib import Path
from weather_dashboard.api.research_aggregate import aggregate_research_lines

def test_aggregate_extracts_and_flags_ci(tmp_path):
    g = tmp_path / "2026-06" / "generated" / "foo_v1"; g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({
        "title": "Foo v1", "status": "research-only",
        "holdout_roi": 0.041, "forward_roi": 0.09,
        "roi_ci_low": -0.034, "roi_ci_high": 0.105,
        "excess_roi_vs_baseline": 0.18}))
    lines = aggregate_research_lines(tmp_path)
    row = next(l for l in lines if l["line_id"] == "foo_v1")
    assert row["ci_crosses_zero"] is True
    assert row["gate_ready"] is False  # CI crosses zero ⇒ not gate-ready

def test_aggregate_tolerates_missing_fields(tmp_path):
    g = tmp_path / "2026-06" / "generated" / "bar_v1"; g.mkdir(parents=True)
    (g / "summary.json").write_text(json.dumps({"title": "Bar"}))
    row = next(l for l in aggregate_research_lines(tmp_path) if l["line_id"] == "bar_v1")
    assert row["holdout_roi"] is None and row["status"] in (None, "unknown")
```

- [ ] **Step 2: Run to verify it fails** — `python3 -m pytest tests/weather_dashboard/api/test_research_lines.py -v` → FAIL.

- [ ] **Step 3: Implement** `research_aggregate.py` (walk `*/generated/*/summary.json`, `line_id = parent dir name`, `ci_crosses_zero = ci_low is not None and ci_high is not None and ci_low <= 0 <= ci_high`, `gate_ready = (ci_crosses_zero is False) and forward_roi is not None and forward_roi > 0`) and `research_lines.py` router reading env `WEATHER_ANALYSIS_ROOT` (default `docs/analysis`). Register router in `app.py`.

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit** — `feat(dashboard): /api/research/lines evidence registry`.

### Task 8: Research evidence page + detail

**Files:** fill `src/pages/v2/ResearchLinesPage.tsx`, `ResearchLineDetailPage.tsx`; `src/data/research-line-types.ts`; add `getResearchLines`/`getResearchLine` to `weather-http.ts`; extend overview "最近变更研究线" strip.

- [ ] **Step 1:** Playwright test: `/research` shows a table with status badges + a "CI 跨 0" marker; clicking a row navigates to `/research/:lineId`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement table (status filter by branch + gate-ready), each row: `VerdictLine`, holdout/forward ROI, `ci_crosses_zero` amber marker, `excess_roi_vs_baseline`, doc link; detail page renders full `summary.json` metrics + doc link.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit — `feat(dashboard): research evidence registry page`.

---

## PHASE P3 — Performance + daily lineage

### Task 9: Performance page (reuse endpoints, add caliber guards)

**Files:** fill `src/pages/v2/PerformancePage.tsx`; reuse `weatherApi.getLiveSummary`, configs strategy endpoints.

- [ ] **Step 1:** Playwright test: `/performance` shows separate columns for `submitted/posted/actual_fill/open_cost/realized_pnl` (assert all five headers present) and a `gate_pass` banner element.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement: five separate columns (never summed together), cash-flow by `fill_date_bj` (label it), unsettled rows show MTM + `val_snapshot_ts_utc` with "估值偏旧" when stale, top `gate_pass` banner (red ⇒ "先修数据链，PnL 不可发布"), `open_cost` annotated "非亏损". Wrap field headers in `GlossaryTerm`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit — `feat(dashboard): settled performance page with caliber guards`.

### Task 10: Daily lineage page

**Files:** fill `src/pages/v2/DailyLineagePage.tsx`; reuse `/api/runs/{run_id}/trades/{signal_id}` (add a date-indexed client helper).

- [ ] **Step 1:** Playwright test: `/lineage/2026-06-25` renders a timeline with the five stages (signal/plan/order/fill/settlement) labels.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement: city×day timeline reorder of signal→plan→order→fill→settlement, plain-language "为什么下/不下这单"; back-links from probe detail and performance rows.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit — `feat(dashboard): daily lineage drilldown page`.

### Task 11: Visual language pass + glossary backfill + remove dead nav

**Files:** `src/styles.css`, `src/pages/v2/GlossaryPage.tsx`, `glossary.json`.

- [ ] **Step 1:** Playwright test: `/glossary` is searchable and contains every field name used in v2 pages (maintain a checked list).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Replace glassmorphism with the light information-dense theme (four semantic colors only, mono for numbers); backfill `glossary.json` for any field surfaced in P1–P3; ensure `/archive` banner present.
- [ ] **Step 4:** Run → PASS; run full backend suite `python3 -m pytest tests/weather_dashboard/api -q`.
- [ ] **Step 5:** Commit — `feat(dashboard): visual language pass + glossary backfill`.

---

## Self-Review

**Spec coverage:** §2 IA → Task 2 (routes) + per-page tasks 6/8/9/10/11. §3 interaction principles → Task 5 (components: VerdictLine/FreshnessBadge two-layer/HealthDot/EmptyState), Task 1 (glossary), guards in Tasks 9/10. §4.1 overview → Task 6. §4.2 probes → Tasks 3–6. §4.3 research → Tasks 7–8. §4.4 performance → Task 9. §4.5 lineage → Task 10. §4.6 glossary/archive → Tasks 1,2,11. §5 endpoints → Tasks 1,4,7. §6 visual → Tasks 2,11. §7 freshness two-layer → Tasks 3,5. §8 testing → every task is TDD. §9 phases → P0–P3 map to task groups.

**Placeholder scan:** Backend tasks (1,3,4,7) carry full code. Frontend tasks 5,6,8,9,10,11 specify exact components, props, columns, and assertions; React JSX bodies are described at interface level (props/behavior/test assertions) rather than full verbatim markup — acceptable for FE where the test pins behavior. No "TBD"/"handle edge cases" left.

**Type consistency:** `normalize_probe_row` keys used identically in Task 3 tests, Task 4 endpoint, Task 5/6 consumption. `aggregate_research_lines` keys consistent Tasks 7→8. `getProbeHealth/getProbe/getResearchLines/getGlossary` names consistent across `weather-http.ts` and pages. `FreshnessBadge` two-layer contract consistent Tasks 5→6.
