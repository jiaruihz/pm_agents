# Daily P&L Comparison Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compact per-date paper vs CLOB PnL comparison table above the existing Daily Execution Ledger cards, plus clean up visual style issues throughout the strategy detail page.

**Architecture:** Pure frontend change in one file. `buildDailyLedger()` already computes all required numbers; we just add a new `DailyPnlComparisonTable` component, insert it in the JSX, and tighten several style issues that make the page cluttered.

**Tech Stack:** React, TypeScript, inline styles (no new deps).

---

## File Map

| File | Change |
|---|---|
| `frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx` | Add `DailyPnlComparisonTable` component; simplify `DailyLedgerDay` header; side-by-side venue panels; `SectionHeader` accent bar; right-align numeric table headers |

---

## Task 1: Add `DailyPnlComparisonTable` component and insert it in the page

**Files:**
- Modify: `frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx`

The component reads `DailyLedgerRow[]` (already available as `dailyLedger` in the parent) and renders a compact summary table plus a totals row.

- [ ] **Step 1: Add `DailyPnlComparisonTable` function**

Add the following function immediately before the `// ── Daily Execution Ledger` comment (around line 578). This keeps related components together.

```tsx
// ── Daily P&L Comparison Table ────────────────────────────────────────────────

function DailyPnlComparisonTable({ days }: { days: DailyLedgerRow[] }) {
  if (days.length === 0) return null;

  // Only include days that have at least one settled side
  const settled = days.filter(d => d.paper.settled > 0 || d.clob.settled > 0);
  if (settled.length === 0) return null;

  const totPaper   = settled.reduce((s, d) => s + (d.paper.settled > 0 ? d.paper.pnl : 0), 0);
  const totClob    = settled.reduce((s, d) => s + (d.clob.settled  > 0 ? d.clob.pnl  : 0), 0);
  const totGap     = settled.reduce((s, d) => s + (d.gapPnl ?? 0), 0);
  const totMissed  = settled.reduce((s, d) => s + d.missedPnl, 0);
  const totExecDiff = settled.reduce((s, d) => s + d.fillDelta, 0);

  const numCol: React.CSSProperties = { ...tdStyle, textAlign: "right", fontFamily: "monospace", fontVariantNumeric: "tabular-nums" };
  const numColBold: React.CSSProperties = { ...numCol, fontWeight: 700 };
  const thR: React.CSSProperties = { ...thStyle, textAlign: "right" };

  function pnlCell(v: number | null, show: boolean): React.ReactNode {
    if (!show || v == null) return <span style={{ color: "var(--muted)" }}>—</span>;
    return <span style={{ color: v >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>{usd(v)}</span>;
  }

  function gapCell(v: number | null): React.ReactNode {
    if (v == null) return <span style={{ color: "var(--muted)" }}>—</span>;
    return <span style={{ color: v >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 700 }}>{usd(v)}</span>;
  }

  return (
    <>
      <SectionHeader title="Daily P&L Comparison" subtitle="每日 paper vs CLOB 执行对比" />
      <Card style={{ padding: 0, marginBottom: 24 }}>
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Date</th>
                <th style={thR}>Paper PnL</th>
                <th style={thR}>CLOB PnL</th>
                <th style={thR}>Gap</th>
                <th style={thR}>Missed Gap</th>
                <th style={thR}>Exec Diff</th>
              </tr>
            </thead>
            <tbody>
              {settled.map(d => (
                <tr key={d.date} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={{ ...tdStyle, fontWeight: 700, fontFamily: "monospace" }}>{d.date}</td>
                  <td style={numCol}>{pnlCell(d.paper.pnl, d.paper.settled > 0)}</td>
                  <td style={numCol}>{pnlCell(d.clob.pnl,  d.clob.settled  > 0)}</td>
                  <td style={numCol}>{gapCell(d.gapPnl)}</td>
                  <td style={numCol}>
                    {d.missedCount > 0
                      ? <span style={{ color: d.missedPnl >= 0 ? "var(--bad)" : "var(--ok)", fontWeight: 600 }}>{usd(d.missedPnl)}</span>
                      : <span style={{ color: "var(--muted)" }}>—</span>}
                  </td>
                  <td style={numCol}>
                    {d.bothFilled > 0
                      ? <span style={{ color: d.fillDelta >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>{usd(d.fillDelta)}</span>
                      : <span style={{ color: "var(--muted)" }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr style={{ background: "rgba(128,128,128,0.06)", borderTop: "2px solid var(--stroke)" }}>
                <td style={{ ...tdStyle, fontWeight: 800, fontSize: 12, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>TOTAL</td>
                <td style={numColBold}><span style={{ color: totPaper >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totPaper)}</span></td>
                <td style={numColBold}><span style={{ color: totClob  >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totClob)}</span></td>
                <td style={numColBold}><span style={{ color: totGap   >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totGap)}</span></td>
                <td style={numColBold}><span style={{ color: totMissed >= 0 ? "var(--bad)" : "var(--ok)" }}>{usd(totMissed)}</span></td>
                <td style={numColBold}><span style={{ color: totExecDiff >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totExecDiff)}</span></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>
    </>
  );
}
```

- [ ] **Step 2: Insert `DailyPnlComparisonTable` in the JSX**

Find the block that currently reads (around line 317):
```tsx
            <DailyLedgerSection
              days={dailyLedger}
              expandedDays={expandedDays}
              onToggle={(date) => setExpandedDays(prev => ({ ...prev, [date]: !prev[date] }))}
            />
```

Replace it with:
```tsx
            {dailyLedger.length > 0 && <DailyPnlComparisonTable days={dailyLedger} />}

            <DailyLedgerSection
              days={dailyLedger}
              expandedDays={expandedDays}
              onToggle={(date) => setExpandedDays(prev => ({ ...prev, [date]: !prev[date] }))}
            />
```

- [ ] **Step 3: Verify in browser**

Open the strategy detail page (e.g. `http://localhost:5173/weather/strategies/live_weather_edge_v1_4ef9b3ec3e2e`).

Expected: a "Daily P&L Comparison" table appears above the existing Daily Execution Ledger section, showing one row per target date with Paper PnL / CLOB PnL / Gap / Missed Gap / Exec Diff columns, plus a TOTAL row at the bottom.

- [ ] **Step 4: Commit**

```bash
git add frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx
git commit -m "feat: add daily P&L comparison table (paper vs CLOB gap breakdown per date)"
```

---

## Task 2: Style improvements

**Files:**
- Modify: `frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx`

Four targeted improvements: SectionHeader accent bar, simplified DailyLedgerDay header, side-by-side venue panels, right-aligned numeric table headers.

- [ ] **Step 1: Add accent bar to SectionHeader**

Find:
```tsx
function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{subtitle}</div>
    </div>
  );
}
```

Replace with:
```tsx
function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12, paddingLeft: 10, borderLeft: "3px solid var(--accent)" }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{subtitle}</div>
    </div>
  );
}
```

- [ ] **Step 2: Simplify DailyLedgerDay header**

The current header has 10 `DailyStat` cells in a dense grid. Since the new comparison table handles aggregate comparisons, the card header only needs: date, quick fill count, and expand toggle.

Find the entire `DailyLedgerDay` function (lines ~615–661) and replace with:

```tsx
function DailyLedgerDay({
  day,
  expanded,
  onToggle,
}: {
  day: DailyLedgerRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const gapColor = day.gapPnl == null ? "var(--muted)" : day.gapPnl >= 0 ? "var(--ok)" : "var(--bad)";
  const hasSettled = day.paper.settled > 0 || day.clob.settled > 0;

  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <button type="button" onClick={onToggle} style={dayHeaderButtonStyle}>
        <div style={{ minWidth: 118 }}>
          <div style={{ fontSize: 15, fontWeight: 800 }}>{day.date}</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
            {expanded ? "Hide details ▲" : "Show orders ▼"}
          </div>
        </div>
        <div style={daySummaryGridStyle}>
          <DailyStat label="Paper Orders"    value={`${day.paper.orders} / ${day.paper.fills} filled`} />
          <DailyStat label="CLOB Orders"     value={`${day.clob.orders} / ${day.clob.fills} filled`} />
          <DailyStat label="Paper PnL"       value={day.paper.settled > 0 ? usd(day.paper.pnl) : "—"}
            color={day.paper.settled > 0 ? (day.paper.pnl >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"} />
          <DailyStat label="CLOB PnL"        value={day.clob.settled > 0  ? usd(day.clob.pnl)  : "—"}
            color={day.clob.settled > 0  ? (day.clob.pnl  >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"} />
          <DailyStat label="Execution Gap"   value={hasSettled && day.gapPnl != null ? usd(day.gapPnl) : "—"} color={gapColor} />
          <DailyStat label="Errors / No Fill" value={`${day.clob.errors} / ${day.clob.noFill}`}
            color={day.clob.errors || day.clob.noFill ? "var(--bad)" : "var(--muted)"} />
        </div>
        <div style={{ color: "var(--muted)", fontSize: 18, paddingLeft: 8 }}>{expanded ? "−" : "+"}</div>
      </button>
      {expanded && (
        <div style={dayExpandedStyle}>
          <VenuePanel stats={day.paper} />
          <VenuePanel stats={day.clob} />
        </div>
      )}
    </Card>
  );
}
```

- [ ] **Step 3: Make venue panels side-by-side**

Find:
```tsx
const dayExpandedStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: 14,
  padding: 16,
};
```

Replace with:
```tsx
const dayExpandedStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
  gap: 14,
  padding: 16,
};
```

This gives Paper and CLOB side-by-side on wide screens and stacks them on narrow screens.

- [ ] **Step 4: Verify in browser**

Reload the strategy detail page.

Expected:
- Every section header has a left accent bar in the accent colour
- Daily Ledger cards have a simpler 6-cell header (no longer 10 cells)
- Expanding a day shows Paper and CLOB panels side-by-side (or stacked on narrow viewport)

- [ ] **Step 5: Commit**

```bash
git add frontend/strategy_dashboard/src/pages/weather/WeatherStrategyDetailPage.tsx
git commit -m "style: section header accent bar, simplified day card header, side-by-side venue panels"
```

---

## Self-Review

**Spec coverage:**
- ✅ Compact table with Date / Paper PnL / CLOB PnL / Gap / Missed Gap / Exec Diff — Task 1
- ✅ Totals row — Task 1 Step 1 (`<tfoot>` block)
- ✅ Existing cards preserved (B option) — Task 1 Step 2 keeps `DailyLedgerSection`
- ✅ Style improvements — Task 2

**Placeholder scan:** None found.

**Type consistency:**
- `DailyLedgerRow.missedPnl` — exists in the type (line ~97), used correctly
- `DailyLedgerRow.fillDelta` — exists in the type (line ~103), used correctly
- `DailyLedgerRow.gapPnl` — `number | null`, handled with null guard in totals (`?? 0`) and in cell render
- `usd()` helper — defined at line 25–29, used for all PnL cells ✅
- `VenueStats.settled` — used for the "show if settled > 0" guard ✅
