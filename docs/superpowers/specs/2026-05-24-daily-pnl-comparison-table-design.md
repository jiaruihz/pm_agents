# Daily P&L Comparison Table — Design Spec

**Goal:** Add a compact per-date summary table to `WeatherStrategyDetailPage` that lets the user compare paper vs CLOB execution PnL side-by-side, making execution gaps immediately readable without having to expand individual day cards.

**Architecture:** Pure frontend change. All required data is already computed by `buildDailyLedger()` in `WeatherStrategyDetailPage.tsx`. A new `DailyPnlComparisonTable` component renders those values as a table, placed directly above the existing `DailyLedgerSection` cards.

**Tech Stack:** React, TypeScript, inline styles (matching the existing page pattern).

---

## What Gets Added

### New component: `DailyPnlComparisonTable`

A compact HTML table rendered inside a `<Card>`, placed between the Execution Funnel section and the existing Daily Execution Ledger cards.

**Columns:**

| Column | Value | Notes |
|---|---|---|
| Date | `day.date` | |
| Paper PnL | `day.paper.pnl` | Only if `paper.settled > 0`, else `—` |
| CLOB PnL | `day.clob.pnl` | Only if `clob.settled > 0`, else `—` |
| Gap | `day.gapPnl` | `clob_pnl − paper_pnl`. Green if ≥ 0, red if < 0 |
| Missed Gap | `day.missedPnl` | Paper PnL on signals where CLOB had no fill. Red if < 0 (opportunity cost) |
| Exec Diff | `day.fillDelta` | `gap − missed_gap`: price execution quality on matched fills. Green ≥ 0, red < 0 |

**Totals row** at the bottom sums all six numeric columns across all days that have settled trades.

**Color rules:**
- Gap: green (`var(--ok)`) if ≥ 0, red (`var(--bad)`) if < 0
- Missed Gap: red if < 0 (opportunity cost), green if > 0 (avoided bad paper trades)
- Exec Diff: green if ≥ 0, red if < 0
- Totals row: bold, slightly highlighted background

### No changes to existing components

The `DailyLedgerSection` (expandable cards) stays exactly as-is below the table.

### Section layout after change

```
KPI Strip
Execution Funnel            ← existing
Daily P&L Comparison        ← NEW table
Daily Execution Ledger      ← existing expand/collapse cards (unchanged)
Equity Curve                ← existing
Open Positions              ← existing
Analytics                   ← existing
```

---

## P&L Attribution

Not a new module. The existing Analytics section (`by_city` / `by_bracket` / `by_model` / `by_side`) already provides P&L attribution by dimension. Deep factor analysis (signal quality decay, time series decomposition) belongs in offline Python notebooks, not the dashboard.

---

## Out of Scope

- No backend changes needed
- No new API endpoints
- No changes to `buildDailyLedger()` logic
- No changes to existing cards, equity curve, or analytics
